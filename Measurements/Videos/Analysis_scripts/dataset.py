"""Map a video campaign clip to its chronograph cache.

Clips live in ``Measurements/Videos/<campaign>/``. Extraction writes
``<campaign>/data/<stem>.csv`` and a local ``<stem>.json`` sidecar.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

for _parent in Path(__file__).resolve().parents:
    if (_parent / "lib" / "paths.py").is_file() and (_parent / "Measurements").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
else:
    raise SystemExit("Could not find repository root (expected lib/paths.py and Measurements/).")

from lib.paths import VIDEO_EXTENSIONS, repo_root, videos_dir

# Official RGB full-frame extractor. Bump when the decode or algorithm changes.
EXTRACTOR_ID = "brightness-v1-rgb"

SEQ_RE = re.compile(r"^V(\d+)_", re.IGNORECASE)
CURRENT_TAIL_RE = re.compile(r"(\d+(?:\.\d+)?)A$", re.IGNORECASE)
CURRENT_ANY_RE = re.compile(r"(\d+(?:\.\d+)?)A", re.IGNORECASE)

_SKIP_DIRS = {"Analysis_scripts", "data", "plots"}


def parse_video_name(path: Path) -> tuple[int, float]:
    """Return (sequence_number, current_A) parsed from the file name.

    Sequence is 0 when the name has no ``Vn_`` prefix. Current is NaN when
    the name has no ampere token. These are labels, not on/off times.
    """
    stem = path.stem
    seq_match = SEQ_RE.match(stem)
    seq = int(seq_match.group(1)) if seq_match else 0
    cur_match = CURRENT_TAIL_RE.search(stem) or CURRENT_ANY_RE.search(stem)
    current = float(cur_match.group(1)) if cur_match else float("nan")
    return seq, current


def clips_in(folder: Path) -> list[Path]:
    """MP4 and MOV files sitting directly in ``folder``."""
    if not folder.is_dir():
        return []
    clips = [
        path
        for path in folder.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    return sorted(clips, key=lambda path: path.name.lower())


def find_videos(folder: Path) -> list[Path]:
    """Clips in ``folder``, or one level down when ``folder`` is the Videos root."""
    folder = folder.resolve()
    direct = clips_in(folder)
    if direct:
        return direct
    if not folder.is_dir():
        return []
    nested: list[Path] = []
    for sub in sorted(path for path in folder.iterdir() if path.is_dir()):
        if sub.name.startswith(".") or sub.name in _SKIP_DIRS:
            continue
        nested.extend(clips_in(sub))
    return nested


def rel_to_root(path: Path) -> str:
    """POSIX path relative to the repository root when possible."""
    path = path.resolve()
    try:
        return path.relative_to(repo_root()).as_posix()
    except ValueError:
        return path.as_posix()


def video_fingerprint(video: Path) -> tuple[int, int]:
    """Return (size_bytes, mtime_ns) for cache invalidation."""
    stat = video.resolve().stat()
    return stat.st_size, stat.st_mtime_ns


def video_to_cache(video: Path) -> tuple[Path, Path]:
    """Map a clip to ``(csv_path, meta_path)`` inside its campaign ``data/`` folder.

    ``Measurements/Videos/<campaign>/<stem>.mp4`` becomes
    ``Measurements/Videos/<campaign>/data/<stem>.csv``.
    """
    video = video.resolve()
    csv_path = video.parent / "data" / f"{video.stem}.csv"
    return csv_path, csv_path.with_suffix(".json")


def video_to_plot(video: Path) -> Path:
    """Plot stem under the campaign ``plots/`` folder, without an extension."""
    video = video.resolve()
    return video.parent / "plots" / video.stem


def load_meta(meta_path: Path) -> dict[str, Any]:
    return json.loads(meta_path.read_text(encoding="utf-8"))


def write_meta(meta_path: Path, meta: dict[str, Any]) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def cache_status(video: Path) -> str:
    """``fresh``, ``stale``, or ``missing`` for this clip's chronograph cache."""
    csv_path, meta_path = video_to_cache(video)
    if cache_is_fresh(video, csv_path, meta_path):
        return "fresh"
    if csv_path.is_file() or meta_path.is_file():
        return "stale"
    return "missing"


def rising_edge_s(video: Path) -> float | None:
    """Detected or hand-corrected rising edge, if the sidecar has one."""
    _csv_path, meta_path = video_to_cache(video)
    if not meta_path.is_file():
        return None
    try:
        meta = load_meta(meta_path)
        value = meta.get("t1_s")
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if value is None:
        return None
    return float(value)


def set_rising_edge(video: Path, t1_s: float) -> None:
    """Store a corrected rising edge. The file fingerprint is left unchanged."""
    _csv_path, meta_path = video_to_cache(video)
    if not meta_path.is_file():
        raise FileNotFoundError(f"No chronograph sidecar for {video.name}")
    meta = load_meta(meta_path)
    meta["t1_s"] = float(t1_s)
    write_meta(meta_path, meta)


def cache_is_fresh(video: Path, csv_path: Path, meta_path: Path) -> bool:
    """True when the CSV and sidecar match this clip and the current extractor."""
    if not csv_path.is_file() or not meta_path.is_file():
        return False
    try:
        meta = load_meta(meta_path)
    except (OSError, json.JSONDecodeError):
        return False
    if meta.get("extractor") != EXTRACTOR_ID:
        return False
    size, mtime_ns = video_fingerprint(video)
    try:
        return int(meta.get("size", -1)) == size and int(meta.get("mtime_ns", -1)) == mtime_ns
    except (TypeError, ValueError):
        return False


def campaign_name_for(video: Path) -> str:
    """Campaign folder name when the clip sits under Measurements/Videos."""
    video = video.resolve()
    try:
        relative = video.relative_to(videos_dir().resolve())
    except ValueError:
        return video.parent.name
    return relative.parts[0] if relative.parts else video.parent.name
