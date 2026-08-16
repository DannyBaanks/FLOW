"""Renderer tests (M2): renderers consume ONLY the trace, never the VM.

Verifies:
- PNG output exists, non-empty, expected dimensions
- GIF output exists, >= 2 frames
- same trace -> same bytes (renderer determinism)
- renderers accept dict / path / ExecutionTrace
- session renderer produces a terminal-style GIF with real stats
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

import flow.core as flow_core
import flow.runtime as flow_runtime
from flow.render import render_gif, render_image, render_session_gif


def _trace(tmp: str, seed: int = 42):
    prog = Path(tmp) / "vortex.png"
    flow_core.make_vortex(str(prog))
    return flow_runtime.run_program(str(prog), seed=seed)


def _gif_frames(path: Path) -> list:
    img = Image.open(path)
    frames = [img.copy()]
    while True:
        try:
            img.seek(img.tell() + 1)
            frames.append(img.copy())
        except EOFError:
            break
    return frames


def test_render_image_arena(tmp_path):
    trace = _trace(str(tmp_path))
    out = render_image(trace, tmp_path / "arena.png", layout="arena", scale=4)
    img = Image.open(out)
    assert img.format == "PNG"
    assert img.size == (64 * 4, 64 * 4)
    assert img.getbbox() is not None


def test_render_image_split_has_panel(tmp_path):
    trace = _trace(str(tmp_path))
    out = render_image(trace, tmp_path / "split.png", layout="split", scale=4)
    img = Image.open(out)
    assert img.format == "PNG"
    assert img.width > 64 * 4
    assert img.height == 64 * 4


def test_render_gif_arena(tmp_path):
    trace = _trace(str(tmp_path))
    out = render_gif(trace, tmp_path / "anim.gif", layout="arena", scale=4)
    frames = _gif_frames(out)
    assert len(frames) >= 2
    assert frames[0].size == (64 * 4, 64 * 4)


def test_render_accepts_dict_and_path(tmp_path):
    trace = _trace(str(tmp_path))
    as_dict = trace.to_dict()
    trace.save(tmp_path / "trace.json")
    p1 = render_image(as_dict, tmp_path / "from_dict.png", scale=3)
    p2 = render_image(str(tmp_path / "trace.json"), tmp_path / "from_path.png", scale=3)
    assert p1.exists() and p2.exists()


def test_render_deterministic_bytes(tmp_path):
    t1 = _trace(str(tmp_path), seed=7)
    t2 = _trace(str(tmp_path), seed=7)
    o1 = render_image(t1, tmp_path / "d1.png", scale=4)
    o2 = render_image(t2, tmp_path / "d2.png", scale=4)
    assert o1.read_bytes() == o2.read_bytes()

    g1 = render_gif(t1, tmp_path / "g1.gif", scale=4, max_frames=12)
    g2 = render_gif(t2, tmp_path / "g2.gif", scale=4, max_frames=12)
    assert g1.read_bytes() == g2.read_bytes()


def test_render_session_gif(tmp_path):
    trace = _trace(str(tmp_path))
    out = render_session_gif(
        trace, tmp_path / "session.gif", scale=4, max_frames=60, duration_ms=80
    )
    frames = _gif_frames(out)
    assert len(frames) >= 2
    # window = terminal panel (520) + arena (256) + chrome margins
    assert frames[0].width == 520 + 64 * 4 + 40
    assert frames[0].height >= 64 * 4 + 40


def test_render_session_gif_deterministic(tmp_path):
    t1 = _trace(str(tmp_path), seed=3)
    t2 = _trace(str(tmp_path), seed=3)
    o1 = render_session_gif(t1, tmp_path / "s1.gif", scale=3, max_frames=40)
    o2 = render_session_gif(t2, tmp_path / "s2.gif", scale=3, max_frames=40)
    assert o1.read_bytes() == o2.read_bytes()


def test_render_session_real_stats_in_panel(tmp_path):
    trace = _trace(str(tmp_path))
    from flow.render.session import SessionScript

    script = SessionScript(trace.to_dict(), "vortex.png")
    assert script.total_chars > 100
    meta = trace.metadata
    assert meta["particles_spawned"] == 8
    assert any("seed" in t for _, t in script.lines)
    assert any("REPLAY OK" in t for _, t in script.lines)