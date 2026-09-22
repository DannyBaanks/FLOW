"""FLOW paint — pincel determinista (periférico gráfico, no VM).

Un programa de pintado describe píxeles con texto y compila a PNG:

    avatar.flow
        ↓ flow painter
    avatar.png

Ver painter.py para el lenguaje, la semántica DENY y las garantías.
"""
from .painter import (
    PaintDeny,
    PaintResult,
    parse_program,
    run_paint_program,
)

__all__ = [
    "PaintDeny",
    "PaintResult",
    "parse_program",
    "run_paint_program",
]
