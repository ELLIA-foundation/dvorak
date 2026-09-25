"""Capture-kind identifiers.

Analyses declare which kinds they accept. The catalogue indexes every kind
even when no viewer exists yet.
"""

from __future__ import annotations

KIND_WAVEFORM = "waveform"
KIND_VIDEO = "video"
KIND_TABLE = "table"

ALL_KINDS = (KIND_WAVEFORM, KIND_VIDEO, KIND_TABLE)

KIND_LABELS = {
    KIND_WAVEFORM: "waveform",
    KIND_VIDEO: "video",
    KIND_TABLE: "table",
}


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)
