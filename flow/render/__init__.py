"""FLOW renderers (M2+M3) — media produced ONLY from an ExecutionTrace."""

from flow.render.gif import render_gif
from flow.render.image import arena_dims, render_image
from flow.render.session import render_session_gif
from flow.render.spec import RenderSpec, KNOWN_EVENT_TYPES
from flow.render.profiles import render_profile

__all__ = [
    "arena_dims", "render_gif", "render_image", "render_session_gif",
    "RenderSpec", "KNOWN_EVENT_TYPES", "render_profile",
]