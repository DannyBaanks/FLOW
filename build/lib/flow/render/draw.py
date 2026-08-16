"""Deterministic drawing primitives + trace scene model (M2).

Everything consumes an ExecutionTrace (or its dict form) and nothing
else. Renderers NEVER re-run the VM and NEVER implement language rules:
the trace is the single source of truth.
"""

from __future__ import annotations

import colorsys
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

BG = (12, 12, 18)
GRID = (28, 28, 40)
DEATH = (220, 60, 60)
SPAWN = (120, 200, 255)


def _as_trace(trace: Any) -> dict:
    """Normalize to a trace dict: ExecutionTrace | dict | str path."""
    if isinstance(trace, dict):
        return trace
    if isinstance(trace, (str, Path)):
        with open(trace, encoding="utf-8") as f:
            return json.load(f)
    if hasattr(trace, "to_dict"):
        return trace.to_dict()
    raise TypeError(f"unsupported trace type: {type(trace)!r}")


def pid_color(pid: int) -> tuple[int, int, int]:
    """Deterministic golden-angle palette for a particle id."""
    h = (pid * 0.618033988749895) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.75, 1.0)
    return int(r * 255), int(g * 255), int(b * 255)


def value_color(value: int) -> tuple[int, int, int]:
    """Deterministic color for a scalar cell value (trace/state)."""
    v = value & 0xFF
    h = v / 255.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.65, 0.85)
    return int(r * 255), int(g * 255), int(b * 255)


@dataclass
class TraceScene:
    """Position/cell state derived ONLY from trace events, per tick."""

    width: int
    height: int
    spawns: dict[int, tuple[int, float, float]] = field(default_factory=dict)
    deaths: dict[int, tuple[int, float, float]] = field(default_factory=dict)
    moves: dict[int, list[tuple[int, float, float, float, float]]] = field(
        default_factory=dict
    )
    cells: dict[int, list[tuple[int, int, int]]] = field(default_factory=dict)
    tick_list: list[int] = field(default_factory=list)
    instructions: dict[str, int] = field(default_factory=dict)
    events_total: int = 0

    @classmethod
    def from_trace(cls, trace: Any) -> TraceScene:
        d = _as_trace(trace)
        cfg = d.get("metadata", {}).get("config", {})
        size = cfg.get("image_size", [1, 1])
        scene = cls(
            width=int(size[0]) if size else 1,
            height=int(size[1]) if len(size) > 1 else 1,
            tick_list=[tk.get("tick", 0) for tk in d.get("ticks", [])],
            events_total=len(d.get("events", [])),
        )
        for ev in d.get("events", []):
            tick, pid = int(ev.get("tick", 0)), int(ev.get("pid", -1))
            typ = ev.get("type", "")
            payload = ev.get("payload", {})
            if typ == "PARTICLE_SPAWN":
                scene.spawns[pid] = (tick, float(payload["x"]), float(payload["y"]))
            elif typ == "PARTICLE_DEATH":
                scene.deaths[pid] = (tick, float(payload["x"]), float(payload["y"]))
            elif typ == "PARTICLE_MOVE":
                scene.moves.setdefault(tick, []).append(
                    (
                        pid,
                        float(payload["x_from"]),
                        float(payload["y_from"]),
                        float(payload["x_to"]),
                        float(payload["y_to"]),
                    )
                )
            elif typ == "PARTICLE_TRACE":
                scene.cells.setdefault(tick, []).append(
                    (int(payload["x"]), int(payload["y"]), int(payload["value"]))
                )
            elif typ == "INSTRUCTION_EXECUTED":
                mnemonic = payload.get("mnemonic", "NOP")
                scene.instructions[mnemonic] = scene.instructions.get(mnemonic, 0) + 1
        return scene

    def positions_at(self, tick: int) -> dict[int, tuple[float, float]]:
        """Last known position per particle at or before `tick`."""
        pos: dict[int, tuple[float, float]] = {}
        for pid, (spawn_tick, x, y) in self.spawns.items():
            if spawn_tick <= tick:
                pos[pid] = (x, y)
        for t in sorted(t for t in self.moves if t <= tick):
            for pid, _x0, _y0, x1, y1 in self.moves[t]:
                if pid in pos:
                    pos[pid] = (x1, y1)
        return pos

    def cells_at(self, tick: int) -> dict[tuple[int, int], int]:
        """Cumulative trace cells up to and including `tick`."""
        out: dict[tuple[int, int], int] = {}
        for t in sorted(t for t in self.cells if t <= tick):
            for x, y, value in self.cells[t]:
                out[(x, y)] = value
        return out

    def trails_upto(self, tick: int) -> list[tuple]:
        return [m for t, m in self.moves.items() if t <= tick for m in m]


def new_arena(scene: TraceScene, scale: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    canvas = Image.new("RGB", (scene.width * scale, scene.height * scale), BG)
    draw = ImageDraw.Draw(canvas)
    for gy in range(scene.height):
        for gx in range(scene.width):
            if (gx + gy) % 2 == 0:
                draw.rectangle(
                    [gx * scale, gy * scale, gx * scale + scale - 1, gy * scale + scale - 1],
                    fill=GRID,
                )
    return canvas, draw


def draw_cells(
    draw: ImageDraw.ImageDraw,
    cells: dict[tuple[int, int], int],
    scale: int,
) -> None:
    for (x, y), value in cells.items():
        color = value_color(value)
        draw.rectangle(
            [x * scale, y * scale, x * scale + scale - 1, y * scale + scale - 1],
            fill=color,
        )


def draw_trails(
    draw: ImageDraw.ImageDraw,
    trails: list[tuple],
    scale: int,
) -> None:
    for pid, x0, y0, x1, y1 in trails:
        color = pid_color(pid)
        draw.line(
            [
                (x0 * scale + scale / 2, y0 * scale + scale / 2),
                (x1 * scale + scale / 2, y1 * scale + scale / 2),
            ],
            fill=color,
            width=max(1, scale // 3),
        )


def draw_particles(
    draw: ImageDraw.ImageDraw,
    positions: dict[int, tuple[float, float]],
    scale: int,
    radius: float | None = None,
) -> None:
    r = radius if radius is not None else scale * 0.35
    for pid, (x, y) in positions.items():
        color = pid_color(pid)
        cx, cy = x * scale + scale / 2, y * scale + scale / 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def draw_deaths(
    draw: ImageDraw.ImageDraw,
    scene: TraceScene,
    scale: int,
) -> None:
    for (_tick, x, y) in scene.deaths.values():
        cx, cy = x * scale + scale / 2, y * scale + scale / 2
        r = scale * 0.45
        draw.line([(cx - r, cy - r), (cx + r, cy + r)], fill=DEATH, width=2)
        draw.line([(cx - r, cy + r), (cx + r, cy - r)], fill=DEATH, width=2)


def join_side_by_side(left: Image.Image, right: Image.Image) -> Image.Image:
    h = max(left.height, right.height)
    canvas = Image.new("RGB", (left.width + right.width, h), BG)
    canvas.paste(left, (0, 0))
    canvas.paste(right, (left.width, 0))
    return canvas