"""Spark-gap event analysis plugin (workspace filled in Phases 3–4)."""

from __future__ import annotations

from typing import Any

from ..kinds import KIND_WAVEFORM
from ..registry import FAMILY_ANALYSIS, AnalysisSpec, get, register
from ..window import AnalysisWindow

SPARK_GAP_ID = "spark_gap"


def _create_window(controller: Any) -> AnalysisWindow:
    return AnalysisWindow(get(SPARK_GAP_ID), controller)


register(
    AnalysisSpec(
        id=SPARK_GAP_ID,
        title="Spark Gap Analysis",
        description=(
            "Detect breakdown events on spark-gap oscilloscope traces, preview "
            "markers, then run the full diagnostic figure pack."
        ),
        family=FAMILY_ANALYSIS,
        accepted_kinds=(KIND_WAVEFORM,),
        window_factory=_create_window,
    )
)
