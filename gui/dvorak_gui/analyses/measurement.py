"""Disabled measurement-family stub so the launcher already has that section."""

from __future__ import annotations

from ..registry import FAMILY_MEASUREMENT, AnalysisSpec, register

register(
    AnalysisSpec(
        id="measure_run",
        title="Run measurement",
        description=(
            "Connect registered instruments and run a campaign or tools CLI. "
            "Writes the same Measurements/ tree this app reads."
        ),
        family=FAMILY_MEASUREMENT,
        accepted_kinds=(),
        enabled=False,
        disabled_reason=(
            "Coming later on lab computers (Windows, master branch): "
            "instrument connect plus the existing campaign / tools CLIs, "
            "writing Measurements/."
        ),
    )
)
