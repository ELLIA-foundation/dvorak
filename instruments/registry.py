"""Load bench config and construct instruments by role."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from instruments.generator import SignalGenerator
from instruments.oscilloscope import Oscilloscope

LAB_CONFIG_PATH = Path(__file__).resolve().parent / "lab.json"


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
    from instruments.oscilloscopes.rigol_mso1104.driver import RigolMSO1104

    return {
        "rigol_mso1104": RigolMSO1104,
    }


def _generator_classes() -> dict[str, type[SignalGenerator]]:
    from instruments.generators.rigol_dg4062.driver import RigolDG4062

    return {
        "rigol_dg4062": RigolDG4062,
    }


def list_oscilloscopes() -> list[str]:
    return sorted(_oscilloscope_classes())


def list_generators() -> list[str]:
    return sorted(_generator_classes())


def open_oscilloscope(model_id: str | None = None) -> Oscilloscope:
    """Construct and connect the oscilloscope for this bench (or ``model_id``)."""
    config = load_lab()
    chosen = _role_model("oscilloscope", model_id, config)
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
