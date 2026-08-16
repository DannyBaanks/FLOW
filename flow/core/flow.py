#!/usr/bin/env python3
"""
FLOW v0.1 — Interpreter
Minimal image-as-vector-field esolang.
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass, field

try:
    import numpy as np
    from PIL import Image
except ImportError:
    print("Requiere: pip install pillow numpy", file=sys.stderr)
    sys.exit(1)


# ============================================================
# Utilidades de interpolación bilineal
# ============================================================

def sample_bilinear(field: np.ndarray, x: float, y: float) -> float:
    """Interpolación bilineal en field 2D (H, W). Clampa en bordes."""
    h, w = field.shape
    x = max(0.0, min(float(w - 1), x))
    y = max(0.0, min(float(h - 1), y))
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    x1, y1 = min(x0 + 1, w - 1), min(y0 + 1, h - 1)
    dx, dy = x - x0, y - y0
    v00 = field[y0, x0]
    v10 = field[y0, x1]
    v01 = field[y1, x0]
    v11 = field[y1, x1]
    return (v00 * (1 - dx) * (1 - dy) +
            v10 * dx * (1 - dy) +
            v01 * (1 - dx) * dy +
            v11 * dx * dy)


def sample_vec(field_r: np.ndarray, field_g: np.ndarray, x: float, y: float) -> tuple[float, float]:
    """Muestrea vector (vx, vy) en (x,y). R,G ∈ [0,255] -> [-1,1]."""
    vx = (sample_bilinear(field_r, x, y) / 127.5) - 1.0
    vy = (sample_bilinear(field_g, x, y) / 127.5) - 1.0
    return vx, vy


# ============================================================
# Partícula
# ============================================================

@dataclass
class Particle:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    state: int = 0
    stack: list[int] = field(default_factory=list)
    alive: bool = True
    pid: int = 0

    def pos(self) -> tuple[float, float]:
        return (self.x, self.y)


# ============================================================
# Instrucciones
# ============================================================

MAX_STACK = 16

def exec_instruction(
    instr: int,
    p: Particle,
    field_b: np.ndarray,
    field_trace: np.ndarray,
    particles: list[Particle],
    pid_counter: int,
    w: int, h: int,
    rng=None
) -> int:
    """Ejecuta instrucción. Retorna nuevo pid_counter si hay split.

    ``rng``: generador a usar para RAND (instr 22). Si es None usa el modulo
    ``random`` global (legacy, no determinista). Pasar ``random.Random(seed)``
    para determinismo — Sin alterar el resto de la semantica.
    """
    if instr == 0:  # HALT (0 = HALT; 255 = NOP / spawn marker)
        p.alive = False
    elif instr == 1:  # NOP
        pass
    elif instr == 2:  # INC
        p.state = (p.state + 1) & 0xFF
    elif instr == 3:  # DEC
        p.state = (p.state - 1) & 0xFF
    elif instr == 4:  # READ (self-read, noop effectively)
        p.state = instr
    elif instr == 5:  # WRITE
        ix, iy = int(round(p.x)), int(round(p.y))
        if 0 <= ix < w and 0 <= iy < h:
            field_b[iy, ix] = p.state & 0xFF
    elif instr == 6:  # JMP+
        speed = math.hypot(p.vx, p.vy)
        if speed > 1e-6:
            p.x += (p.vx / speed) * 5.0
            p.y += (p.vy / speed) * 5.0
    elif instr == 7:  # JMP-
        speed = math.hypot(p.vx, p.vy)
        if speed > 1e-6:
            p.x -= (p.vx / speed) * 5.0
            p.y -= (p.vy / speed) * 5.0
    elif instr == 8:  # SPLIT
        # hija perpendicular a la velocidad
        speed = math.hypot(p.vx, p.vy)
        if speed > 1e-6:
            px, py = -p.vy / speed, p.vx / speed  # perpendicular
        else:
            px, py = 1.0, 0.0
        child = Particle(
            x=p.x + px * 3.0,
            y=p.y + py * 3.0,
            vx=p.vx, vy=p.vy,
            state=p.state,
            stack=p.stack.copy(),
            pid=pid_counter
        )
        particles.append(child)
        pid_counter += 1
    elif instr == 9:  # TURN+
        angle = math.pi / 4
        vx, vy = p.vx, p.vy
        p.vx = vx * math.cos(angle) - vy * math.sin(angle)
        p.vy = vx * math.sin(angle) + vy * math.cos(angle)
    elif instr == 10:  # TURN-
        angle = -math.pi / 4
        vx, vy = p.vx, p.vy
        p.vx = vx * math.cos(angle) - vy * math.sin(angle)
        p.vy = vx * math.sin(angle) + vy * math.cos(angle)
    elif instr == 11:  # PUSH
        if len(p.stack) < MAX_STACK:
            p.stack.append(p.state)
    elif instr == 12:  # POP
        p.state = p.stack.pop() if p.stack else 0
    elif instr == 13:  # DUP
        if len(p.stack) < MAX_STACK:
            p.stack.append(p.state)
    elif instr == 14:  # SWAP
        if p.stack:
            p.state, p.stack[-1] = p.stack[-1], p.state
    elif instr == 15:  # ADD
        if len(p.stack) >= 2:
            a = p.stack.pop()
            b = p.stack.pop()
            p.stack.append((a + b) & 0xFF)
    elif instr == 16:  # SUB
        if len(p.stack) >= 2:
            a = p.stack.pop()
            b = p.stack.pop()
            p.stack.append((b - a) & 0xFF)
    elif instr == 17:  # MUL
        if len(p.stack) >= 2:
            a = p.stack.pop()
            b = p.stack.pop()
            p.stack.append((a * b) & 0xFF)
    elif instr == 18:  # DIV
        if len(p.stack) >= 2:
            a = p.stack.pop()
            b = p.stack.pop()
            p.stack.append((b // a) & 0xFF if a != 0 else 0)
    elif instr == 19:  # MOD
        if len(p.stack) >= 2:
            a = p.stack.pop()
            b = p.stack.pop()
            p.stack.append((b % a) & 0xFF if a != 0 else 0)
    elif instr == 20:  # EQ
        if len(p.stack) >= 2:
            a = p.stack.pop()
            b = p.stack.pop()
            p.stack.append(1 if a == b else 0)
    elif instr == 21:  # LT
        if len(p.stack) >= 2:
            a = p.stack.pop()
            b = p.stack.pop()
            p.stack.append(1 if b < a else 0)
    elif instr == 22:  # RAND
        r = rng if rng is not None else random
        p.state = r.randint(0, 255)
    elif instr == 23:  # COLOR (tinte)
        ix, iy = int(round(p.x)), int(round(p.y))
        if 0 <= ix < w and 0 <= iy < h:
            field_b[iy, ix] = (field_b[iy, ix] + p.state) & 0xFF
    elif instr == 24:  # TRACE
        ix, iy = int(round(p.x)), int(round(p.y))
        if 0 <= ix < w and 0 <= iy < h:
            field_trace[iy, ix] = p.state & 0xFF
    # 25..254: NOP
    return pid_counter


# ============================================================
# Motor principal
# ============================================================

@dataclass
class FlowConfig:
    max_ticks: int = 10000
    dt: float = 1.0
    damping: float = 0.9
    field_gain: float = 0.1
    trace_enabled: bool = True
    seed: int | None = None  # None = RAND no determinista (legacy); int = determinista


class FlowVM:
    def __init__(self, image_path: str, config: FlowConfig | None = None):
        self.config = config or FlowConfig()
        img = Image.open(image_path).convert("RGB")
        self.w, self.h = img.size
        arr = np.array(img, dtype=np.float32)
        self.field_r = arr[:, :, 0]  # R = vx
        self.field_g = arr[:, :, 1]  # G = vy
        self.field_b = arr[:, :, 2].copy().astype(np.uint8)  # B = scalar (mutable)
        self.field_trace = np.zeros((self.h, self.w), dtype=np.uint8)
        self.particles: list[Particle] = []
        self.tick = 0
        self.pid_counter = 0
        self.log: list[tuple] = []
        # RNG determinista para RAND (instr 22). seed=None -> entropia del OS.
        self.rng = random.Random(self.config.seed)
        self._spawn_initial()

    def _spawn_initial(self):
        """Spawnea partículas donde B == 255 (marcador de inicio)."""
        ys, xs = np.where(self.field_b == 255)
        for y, x in zip(ys, xs):
            p = Particle(
                x=x + 0.5,
                y=y + 0.5,
                vx=0.0, vy=0.0,
                state=0,
                pid=self.pid_counter
            )
            self.particles.append(p)
            self.pid_counter += 1
        # 255 = NOP, no hace falta limpiar

    def step(self) -> bool:
        """Ejecuta un tick. Retorna True si quedan partículas vivas."""
        if self.tick >= self.config.max_ticks:
            return False
        any_alive = False
        for p in self.particles:
            if not p.alive:
                continue
            any_alive = True

            # 1. READ instruction
            instr = int(sample_bilinear(self.field_b, p.x, p.y) + 0.5) & 0xFF

            # 2. EXECUTE
            self.pid_counter = exec_instruction(
                instr, p, self.field_b, self.field_trace,
                self.particles, self.pid_counter, self.w, self.h, self.rng
            )

            # Log
            self.log.append((self.tick, p.pid, p.x, p.y, p.state, instr))

            if not p.alive:
                continue

            # 3. FIELD: sample vector field, update velocity
            fx, fy = sample_vec(self.field_r, self.field_g, p.x, p.y)
            p.vx = p.vx * self.config.damping + fx * self.config.field_gain
            p.vy = p.vy * self.config.damping + fy * self.config.field_gain

            # 4. MOVE
            p.x += p.vx * self.config.dt
            p.y += p.vy * self.config.dt

            # 5. BOUNDS
            if not (0 <= p.x < self.w and 0 <= p.y < self.h):
                p.alive = False

        self.tick += 1
        return any_alive

    def run(self) -> dict:
        """Ejecuta hasta terminación."""
        while self.step():
            pass
        return {
            "ticks": self.tick,
            "particles_spawned": self.pid_counter,
            "final_particles_alive": sum(1 for p in self.particles if p.alive),
            "log": self.log,
        }

    def save_output(self, base_path: str):
        """Guarda campo B final y trace como PNG."""
        Image.fromarray(self.field_b, mode="L").save(f"{base_path}_b.png")
        if self.config.trace_enabled and self.field_trace.any():
            Image.fromarray(self.field_trace, mode="L").save(f"{base_path}_trace.png")

    def save_log(self, path: str):
        import csv
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["tick", "pid", "x", "y", "state", "instr"])
            w.writerows(self.log)


# ============================================================
# Generador de ejemplos
# ============================================================

def make_hello_flow(path: str):
    """Programa mínimo: partícula fluye a la derecha, ejecuta INCx5, WRITE, HALT."""
    w, h = 32, 16
    img = Image.new("RGB", (w, h), (128, 128, 0))  # R=G=128 (v=0), B=0
    px = img.load()

    # Spawn en (2, 2) -> B=255
    px[2, 2] = (128, 128, 255)

    # Campo vectorial: empuje a la derecha en la fila y=2, x=2..10
    # R = 128 + 127 = 255 (vx = +1.0), G = 128 (vy = 0)
    for x in range(2, 11):
        px[x, 2] = (255, 128, 0)

    # Programa en B en la misma fila, justo después del spawn
    # x=3: INC (2), x=4: INC, x=5: INC, x=8: WRITE, x=9: HALT
    # B valores en la fila y=2:
    px[3, 2] = (255, 128, 2)   # INC
    px[4, 2] = (255, 128, 2)   # INC
    px[5, 2] = (255, 128, 2)   # INC
    px[6, 2] = (255, 128, 2)   # INC
    px[7, 2] = (255, 128, 2)   # INC  (5 INCs -> state=5)
    px[8, 2] = (255, 128, 5)   # WRITE
    px[9, 2] = (255, 128, 0)   # HALT

    # Spawn marker en (2,2)
    px[2, 2] = (255, 128, 255)

    img.save(path)
    print(f"Generado {path} ({w}x{h})")


def make_vortex(path: str):
    """Vórtice: campo vectorial rotacional + 8 spawns en anillo con SPLIT+TRACE."""
    w, h = 64, 64
    img = Image.new("RGB", (w, h), (128, 128, 0))
    px = img.load()
    cx, cy = w // 2, h // 2

    # Campo vectorial: vórtice tangencial
    for y in range(h):
        for x in range(w):
            dx, dy = x - cx, y - cy
            r = math.hypot(dx, dy)
            if r > 1:
                vx = -dy / r
                vy = dx / r
                r_val = int((vx + 1.0) * 127.5)
                g_val = int((vy + 1.0) * 127.5)
                px[x, y] = (r_val, g_val, 0)

    # 8 spawns en anillo radio 3, cada uno con SPLIT + vector tangencial
    for i in range(8):
        angle = i * math.pi / 4
        x = int(cx + 3 * math.cos(angle))
        y = int(cy + 3 * math.sin(angle))
        if 0 <= x < w and 0 <= y < h:
            # Vector tangencial en R,G + spawn marker en B
            vx_t = -math.sin(angle)
            vy_t = math.cos(angle)
            r_val = int((vx_t + 1.0) * 127.5)
            g_val = int((vy_t + 1.0) * 127.5)
            px[x, y] = (r_val, g_val, 255)  # spawn marker + vector

    img.save(path)
    print(f"Generado {path} ({w}x{h})")


# ============================================================
# CLI
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="FLOW v0.1 interpreter")
    parser.add_argument("image", nargs="?", help="Archivo PNG programa")
    parser.add_argument("--max-ticks", type=int, default=10000)
    parser.add_argument("--gen-hello", help="Generar hello_flow.png en ruta")
    parser.add_argument("--gen-vortex", help="Generar vortex.png en ruta")
    parser.add_argument("--output", "-o", default="out", help="Prefijo salida")
    args = parser.parse_args()

    if args.gen_hello:
        make_hello_flow(args.gen_hello)
        return
    if args.gen_vortex:
        make_vortex(args.gen_vortex)
        return

    if not args.image:
        parser.error("Se requiere archivo de imagen (o --gen-hello/--gen-vortex)")

    config = FlowConfig(max_ticks=args.max_ticks)
    vm = FlowVM(args.image, config)
    print(f"Cargado {args.image} ({vm.w}x{vm.h})")
    print(f"Partículas iniciales: {len(vm.particles)}")

    result = vm.run()
    print(f"Ticks: {result['ticks']}")
    print(f"Partículas spawn: {result['particles_spawned']}")
    print(f"Vivas al final: {result['final_particles_alive']}")

    vm.save_output(args.output)
    vm.save_log(f"{args.output}.csv")
    print(f"Salida: {args.output}_b.png, {args.output}_trace.png, {args.output}.csv")


if __name__ == "__main__":
    main()