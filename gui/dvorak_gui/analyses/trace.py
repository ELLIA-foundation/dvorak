"""Oscilloscope trace viewer plugin (workspace filled in Phase 2)."""

from __future__ import annotations

from typing import Any

from ..kinds import KIND_WAVEFORM
from ..registry import FAMILY_ANALYSIS, AnalysisSpec, get, register
from ..window import AnalysisWindow

TRACE_ID = "trace"


def _create_window(controller: Any) -> AnalysisWindow:
    return AnalysisWindow(get(TRACE_ID), controller)


register(
    AnalysisSpec(
        id=TRACE_ID,
        title="Oscilloscope Trace Analysis",
        description=(
            "Interactively plot captured oscilloscope waveforms. Pan and zoom "
            "time windows without drawing every sample."
        ),
        family=FAMILY_ANALYSIS,
        accepted_kinds=(KIND_WAVEFORM,),
        window_factory=_create_window,
    )
)
