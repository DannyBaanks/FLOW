"""GIF renderer (M2) — frames derived from the real ExecutionTrace.

Each frame is built from events up to that tick: cumulative trace
cells, trails from PARTICLE_MOVE events, and live particle positions.
Nothing is invented between events.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from flow.render.draw import (
    TraceScene,
    draw_cells,
    draw_deaths,
    draw_particles,
    draw_trails,
    join_side_by_side,
    new_arena,
)
from flow.render.image import _text_panel


def _frame_ticks(scene: TraceScene, max_frames: int) -> list[int]:
    """Pick which ticks become frames: all of them, subsampled evenly."""
    ticks = list(dict.fromkeys(scene.tick_list))
    if not ticks:
        return [0]
    if len(ticks) <= max_frames:
        return ticks
    step = len(ticks) / max_frames
    return [ticks[int(i * step)] for i in range(max_frames)]


def _frame(
    scene: TraceScene,
    tick: int,
    scale: int,
    layout: str,
    panel_title: str,
) -> Image.Image:
    canvas, draw = new_arena(scene, scale)
    draw_cells(draw, scene.cells_at(tick), scale)
    draw_trails(draw, scene.trails_upto(tick), scale)

    positions = scene.positions_at(tick)
    for pid, (death_tick, _x, _y) in scene.deaths.items():
        if pid in positions and death_tick <= tick:
            del positions[pid]
    draw_particles(draw, positions, scale)
    draw_deaths(draw, scene, scale)

    if layout == "split":
        canvas = join_side_by_side(
            _text_panel(scene, panel_title, [f"frame tick: {tick}"]), canvas
        )
    return canvas


def render_gif(
    trace: Any,
    output_path: str | Path,
    layout: str = "arena",
    scale: int = 4,
    duration_ms: int = 70,
    max_frames: int = 96,
    loop: int = 0,
    panel_title: str = "FLOW / EXECUTION",
) -> Path:
    """Render a trace to an animated GIF. Frames come only from events."""
    if layout not in ("arena", "split"):
        raise ValueError(f"unknown layout: {layout!r}")
    scene = TraceScene.from_trace(trace)
    ticks = _frame_ticks(scene, max_frames)

    frames = [_frame(scene, t, scale, layout, panel_title) for t in ticks]
    first = frames[0]
    if len(frames) > 1:
        first.save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=duration_ms,
            loop=loop,
        )
    else:
        first.save(output_path, duration=duration_ms, loop=loop)
    return Path(output_path)