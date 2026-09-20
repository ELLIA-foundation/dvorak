"""Instrument roles, bench config, and model registry.

Campaigns and tools should import ``open_oscilloscope`` / ``open_generator``
/ ``open_camera`` from this package, not a specific model module.
"""

from instruments.registry import (
    DEFAULT_CAMERA,
    DEFAULT_OSCILLOSCOPE,
    OscilloscopeId,
    load_lab,
    open_camera,
    open_generator,
    open_oscilloscope,
)

__all__ = [
    "DEFAULT_CAMERA",
    "DEFAULT_OSCILLOSCOPE",
    "OscilloscopeId",
    "load_lab",
    "open_camera",
    "open_generator",
    "open_oscilloscope",
]
