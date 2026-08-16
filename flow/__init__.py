"""FLOW — paquete principal, re-exporta API de core."""
from .core import (
    FlowVM,
    FlowConfig,
    Particle,
    exec_instruction,
    sample_bilinear,
    sample_vec,
    make_hello_flow,
    make_vortex,
)

__all__ = [
    "FlowVM",
    "FlowConfig",
    "Particle",
    "exec_instruction",
    "sample_bilinear",
    "sample_vec",
    "make_hello_flow",
    "make_vortex",
]