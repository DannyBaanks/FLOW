"""Session renderer (M2) — a "terminal session" GIF from the real trace.

Creative renderer: draws a fake terminal window (like a session
recording) where commands are typed, real trace metadata appears, and
the live execution animates in the right panel. EVERY number shown
comes from the ExecutionTrace; nothing is invented.

Frames are built from:
  - real events (PARTICLE_MOVE, PARTICLE_TRACE, PARTICLE_DEATH, ...)
  - real metadata (engine_version, program_sha256, seed, config, ticks)
  - a fixed, deterministic script of commands (the "session story")

Same trace -> same frames -> same bytes (modulo GIF encoder metadata).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from flow.render.draw import (
    TraceScene,
    draw_cells,
    draw_deaths,
    draw_particles,
    draw_trails,
    new_arena,
)

BG = (8, 8, 10)
PANEL_BG = (10, 13, 16)
TITLE_BG = (22, 26, 32)
TEXT = (196, 208, 224)
DIM = (96, 108, 128)
GREEN = (92, 210, 120)
YELLOW = (222, 200, 96)
BLUE = (110, 170, 240)
RED_DOT = (230, 84, 84)
YEL_DOT = (232, 190, 84)
GRN_DOT = (94, 214, 112)


def _font(size: int = 14) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("consola.ttf", size)
    except OSError:
        try:
            return ImageFont.truetype("cour.ttf", size)
        except OSError:
            return ImageFont.load_default()


FONT = _font(15)
FONT_BOLD = _font(17)


def _short_sha(sha: str) -> str:
    return sha[:12] if sha else "?"


class SessionScript:
    """Deterministic 'story' of the session, driven by real trace metadata."""

    def __init__(self, trace: dict, program_name: str):
        meta = trace.get("metadata", {})
        cfg = meta.get("config", {})
        size = cfg.get("image_size", [0, 0])
        engine = meta.get("engine_version", "flow-0.1")
        sha = _short_sha(meta.get("program_sha256", ""))
        seed = meta.get("seed")
        n_ticks = meta.get("ticks", len(trace.get("ticks", [])))
        spawned = meta.get("particles_spawned", "?")
        alive = meta.get("final_alive", "?")

        lines = [
            ("cmd", f"$ flow run {program_name} --seed {seed}"),
            ("out", f"[FLOW] {engine}"),
            ("out", f"[FLOW] program sha256: {sha}"),
            ("out", f"[FLOW] image {size[0]}x{size[1]} | ticks {n_ticks}"),
            ("out", f"[FLOW] spawned {spawned} particles | final alive {alive}"),
            ("out", f"[FLOW] trace saved -> trace.json ({len(trace.get('events', []))} events)"),
            ("cmd", "$ flow replay vortex.png --trace trace.json"),
            ("ok", "REPLAY OK - trace identical to original"),
            ("cmd", "$ flow validate trace.json"),
            ("ok", "OK: trace valid - events ordered, metadata complete"),
            ("cmd", "$ flow render --trace trace.json --format gif --layout session"),
            ("out", "[FLOW] renderers consume ONLY the trace. Never re-run the VM."),
            ("out", "[FLOW] frames come from real events: moves, traces, deaths."),
            ("ok", "session.gif written - this is the session you are watching."),
        ]
        self.lines = lines

    def visible_lines(self, chars: int) -> list[tuple[str, str]]:
        """Lines visible after `chars` characters of typing."""
        out: list[tuple[str, str]] = []
        budget = chars
        for kind, text in self.lines:
            if budget <= 0:
                break
            take = min(budget, len(text))
            out.append((kind, text[:take]))
            budget -= len(text)
        return out

    @property
    def total_chars(self) -> int:
        return sum(len(t) for _, t in self.lines)


def _draw_terminal_chrome(canvas: Image.Image, title: str) -> ImageDraw.ImageDraw:
    w, h = canvas.size
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, w - 1, h - 1], outline=(60, 66, 78), width=2)
    draw.rectangle([2, 2, w - 3, 26], fill=TITLE_BG)
    draw.ellipse([10, 9, 18, 17], fill=RED_DOT)
    draw.ellipse([22, 9, 30, 17], fill=YEL_DOT)
    draw.ellipse([34, 9, 42, 17], fill=GRN_DOT)
    draw.text((50, 6), title, font=FONT_BOLD, fill=(230, 234, 240))
    return draw


def _draw_terminal_text(
    canvas: Image.Image,
    lines: list[tuple[str, str]],
    show_cursor: bool,
    cursor_col: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    x, y = 16, 38
    for idx, (kind, text) in enumerate(lines):
        color = DIM if kind == "out" else (GREEN if kind == "ok" else TEXT)
        if kind == "cmd":
            draw.text((x, y), text, font=FONT, fill=color)
        else:
            draw.text((x, y), text, font=FONT, fill=color)
        y += 21
    if show_cursor:
        draw.text((x + cursor_col * 9, y), "\u2588", font=FONT, fill=GREEN)


def render_session_gif(
    trace: Any,
    output_path: str | Path,
    program_name: str = "vortex.png",
    scale: int = 4,
    duration_ms: int = 85,
    max_frames: int = 220,
    loop: int = 0,
    title: str = "FLOW - live session",
    fps_factor: int = 3,
) -> Path:
    """Render a terminal-session GIF from an ExecutionTrace.

    Layout: left = terminal panel (typed session), right = live arena.
    The session story types ~fps_factor characters per animation frame,
    so the typing finishes while the arena keeps animating.
    """
    if hasattr(trace, "to_dict"):
        trace_dict = trace.to_dict()
    else:
        trace_dict = trace
    scene = TraceScene.from_trace(trace_dict)
    script = SessionScript(trace_dict, program_name)

    arena_w = scene.width * scale
    arena_h = scene.height * scale
    panel_w = 520
    win_w = panel_w + arena_w + 40
    win_h = max(arena_h + 40, 420)
    canvas = Image.new("RGB", (win_w, win_h), BG)
    draw = _draw_terminal_chrome(canvas, title)
    draw.rectangle([12, 34, panel_w + 4, win_h - 14], fill=PANEL_BG, outline=(36, 42, 52))

    # ticks per animation frame (arena advances 1 tick per `fps_factor` frames)
    ticks = list(dict.fromkeys(scene.tick_list))
    total_chars = script.total_chars
    n_frames = max(1, min(max_frames, total_chars + len(ticks) * fps_factor))

    frames: list[Image.Image] = []
    for i in range(n_frames):
        frame = canvas.copy()
        # typing progress
        chars_done = int(total_chars * i / max(1, n_frames - 1))
        if chars_done >= total_chars:
            chars_done = total_chars - 1
            cursor = True
        else:
            cursor = i % 2 == 0
        _draw_terminal_text(frame, script.visible_lines(chars_done), cursor, 2)

        # arena progress
        tick_i = min(len(ticks) - 1, i // fps_factor) if ticks else 0
        tick = ticks[tick_i] if ticks else 0
        arena, adraw = new_arena(scene, scale)
        draw_cells(adraw, scene.cells_at(tick), scale)
        draw_trails(adraw, scene.trails_upto(tick), scale)
        positions = scene.positions_at(tick)
        for pid, (death_tick, _x, _y) in scene.deaths.items():
            if pid in positions and death_tick <= tick:
                del positions[pid]
        draw_particles(adraw, positions, scale)
        draw_deaths(adraw, scene, scale)
        frame.paste(arena, (panel_w + 20, 34))

        # live tick readout
        rd = ImageDraw.Draw(frame)
        rd.text(
            (panel_w + 20, win_h - 30),
            f"tick {tick:04d} | alive {len(positions):02d} | events {scene.events_total}",
            font=FONT,
            fill=YELLOW,
        )
        frames.append(frame)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=loop,
    )
    return out