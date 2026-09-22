"""Tests del pincel determinista (flow.paint).

Puro: sin FS salvo tmp_path para el roundtrip SAVE (el intérprete no toca
disco; el CLI sí). Sin red, sin RNG: todo es byte-exacto y repetible.
"""
import pytest

from flow.paint import PaintDeny, parse_program, run_paint_program


def px(png_bytes, x, y):
    """Lee un píxel RGBA de un PNG emitido (vía PIL, como un consumidor)."""
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(png_bytes)).convert('RGBA')
    return img.getpixel((x, y))


def test_pixel_y_color_por_defecto_y_explicito():
    r = run_paint_program("CANVAS 4 4\nCOLOR rojo 255 0 0\nPIXEL 1 1\nPIXEL 2 2 #00ff00\nSAVE x.png\n")
    assert r.ok, r.error
    assert (r.width, r.height) == (4, 4)
    assert px(r.png_bytes, 1, 1) == (255, 0, 0, 255)
    assert px(r.png_bytes, 2, 2) == (0, 255, 0, 255)
    assert px(r.png_bytes, 0, 0) == (0, 0, 0, 0)
    assert r.save_hint == 'x.png'
    assert [t['op'] for t in r.trace] == ['CANVAS', 'COLOR', 'PIXEL', 'PIXEL', 'SAVE']


def test_line_diagonal_bresenham():
    r = run_paint_program("CANVAS 5 5\nCOLOR c 10 20 30\nLINE 0 0 4 4\n")
    assert r.ok, r.error
    for i in range(5):
        assert px(r.png_bytes, i, i)[:3] == (10, 20, 30)
    assert px(r.png_bytes, 0, 4)[3] == 0


def test_rect_relleno():
    r = run_paint_program("CANVAS 6 6\nRECT 1 1 3 2 #0000ff\n")
    assert r.ok, r.error
    assert px(r.png_bytes, 1, 1)[:3] == (0, 0, 255)
    assert px(r.png_bytes, 3, 2)[:3] == (0, 0, 255)
    assert px(r.png_bytes, 4, 2)[3] == 0
    assert px(r.png_bytes, 0, 0)[3] == 0


def test_fill_respeta_bordes():
    prog = ("CANVAS 6 6\nCOLOR marco 255 0 0\n"
            "LINE 1 1 4 1\nLINE 1 4 4 4\nLINE 1 1 1 4\nLINE 4 1 4 4\n"
            "COLOR agua 0 0 255\nFILL 2 2\n")
    r = run_paint_program(prog)
    assert r.ok, r.error
    assert px(r.png_bytes, 2, 2)[:3] == (0, 0, 255)  # dentro: lleno
    assert px(r.png_bytes, 0, 0)[3] == 0  # fuera: intacto
    assert px(r.png_bytes, 1, 1)[:3] == (255, 0, 0)  # marco intacto


def test_copy_con_solape():
    prog = ("CANVAS 8 4\nCOLOR c 1 2 3\nRECT 0 0 4 2\nCOPY 0 0 4 2 2 1\n")
    r = run_paint_program(prog)
    assert r.ok, r.error
    assert px(r.png_bytes, 2, 1)[:3] == (1, 2, 3)
    assert px(r.png_bytes, 5, 2)[:3] == (1, 2, 3)


def test_mirror_doble_es_identidad():
    prog = ("CANVAS 6 4\nCOLOR c 9 9 9\nPIXEL 1 1\nPIXEL 4 2\n"
            "MIRROR_X\nMIRROR_X\n")
    r = run_paint_program(prog)
    assert r.ok, r.error
    assert px(r.png_bytes, 1, 1)[:3] == (9, 9, 9)
    assert px(r.png_bytes, 4, 2)[:3] == (9, 9, 9)


def test_undo_restaura():
    prog = "CANVAS 4 4\nCOLOR c 7 7 7\nPIXEL 0 0\nPIXEL 3 3\nUNDO\n"
    r = run_paint_program(prog)
    assert r.ok, r.error
    assert px(r.png_bytes, 0, 0)[:3] == (7, 7, 7)
    assert px(r.png_bytes, 3, 3)[3] == 0


def test_color_antes_de_canvas_funciona():
    r = run_paint_program("COLOR tarde 1 2 3\nCANVAS 3 3\nPIXEL 0 0 tarde\n")
    assert r.ok, r.error
    assert px(r.png_bytes, 0, 0)[:3] == (1, 2, 3)


@pytest.mark.parametrize("prog,frag", [
    ("CANVAS 4 4\nPIXEL 9 9\n", "OUT_OF_CANVAS"),
    ("CANVAS 4 4\nLINE 0 0 9 0\n", "OUT_OF_CANVAS"),
    ("CANVAS 4 4\nRECT 2 2 4 4\n", "OUT_OF_CANVAS"),
    ("CANVAS 4 4\nFILL 9 9\n", "OUT_OF_CANVAS"),
    ("CANVAS 4 4\nCOPY 0 0 2 2 3 3\n", "OUT_OF_CANVAS"),
    ("CANVAS 4 4\nMIRROR_X 99\n", "OUT_OF_CANVAS"),
    ("CANVAS 4 4\nPIXEL 0 0 noexiste\n", "UNKNOWN_COLOR"),
    ("CANVAS 4 4\nVOLAR 0 0\n", "UNKNOWN_OP"),
    ("CANVAS 4\n", "BAD_ARGS"),
    ("CANVAS 0 5\n", "BAD_ARGS"),
    ("CANVAS 4 4\nPIXEL 1\n", "BAD_ARGS"),
    ("CANVAS 4 4\nCOLOR mal 300 0 0\n", "BAD_ARGS"),
    ("CANVAS 4 4\nCOLOR !!! 0 0 0\n", "BAD_ARGS"),
    ("CANVAS 4 4\nCOLOR c #zzzzzz\n", "BAD_ARGS"),
    ("CANVAS 4 4\nRECT 0 0 0 2\n", "BAD_ARGS"),
    ("CANVAS 4 4\nUNDO\n", "EMPTY_UNDO"),
    ("CANVAS 4 4\nUNDO 0\n", "BAD_ARGS"),
    ("PIXEL 0 0\n", "NO_CANVAS"),
    ("CANVAS 4 4\nCANVAS 2 2\n", "SECOND_CANVAS"),
    ("CANVAS 4 4\nSAVE\n", "BAD_ARGS"),
    ("CANVAS 4 4\nPIXEL 0 0 \"sin cerrar\n", "BAD_ARGS"),
])
def test_deny_matrix(prog, frag):
    r = run_paint_program(prog)
    assert not r.ok, f"{prog!r} debió fallar"
    assert r.png_bytes is None, "DENY no emite nada a medias"
    assert frag in (r.error or ''), f"{r.error!r} debe contener {frag}"


def test_determinismo_byte_exacto():
    prog = ("CANVAS 18 28\nCOLOR piel 247 201 170\nCOLOR pelo 92 60 34\n"
            "RECT 5 10 8 9 piel\nLINE 5 4 12 4 pelo\nFILL 0 0\n")
    a = run_paint_program(prog)
    b = run_paint_program(prog)
    assert a.ok and b.ok
    assert a.png_bytes == b.png_bytes


def test_base_mismatch_es_deny():
    import io
    from PIL import Image
    base = io.BytesIO()
    Image.new('RGBA', (10, 10), (0, 0, 0, 0)).save(base, format='PNG')
    r = run_paint_program("CANVAS 18 28\nPIXEL 0 0\n", base.getvalue())
    assert not r.ok and 'BASE_MISMATCH' in (r.error or '')


def test_sin_canvas_no_hay_salida():
    r = run_paint_program("# solo comentarios\n\n")
    assert not r.ok and 'NO_CANVAS' in (r.error or '')
    assert r.png_bytes is None


def test_avatar_gafas_escenario_usuario():
    # El ejemplo literal del usuario: lentes sobre filas de ojos.
    prog = ("CANVAS 18 28\nCOLOR marco 60 54 62\nCOLOR brillo 236 240 246\n"
            "LINE 5 8 6 8 marco\nLINE 5 10 6 10 marco\n"
            "LINE 10 8 11 8 marco\nLINE 10 10 11 10 marco\n"
            "PIXEL 8 8 marco\nPIXEL 4 8 brillo\nPIXEL 9 8 brillo\n")
    r = run_paint_program(prog)
    assert r.ok, r.error
    assert px(r.png_bytes, 5, 8)[:3] == (60, 54, 62)
    assert px(r.png_bytes, 4, 8)[:3] == (236, 240, 246)
    assert len(r.trace) == 1 + 2 + 7  # CANVAS + 2 COLOR + 4 LINE + 3 PIXEL
