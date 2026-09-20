"""Load bench config and construct instruments by role."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from instruments.camera import Camera
from instruments.generator import SignalGenerator
from instruments.oscilloscope import Oscilloscope

LAB_CONFIG_PATH = Path(__file__).resolve().parent / "lab.json"


class OscilloscopeId(StrEnum):
    """Registered oscilloscope model ids (lab.json ``connections`` keys)."""

    MSO1104 = "rigol_mso1104"
    MHO954 = "rigol_mho954"


# Flip this to change what open_oscilloscope() / CLIs use when --scope is omitted.
DEFAULT_OSCILLOSCOPE = OscilloscopeId.MSO1104
DEFAULT_CAMERA = "marshall_cv420_30x_ndi"


def load_lab(path: Path | None = None) -> dict[str, Any]:
    config_path = path or LAB_CONFIG_PATH
    return json.loads(config_path.read_text(encoding="utf-8"))


def connection_for(model_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    lab = config or load_lab()
    connections = lab.get("connections", {})
    return dict(connections.get(model_id, {}))


def _role_model(role: str, model_id: str | None, config: dict[str, Any]) -> str:
    if model_id:
        return model_id
    roles = config.get("roles", {})
    chosen = roles.get(role)
    if not chosen:
        raise KeyError(f"No model configured for role {role!r} in {LAB_CONFIG_PATH.name}")
    return str(chosen)


def _oscilloscope_classes() -> dict[str, type[Oscilloscope]]:
    from instruments.oscilloscopes.rigol_mho954.driver import RigolMHO954
    from instruments.oscilloscopes.rigol_mso1104.driver import RigolMSO1104

    return {
        OscilloscopeId.MSO1104: RigolMSO1104,
        OscilloscopeId.MHO954: RigolMHO954,
    }


def _generator_classes() -> dict[str, type[SignalGenerator]]:
    from instruments.generators.rigol_dg4062.driver import RigolDG4062

    return {
        "rigol_dg4062": RigolDG4062,
    }


def list_oscilloscopes() -> list[str]:
    return sorted(str(name) for name in _oscilloscope_classes())


def list_generators() -> list[str]:
    return sorted(_generator_classes())


def _camera_classes() -> dict[str, type[Camera]]:
    from instruments.cameras.marshall_cv420_30x_ndi.driver import MarshallCV42030XNDI

    return {
        "marshall_cv420_30x_ndi": MarshallCV42030XNDI,
    }


def list_cameras() -> list[str]:
    return sorted(_camera_classes())


def open_oscilloscope(model_id: str | OscilloscopeId | None = None) -> Oscilloscope:
    """Construct and connect the oscilloscope for this bench (or ``model_id``).

    Resolution: explicit ``model_id`` / ``--scope`` → ``DEFAULT_OSCILLOSCOPE``
    → ``lab.json`` ``roles.oscilloscope``.
    """
    config = load_lab()
    if model_id:
        chosen = str(model_id)
    elif DEFAULT_OSCILLOSCOPE:
        chosen = str(DEFAULT_OSCILLOSCOPE)
    else:
        chosen = _role_model("oscilloscope", None, config)
    classes = _oscilloscope_classes()
    if chosen not in classes:
        known = ", ".join(classes) or "(none)"
        raise KeyError(f"Unknown oscilloscope {chosen!r}. Registered: {known}")
    instrument = classes[chosen](connection_for(chosen, config))
    instrument.connect()
    return instrument


def open_generator(model_id: str | None = None) -> SignalGenerator:
    """Construct and connect the generator for this bench (or ``model_id``)."""
    config = load_lab()
    chosen = _role_model("generator", model_id, config)
    classes = _generator_classes()
    if chosen not in classes:
        known = ", ".join(classes) or "(none)"
        raise KeyError(f"Unknown generator {chosen!r}. Registered: {known}")
    instrument = classes[chosen](connection_for(chosen, config))
    instrument.connect()
    return instrument


def open_camera(model_id: str | None = None) -> Camera:
    """Construct and connect the camera for this bench (or ``model_id``).

    Resolution: explicit ``model_id`` / ``--camera`` → ``DEFAULT_CAMERA``
    → ``lab.json`` ``roles.camera``.
    """
    config = load_lab()
    if model_id:
        chosen = str(model_id)
    elif DEFAULT_CAMERA:
        chosen = str(DEFAULT_CAMERA)
    else:
        chosen = _role_model("camera", None, config)
    classes = _camera_classes()
    if chosen not in classes:
        known = ", ".join(classes) or "(none)"
        raise KeyError(f"Unknown camera {chosen!r}. Registered: {known}")
    instrument = classes[chosen](connection_for(chosen, config))
    instrument.connect()
    return instrument
