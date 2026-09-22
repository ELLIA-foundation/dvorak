"""Camera-clip stub: video kind is catalogued; no viewer yet."""

from __future__ import annotations

from ..kinds import KIND_VIDEO
from ..registry import FAMILY_ANALYSIS, AnalysisSpec, register

register(
    AnalysisSpec(
        id="camera_clip",
        title="Camera clip",
        description=(
            "Open camera captures (MP4 / MOV + JSON sidecar). A dedicated "
            "viewer is not implemented yet."
        ),
        family=FAMILY_ANALYSIS,
        accepted_kinds=(KIND_VIDEO,),
        enabled=False,
        disabled_reason=(
            "No viewer yet. Camera clips already appear in the catalogue as "
            "video captures."
        ),
    )
)
