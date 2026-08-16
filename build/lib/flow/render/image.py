"""Image renderer (M2) — one final image representing the real execution.

Consumes ONLY an ExecutionTrace. Never re-runs the VM, never invents
movement: trails come from PARTICLE_MOVE events, cells from
PARTICLE_TRACE events, deaths from PARTICLE_DEATH events.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from flow.render.draw import (
    BG,
    TraceScene,
    draw_cells,
    draw_deaths,
    draw_particles,
    draw_trails,
    join_side_by_side,
    new_arena,
)

LINE_H = 14
PANEL_W = 300


def _text_panel(scene: TraceScene, title: str, extra: list[str] | None) -> Image.Image:
    lines = [title, "-" * 26]
    lines.append(f"ticks:       {len(scene.tick_list)}")
    lines.append(f"spawned:     {len(scene.spawns)}")
    lines.append(f"deaths:      {len(scene.deaths)}")
    lines.append(f"events:      {scene.events_total}")
    lines.append(f"trace cells: {sum(len(v) for v in scene.cells.values())}")
    lines.extend(extra or [])
    lines.append("-" * 26)
    for mnemonic in sorted(scene.instructions):
        lines.append(f"{mnemonic:<9} {scene.instructions[mnemonic]}")
    height = max(60, len(lines) * LINE_H + 10)
    canvas = Image.new("RGB", (PANEL_W, height), BG)
    draw = ImageDraw.Draw(canvas)
    y = 6
    for line in lines:
        draw.text((8, y), line, fill=(200, 200, 215))
        y += LINE_H
    return canvas


def render_image(
    trace: Any,
    output_path: str | Path,
    layout: str = "arena",
    scale: int = 6,
    panel_title: str = "FLOW / EXECUTION",
    extra_lines: list[str] | None = None,
) -> Path:
    """Render a trace to a single PNG.

    layout: "arena" (visual only) or "split" (info panel + arena).
    """
    if layout not in ("arena", "split"):
        raise ValueError(f"unknown layout: {layout!r}")
    scene = TraceScene.from_trace(trace)
    canvas, draw = new_arena(scene, scale)

    last = max(scene.tick_list, default=0)
    draw_cells(draw, scene.cells_at(last), scale)
    draw_trails(draw, scene.trails_upto(last), scale)
    draw_deaths(draw, scene, scale)
    draw_particles(draw, scene.positions_at(last), scale)

    if layout == "split":
        canvas = join_side_by_side(_text_panel(scene, panel_title, extra_lines), canvas)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def arena_dims(trace: Any, scale: int) -> tuple[int, int]:
    scene = TraceScene.from_trace(trace)
    return scene.width * scale, scene.height * scale