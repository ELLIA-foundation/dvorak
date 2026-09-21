"""Capture-kind identifiers.

Phase 1 will attach scanners and loaders to these ids. Analyses declare which
kinds they accept; the catalogue indexes every kind even when no viewer exists.
"""

KIND_WAVEFORM = "waveform"
KIND_VIDEO = "video"
KIND_TABLE = "table"

ALL_KINDS = (KIND_WAVEFORM, KIND_VIDEO, KIND_TABLE)
