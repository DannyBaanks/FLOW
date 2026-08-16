"""FLOW core VM — exporta la API pública."""
from .flow import (
    FlowConfig,
    FlowVM,
    Particle,
    exec_instruction,
    make_hello_flow,
    make_vortex,
    sample_bilinear,
    sample_vec,
)

__all__ = [
    "FlowConfig",
    "FlowVM",
    "Particle",
    "exec_instruction",
    "make_hello_flow",
    "make_vortex",
    "sample_bilinear",
    "sample_vec",
]