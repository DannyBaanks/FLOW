"""FLOW render contract (FASE 2): RenderSpec + validation.

RenderSpec NEVER alters the ExecutionTrace. It only selects and parameterizes
a presentation of events that already occurred.

Contract:
  SEMANTIC EVENT -> produced by flow.runtime (types below, observed from VM).
  VISUAL EFFECT  -> produced by render profiles (pulses, trails, interpolation,
                    persistence, camera/labels). Never becomes a trace event.

  INTERPOLATED_POSITION = PRESENTATION (frames between two real tick states).
  EVENT_OCCURRENCE_COUNT (trace) != EVENT_VISUAL_DURATION (frames on screen).

Validation rules (NO GUESSING):
  - unknown profile                     -> FAIL
  - unknown top-level spec key          -> FAIL
  - wrong schema_version                -> FAIL
  - entity missing id / bad shape       -> FAIL
  - event_styles key not an event type  -> FAIL
  - event_styles referencing unknown entity -> FAIL
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

PROFILES = ("debug", "trails", "heatmap", "graph", "orbit", "story", "cinematic")

# Event types producible by flow.runtime.flow_trace (single source of truth:
# they appear verbatim in run_program). A spec may only reference these.
KNOWN_EVENT_TYPES = frozenset({
    "PARTICLE_SPAWN", "PARTICLE_DEATH", "PARTICLE_MOVE", "PARTICLE_TURN",
    "STATE_CHANGE", "INSTRUCTION_EXECUTED", "FIELD_WRITE", "PARTICLE_TRACE",
})

_TOP_KEYS = {"schema_version", "profile", "scale", "duration_ms", "max_frames",
             "interpolate", "persistence_frames", "trail_length",
             "entities", "layout", "event_styles", "timing"}

_LAYOUT_KEYS = {"canvas", "radius", "center", "arena_scale"}
_TIMING_KEYS = {"lead_in_frames", "lead_out_frames", "frames_per_event"}
_STYLE_KEYS = {"entity", "emphasis", "color", "text"}


class SpecError(ValueError):
    """Raised when a RenderSpec or profile is invalid. Never silent."""


@dataclass
class RenderEntity:
    """Visual entity: a labelled element in the presentation layer.

    `pid` binds the entity to a REAL particle id from the trace (optional).
    `position` is a PRESENTATION coordinate (canvas units of the profile);
    it claims nothing about VM positions.
    """
    id: str
    label: str = ""
    pid: int | None = None
    position: tuple[float, float] | None = None


@dataclass
class RenderSpec:
    profile: str
    scale: int = 4
    duration_ms: int = 80
    max_frames: int = 220
    interpolate: int = 6              # visual frames between ticks (presentational)
    persistence_frames: int = 8       # how long an event pulse remains visible
    trail_length: int = 14            # visual trail segments kept per particle
    entities: list[RenderEntity] = field(default_factory=list)
    layout: dict[str, Any] = field(default_factory=dict)
    event_styles: dict[str, dict[str, Any]] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)

    # -- construction / validation -------------------------------------
    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RenderSpec":
        if not isinstance(raw, dict):
            raise SpecError("spec must be a JSON object")
        unknown = set(raw) - _TOP_KEYS
        if unknown:
            raise SpecError(f"unknown spec keys: {sorted(unknown)}")
        version = raw.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise SpecError(
                f"unsupported schema_version {version!r} (expected {SCHEMA_VERSION})")
        profile = raw.get("profile")
        if profile not in PROFILES:
            raise SpecError(f"unknown profile: {profile!r} (allowed: {list(PROFILES)})")

        entities: list[RenderEntity] = []
        seen_ids: set[str] = set()
        for e in raw.get("entities", []) or []:
            if not isinstance(e, dict) or not isinstance(e.get("id"), str):
                raise SpecError(f"entity must be an object with a string 'id': {e!r}")
            if e["id"] in seen_ids:
                raise SpecError(f"duplicate entity id: {e['id']!r}")
            seen_ids.add(e["id"])
            pos = e.get("position")
            if pos is not None and (not isinstance(pos, (list, tuple)) or len(pos) != 2):
                raise SpecError(f"entity {e['id']!r}: position must be [x, y]")
            pid = e.get("pid")
            if pid is not None and not isinstance(pid, int):
                raise SpecError(f"entity {e['id']!r}: pid must be an integer")
            entities.append(RenderEntity(
                id=e["id"], label=str(e.get("label", e["id"])), pid=pid,
                position=(float(pos[0]), float(pos[1])) if pos is not None else None))

        layout = raw.get("layout", {}) or {}
        if not isinstance(layout, dict) or set(layout) - _LAYOUT_KEYS:
            raise SpecError(f"layout: bad keys {sorted(set(layout) - _LAYOUT_KEYS)}")

        timing = raw.get("timing", {}) or {}
        if not isinstance(timing, dict) or set(timing) - _TIMING_KEYS:
            raise SpecError(f"timing: bad keys {sorted(set(timing) - _TIMING_KEYS)}")

        styles = raw.get("event_styles", {}) or {}
        if not isinstance(styles, dict):
            raise SpecError("event_styles must be an object")
        for ev_type, sty in styles.items():
            if ev_type not in KNOWN_EVENT_TYPES:
                raise SpecError(
                    f"event_styles: unknown semantic event type {ev_type!r} "
                    f"(allowed: {sorted(KNOWN_EVENT_TYPES)})")
            if not isinstance(sty, dict) or set(sty) - _STYLE_KEYS:
                raise SpecError(
                    f"event_styles[{ev_type!r}]: bad keys "
                    f"{sorted(set(sty) - _STYLE_KEYS)}")
            ent = sty.get("entity")
            if ent is not None and ent not in seen_ids:
                raise SpecError(
                    f"event_styles[{ev_type!r}] references unknown entity {ent!r}")

        return cls(
            profile=profile,
            scale=int(raw.get("scale", 6)),
            duration_ms=int(raw.get("duration_ms", 80)),
            max_frames=int(raw.get("max_frames", 220)),
            interpolate=max(1, int(raw.get("interpolate", 6))),
            persistence_frames=max(1, int(raw.get("persistence_frames", 8))),
            trail_length=max(0, int(raw.get("trail_length", 14))),
            entities=entities, layout=layout, event_styles=styles, timing=timing,
        )

    @classmethod
    def load(cls, path: str | Path) -> "RenderSpec":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SpecError(f"malformed spec JSON: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def default(cls, profile: str, **overrides: Any) -> "RenderSpec":
        raw: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "profile": profile}
        raw.update(overrides)
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": self.profile,
            "scale": self.scale,
            "duration_ms": self.duration_ms,
            "max_frames": self.max_frames,
            "interpolate": self.interpolate,
            "persistence_frames": self.persistence_frames,
            "trail_length": self.trail_length,
            "entities": [
                {"id": e.id, "label": e.label, "pid": e.pid,
                 "position": list(e.position) if e.position else None}
                for e in self.entities],
            "layout": self.layout,
            "event_styles": self.event_styles,
            "timing": self.timing,
        }
