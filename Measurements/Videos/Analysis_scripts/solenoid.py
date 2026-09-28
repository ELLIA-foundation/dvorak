"""Solenoid timing for one video campaign.

``<campaign>/cycle.json`` exists only after that analysis is turned on. Times
are seconds from the start of the clip. The chronograph CSV supplies window
means. Frame samples decode only the short current-off and current-on
stretches.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

CYCLE_FILENAME = "cycle.json"
FIELDS = ("tube_on_s", "current_on_s", "current_off_s", "tube_off_s")

EDGE_MARGIN_S = 0.3
SAMPLE_DUR_S = 0.7
DARK_DUR_S = 1.0
DARK_MIN_TUBE_ON_S = 0.05
PROJ_SMOOTH_PX = 5
PRESET_STEP_S = 3.0


@dataclass(frozen=True)
class PhaseWindow:
    label: str
    t0: float
    t1: float
    role: str


def cycle_path(campaign_dir: Path) -> Path:
    return campaign_dir / CYCLE_FILENAME


def new_cycle(tube_on_s: float | None = None) -> dict[str, Any]:
    cycle: dict[str, Any] = {key: None for key in FIELDS}
    cycle["clips"] = {}
    if tube_on_s is not None:
        cycle["tube_on_s"] = float(tube_on_s)
    return cycle


def load_cycle(campaign_dir: Path) -> dict[str, Any] | None:
    path = cycle_path(campaign_dir)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not an object")
    return _normalize(data)


def save_cycle(campaign_dir: Path, cycle: Mapping[str, Any]) -> None:
    path = cycle_path(campaign_dir)
    payload = _normalize(dict(cycle))
    text = json.dumps(payload, indent=2) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def delete_cycle(campaign_dir: Path) -> None:
    cycle_path(campaign_dir).unlink(missing_ok=True)


def write_scope(
    campaign_dir: Path,
    times: Mapping[str, Any],
    *,
    clip_stem: str | None,
) -> dict[str, Any]:
    """Store campaign defaults, or one clip's overrides when ``clip_stem`` is set.

    Empty per-clip fields are omitted so they fall back to the campaign times.
    """
    cycle = load_cycle(campaign_dir)
    if cycle is None:
        raise FileNotFoundError(f"No {CYCLE_FILENAME} in {campaign_dir}")
    cleaned = {key: _optional_float(times.get(key)) for key in FIELDS}
    if clip_stem is None:
        for key in FIELDS:
            cycle[key] = cleaned[key]
    else:
        stored = {key: value for key, value in cleaned.items() if value is not None}
        clips = cycle.setdefault("clips", {})
        if stored:
            clips[clip_stem] = stored
        else:
            clips.pop(clip_stem, None)
    save_cycle(campaign_dir, cycle)
    return cycle


def resolved_times(cycle: Mapping[str, Any], stem: str | None) -> dict[str, float | None]:
    """Campaign times, with any stored per-clip values taking over field by field."""
    clips = cycle.get("clips") or {}
    override = clips.get(stem, {}) if stem else {}
    if not isinstance(override, Mapping):
        override = {}
    resolved: dict[str, float | None] = {}
    for key in FIELDS:
        if key in override and override[key] is not None:
            resolved[key] = float(override[key])
        elif cycle.get(key) is not None:
            resolved[key] = float(cycle[key])
        else:
            resolved[key] = None
    return resolved


def apply_three_second(times: Mapping[str, Any]) -> dict[str, float | None]:
    """From tube on: current off 0–3 s, current on 3–6 s, current off 6–9 s."""
    tube = _optional_float(times.get("tube_on_s"))
    if tube is None:
        raise ValueError("Set tube on before applying the 3 s cycle.")
    out = {key: _optional_float(times.get(key)) for key in FIELDS}
    out["tube_on_s"] = tube
    out["current_on_s"] = tube + PRESET_STEP_S
    out["current_off_s"] = tube + 2.0 * PRESET_STEP_S
    out["tube_off_s"] = tube + 3.0 * PRESET_STEP_S
    return out


def phase_windows(times: Mapping[str, Any]) -> list[PhaseWindow]:
    """Current phases from the four timestamps. A lone tube span is used when current times are absent."""
    tube_on = _optional_float(times.get("tube_on_s"))
    current_on = _optional_float(times.get("current_on_s"))
    current_off = _optional_float(times.get("current_off_s"))
    tube_off = _optional_float(times.get("tube_off_s"))
    windows: list[PhaseWindow] = []

    def add(label: str, t0: float | None, t1: float | None, role: str) -> None:
        if t0 is None or t1 is None or not t1 > t0:
            return
        windows.append(PhaseWindow(label, t0, t1, role))

    add("current off", tube_on, current_on, "off")
    add("current on", current_on, current_off, "on")
    add("current off", current_off, tube_off, "off2")
    if not windows:
        add("tube on", tube_on, tube_off, "tube")
    return windows


def plot_marks(
    times: Mapping[str, Any],
) -> tuple[list[tuple[str, float, float, str]], list[tuple[str, float]]]:
    """Filled phase spans, plus tube on/off lines when current phases are drawn."""
    windows = phase_windows(times)
    spans = [(window.label, window.t0, window.t1, window.role) for window in windows]
    if not any(window.role in {"off", "on", "off2"} for window in windows):
        return spans, []
    markers: list[tuple[str, float]] = []
    tube_on = _optional_float(times.get("tube_on_s"))
    tube_off = _optional_float(times.get("tube_off_s"))
    if tube_on is not None:
        markers.append(("tube on", tube_on))
    if tube_off is not None:
        markers.append(("tube off", tube_off))
    return spans, markers


def window_mean(
    time_s: np.ndarray,
    intensity: np.ndarray,
    t0: float,
    t1: float,
    margin: float = EDGE_MARGIN_S,
) -> float | None:
    """Mean of samples in ``[t0 + margin, t1 - margin)``. The full span is used when it is shorter."""
    time_s = np.asarray(time_s, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    start = t0 + margin
    stop = t1 - margin
    if stop <= start:
        start, stop = t0, t1
    if stop <= start:
        return None
    mask = (time_s >= start) & (time_s < stop)
    if not np.any(mask):
        return None
    return float(np.mean(intensity[mask]))


def brightness_delta(
    time_s: np.ndarray,
    intensity: np.ndarray,
    times: Mapping[str, Any],
) -> float | None:
    """Mean current-on minus mean of the first current-off window."""
    by_role = {window.role: window for window in phase_windows(times)}
    off = by_role.get("off")
    on = by_role.get("on")
    if off is None or on is None:
        return None
    off_mean = window_mean(time_s, intensity, off.t0, off.t1)
    on_mean = window_mean(time_s, intensity, on.t0, on.t1)
    if off_mean is None or on_mean is None:
        return None
    return on_mean - off_mean


def intensity_summary(
    time_s: np.ndarray | None,
    intensity: np.ndarray | None,
    times: Mapping[str, Any],
) -> str:
    windows = phase_windows(times)
    if not windows:
        return ""
    lines: list[str] = []
    for window in windows:
        span = f"{window.t0:.4g}–{window.t1:.4g} s"
        if time_s is None or intensity is None:
            lines.append(f"{window.label} {span}")
            continue
        mean = window_mean(time_s, intensity, window.t0, window.t1)
        if mean is None:
            lines.append(f"{window.label} {span}: no samples")
        else:
            lines.append(f"{window.label} {span}: {mean:.4g}")
    if time_s is not None and intensity is not None and any(window.role == "off" for window in windows):
        if any(window.role == "on" for window in windows):
            delta = brightness_delta(time_s, intensity, times)
            if delta is None:
                lines.append("on − first off: no samples")
            else:
                lines.append(f"on − first off {delta:+.4g}")
    return "\n".join(lines)


def sample_bounds(t0: float, t1: float) -> tuple[float, float]:
    """Start and duration of the averaged frame, centered in the phase with an edge margin."""
    lo = t0 + EDGE_MARGIN_S
    hi = t1 - EDGE_MARGIN_S
    if hi <= lo:
        lo, hi = t0, t1
    span = max(hi - lo, 1e-3)
    duration = min(SAMPLE_DUR_S, span)
    start = 0.5 * (lo + hi) - 0.5 * duration
    return start, duration


def sample_windows(video: Path, times: Mapping[str, Any]) -> dict[str, Any]:
    """Median gray frame and X/Y projections for the first current-off window and the current-on window."""
    by_role = {window.role: window for window in phase_windows(times)}
    off = by_role.get("off")
    on = by_role.get("on")
    if off is None or on is None:
        raise ValueError("Set tube on, current on, and current off before reading frames.")
    width, height, fps = _probe(video)
    dark = _read_dark(video, _optional_float(times.get("tube_on_s")), width, height, fps)
    sampled = {}
    for key, window in (("off", off), ("on", on)):
        start, duration = sample_bounds(window.t0, window.t1)
        image = _median_gray(video, start, duration, width, height, fps)
        if dark is not None:
            image = image - dark
        projection_x, projection_y = _project_xy(image)
        sampled[key] = {
            "image": image,
            "x": projection_x,
            "y": projection_y,
            "t0": start,
            "t1": start + duration,
        }
    return {"dark": dark is not None, "off": sampled["off"], "on": sampled["on"]}


def _normalize(data: Mapping[str, Any]) -> dict[str, Any]:
    cycle: dict[str, Any] = {key: _optional_float(data.get(key)) for key in FIELDS}
    clips_in = data.get("clips") or {}
    if not isinstance(clips_in, dict):
        raise ValueError(f"{CYCLE_FILENAME} clips must be an object")
    clips: dict[str, dict[str, float]] = {}
    for stem, raw in clips_in.items():
        if not isinstance(raw, dict):
            raise ValueError(f"{CYCLE_FILENAME} clip {stem!r} must be an object")
        stored: dict[str, float] = {}
        for key in FIELDS:
            if key in raw and raw[key] is not None:
                value = _optional_float(raw[key])
                if value is not None:
                    stored[key] = value
        if stored:
            clips[str(stem)] = stored
    cycle["clips"] = clips
    return cycle


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Expected a time in seconds, got {value!r}")
    return float(value)


def _probe(video: Path) -> tuple[int, int, float]:
    import extract_brightness

    width, height, fps, _expected = extract_brightness.probe_video(video)
    if width <= 0 or height <= 0 or fps <= 0:
        raise RuntimeError(f"Could not read video size and frame rate from {video.name}")
    return width, height, fps


def _read_dark(
    video: Path,
    tube_on_s: float | None,
    width: int,
    height: int,
    fps: float,
) -> np.ndarray | None:
    if tube_on_s is None or tube_on_s <= DARK_MIN_TUBE_ON_S:
        return None
    start = max(0.0, tube_on_s - DARK_DUR_S)
    duration = min(DARK_DUR_S, tube_on_s - start)
    if duration < 1.0 / max(fps, 1.0):
        return None
    return _median_gray(video, start, duration, width, height, fps)


def _median_gray(
    video: Path,
    start_s: float,
    duration_s: float,
    width: int,
    height: int,
    fps: float,
) -> np.ndarray:
    n_want = max(1, int(round(duration_s * fps)))
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, start_s):.3f}",
        "-i",
        str(video),
        "-frames:v",
        str(n_want),
        "-vf",
        "format=gray",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=120)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg must be on PATH") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "ffmpeg failed")
    frame_bytes = width * height
    n_frames = len(result.stdout) // frame_bytes
    if n_frames == 0:
        raise RuntimeError(f"No frames in {video.name} at {start_s:.3f} s")
    stack = np.frombuffer(result.stdout[: n_frames * frame_bytes], dtype=np.uint8).reshape(
        n_frames, height, width
    )
    median = np.partition(stack, n_frames // 2, axis=0)[n_frames // 2]
    return median.astype(np.float64)


def _project_xy(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    height, width = image.shape
    projection_x = image.sum(axis=0) / height
    projection_y = image.sum(axis=1) / width
    return _box_smooth(projection_x, PROJ_SMOOTH_PX), _box_smooth(projection_y, PROJ_SMOOTH_PX)


def _box_smooth(values: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or values.size < width:
        return values
    half = width // 2
    smoothed = np.empty_like(values, dtype=float)
    for index in range(values.size):
        start = index - half
        stop = start + width - 1
        start = max(0, start)
        stop = min(values.size - 1, stop)
        smoothed[index] = float(np.mean(values[start : stop + 1]))
    return smoothed
