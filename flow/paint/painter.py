#!/usr/bin/env python3
"""
FLOW paint — pincel determinista.

Un periférico gráfico mínimo y determinista para que cualquier modelo que
produzca TEXTO obtenga manos sobre el píxel, sin visión generativa, sin
diffusion y sin APIs especiales:

    programa .flowpaint (texto)
        ↓ run_paint_program
    PNG (bytes)

Superficie exacta (ni más ni menos): canvas, palette, cursor, pixel, line,
rect, fill, copy, mirror, undo, emit.

Lenguaje (una instrucción por línea, `#` comentarios, líneas vacías se
ignoran; strings con espacios van en "comillas dobles"):

    CANVAS 18 28            # obligatorio primero; 1..512 por lado
    COLOR skin_04 247 201 170 [a]   # define Y selecciona como pincel
    MOVE 9 4                # cursor (lo actualiza cada trazo)
    PIXEL 8 4 [color]       # color: nombre | #rrggbb | #rrggbbaa
    LINE 6 10 11 10 [color] # Bresenham inclusivo, 1px
    RECT 7 12 4 2 [color]   # x y w h, relleno (w,h >= 1)
    FILL 0 0 [color]        # inundación 4-conexa desde el punto
    COPY 5 4 8 2 5 14       # región x0 y0 w h desplazada a (x0+dx, y0+dy)
    MIRROR_X [eje]          # espejo vertical (eje = columna, def centro)
    MIRROR_Y [eje]          # espejo horizontal (eje = fila, def centro)
    UNDO [n]                # deshace n trazos (def 1)
    SAVE avatar.png         # intención de emisión (el llamador escribe)

Semántica DENY (fail-closed): CUALQUIER falta — fuera de canvas, color
desconocido, op desconocida, aridad/tipo/rango mal, UNDO en vacío, pintar
sin CANVAS, segundo CANVAS, base de distinto tamaño — aborta el programa
COMPLETO sin escribir nada. El llamador recibe PaintDeny con motivo
(`DENY OUT_OF_CANVAS`, ...). Nada se corrompe a medias, nunca.

Determinismo: cero aleatoriedad en v1 (sin RNG que sembrar) + dicts de
inserción ordenada. Mismo programa + misma base = mismos bytes, siempre.

Trazabilidad: cada trazo aplicado queda en result.trace (seq, op, args,
cursor) para auditoría y reversión manual.

El intérprete es PURO respecto al FS (recibe texto+bytes, devuelve bytes);
quien lo llama decide archivos. Así se testea sin tmpfiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]

MIN_DIM = 1
MAX_DIM = 512
UNDO_CAP = 1024


class PaintDeny(Exception):
    """Falta del PROGRAMA (no del entorno): aborta sin emitir nada."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"DENY {code}" + (f": {detail}" if detail else ""))


@dataclass
class PaintResult:
    ok: bool
    png_bytes: bytes | None = None
    error: str | None = None
    trace: list = field(default_factory=list)
    save_hint: str | None = None
    width: int = 0
    height: int = 0


def _parse_color(tok: str) -> tuple[int, int, int, int]:
    """#rrggbb | #rrggbbaa -> RGBA. Lanza PaintDeny si malforma."""
    t = tok.strip()
    if t.startswith('#'):
        h = t[1:]
        if len(h) == 6:
            h += 'ff'
        if len(h) != 8:
            raise PaintDeny('BAD_ARGS', f'color malformado {tok!r}')
        try:
            v = bytes.fromhex(h)
        except ValueError:
            raise PaintDeny('BAD_ARGS', f'color malformado {tok!r}')
        return (v[0], v[1], v[2], v[3])
    raise PaintDeny('BAD_ARGS', f'color malformado {tok!r}')


def _split(line: str) -> list[str]:
    """Tokeniza respetando "comillas dobles" (rutas con espacios)."""
    out: list[str] = []
    cur: list[str] = []
    in_q = False
    for ch in line:
        if ch == '"':
            in_q = not in_q
            continue
        if ch in (' ', '\t') and not in_q:
            if cur:
                out.append(''.join(cur))
                cur = []
            continue
        cur.append(ch)
    if in_q:
        raise PaintDeny('BAD_ARGS', 'comilla sin cerrar')
    if cur:
        out.append(''.join(cur))
    return out


def parse_program(source: str) -> list[tuple[str, list[str], int]]:
    """Texto -> [(OP, args, nlínea)]. Puro; no valida semántica (eso es run)."""
    ops: list[tuple[str, list[str], int]] = []
    for n, raw in enumerate(source.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        toks = _split(line)
        if not toks:
            continue
        ops.append((toks[0].upper(), toks[1:], n))
    return ops


class _Canvas:
    def __init__(self, w: int, h: int, base: bytes | None):
        self.w = w
        self.h = h
        # RGBA plano; base PNG decodificado o transparente.
        self.px = bytearray(w * h * 4)
        if base is not None:
            if Image is None:  # pragma: no cover
                raise PaintDeny('BASE_INVALID', 'PIL ausente')
            try:
                img = Image.open(__import__('io').BytesIO(base)).convert('RGBA')
            except Exception as e:
                raise PaintDeny('BASE_INVALID', f'base no decodifica: {e}')
            if img.size != (w, h):
                raise PaintDeny('BASE_MISMATCH', f'base {img.size[0]}x{img.size[1]} vs canvas {w}x{h}')
            self.px = bytearray(img.tobytes())
        # Pila de undo (instantáneas completas; a 18×28 son 2KB c/u).
        self._undo: list[bytearray] = []
        self.cx = 0
        self.cy = 0
        self.pen: tuple[int, int, int, int] = (0, 0, 0, 255)
        self.palette: dict[str, tuple[int, int, int, int]] = {}

    def _check(self, x: int, y: int, op: str) -> None:
        if not (0 <= x < self.w and 0 <= y < self.h):
            raise PaintDeny('OUT_OF_CANVAS', f'{op} ({x},{y}) fuera de {self.w}x{self.h}')

    def _snap(self) -> None:
        self._undo.append(bytearray(self.px))
        if len(self._undo) > UNDO_CAP:
            del self._undo[0]

    def _at(self, x: int, y: int) -> int:
        return (y * self.w + x) * 4

    def setpx(self, x: int, y: int, c: tuple[int, int, int, int], op: str) -> None:
        self._check(x, y, op)
        o = self._at(x, y)
        self.px[o:o + 4] = bytes(c)

    def line(self, x0: int, y0: int, x1: int, y1: int, c: tuple[int, int, int, int]) -> None:
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        x, y = x0, y0
        for _ in range(self.w + self.h + 2):
            self.setpx(x, y, c, 'LINE')
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy

    def rect(self, x: int, y: int, w: int, h: int, c: tuple[int, int, int, int]) -> None:
        if w < 1 or h < 1:
            raise PaintDeny('BAD_ARGS', f'RECT {w}x{h} inválido')
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.setpx(xx, yy, c, 'RECT')

    def fill(self, x: int, y: int, c: tuple[int, int, int, int]) -> None:
        self._check(x, y, 'FILL')
        target = bytes(self.px[self._at(x, y):self._at(x, y) + 4])
        if target == bytes(c):
            return
        stack = [(x, y)]
        seen = set()
        while stack:
            cx, cy = stack.pop()
            if (cx, cy) in seen:
                continue
            seen.add((cx, cy))
            if not (0 <= cx < self.w and 0 <= cy < self.h):
                continue
            o = self._at(cx, cy)
            if bytes(self.px[o:o + 4]) != target:
                continue
            self.px[o:o + 4] = bytes(c)
            stack.extend([(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)])

    def copy(self, x0: int, y0: int, w: int, h: int, dx: int, dy: int) -> None:
        if w < 1 or h < 1:
            raise PaintDeny('BAD_ARGS', f'COPY {w}x{h} inválido')
        # Valida origen Y destino ANTES de tocar nada (copia vía temporal).
        for yy in range(y0, y0 + h):
            for xx in range(x0, x0 + w):
                self._check(xx, yy, 'COPY-src')
                self._check(xx + dx, yy + dy, 'COPY-dst')
        tmp = bytearray(w * h * 4)
        for yy in range(h):
            for xx in range(w):
                s = self._at(x0 + xx, y0 + yy)
                tmp[(yy * w + xx) * 4:(yy * w + xx) * 4 + 4] = self.px[s:s + 4]
        for yy in range(h):
            for xx in range(w):
                d = self._at(x0 + xx + dx, y0 + yy + dy)
                t = (yy * w + xx) * 4
                self.px[d:d + 4] = tmp[t:t + 4]

    def mirror_x(self, axis: float | None) -> None:
        a = (self.w - 1) / 2 if axis is None else axis
        new = bytearray(self.px)
        for y in range(self.h):
            for x in range(self.w):
                sx = int(round(2 * a - x))
                if not (0 <= sx < self.w):
                    raise PaintDeny('OUT_OF_CANVAS', f'MIRROR_X eje {axis} saca x={sx}')
                s = self._at(sx, y)
                d = self._at(x, y)
                new[d:d + 4] = self.px[s:s + 4]
        self.px = new

    def mirror_y(self, axis: float | None) -> None:
        a = (self.h - 1) / 2 if axis is None else axis
        new = bytearray(self.px)
        for y in range(self.h):
            sy = int(round(2 * a - y))
            if not (0 <= sy < self.h):
                raise PaintDeny('OUT_OF_CANVAS', f'MIRROR_Y eje {axis} saca y={sy}')
            for x in range(self.w):
                s = self._at(x, sy)
                d = self._at(x, y)
                new[d:d + 4] = self.px[s:s + 4]
        self.px = new

    def undo(self, n: int) -> None:
        if n < 1:
            raise PaintDeny('BAD_ARGS', f'UNDO {n} inválido')
        for _ in range(n):
            if not self._undo:
                raise PaintDeny('EMPTY_UNDO', 'nada que deshacer')
            self.px = self._undo.pop()

    def emit_png(self) -> bytes:
        if Image is None:  # pragma: no cover
            raise PaintDeny('BASE_INVALID', 'PIL ausente')
        img = Image.frombytes('RGBA', (self.w, self.h), bytes(self.px))
        import io
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()


def _as_int(tok: str, what: str) -> int:
    try:
        v = int(tok, 10)
    except ValueError:
        raise PaintDeny('BAD_ARGS', f'{what} no es entero: {tok!r}')
    return v


def _as_num(tok: str, what: str) -> float:
    try:
        return float(tok)
    except ValueError:
        raise PaintDeny('BAD_ARGS', f'{what} no es número: {tok!r}')


def run_paint_program(source: str, base: bytes | None = None) -> PaintResult:
    """Ejecuta un programa .flowpaint. Puro respecto al FS (sin archivos).

    Devuelve PaintResult(ok, png_bytes?, error?, trace, save_hint?).
    Cualquier falta del programa -> ok False con `DENY CÓDIGO: detalle`,
    sin emitir nada a medias.
    """
    trace: list[dict] = []
    try:
        ops = parse_program(source)
    except PaintDeny as e:
        return PaintResult(ok=False, error=str(e))
    canvas: _Canvas | None = None
    save_hint: str | None = None
    # Paleta por etapas (COLOR antes de CANVAS): LOCAL por corrida, nunca
    # global — un global filtraría colores entre programas (no determinista).
    staged: dict[str, tuple[int, int, int, int]] = {}
    try:
        for op, args, nline in ops:
            if op == 'CANVAS':
                if canvas is not None:
                    raise PaintDeny('SECOND_CANVAS', f'línea {nline}')
                if len(args) != 2:
                    raise PaintDeny('BAD_ARGS', f'CANVAS necesita w h (línea {nline})')
                w, h = _as_int(args[0], 'w'), _as_int(args[1], 'h')
                if not (MIN_DIM <= w <= MAX_DIM and MIN_DIM <= h <= MAX_DIM):
                    raise PaintDeny('BAD_ARGS', f'CANVAS {w}x{h} fuera de {MIN_DIM}..{MAX_DIM}')
                canvas = _Canvas(w, h, base)
                canvas.palette.update(staged)
                staged.clear()
            elif op == 'COLOR':
                if len(args) not in (4, 5):
                    raise PaintDeny('BAD_ARGS', f'COLOR necesita nombre r g b [a] (línea {nline})')
                name = args[0]
                if not name.replace('_', '').isalnum():
                    raise PaintDeny('BAD_ARGS', f'nombre de color inválido {name!r}')
                try:
                    vals = [int(v) for v in args[1:]]
                except ValueError:
                    raise PaintDeny('BAD_ARGS', f'componentes no enteros (línea {nline})')
                if any(not 0 <= v <= 255 for v in vals):
                    raise PaintDeny('BAD_ARGS', f'componentes 0..255 (línea {nline})')
                rgba = (vals[0], vals[1], vals[2], vals[3] if len(vals) == 4 else 255)
                # La paleta vive en el canvas si ya existe; si no, en espera
                # hasta el CANVAS (COLOR antes de CANVAS es legal).
                if canvas is None:
                    staged[name] = rgba
                else:
                    canvas.palette[name] = rgba
                    canvas.pen = rgba
            elif op == 'MOVE':
                canvas = _need(canvas, nline, op)
                if len(args) != 2:
                    raise PaintDeny('BAD_ARGS', f'MOVE necesita x y (línea {nline})')
                x, y = _as_int(args[0], 'x'), _as_int(args[1], 'y')
                canvas._check(x, y, op)
                canvas.cx, canvas.cy = x, y
            elif op in ('PIXEL', 'LINE', 'RECT', 'FILL', 'COPY'):
                canvas = _need(canvas, nline, op)
                canvas._snap()
                try:
                    if op == 'PIXEL':
                        if len(args) not in (2, 3):
                            raise PaintDeny('BAD_ARGS', f'PIXEL necesita x y [color] (línea {nline})')
                        x, y = _as_int(args[0], 'x'), _as_int(args[1], 'y')
                        canvas.setpx(x, y, _color(canvas, args[2] if len(args) == 3 else None, nline), op)
                    elif op == 'LINE':
                        if len(args) not in (4, 5):
                            raise PaintDeny('BAD_ARGS', f'LINE necesita x0 y0 x1 y1 [color] (línea {nline})')
                        x0, y0, x1, y1 = (_as_int(a, 'coord') for a in args[:4])
                        canvas.line(x0, y0, x1, y1, _color(canvas, args[4] if len(args) == 5 else None, nline))
                    elif op == 'RECT':
                        if len(args) not in (4, 5):
                            raise PaintDeny('BAD_ARGS', f'RECT necesita x y w h [color] (línea {nline})')
                        x, y, w, h = (_as_int(a, 'dim') for a in args[:4])
                        canvas.rect(x, y, w, h, _color(canvas, args[4] if len(args) == 5 else None, nline))
                    elif op == 'FILL':
                        if len(args) not in (2, 3):
                            raise PaintDeny('BAD_ARGS', f'FILL necesita x y [color] (línea {nline})')
                        x, y = _as_int(args[0], 'x'), _as_int(args[1], 'y')
                        canvas.fill(x, y, _color(canvas, args[2] if len(args) == 3 else None, nline))
                    elif op == 'COPY':
                        if len(args) != 6:
                            raise PaintDeny('BAD_ARGS', f'COPY necesita x0 y0 w h dx dy (línea {nline})')
                        x0, y0, w, h, dx, dy = (_as_int(a, 'dim') for a in args)
                        canvas.copy(x0, y0, w, h, dx, dy)
                    canvas.cx, canvas.cy = _last_point(op, args)
                except PaintDeny:
                    # Revierte el snapshot del trazo fallido: o todo o nada.
                    if canvas._undo:
                        canvas.px = canvas._undo.pop()
                    raise
            elif op in ('MIRROR_X', 'MIRROR_Y'):
                canvas = _need(canvas, nline, op)
                if len(args) > 1:
                    raise PaintDeny('BAD_ARGS', f'{op} lleva a lo sumo eje (línea {nline})')
                axis = _as_num(args[0], 'eje') if args else None
                canvas._snap()
                try:
                    if op == 'MIRROR_X':
                        canvas.mirror_x(axis)
                    else:
                        canvas.mirror_y(axis)
                except PaintDeny:
                    if canvas._undo:
                        canvas.px = canvas._undo.pop()
                    raise
            elif op == 'UNDO':
                canvas = _need(canvas, nline, op)
                if len(args) > 1:
                    raise PaintDeny('BAD_ARGS', f'UNDO lleva a lo sumo n (línea {nline})')
                n = _as_int(args[0], 'n') if args else 1
                canvas.undo(n)
            elif op == 'SAVE':
                if len(args) != 1:
                    raise PaintDeny('BAD_ARGS', f'SAVE necesita ruta (línea {nline})')
                save_hint = args[0]
            else:
                raise PaintDeny('UNKNOWN_OP', f'{op} (línea {nline})')
            trace.append({'seq': len(trace), 'op': op, 'args': args})
        if canvas is None:
            raise PaintDeny('NO_CANVAS', 'programa sin CANVAS')
        return PaintResult(ok=True, png_bytes=canvas.emit_png(),
                           trace=trace, save_hint=save_hint,
                           width=canvas.w, height=canvas.h)
    except PaintDeny as e:
        return PaintResult(ok=False, error=str(e), trace=trace)


def _need(canvas: _Canvas | None, nline: int, op: str) -> _Canvas:
    if canvas is None:
        raise PaintDeny('NO_CANVAS', f'{op} antes de CANVAS (línea {nline})')
    return canvas


def _color(canvas: _Canvas, tok: str | None, nline: int) -> tuple[int, int, int, int]:
    if tok is None:
        return canvas.pen
    if tok.startswith('#'):
        return _parse_color(tok)
    if tok in canvas.palette:
        return canvas.palette[tok]
    raise PaintDeny('UNKNOWN_COLOR', f'{tok!r} (línea {nline})')


def _last_point(op: str, args: list[str]) -> tuple[int, int]:
    try:
        if op == 'PIXEL':
            return int(args[0]), int(args[1])
        if op == 'LINE':
            return int(args[2]), int(args[3])
        if op == 'RECT':
            return int(args[0]), int(args[1])
        if op == 'FILL':
            return int(args[0]), int(args[1])
        if op == 'COPY':
            return int(args[0]) + int(args[4]), int(args[1]) + int(args[5])
    except ValueError:
        pass
    return (0, 0)
