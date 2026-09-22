"""Frequency-response stub: table kind is catalogued; no viewer yet."""

from __future__ import annotations

from ..kinds import KIND_TABLE
from ..registry import FAMILY_ANALYSIS, AnalysisSpec, register

register(
    AnalysisSpec(
        id="frequency_response",
        title="Frequency response",
        description=(
            "Browse frequency-response tables (CSV / JSON). A dedicated viewer "
            "is not implemented yet."
        ),
        family=FAMILY_ANALYSIS,
        accepted_kinds=(KIND_TABLE,),
        enabled=False,
        disabled_reason=(
            "No viewer yet. Frequency-response CSV/JSON already appear in the "
            "catalogue as table captures."
        ),
    )
)
