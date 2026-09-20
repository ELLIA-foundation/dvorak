"""Model-agnostic video capture records and sidecar I/O."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class VideoCapture:
    """Interchange format returned by camera drivers."""

    video_path: Path
    idn: str
    model_id: str
    duration_s: float
    frame_count: int
    width: int
    height: int
    frame_rate_hz: float
    captured_at: str
    extra: dict[str, Any] = field(default_factory=dict)

    def metadata_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "idn": self.idn,
            "model_id": self.model_id,
            "duration_s": self.duration_s,
            "frame_count": self.frame_count,
            "width": self.width,
            "height": self.height,
            "frame_rate_hz": self.frame_rate_hz,
            "captured_at": self.captured_at,
            "video_path": str(self.video_path),
        }
        payload.update(self.extra)
        return payload


def save_video(
    capture: VideoCapture,
    output_dir: Path | None = None,
    write_preview: bool = True,
) -> dict[str, Path]:
    """Write the JSON sidecar (and optional first-frame PNG) next to the MP4."""
    video_path = Path(capture.video_path)
    if output_dir is not None and video_path.parent.resolve() != output_dir.resolve():
        output_dir.mkdir(parents=True, exist_ok=True)
        dest = output_dir / video_path.name
        shutil.move(str(video_path), dest)
        capture.video_path = dest
        video_path = dest

    json_path = video_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(capture.metadata_dict(), indent=2),
        encoding="utf-8",
    )
    paths = {"mp4": video_path, "json": json_path}

    if write_preview:
        preview_dir = video_path.parent / "plots"
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / f"{video_path.stem}.png"
        extracted = _extract_preview_frame(video_path, preview_path)
        if extracted is not None:
            paths["preview"] = extracted

    return paths


def _extract_preview_frame(video_path: Path, preview_path: Path) -> Path | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            str(preview_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not preview_path.is_file():
        return None
    return preview_path


def latest_capture(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("video_*.mp4"))
    return files[-1] if files else None


def video_stem(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"video_{stamp}"
