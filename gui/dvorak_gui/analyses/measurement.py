"""Disabled measurement-family stub so the launcher already has that section."""

from __future__ import annotations

from ..registry import FAMILY_MEASUREMENT, AnalysisSpec, register

register(
    AnalysisSpec(
        id="measure_run",
        title="Run measurement",
        description=(
            "Connect registered instruments and run a campaign measurement script."
        ),
        family=FAMILY_MEASUREMENT,
        accepted_kinds=(),
        enabled=False,
        disabled_reason=(
            "Coming later. The Measurement GUI will run on lab computers "
            "(Windows) on the master branch."
        ),
    )
)
