"""Plugin registry for analysis (and later measurement) windows.

A plugin is a small ``AnalysisSpec``. Importing a module that calls ``register``
is enough to make it appear in the launcher — the launcher does not hard-code
titles. Keep this module free of Qt and of ``instruments``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

FAMILY_ANALYSIS = "analysis"
FAMILY_MEASUREMENT = "measurement"

WindowFactory = Callable[[Any], Any]


@dataclass(frozen=True)
class Option:
    """Schema entry for a parameter form. Unused until Phase 3."""

    key: str
    type: str
    label: str
    default: Any = None
    help: str = ""
    unit: str = ""
    choices: tuple[str, ...] = ()


@dataclass
class AnalysisSpec:
    id: str
    title: str
    description: str
    family: str
    accepted_kinds: tuple[str, ...] = ()
    options: tuple[Option, ...] = ()
    window_factory: WindowFactory | None = None
    enabled: bool = True
    disabled_reason: str = ""


_REGISTRY: dict[str, AnalysisSpec] = {}


def register(spec: AnalysisSpec) -> AnalysisSpec:
    if spec.id in _REGISTRY:
        raise ValueError(f"analysis id already registered: {spec.id!r}")
    _REGISTRY[spec.id] = spec
    return spec


def get(analysis_id: str) -> AnalysisSpec:
    try:
        return _REGISTRY[analysis_id]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"unknown analysis {analysis_id!r}. Registered: {known}") from exc


def list_analyses(family: str | None = None) -> list[AnalysisSpec]:
    specs: Iterable[AnalysisSpec] = _REGISTRY.values()
    if family is not None:
        specs = [spec for spec in specs if spec.family == family]
    return sorted(specs, key=lambda spec: spec.title.lower())


def clear_for_tests() -> None:
    _REGISTRY.clear()
