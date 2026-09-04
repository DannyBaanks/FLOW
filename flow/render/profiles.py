"""Multi-profile renderers for FLOW ExecutionTrace.

All profiles consume ONLY the ExecutionTrace + RenderSpec.
The trace is never modified. Interpolated positions are PRESENTATION,
not VM states. Event persistence is visual, not semantic.

Contract:
  INTERPOLATED_POSITION = PRESENTATION (not a VM state)
  EVENT_OCCURRENCE_COUNT != EVENT_VISUAL_DURATION
  decorative effects never enter the trace
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from flow.render.draw import (
    BG, GRID, DEATH, SPAWN, TraceScene, pid_color, value_color, _as_trace,
    new_arena, draw_cells, draw_deaths, draw_particles, draw_trails,
)
from flow.render.spec import RenderSpec, PROFILES

# ── Palette ─────────────────────────────────────────────────────────
DARK_BG = (8, 8, 14)
GLOW_BG = (14, 14, 22)
HEAT_COLD = (8, 8, 40)
HEAT_WARM = (255, 80, 40)
HEAT_HOT = (255, 220, 60)
TEXT_COLOR = (200, 208, 224)
DIM = (80, 88, 104)
ACCENT = (120, 200, 255)
HALO = (100, 160, 220)

# ── Font ────────────────────────────────────────────────────────────
def _font(size: int = 14) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("consola.ttf", "cour.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()

FONT_S = _font(11)
FONT_M = _font(14)
FONT_L = _font(18)

# ── Color helpers ───────────────────────────────────────────────────
def _blend(c1: tuple, c2: tuple, t: float) -> tuple[int, int, int]:
    """Deterministic linear blend, t in [0,1]."""
    return tuple(int(a + (b - a) * max(0.0, min(1.0, t))) for a, b in zip(c1, c2))

def _heat_color(ratio: float) -> tuple[int, int, int]:
    """0.0 = cold, 0.5 = warm, 1.0 = hot."""
    if ratio < 0.5:
        return _blend(HEAT_COLD, HEAT_WARM, ratio * 2)
    return _blend(HEAT_WARM, HEAT_HOT, (ratio - 0.5) * 2)

def _dim_color(color: tuple, factor: float) -> tuple[int, int, int]:
    return _blend(DARK_BG, color, factor)

# ── Timeline helpers ────────────────────────────────────────────────
def _unique_ticks(scene: TraceScene) -> list[int]:
    return sorted(set(scene.tick_list))

def _build_frame_plan(ticks: list[int], spec: RenderSpec) -> list[tuple[int, float]]:
    """Return list of (tick_index, fraction) for each visual frame.

    tick_index is index into `ticks`; fraction=0 means exact tick position,
    fraction in (0,1) means interpolated between tick_index and tick_index+1.
    """
    if len(ticks) <= 1:
        return [(0, 0.0)]
    interp = spec.interpolate
    total = (len(ticks) - 1) * interp + 1
    if total > spec.max_frames:
        interp = max(1, (spec.max_frames - 1) // (len(ticks) - 1))
        total = (len(ticks) - 1) * interp + 1
    plan: list[tuple[int, float]] = []
    for j in range(total):
        ti = j // interp
        frac = (j % interp) / interp if ti < len(ticks) - 1 else 0.0
        plan.append((ti, frac))
    return plan

def _interpolated_positions(
    scene: TraceScene, ticks: list[int], ti: int, frac: float
) -> dict[int, tuple[float, float]]:
    """Positions at a fractional point between ticks[ti] and ticks[ti+1]."""
    if ti >= len(ticks) - 1 or frac == 0.0:
        return scene.positions_at(ticks[ti])
    p0 = scene.positions_at(ticks[ti])
    p1 = scene.positions_at(ticks[ti + 1])
    result: dict[int, tuple[float, float]] = {}
    all_pids = set(p0) | set(p1)
    for pid in all_pids:
        a = p0.get(pid)
        b = p1.get(pid)
        if a is None:
            result[pid] = b
        elif b is None:
            result[pid] = a
        else:
            result[pid] = (a[0] + (b[0] - a[0]) * frac,
                           a[1] + (b[1] - a[1]) * frac)
    return result

def _events_by_tick(trace: dict) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for ev in trace.get("events", []):
        t = int(ev.get("tick", 0))
        out.setdefault(t, []).append(ev)
    return out

def _is_persistent_event(ev_type: str) -> bool:
    """Events that benefit visually from persistence (pulses)."""
    return ev_type in {"PARTICLE_SPAWN", "PARTICLE_DEATH", "PARTICLE_TURN",
                       "INSTRUCTION_EXECUTED", "FIELD_WRITE"}

# ── Drawing helpers ─────────────────────────────────────────────────
def _draw_grid(canvas: Image.Image, scene: TraceScene, scale: int) -> ImageDraw.ImageDraw:
    draw = ImageDraw.Draw(canvas)
    for gy in range(scene.height):
        for gx in range(scene.width):
            if (gx + gy) % 2 == 0:
                draw.rectangle(
                    [gx * scale, gy * scale, gx * scale + scale - 1, gy * scale + scale - 1],
                    fill=GRID)
    return draw

def _draw_glow_circle(draw: ImageDraw.ImageDraw, cx: float, cy: float,
                       radius: float, color: tuple, intensity: float = 0.35):
    """Concentric circles blending toward BG. Deterministic."""
    for i in range(3, 0, -1):
        r = radius * (1.0 + i * 0.6)
        c = _blend(DARK_BG, color, intensity * (1.0 / i))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)

def _draw_entity_marker(draw: ImageDraw.ImageDraw, cx: float, cy: float,
                         color: tuple, radius: float = 8.0, pulse: bool = False):
    """Entity dot with optional pulse ring."""
    if pulse:
        for i in range(3, 0, -1):
            pr = radius * (1.0 + i * 0.5)
            c = _blend(DARK_BG, color, 0.15 * (1.0 / i))
            draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=c)
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=color)

def _draw_label(draw: ImageDraw.ImageDraw, x: float, y: float, text: str,
                color: tuple = TEXT_COLOR, font=None):
    font = font or FONT_S
    draw.text((x + 6, y - 8), text, font=font, fill=color)

def _draw_caption_bar(canvas: Image.Image, text: str, y_offset: int,
                       color: tuple = ACCENT):
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, y_offset, canvas.width - 1, canvas.height - 1], fill=(6, 6, 10))
    draw.text((12, y_offset + 8), text, font=FONT_M, fill=color)

# ── Profile: debug (wraps existing renderer) ───────────────────────
def _render_debug(scene: TraceScene, spec: RenderSpec, trace: Any,
                  output: Path) -> Path:
    from flow.render.gif import render_gif
    return render_gif(
        trace, output, layout="arena", scale=spec.scale,
        duration_ms=spec.duration_ms, max_frames=spec.max_frames)

# ── Profile: trails ────────────────────────────────────────────────
def _render_trails(scene: TraceScene, spec: RenderSpec, trace: dict,
                   output: Path) -> Path:
    ticks = _unique_ticks(scene)
    plan = _build_frame_plan(ticks, spec)
    ebk = _events_by_tick(trace)
    pers = spec.persistence_frames

    canvas_base = Image.new("RGB", (scene.width * spec.scale, scene.height * spec.scale), DARK_BG)
    frames: list[Image.Image] = []

    for ti, frac in plan:
        frame = canvas_base.copy()
        draw = _draw_grid(frame, scene, spec.scale)
        sc = spec.scale

        # cumulative trail segments up to current visual time
        trails = scene.trails_upto(ticks[ti])
        # fade: draw old trails dimmer
        n_trails = len(trails)
        trail_start = max(0, n_trails - spec.trail_length * 2)
        for idx, (pid, x0, y0, x1, y1) in enumerate(trails[trail_start:], trail_start):
            age = (n_trails - idx) / max(1, n_trails - trail_start)
            c = _dim_color(pid_color(pid), 1.0 - age * 0.7)
            draw.line(
                [(x0 * sc + sc / 2, y0 * sc + sc / 2),
                 (x1 * sc + sc / 2, y1 * sc + sc / 2)],
                fill=c, width=max(1, sc // 3))

        # partial interpolated trail for current segment
        if frac > 0 and ti < len(ticks) - 1:
            pos0 = scene.positions_at(ticks[ti])
            pos1 = scene.positions_at(ticks[ti + 1])
            for pid in set(pos0) & set(pos1):
                a, b = pos0[pid], pos1[pid]
                ix = a[0] + (b[0] - a[0]) * frac
                iy = a[1] + (b[1] - a[1]) * frac
                c = _dim_color(pid_color(pid), 0.4)
                draw.line(
                    [(a[0] * sc + sc / 2, a[1] * sc + sc / 2),
                     (ix * sc + sc / sc * sc / 2, iy * sc + sc / 2)],
                    fill=c, width=max(1, sc // 4))

        # event pulses (persistence)
        frame_idx = ti * spec.interpolate + int(frac * spec.interpolate)
        for t in range(max(0, ti - pers), ti + 1):
            for ev in ebk.get(ticks[t], []):
                if not _is_persistent_event(ev["type"]):
                    continue
                payload = ev.get("payload", {})
                px = payload.get("x") or payload.get("x_to")
                py = payload.get("y") or payload.get("y_to")
                if px is None or py is None:
                    continue
                age = (ti - t) / max(1, pers)
                c = _blend(DIM, ACCENT, 1.0 - age)
                _draw_glow_circle(draw, px * sc + sc / 2, py * sc + sc / 2,
                                  sc * 0.5, c, intensity=0.3 * (1.0 - age))

        # particles
        pos = _interpolated_positions(scene, ticks, ti, frac)
        dead = {pid for pid, (dt, _, _) in scene.deaths.items() if dt <= ticks[ti]}
        for pid in set(pos) - dead:
            x, y = pos[pid]
            c = pid_color(pid)
            cx, cy = x * sc + sc / 2, y * sc + sc / 2
            _draw_glow_circle(draw, cx, cy, sc * 0.3, c, intensity=0.25)
            draw.ellipse([cx - sc * 0.28, cy - sc * 0.28, cx + sc * 0.28, cy + sc * 0.28], fill=c)

        # death markers
        for pid, (dt, x, y) in scene.deaths.items():
            if dt <= ticks[ti]:
                cx, cy = x * sc + sc / 2, y * sc + sc / 2
                r = sc * 0.35
                draw.line([(cx - r, cy - r), (cx + r, cy + r)], fill=DEATH, width=2)
                draw.line([(cx - r, cy + r), (cx + r, cy - r)], fill=DEATH, width=2)

        # tick label
        draw.text((6, 4), f"tick {ticks[ti]:04d}", font=FONT_S, fill=DIM)
        frames.append(frame)

    return _save_gif(frames, output, spec)

# ── Profile: heatmap ───────────────────────────────────────────────
def _render_heatmap(scene: TraceScene, spec: RenderSpec, trace: dict,
                    output: Path) -> Path:
    ticks = _unique_ticks(scene)
    plan = _build_frame_plan(ticks, spec)
    sc = spec.scale
    canvas_w, canvas_h = scene.width * sc, scene.height * sc

    # accumulate cell visits from real events (semantic, not invented)
    heat: dict[tuple[int, int], int] = {}
    for ev in trace.get("events", []):
        payload = ev.get("payload", {})
        for key in ("x", "x_to"):
            if key in payload and f"{'y' if key == 'x' else 'y_to'}" in payload:
                yk = "y" if key == "x" else "y_to"
                cx, cy = int(payload[key]), int(payload[yk])
                heat[(cx, cy)] = heat.get((cx, cy), 0) + 1
    max_heat = max(heat.values()) if heat else 1

    # cumulative heatmap per tick index
    cum_heat: dict[int, dict[tuple[int, int], int]] = {}
    running: dict[tuple[int, int], int] = {}
    for ti, t in enumerate(ticks):
        for ev in _events_by_tick(trace).get(t, []):
            payload = ev.get("payload", {})
            for key in ("x", "x_to"):
                if key in payload:
                    yk = "y" if key == "x" else "y_to"
                    if yk in payload:
                        cell = (int(payload[key]), int(payload[yk]))
                        running[cell] = running.get(cell, 0) + 1
        cum_heat[ti] = dict(running)

    frames: list[Image.Image] = []
    for ti, frac in plan:
        frame = Image.new("RGB", (canvas_w, canvas_h), DARK_BG)
        draw = _draw_grid(frame, scene, sc)
        ch = cum_heat.get(ti, {})

        # heatmap cells
        for (x, y), count in ch.items():
            ratio = count / max_heat
            c = _heat_color(ratio)
            draw.rectangle([x * sc, y * sc, x * sc + sc - 1, y * sc + sc - 1], fill=c)

        # particles overlaid
        pos = _interpolated_positions(scene, ticks, ti, frac)
        dead = {pid for pid, (dt, _, _) in scene.deaths.items() if dt <= ticks[ti]}
        for pid in set(pos) - dead:
            x, y = pos[pid]
            cx, cy = x * sc + sc / 2, y * sc + sc / 2
            draw.ellipse([cx - sc * 0.25, cy - sc * 0.25, cx + sc * 0.25, cy + sc * 0.25],
                         fill=(255, 255, 255), outline=ACCENT)

        draw.text((6, 4), f"tick {ticks[ti]:04d}  heat cells: {len(ch)}", font=FONT_S, fill=DIM)
        frames.append(frame)

    return _save_gif(frames, output, spec)

# ── Profile: graph ─────────────────────────────────────────────────
def _render_graph(scene: TraceScene, spec: RenderSpec, trace: dict,
                  output: Path) -> Path:
    # build event sequence (semantic: from real trace only)
    ev_seq = [e["type"] for e in trace.get("events", [])]
    if not ev_seq:
        canvas = Image.new("RGB", (512, 512), DARK_BG)
        draw = ImageDraw.Draw(canvas)
        draw.text((200, 240), "NO EVENTS", font=FONT_L, fill=DIM)
        canvas.save(output)
        return output

    # unique types in first-appearance order
    seen: list[str] = []
    for t in ev_seq:
        if t not in seen:
            seen.append(t)
    node_idx = {n: i for i, n in enumerate(seen)}
    n_nodes = len(seen)

    # edges: consecutive pairs
    edges: list[tuple[str, str]] = []
    for i in range(1, len(ev_seq)):
        edges.append((ev_seq[i - 1], ev_seq[i]))
    n_edges = len(edges)

    # circle layout
    W, H = 640, 480
    cx, cy, R = W // 2, H // 2, min(W, H) * 0.36
    node_pos: dict[str, tuple[float, float]] = {}
    for name, idx in node_idx.items():
        angle = idx * 2 * math.pi / max(1, n_nodes) - math.pi / 2
        node_pos[name] = (cx + R * math.cos(angle), cy + R * math.sin(angle))

    # progressive reveal
    frames_per_edge = max(1, spec.max_frames // max(1, n_edges))
    total_frames = min(spec.max_frames, n_edges * frames_per_edge + 2)

    frames: list[Image.Image] = []
    for fi in range(total_frames):
        frame = Image.new("RGB", (W, H), DARK_BG)
        draw = ImageDraw.Draw(frame)

        # edges revealed up to fi
        n_show = min(n_edges, max(0, (fi - 1) * n_edges // max(1, total_frames - 2)))
        for ei in range(n_show):
            src, dst = edges[ei]
            x0, y0 = node_pos[src]
            x1, y1 = node_pos[dst]
            c = _dim_color(pid_color(ei % 8), 0.7)
            draw.line([(x0, y0), (x1, y1)], fill=c, width=2)

        # nodes
        for name in seen:
            x, y = node_pos[name]
            color = ACCENT
            draw.ellipse([x - 18, y - 18, x + 18, y + 18], fill=DARK_BG, outline=color, width=2)
            # label below
            draw.text((x - 30, y + 22), name, font=FONT_S, fill=TEXT_COLOR)

        # stats
        draw.text((10, H - 24), f"events: {n_edges}  nodes: {n_nodes}", font=FONT_S, fill=DIM)
        frames.append(frame)

    return _save_gif(frames, output, spec)

# ── Profile: orbit ─────────────────────────────────────────────────
def _render_orbit(scene: TraceScene, spec: RenderSpec, trace: dict,
                  output: Path) -> Path:
    ticks = _unique_ticks(scene)
    plan = _build_frame_plan(ticks, spec)
    ebk = _events_by_tick(trace)
    pers = spec.persistence_frames

    # entities: from spec, or default: one per particle spawn
    entities = spec.entities
    if not entities:
        for pid, (t0, x, y) in sorted(scene.spawns.items()):
            from flow.render.spec import RenderEntity
            entities.append(RenderEntity(id=f"pid_{pid}", label=f"particle {pid}", pid=pid))

    n_ent = len(entities)
    W = spec.layout.get("canvas", [480, 480])[0] if spec.layout.get("canvas") else 480
    H = spec.layout.get("canvas", [480, 480])[1] if spec.layout.get("canvas") else 480
    radius = spec.layout.get("radius", min(W, H) * 0.32)
    ecx, ecy = W // 2, H // 2

    # entity positions (presentation coordinates)
    ent_pos: dict[str, tuple[float, float]] = {}
    for i, e in enumerate(entities):
        if e.position:
            ent_pos[e.id] = (e.position[0] * W, e.position[1] * H)
        elif n_ent:
            angle = i * 2 * math.pi / n_ent - math.pi / 2
            ent_pos[e.id] = (ecx + radius * math.cos(angle),
                             ecy + radius * math.sin(angle))
        else:
            ent_pos[e.id] = (ecx, ecy)

    pid_to_ent: dict[int, str] = {}
    for e in entities:
        if e.pid is not None:
            pid_to_ent[e.pid] = e.id

    frames: list[Image.Image] = []
    for ti, frac in plan:
        frame = Image.new("RGB", (W, H), DARK_BG)
        draw = ImageDraw.Draw(frame)

        # subtle radial grid
        for r in range(40, int(radius * 1.3), 40):
            draw.ellipse([ecx - r, ecy - r, ecx + r, ecy + r], outline=(20, 20, 30))

        # connection lines for active particles
        pos = _interpolated_positions(scene, ticks, ti, frac)
        for pid, (x, y) in pos.items():
            eid = pid_to_ent.get(pid)
            if eid and eid in ent_pos:
                ex, ey = ent_pos[eid]
                draw.line([(ex, ey), (x * spec.scale * scene.width / max(1, scene.width) + 10,
                                       y * spec.scale * scene.height / max(1, scene.height) + 10)],
                          fill=_dim_color(pid_color(pid), 0.15), width=1)

        # event pulses on entities
        frame_idx = ti * spec.interpolate
        for t in range(max(0, ti - pers), ti + 1):
            for ev in ebk.get(ticks[t], []):
                ev_type = ev["type"]
                pid = ev.get("pid", -1)
                eid = pid_to_ent.get(pid)
                # check event_styles mapping
                if not eid and ev_type in spec.event_styles:
                    eid = spec.event_styles[ev_type].get("entity")
                if eid and eid in ent_pos:
                    age = (ti - t) / max(1, pers)
                    pulse = age < 0.3
                    ex, ey = ent_pos[eid]
                    c = _blend(DIM, ACCENT, 1.0 - age)
                    _draw_entity_marker(draw, ex, ey, c, radius=10 + 6 * (1 - age), pulse=pulse)

        # entity dots + labels
        for e in entities:
            ex, ey = ent_pos[e.id]
            _draw_entity_marker(draw, ex, ey, ACCENT, radius=8, pulse=False)
            _draw_label(draw, ex, ey - 14, e.label, TEXT_COLOR, FONT_S)

        draw.text((8, 8), f"tick {ticks[ti]:04d}", font=FONT_S, fill=DIM)
        frames.append(frame)

    return _save_gif(frames, output, spec)

# ── Profile: story ─────────────────────────────────────────────────
def _render_story(scene: TraceScene, spec: RenderSpec, trace: dict,
                  output: Path) -> Path:
    ticks = _unique_ticks(scene)
    plan = _build_frame_plan(ticks, spec)
    ebk = _events_by_tick(trace)
    pers = spec.persistence_frames

    entities = spec.entities or []
    n_ent = len(entities)
    W = spec.layout.get("canvas", [640, 480])[0] if spec.layout.get("canvas") else 640
    H = spec.layout.get("canvas", [640, 480])[1] if spec.layout.get("canvas") else 480
    radius = spec.layout.get("radius", min(W, H) * 0.30)
    ecx, ecy = W // 2, H // 2 - 20

    ent_pos: dict[str, tuple[float, float]] = {}
    for i, e in enumerate(entities):
        if e.position:
            ent_pos[e.id] = (e.position[0] * W, e.position[1] * H)
        elif n_ent:
            angle = i * 2 * math.pi / n_ent - math.pi / 2
            ent_pos[e.id] = (ecx + radius * math.cos(angle),
                             ecy + radius * math.sin(angle))
        else:
            ent_pos[e.id] = (ecx, ecy)

    pid_to_ent: dict[int, str] = {}
    for e in entities:
        if e.pid is not None:
            pid_to_ent[e.pid] = e.id

    caption_y = H - 60

    frames: list[Image.Image] = []
    for ti, frac in plan:
        frame = Image.new("RGB", (W, H), DARK_BG)
        draw = ImageDraw.Draw(frame)

        # entities
        for e in entities:
            ex, ey = ent_pos[e.id]
            _draw_entity_marker(draw, ex, ey, ACCENT, radius=10, pulse=False)
            _draw_label(draw, ex, ey - 18, e.label, TEXT_COLOR, FONT_M)

        # event pulses + caption
        caption = ""
        frame_idx = ti * spec.interpolate
        for t in range(max(0, ti - pers), ti + 1):
            for ev in ebk.get(ticks[t], []):
                ev_type = ev["type"]
                pid = ev.get("pid", -1)
                eid = pid_to_ent.get(pid)
                if not eid and ev_type in spec.event_styles:
                    eid = spec.event_styles[ev_type].get("entity")
                if eid and eid in ent_pos:
                    age = (ti - t) / max(1, pers)
                    ex, ey = ent_pos[eid]
                    c = _blend(DIM, ACCENT, 1.0 - age)
                    _draw_entity_marker(draw, ex, ey, c, radius=12 + 8 * (1 - age), pulse=True)
                # update caption for most recent event
                if t == ticks[ti] and ti == t:
                    caption = f"tick {ev_type}  pid {pid}"

        # caption bar
        _draw_caption_bar(frame, caption, caption_y)

        draw.text((8, 8), f"tick {ticks[ti]:04d}", font=FONT_S, fill=DIM)
        frames.append(frame)

    return _save_gif(frames, output, spec)

# ── Profile: cinematic ─────────────────────────────────────────────
def _render_cinematic(scene: TraceScene, spec: RenderSpec, trace: dict,
                      output: Path) -> Path:
    ticks = _unique_ticks(scene)
    plan = _build_frame_plan(ticks, spec)
    ebk = _events_by_tick(trace)
    pers = spec.persistence_frames
    sc = spec.scale
    canvas_w, canvas_h = scene.width * sc, scene.height * sc

    canvas_base = Image.new("RGB", (canvas_w, canvas_h), GLOW_BG)
    frames: list[Image.Image] = []

    for ti, frac in plan:
        frame = canvas_base.copy()
        draw = _draw_grid(frame, scene, sc)

        # cumulative trails with glow
        trails = scene.trails_upto(ticks[ti])
        for idx, (pid, x0, y0, x1, y1) in enumerate(trails):
            age = max(0, 1.0 - (len(trails) - idx) / max(1, spec.trail_length * 4))
            c = _dim_color(pid_color(pid), 0.3 + 0.7 * age)
            w = max(1, int(sc * 0.4 * (0.5 + 0.5 * age)))
            draw.line(
                [(x0 * sc + sc / 2, y0 * sc + sc / 2),
                 (x1 * sc + sc / 2, y1 * sc + sc / 2)],
                fill=c, width=w)

        # interpolated partial trail
        if frac > 0 and ti < len(ticks) - 1:
            pos0 = scene.positions_at(ticks[ti])
            pos1 = scene.positions_at(ticks[ti + 1])
            for pid in set(pos0) & set(pos1):
                a, b = pos0[pid], pos1[pid]
                ix = a[0] + (b[0] - a[0]) * frac
                iy = a[1] + (b[1] - a[1]) * frac
                c = _dim_color(pid_color(pid), 0.25)
                draw.line(
                    [(a[0] * sc + sc / 2, a[1] * sc + sc / 2),
                     (ix * sc + sc / 2, iy * sc + sc / 2)],
                    fill=c, width=max(1, sc // 4))

        # event persistence pulses with glow
        for t in range(max(0, ti - pers), ti + 1):
            for ev in ebk.get(ticks[t], []):
                if not _is_persistent_event(ev["type"]):
                    continue
                payload = ev.get("payload", {})
                px = payload.get("x") or payload.get("x_to")
                py = payload.get("y") or payload.get("y_to")
                if px is None or py is None:
                    continue
                age = (ti - t) / max(1, pers)
                c = _blend(HALO, ACCENT, 1.0 - age)
                _draw_glow_circle(draw, px * sc + sc / 2, py * sc + sc / 2,
                                  sc * 0.6, c, intensity=0.4 * (1.0 - age))

        # particles with glow
        pos = _interpolated_positions(scene, ticks, ti, frac)
        dead = {pid for pid, (dt, _, _) in scene.deaths.items() if dt <= ticks[ti]}
        for pid in set(pos) - dead:
            x, y = pos[pid]
            c = pid_color(pid)
            cx, cy = x * sc + sc / 2, y * sc + sc / 2
            _draw_glow_circle(draw, cx, cy, sc * 0.5, c, intensity=0.4)
            draw.ellipse([cx - sc * 0.22, cy - sc * 0.22, cx + sc * 0.22, cy + sc * 0.22], fill=c)

        # death markers
        for pid, (dt, x, y) in scene.deaths.items():
            if dt <= ticks[ti]:
                cx, cy = x * sc + sc / 2, y * sc + sc / 2
                r = sc * 0.3
                draw.line([(cx - r, cy - r), (cx + r, cy + r)], fill=DEATH, width=2)
                draw.line([(cx - r, cy + r), (cx + r, cy - r)], fill=DEATH, width=2)

        frames.append(frame)

    return _save_gif(frames, output, spec)

# ── GIF encoder ────────────────────────────────────────────────────
def _save_gif(frames: list[Image.Image], output: Path, spec: RenderSpec) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(frames) == 1:
        frames[0].save(output, duration=spec.duration_ms, loop=0)
    else:
        frames[0].save(
            output, save_all=True, append_images=frames[1:],
            duration=spec.duration_ms, loop=0)
    return output

# ── Dispatcher ─────────────────────────────────────────────────────
_RENDERERS = {
    "debug": _render_debug,
    "trails": _render_trails,
    "heatmap": _render_heatmap,
    "graph": _render_graph,
    "orbit": _render_orbit,
    "story": _render_story,
    "cinematic": _render_cinematic,
}

def render_profile(
    trace: Any,
    output: str | Path,
    spec: RenderSpec,
    *,
    program_name: str = "program",
) -> Path:
    """Render a trace to output using the requested profile.

    Returns Path to the written file.
    """
    if spec.profile not in _RENDERERS:
        raise ValueError(f"unknown profile: {spec.profile!r}")
    d = _as_trace(trace)
    scene = TraceScene.from_trace(d)
    fn = _RENDERERS[spec.profile]
    return fn(scene, spec, d, Path(output))
