"""Index captures under a Measurements-style data root.

Qt-free: the browser widget consumes ``CaptureRecord`` lists. Scanners look at
each campaign's ``Data/`` folder and one level of session folders (never
``plots/``), and read JSON sidecars without loading NPZ arrays.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .kinds import KIND_TABLE, KIND_VIDEO, KIND_WAVEFORM, kind_label

_SKIP_META_KEYS = {"rows", "video_path"}


def default_data_root() -> Path:
    try:
        from lib.paths import measurements_dir

        return measurements_dir()
    except ImportError:
        return Path(__file__).resolve().parent.parent.parent / "Measurements"


@dataclass
class CaptureRecord:
    path: Path
    kind: str
    campaign: str
    stem: str
    session: str | None = None
    captured_at: str | None = None
    run_name: str | None = None
    points: int | None = None
    channel: int | str | None = None
    model_id: str | None = None
    sidecar: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def kind_label(self) -> str:
        return kind_label(self.kind)

    def search_haystack(self) -> str:
        parts: list[str] = [
            self.stem,
            self.campaign,
            self.session or "",
            self.kind,
            self.kind_label,
            str(self.path),
            self.run_name or "",
            self.captured_at or "",
            self.model_id or "",
            "" if self.channel is None else str(self.channel),
        ]
        for key, value in self.metadata.items():
            if key in _SKIP_META_KEYS or isinstance(value, (list, dict)):
                continue
            if value is None:
                continue
            parts.append(str(value))
        return " ".join(parts).lower()

    def matches(self, query: str) -> bool:
        needle = query.strip().lower()
        if not needle:
            return True
        haystack = self.search_haystack()
        return all(token in haystack for token in needle.split())


def scan(data_root: Path) -> list[CaptureRecord]:
    """Return captures under ``data_root``, newest first within each campaign."""
    root = Path(data_root)
    if not root.is_dir():
        return []

    records: list[CaptureRecord] = []
    campaign_dirs = [
        path
        for path in sorted(root.iterdir())
        if path.is_dir() and not path.name.startswith(".") and (path / "Data").is_dir()
    ]
    if campaign_dirs:
        for campaign_dir in campaign_dirs:
            records.extend(_scan_data_dir(campaign_dir.name, campaign_dir / "Data"))
        return records

    if (root / "Data").is_dir():
        return _scan_data_dir(root.name, root / "Data")

    if root.name == "Data" or _looks_like_data_dir(root):
        campaign = root.parent.name if root.name == "Data" else root.name
        return _scan_data_dir(campaign, root)
    return []


def _looks_like_data_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    if any(path.glob("waveform_*.npz")) or any(path.glob("video_*.json")) or any(
        path.glob("*.csv")
    ):
        return True
    for child in path.iterdir():
        if child.is_dir() and not child.name.startswith(".") and child.name.lower() != "plots":
            if any(child.glob("waveform_*.npz")):
                return True
    return False


def _scan_data_dir(campaign: str, data_dir: Path) -> list[CaptureRecord]:
    records: list[CaptureRecord] = []
    table_stems: set[str] = set()
    video_stems: set[str] = set()

    folders = [data_dir]
    for child in sorted(data_dir.iterdir()):
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and child.name.lower() != "plots"
        ):
            folders.append(child)

    for folder in folders:
        session = None if folder == data_dir else folder.name
        for npz_path in sorted(folder.glob("waveform_*.npz")):
            records.append(_from_waveform(campaign, npz_path, session=session))

        for json_path in sorted(folder.glob("video_*.json")):
            records.append(_from_video_json(campaign, json_path, session=session))
            video_stems.add(json_path.stem)

        for pattern in ("video_*.mp4", "video_*.mov"):
            for media_path in sorted(folder.glob(pattern)):
                if media_path.stem in video_stems:
                    continue
                records.append(_from_video_media(campaign, media_path, session=session))
                video_stems.add(media_path.stem)

        for csv_path in sorted(folder.glob("*.csv")):
            records.append(_from_table(campaign, csv_path, session=session))
            table_stems.add(csv_path.stem)

        for json_path in sorted(folder.glob("freq_resp_*.json")):
            if json_path.stem in table_stems:
                continue
            records.append(_from_table(campaign, json_path, session=session))
            table_stems.add(json_path.stem)

    records.sort(key=_sort_key)
    return records


def _sort_key(record: CaptureRecord) -> tuple[str, str, str, str]:
    stamp = record.captured_at or ""
    return (record.campaign.lower(), (record.session or "").lower(), stamp, record.stem)


def _from_waveform(campaign: str, npz_path: Path, session: str | None = None) -> CaptureRecord:
    sidecar = _sidecar_json(npz_path)
    meta = _read_json(sidecar)
    return CaptureRecord(
        path=npz_path,
        kind=KIND_WAVEFORM,
        campaign=campaign,
        stem=npz_path.stem,
        session=session or _as_str(meta.get("session")),
        captured_at=_first(meta.get("captured_at"), meta.get("measured_at")),
        run_name=_as_str(meta.get("run_name")),
        points=_as_int(meta.get("points")),
        channel=_channel(meta),
        model_id=_model_id(meta),
        sidecar=sidecar,
        metadata=meta,
    )


def _from_video_json(campaign: str, json_path: Path, session: str | None = None) -> CaptureRecord:
    meta = _read_json(json_path)
    media = _video_media_path(json_path, meta)
    return CaptureRecord(
        path=media if media is not None else json_path,
        kind=KIND_VIDEO,
        campaign=campaign,
        stem=json_path.stem,
        session=session,
        captured_at=_first(meta.get("captured_at"), meta.get("measured_at")),
        run_name=_as_str(meta.get("run_name")),
        points=_as_int(meta.get("frame_count")),
        channel=_channel(meta),
        model_id=_model_id(meta),
        sidecar=json_path,
        metadata=meta,
    )


def _from_video_media(campaign: str, media_path: Path, session: str | None = None) -> CaptureRecord:
    sidecar = _sidecar_json(media_path)
    meta = _read_json(sidecar)
    return CaptureRecord(
        path=media_path,
        kind=KIND_VIDEO,
        campaign=campaign,
        stem=media_path.stem,
        session=session,
        captured_at=_first(meta.get("captured_at"), meta.get("measured_at")),
        run_name=_as_str(meta.get("run_name")),
        points=_as_int(meta.get("frame_count")),
        channel=_channel(meta),
        model_id=_model_id(meta),
        sidecar=sidecar,
        metadata=meta,
    )


def _from_table(campaign: str, path: Path, session: str | None = None) -> CaptureRecord:
    sidecar = path if path.suffix.lower() == ".json" else _sidecar_json(path)
    meta = _read_json(sidecar)
    points = _as_int(meta.get("points"))
    if points is None:
        rows = meta.get("rows")
        if isinstance(rows, list):
            points = len(rows)
    return CaptureRecord(
        path=path,
        kind=KIND_TABLE,
        campaign=campaign,
        stem=path.stem,
        session=session,
        captured_at=_first(meta.get("captured_at"), meta.get("measured_at")),
        run_name=_as_str(meta.get("run_name")),
        points=points,
        channel=_channel(meta),
        model_id=_model_id(meta),
        sidecar=sidecar,
        metadata=meta,
    )


def _video_media_path(json_path: Path, meta: dict[str, Any]) -> Path | None:
    for suffix in (".mp4", ".mov"):
        candidate = json_path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    listed = meta.get("video_path")
    if listed:
        listed_path = Path(str(listed))
        if listed_path.is_file():
            return listed_path
    return None


def _sidecar_json(path: Path) -> Path | None:
    json_path = path.with_suffix(".json")
    return json_path if json_path.is_file() else None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _first(*values: Any) -> str | None:
    for value in values:
        text = _as_str(value)
        if text:
            return text
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _channel(meta: dict[str, Any]) -> int | str | None:
    for key in ("channel", "scope_channel", "gen_channel"):
        value = meta.get(key)
        if value is None:
            continue
        as_int = _as_int(value)
        if as_int is not None:
            return as_int
        text = _as_str(value)
        if text:
            return text
    return None


def _model_id(meta: dict[str, Any]) -> str | None:
    return _first(
        meta.get("model_id"),
        meta.get("oscilloscope_model"),
        meta.get("generator_model"),
    )


def count_by_kind(records: Iterable[CaptureRecord]) -> dict[str, int]:
    counts = {KIND_WAVEFORM: 0, KIND_VIDEO: 0, KIND_TABLE: 0}
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
    return counts
