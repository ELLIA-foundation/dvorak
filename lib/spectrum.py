"""Amptek MCA / spectrum capture records, energy calibration, and NPZ I/O."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from lib.paths import spectrum_calibration_path

MODEL_ID = "x123_sipin"
DEFAULT_OFFSET_KEV = 0.0007
DEFAULT_SLOPE_KEV = 0.01466


@dataclass
class EnergyCalibration:
    offset_kev: float = DEFAULT_OFFSET_KEV
    slope_kev_per_channel: float = DEFAULT_SLOPE_KEV
    channel_origin: int = 0
    source: str = ""

    def energy_kev(self, channel: np.ndarray) -> np.ndarray:
        origin = float(self.channel_origin)
        return self.offset_kev + self.slope_kev_per_channel * (
            np.asarray(channel, dtype=np.float64) - origin
        )

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "offset_kev": self.offset_kev,
            "slope_kev_per_channel": self.slope_kev_per_channel,
            "channel_origin": self.channel_origin,
        }
        if self.source:
            payload["source"] = self.source
        return payload


@dataclass
class SpectrumCapture:
    channel: np.ndarray
    counts: np.ndarray
    energy_kev: np.ndarray
    model_id: str = MODEL_ID
    path: Path | None = None
    captured_at: str | None = None
    run_name: str | None = None
    phase: str | None = None
    live_time_s: float | None = None
    real_time_s: float | None = None
    dead_time_pct: float | None = None
    calibration: EnergyCalibration = field(default_factory=EnergyCalibration)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def points(self) -> int:
        return int(len(self.counts))

    def metadata_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model_id": self.model_id,
            "points": self.points,
            "calibration": self.calibration.as_dict(),
        }
        if self.captured_at:
            payload["captured_at"] = self.captured_at
        if self.run_name:
            payload["run_name"] = self.run_name
        if self.phase:
            payload["phase"] = self.phase
        if self.live_time_s is not None:
            payload["live_time_s"] = self.live_time_s
        if self.real_time_s is not None:
            payload["real_time_s"] = self.real_time_s
        if self.dead_time_pct is not None:
            payload["dead_time_pct"] = self.dead_time_pct
        payload.update(self.extra)
        return payload


def load_calibration(path: Path | None = None) -> EnergyCalibration:
    cal_path = Path(path) if path is not None else spectrum_calibration_path()
    if not cal_path.is_file():
        return EnergyCalibration()
    raw = json.loads(cal_path.read_text(encoding="utf-8"))
    return EnergyCalibration(
        offset_kev=float(raw.get("offset_kev", DEFAULT_OFFSET_KEV)),
        slope_kev_per_channel=float(
            raw.get("slope_kev_per_channel", DEFAULT_SLOPE_KEV)
        ),
        channel_origin=int(raw.get("channel_origin", 0)),
        source=str(raw.get("source", "")),
    )


def infer_phase(name: str) -> str | None:
    lowered = name.lower()
    if "pre" in lowered:
        return "pre"
    if "post" in lowered:
        return "post"
    return None


def load_spectrum_file(
    path: Path,
    calibration: EnergyCalibration | None = None,
) -> SpectrumCapture:
    """Load an Amptek ``.mca`` or an imported ``spectrum_*.npz``."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".mca":
        return load_mca(path, calibration)
    if suffix == ".npz":
        return load_spectrum_npz(path, calibration)
    raise ValueError(f"Unsupported spectrum file: {path}")


def load_mca(
    path: Path,
    calibration: EnergyCalibration | None = None,
) -> SpectrumCapture:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    header, counts, config, status = _parse_mca_text(text)
    cal = calibration if calibration is not None else load_calibration()
    channel = np.arange(len(counts), dtype=np.int32)
    live = _float_or_none(header.get("LIVE_TIME"))
    real = _float_or_none(header.get("REAL_TIME"))
    if real is None:
        real = _float_or_none(status.get("Real Time"))
    if real is None:
        real = _float_or_none(status.get("Accumulation Time"))
    dead = _percent_or_none(status.get("Dead Time"))
    extra: dict[str, Any] = {
        "source_format": "amptek_mca",
        "gain": header.get("GAIN"),
        "description": header.get("DESCRIPTION", ""),
    }
    extra.update(_status_extra(status))
    extra.update(_config_extra(config))
    return SpectrumCapture(
        channel=channel,
        counts=counts,
        energy_kev=cal.energy_kev(channel),
        model_id=MODEL_ID,
        path=path,
        captured_at=header.get("START_TIME") or None,
        run_name=path.stem,
        phase=infer_phase(path.stem),
        live_time_s=live,
        real_time_s=real,
        dead_time_pct=dead,
        calibration=cal,
        extra=extra,
    )


def load_spectrum_npz(
    path: Path,
    calibration: EnergyCalibration | None = None,
) -> SpectrumCapture:
    path = Path(path)
    data = np.load(path)
    channel = np.asarray(data["channel"], dtype=np.int32)
    counts = np.asarray(data["counts"], dtype=np.float64)
    cal = calibration if calibration is not None else load_calibration()
    sidecar = path.with_suffix(".json")
    meta: dict[str, Any] = {}
    if sidecar.is_file():
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    extra = {
        key: value
        for key, value in meta.items()
        if key
        not in {
            "model_id",
            "points",
            "calibration",
            "captured_at",
            "run_name",
            "phase",
            "live_time_s",
            "real_time_s",
            "dead_time_pct",
        }
    }
    return SpectrumCapture(
        channel=channel,
        counts=counts,
        energy_kev=cal.energy_kev(channel),
        model_id=str(meta.get("model_id", MODEL_ID)),
        path=path,
        captured_at=meta.get("captured_at"),
        run_name=meta.get("run_name") or path.stem,
        phase=meta.get("phase") or infer_phase(path.stem),
        live_time_s=_float_or_none(meta.get("live_time_s")),
        real_time_s=_float_or_none(meta.get("real_time_s")),
        dead_time_pct=_float_or_none(meta.get("dead_time_pct")),
        calibration=cal,
        extra=extra,
    )


def save_spectrum(
    capture: SpectrumCapture,
    output_dir: Path,
    stem: str | None = None,
) -> dict[str, Path]:
    """Write channel/counts NPZ plus a JSON sidecar. Energy is not baked in."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    name = stem or _slug(capture.run_name or "spectrum")
    if not name.startswith("spectrum_"):
        name = f"spectrum_{name}"
    npz_path = output_dir / f"{name}.npz"
    json_path = output_dir / f"{name}.json"
    np.savez_compressed(npz_path, channel=capture.channel, counts=capture.counts)
    json_path.write_text(
        json.dumps(capture.metadata_dict(), indent=2),
        encoding="utf-8",
    )
    return {"npz": npz_path, "json": json_path}


def _parse_mca_text(
    text: str,
) -> tuple[dict[str, str], np.ndarray, dict[str, str], dict[str, str]]:
    lines = text.splitlines()
    header: dict[str, str] = {}
    config: dict[str, str] = {}
    status: dict[str, str] = {}
    counts: list[int] = []
    section = "header"
    for raw in lines:
        line = raw.strip()
        if line == "<<PMCA SPECTRUM>>":
            section = "header"
            continue
        if line == "<<DATA>>":
            section = "data"
            continue
        if line == "<<END>>":
            section = "after_data"
            continue
        if line == "<<DP5 CONFIGURATION>>":
            section = "config"
            continue
        if line == "<<DP5 CONFIGURATION END>>":
            section = "after_config"
            continue
        if line == "<<DPP STATUS>>":
            section = "status"
            continue
        if line == "<<DPP STATUS END>>":
            section = "done"
            continue
        if not line:
            continue
        if section == "header" and " - " in line:
            key, value = line.split(" - ", 1)
            header[key.strip()] = value.strip()
        elif section == "data":
            counts.append(int(float(line)))
        elif section == "config" and "=" in line:
            left, _, rest = line.partition("=")
            value = rest.split(";", 1)[0].strip()
            config[left.strip()] = value
        elif section == "status" and ":" in line:
            key, value = line.split(":", 1)
            status[key.strip()] = value.strip()
    if not counts:
        raise ValueError("MCA file has no <<DATA>> histogram")
    return header, np.asarray(counts, dtype=np.float64), config, status


def _status_extra(status: dict[str, str]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    mapping = {
        "Device Type": "device_type",
        "Serial Number": "serial_number",
        "Firmware": "firmware",
        "FPGA": "fpga",
        "Fast Count": "fast_count",
        "Slow Count": "slow_count",
        "GP Count": "gp_count",
        "HV Volt": "hv_volt",
        "TEC Temp": "tec_temp",
        "Board Temp": "board_temp",
    }
    for src, dest in mapping.items():
        if src in status:
            extra[dest] = _maybe_number(status[src])
    return extra


def _config_extra(config: dict[str, str]) -> dict[str, Any]:
    wanted = ("TPEA", "GAIN", "MCAC", "HVSE", "TECS", "AINP", "CLCK")
    extra: dict[str, Any] = {}
    for key in wanted:
        if key in config:
            extra[f"dp5_{key.lower()}"] = _maybe_number(config[key])
    return extra


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent_or_none(value: str | None) -> float | None:
    if not value:
        return None
    return _float_or_none(str(value).rstrip("%").strip())


def _maybe_number(value: str) -> Any:
    text = value.strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    try:
        return float(text)
    except ValueError:
        return value


def _slug(name: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", name.strip()).strip("_")
    return slug or "spectrum"
