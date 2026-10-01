"""Sweep a sine on the generator and record V_scope / V_nominal vs frequency."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

for _parent in Path(__file__).resolve().parents:
    if (_parent / "lib" / "paths.py").is_file() and (_parent / "Measurements").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
else:
    raise SystemExit("Could not find repository root (expected lib/paths.py and Measurements/).")

from instruments import DEFAULT_OSCILLOSCOPE, open_generator, open_oscilloscope
from instruments.registry import list_oscilloscopes
from lib.paths import CAMPAIGN_FREQUENCY_RESPONSES, campaign_data, campaign_plots
from sine_metrics import (
    coarser_by,
    decide_vertical,
    nearest_scale,
    needs_recenter,
    next_finer,
    sample_span,
    seed_volts_per_div,
    sine_thd,
    status_if_reversed,
    vertical_ladder,
)

# ---------------------------------------------------------------------------
# Sweep configuration — edit these defaults, or override on the command line.
# ---------------------------------------------------------------------------

# Generator output channel (1 or 2 on the DG4000 series).
DEFAULT_GEN_CHANNEL = 1

# Oscilloscope analog input that reads the DUT/output signal (1–4 on MSO1104).
DEFAULT_SCOPE_CHANNEL = 2

# Label for this run. Output files become freq_resp_<name>_<timestamp>.* .
# None = timestamp only (freq_resp_<timestamp>.*).
DEFAULT_RUN_NAME = "high_freq"

# Log-spaced sweep range. Default f_max matches MSO1104Z analog bandwidth.
DEFAULT_F_MIN_HZ = 100e6
DEFAULT_F_MAX_HZ = 200e6
DEFAULT_POINTS = 21
FREQUENCY_SPACINGS = ("log", "linear")
DEFAULT_FREQUENCY_SPACING = "log"
# A linear grid is built from the step, so a very small step can request a huge run.
MAX_SWEEP_POINTS = 10_001

# Sine amplitude (Vpp). None = use max_sine_vpp(f) at each frequency; a float
# caps the output (still clamped by the generator limit at that frequency).
DEFAULT_AMPLITUDE_VPP = 1

# Generator front-panel load assumption: 50 for terminated cable, math.inf for High-Z.
DEFAULT_LOAD_OHM = math.inf

# Scope analog bandwidth (Hz). Points above this are flagged scope_limited in output.
DEFAULT_SCOPE_BW_HZ = 100e6

# Wait after each frequency change: fixed delay plus a few signal periods.
# The MSO1104Z needs extra time after a timebase/vertical change before VPP is valid.
SETTLE_BASE_S = 0.15
SETTLE_PERIODS = 5.0
# Matches prepare_sine: one on-screen window is SINE_CYCLES_ON_SCREEN periods.
ACQ_CYCLES_PER_AVERAGE = 8
AVERAGE_CHOICES = (1, 2, 4, 8, 16, 32, 64, 128, 256)
DEFAULT_AVERAGES = 1
STOP_POLL_S = 0.1


class SweepCancelled(Exception):
    """Leave the frequency loop after a GUI stop or Ctrl+C."""
CSV_COLUMNS = (
    "frequency_hz",
    "v_requested_vpp",
    "v_nominal_vpp",
    "v_scope_vpp",
    "f_scope_hz",
    "ratio",
    "ratio_db",
    "scope_limited",
    "v_div",
    "vertical_status",
    "thd",
)
DUAL_COLUMNS = (
    "v_in_vpp",
    "v_in_div",
    "v_in_status",
    "gain",
    "gain_db",
    "phase_deg",
)
THD_DEFINITION = (
    "THD = sqrt(V2^2 + ... + VH^2) / V1 from a least-squares fit of the "
    "on-screen trace at the commanded frequency and its integer harmonics. "
    "H is at most 10 and stops below 40% of the screen sample rate. "
    "Stored as a fraction. NaN when vertical_status is clipped or below_floor."
)
PHASE_DEFINITION = (
    "Phase of the output channel relative to the input channel, in degrees, "
    "from the scope RPHase measurement. NaN unless both channels finish ok."
)


def linear_point_count(f_min: float, f_max: float, step_hz: float) -> int:
    """How many frequencies a linear grid from ``f_min`` to ``f_max`` will use."""
    _require_frequency_bounds(f_min, f_max)
    if not math.isfinite(step_hz) or step_hz <= 0:
        raise ValueError("frequency step must be positive")
    n_steps = int(math.floor((f_max - f_min) / step_hz + 1e-9))
    count = n_steps + 1
    if f_max - (f_min + n_steps * step_hz) > step_hz * 1e-6:
        count += 1
    return count


def linear_frequencies(f_min: float, f_max: float, step_hz: float) -> np.ndarray:
    """Start at ``f_min``, step by ``step_hz``, and include ``f_max``."""
    count = linear_point_count(f_min, f_max, step_hz)
    if count > MAX_SWEEP_POINTS:
        raise ValueError(
            f"frequency step {step_hz:g} Hz from {f_min:g} Hz to {f_max:g} Hz "
            f"is {count} points; increase the step (limit {MAX_SWEEP_POINTS})"
        )
    n_steps = int(math.floor((f_max - f_min) / step_hz + 1e-9))
    frequencies = f_min + np.arange(n_steps + 1, dtype=float) * step_hz
    if count > len(frequencies):
        frequencies = np.append(frequencies, f_max)
    else:
        frequencies[-1] = f_max
    frequencies[0] = f_min
    return frequencies


def _require_frequency_bounds(f_min: float, f_max: float) -> None:
    if f_min <= 0 or f_max <= 0:
        raise ValueError("Frequencies must be positive")
    if f_max < f_min:
        raise ValueError("--f-max must be >= --f-min")


def _frequencies(
    f_min: float,
    f_max: float,
    points: int,
    spacing: str,
    step_hz: float | None = None,
) -> np.ndarray:
    _require_frequency_bounds(f_min, f_max)
    if spacing not in FREQUENCY_SPACINGS:
        allowed = ", ".join(FREQUENCY_SPACINGS)
        raise ValueError(f"spacing must be one of: {allowed}")
    if spacing == "linear":
        if step_hz is None:
            raise ValueError("linear spacing requires a positive frequency step")
        return linear_frequencies(f_min, f_max, step_hz)
    if points < 2:
        raise ValueError("--points must be at least 2")
    if points > MAX_SWEEP_POINTS:
        raise ValueError(f"--points must be at most {MAX_SWEEP_POINTS}")
    frequencies = np.logspace(math.log10(f_min), math.log10(f_max), points)
    # np.logspace can overshoot f_max slightly (e.g. 200e6 -> 200000000.00000003).
    frequencies[0] = f_min
    frequencies[-1] = f_max
    return frequencies


def _parse_load(value: str) -> float:
    text = value.strip().lower()
    if text in {"inf", "infinity", "highz", "high-z", "hiz"}:
        return math.inf
    ohms = float(text)
    if ohms <= 0:
        raise argparse.ArgumentTypeError("load must be positive or inf")
    return ohms


def _ratio_db(ratio: float) -> float:
    if not math.isfinite(ratio) or ratio <= 0:
        return float("nan")
    return 20.0 * math.log10(ratio)


def _parse_averages(value: str) -> int:
    averages = int(value)
    if averages not in AVERAGE_CHOICES:
        allowed = ", ".join(str(item) for item in AVERAGE_CHOICES)
        raise argparse.ArgumentTypeError(f"averages must be one of: {allowed}")
    return averages


def _stopped(should_stop: object) -> bool:
    if should_stop is None:
        return False
    if callable(should_stop):
        return bool(should_stop())
    return bool(should_stop)


def _raise_if_stopped(should_stop: object) -> None:
    if _stopped(should_stop):
        raise SweepCancelled()


def _interruptible_sleep(duration_s: float, should_stop: object = None) -> None:
    deadline = time.monotonic() + max(duration_s, 0.0)
    while True:
        _raise_if_stopped(should_stop)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(STOP_POLL_S, remaining))


def _gen_settle(frequency_hz: float, should_stop: object = None) -> None:
    _interruptible_sleep(SETTLE_BASE_S + SETTLE_PERIODS / frequency_hz, should_stop)


def _acquire_wait_s(frequency_hz: float, averages: int) -> float:
    """Rest after timebase change: base delay plus one screen per average."""
    if frequency_hz <= 0:
        raise ValueError("frequency_hz must be positive")
    if averages < 1:
        raise ValueError("averages must be at least 1")
    return SETTLE_BASE_S + averages * (ACQ_CYCLES_PER_AVERAGE / frequency_hz)


def _acquire_settle(frequency_hz: float, averages: int, should_stop: object = None) -> None:
    _interruptible_sleep(_acquire_wait_s(frequency_hz, averages), should_stop)


def _run_stem(run_name: str | None) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if run_name is None or not str(run_name).strip():
        return f"freq_resp_{stamp}"
    slug = re.sub(r"[^\w\-]+", "_", str(run_name).strip()).strip("_")
    if not slug:
        return f"freq_resp_{stamp}"
    return f"freq_resp_{slug}_{stamp}"


def _nominal_vpp(requested_vpp: float | None, v_max: float) -> float:
    if requested_vpp is None:
        return v_max
    return min(requested_vpp, v_max)


def _csv_columns(rows: list[dict]) -> list[str]:
    columns = list(CSV_COLUMNS)
    if any("phase_deg" in row for row in rows):
        columns.extend(DUAL_COLUMNS)
    return columns


def _write_csv(path: Path, rows: list[dict]) -> None:
    columns = _csv_columns(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = {}
            for name in columns:
                value = row[name]
                if name == "v_requested_vpp" and value is None:
                    out[name] = "max"
                else:
                    out[name] = value
            writer.writerow(out)


def _figure_from_spec():
    for parent in Path(__file__).resolve().parents:
        spec_path = parent / "gui" / "dvorak_gui" / "mpl_spec.py"
        if spec_path.is_file():
            gui_dir = parent / "gui"
            if str(gui_dir) not in sys.path:
                sys.path.insert(0, str(gui_dir))
            from dvorak_gui.mpl_spec import figure_from_spec

            return figure_from_spec
    raise RuntimeError("Could not find gui/dvorak_gui/mpl_spec.py")


def _plot(
    rows: list[dict],
    output_path: Path,
    scope_bw_hz: float,
    show: bool,
    *,
    logx: bool = True,
) -> None:
    import frequency_plot

    spec = frequency_plot.frequency_spec(rows, scope_bw_hz, logx=logx)
    draw = _figure_from_spec()
    if show:
        fig = plt.figure()
    else:
        fig = Figure()
        FigureCanvasAgg(fig)
    draw(spec, fig)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    if show:
        plt.show()
    else:
        fig.clear()


def _screen_samples(scope, channel: int):
    """On-screen volts, or None when the download fails."""
    try:
        _time_s, voltage_v = scope.read_screen(channel)
    except SweepCancelled:
        raise
    except Exception as exc:
        print(f"Scale screen read failed: {exc}")
        return None
    return voltage_v


def _screen_span(samples: np.ndarray | None) -> tuple[float, float, float] | None:
    """Return (vmin, vmax, vavg) from the visible trace, or None."""
    if samples is None:
        return None
    vmin, vmax = sample_span(samples)
    if not (math.isfinite(vmin) and math.isfinite(vmax) and vmax > vmin):
        return None
    finite = np.asarray(samples, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size < 8:
        return None
    return vmin, vmax, float(np.mean(finite))


def _tune_vertical(
    scope,
    channel: int,
    start_scale: float,
    frequency_hz: float,
    averages: int,
    should_stop: object,
) -> tuple[str, float, float | None, float]:
    """Walk V/div until the on-screen trace fills the graticule.

    The scope's VPP/VMIN/VMAX queries can repeat a rescaled copy of the
    previous acquisition for several readings after a V/div change, including
    a stable value near half the real amplitude. The downloaded trace does
    not, so the ladder follows that trace. The third return is that trace's
    peak-to-peak voltage when the screen read succeeded.

    Returns status, V/div, screen Vpp or None, and the offset at screen center.
    """
    scale = nearest_scale(start_scale)
    offset = 0.0
    previous: float | None = None
    clip_run = 0
    screen_vpp: float | None = None
    for _ in range(len(vertical_ladder())):
        _raise_if_stopped(should_stop)
        scope.set_vertical(channel, scale, offset)
        _acquire_settle(frequency_hz, averages, should_stop)
        samples = _screen_samples(scope, channel)
        span = _screen_span(samples)
        from_screen = span is not None
        if from_screen:
            vmin, vmax, vavg = span
        else:
            vmin, vmax, vavg = scope.measure_voltage_span(channel)
        if needs_recenter(offset, vavg, scale):
            offset = float(vavg)
            scope.set_vertical(channel, scale, offset)
            _acquire_settle(frequency_hz, averages, should_stop)
            samples = _screen_samples(scope, channel)
            span = _screen_span(samples)
            from_screen = span is not None
            if from_screen:
                vmin, vmax, vavg = span
            else:
                vmin, vmax, vavg = scope.measure_voltage_span(channel)
        measured = math.isfinite(vmin) and math.isfinite(vmax) and vmax > vmin
        action = decide_vertical(scale, offset, vmin, vmax, samples if from_screen else None)
        if from_screen:
            clip_run = 0
        elif not measured and action == "coarser":
            clip_run += 1
        else:
            clip_run = 0
        screen_vpp = (vmax - vmin) if from_screen and measured else None
        if action in {"ok", "below_floor", "clipped"}:
            return action, scale, screen_vpp, offset
        if action == "finer":
            nxt = next_finer(scale)
        else:
            nxt = coarser_by(scale, clip_run)
        if nxt is None or nxt == previous:
            terminal = status_if_reversed(action, scale, offset, vmin, vmax)
            kept = screen_vpp if terminal in {"ok", "below_floor"} else None
            return terminal, scale, kept, offset
        previous = scale
        scale = nxt
    return "clipped", scale, None, offset


def _channel_seed(
    v_nominal: float,
    last_ok_vpp: float | None,
    floor_scale: float | None,
) -> tuple[float, float]:
    """Return the ladder start and the Vpp passed to prepare_sine."""
    if floor_scale is not None:
        start = nearest_scale(floor_scale)
        return start, start * 2.0
    prepare_vpp = v_nominal if last_ok_vpp is None else last_ok_vpp
    return seed_volts_per_div(prepare_vpp), prepare_vpp


def _next_seed(
    status: str,
    vpp: float,
    v_div: float,
    last_ok_vpp: float | None,
) -> tuple[float | None, float | None]:
    """Return (last ok Vpp, floor scale) for the following point."""
    if status == "below_floor" and math.isfinite(vpp):
        return None, v_div
    if status == "ok" and math.isfinite(vpp) and vpp > 0:
        return vpp, None
    return last_ok_vpp, None


def _apply_sample_vpp(v_scope: float, sample_vpp: float | None) -> float:
    if math.isfinite(v_scope) or sample_vpp is None:
        return v_scope
    return sample_vpp


def _recorded_vpp(status: str, measured_vpp: float, screen_vpp: float | None) -> float:
    """Prefer the visible trace once the ladder has accepted the scale.

    A clipped scale keeps the scope query: on some models that still reports
    the over-range amplitude while the pixels sit on the rails.
    """
    if (
        status in {"ok", "below_floor"}
        and screen_vpp is not None
        and math.isfinite(screen_vpp)
        and screen_vpp > 0
    ):
        return screen_vpp
    return _apply_sample_vpp(measured_vpp, screen_vpp)


def _format_thd(thd: float) -> str:
    if not math.isfinite(thd):
        return "n/a"
    return f"{100.0 * thd:.2f} %"


def run_sweep(
    f_min_hz: float,
    f_max_hz: float,
    points: int,
    amplitude_vpp: float | None,
    load_ohm: float,
    gen_channel: int,
    scope_channel: int,
    scope_bw_hz: float,
    scope_model: str | None = None,
    averages: int = DEFAULT_AVERAGES,
    should_stop: object = None,
    input_channel: int | None = None,
    spacing: str = DEFAULT_FREQUENCY_SPACING,
    step_hz: float | None = None,
) -> dict:
    if input_channel is not None:
        if input_channel not in (1, 2, 3, 4):
            raise ValueError("input_channel must be 1-4")
        if input_channel == scope_channel:
            raise ValueError("input_channel and scope_channel must differ")
    if averages not in AVERAGE_CHOICES:
        allowed = ", ".join(str(item) for item in AVERAGE_CHOICES)
        raise ValueError(f"averages must be one of: {allowed}")
    frequencies = _frequencies(f_min_hz, f_max_hz, points, spacing, step_hz)
    points = int(len(frequencies))
    gen = open_generator()
    scope = None
    gen_idn = ""
    scope_idn = ""
    rows: list[dict] = []
    cancelled = False
    try:
        _raise_if_stopped(should_stop)
        gen_idn = gen.identify()
        scope = open_oscilloscope(scope_model)
        scope_idn = scope.identify()
        gen.set_load(gen_channel, load_ohm)
        gen.output(gen_channel, True)
        out_ok: float | None = None
        out_floor: float | None = None
        in_ok: float | None = None
        in_floor: float | None = None
        dual = input_channel is not None

        for frequency_hz in frequencies:
            _raise_if_stopped(should_stop)
            v_max = gen.max_sine_vpp(float(frequency_hz))
            v_nominal = _nominal_vpp(amplitude_vpp, v_max)
            gen.set_waveform(gen_channel, "sine", float(frequency_hz), v_nominal)
            _gen_settle(float(frequency_hz), should_stop)
            trigger_channel = input_channel if dual else scope_channel
            trigger_ok = in_ok if dual else out_ok
            trigger_floor = in_floor if dual else out_floor
            start_scale, prepare_vpp = _channel_seed(v_nominal, trigger_ok, trigger_floor)
            scope.prepare_sine(
                trigger_channel,
                float(frequency_hz),
                prepare_vpp,
                averages=averages,
            )
            tuned = _tune_vertical(
                scope,
                trigger_channel,
                start_scale,
                float(frequency_hz),
                averages,
                should_stop,
            )
            vertical_status, v_div, sample_vpp, offset_v = tuned
            if dual:
                scope.set_trigger_edge(input_channel, offset_v)
                in_status, in_div = vertical_status, v_div
                v_in = _recorded_vpp(
                    in_status,
                    scope.measure_vpp(input_channel, allow_rescale=False),
                    sample_vpp,
                )
                in_ok, in_floor = _next_seed(in_status, v_in, in_div, in_ok)
                out_scale, _out_prepare = _channel_seed(v_nominal, out_ok, out_floor)
                out_status, out_div, out_sample, _out_offset = _tune_vertical(
                    scope,
                    scope_channel,
                    out_scale,
                    float(frequency_hz),
                    averages,
                    should_stop,
                )
                vertical_status, v_div, sample_vpp = out_status, out_div, out_sample
            else:
                in_status = in_div = None
                v_in = float("nan")
            v_scope = _recorded_vpp(
                vertical_status,
                scope.measure_vpp(scope_channel, allow_rescale=False),
                sample_vpp,
            )
            f_scope = scope.measure_frequency(scope_channel)
            thd = float("nan")
            if vertical_status == "ok":
                try:
                    time_s, voltage_v = scope.read_screen(scope_channel)
                    thd = sine_thd(time_s, voltage_v, float(frequency_hz))
                    if not math.isfinite(v_scope):
                        sample_min, sample_max = sample_span(voltage_v)
                        if (
                            math.isfinite(sample_min)
                            and math.isfinite(sample_max)
                            and sample_max > sample_min
                        ):
                            v_scope = sample_max - sample_min
                except SweepCancelled:
                    raise
                except Exception as exc:
                    print(f"THD screen read failed: {exc}")
            out_ok, out_floor = _next_seed(vertical_status, v_scope, v_div, out_ok)
            ratio = (
                v_scope / v_nominal
                if math.isfinite(v_scope) and v_nominal > 0
                else float("nan")
            )
            row = {
                "frequency_hz": float(frequency_hz),
                "v_requested_vpp": amplitude_vpp,
                "v_nominal_vpp": v_nominal,
                "v_scope_vpp": v_scope,
                "f_scope_hz": f_scope,
                "ratio": ratio,
                "ratio_db": _ratio_db(ratio),
                "scope_limited": bool(frequency_hz > scope_bw_hz),
                "v_div": v_div,
                "vertical_status": vertical_status,
                "thd": thd,
            }
            gain_db = float("nan")
            phase = float("nan")
            if dual:
                assert input_channel is not None
                gain = float("nan")
                if in_status == "ok" and vertical_status == "ok":
                    phase = scope.measure_phase(scope_channel, input_channel)
                    if not math.isfinite(phase):
                        _acquire_settle(float(frequency_hz), averages, should_stop)
                        phase = scope.measure_phase(scope_channel, input_channel)
                    if math.isfinite(v_scope) and math.isfinite(v_in) and v_in > 0:
                        gain = v_scope / v_in
                        gain_db = _ratio_db(gain)
                row.update(
                    {
                        "v_in_vpp": v_in,
                        "v_in_div": in_div,
                        "v_in_status": in_status,
                        "gain": gain,
                        "gain_db": gain_db,
                        "phase_deg": phase,
                    }
                )
            rows.append(row)
            message = (
                f"{frequency_hz:12.4g} Hz  "
                f"Vnom={v_nominal:.4g} Vpp  "
                f"Vscope={v_scope:.4g} Vpp  "
                f"ratio={_ratio_db(ratio):.2f} dB  "
                f"{vertical_status}  THD={_format_thd(thd)}"
            )
            if dual:
                phase_txt = f"{phase:.2f} deg" if math.isfinite(phase) else "n/a"
                gain_txt = f"{gain_db:.2f} dB" if math.isfinite(gain_db) else "n/a"
                message += f"  Vin={v_in:.4g} Vpp  gain={gain_txt}  phase={phase_txt}"
            print(message)
    except (SweepCancelled, KeyboardInterrupt):
        cancelled = True
        print("Sweep stopped")
    finally:
        try:
            gen.output(gen_channel, False)
        except Exception:
            pass
        gen.close()
        if scope is not None:
            scope.close()

    return {
        "campaign": CAMPAIGN_FREQUENCY_RESPONSES,
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "generator_idn": gen_idn,
        "oscilloscope_idn": scope_idn,
        "generator_model": gen.model_id,
        "oscilloscope_model": None if scope is None else scope.model_id,
        "gen_channel": gen_channel,
        "scope_channel": scope_channel,
        "input_channel": input_channel,
        "load_ohm": None if math.isinf(load_ohm) else load_ohm,
        "load_highz": math.isinf(load_ohm),
        "f_min_hz": f_min_hz,
        "f_max_hz": f_max_hz,
        "points": points,
        "frequency_spacing": spacing,
        "frequency_step_hz": None if spacing != "linear" else step_hz,
        "amplitude_requested_vpp": amplitude_vpp,
        "amplitude_mode": "max" if amplitude_vpp is None else "fixed",
        "averages": averages,
        "cancelled": cancelled,
        "scope_bw_hz": scope_bw_hz,
        "thd_definition": THD_DEFINITION,
        "phase_definition": None if input_channel is None else PHASE_DEFINITION,
        "ratio_definition": (
            "V_scope / V_nominal, where V_nominal is the generator sine Vpp "
            "(max at each frequency by default, or min(requested, max) when --amplitude is set)"
        ),
        "rows": rows,
    }


def save_sweep(payload: dict, *, show: bool = False) -> dict[str, Path]:
    """Write the CSV, JSON, and PNG the CLI writes. Returns those paths."""
    stem = _run_stem(payload.get("run_name"))
    data_dir = campaign_data(CAMPAIGN_FREQUENCY_RESPONSES)
    plots_dir = campaign_plots(CAMPAIGN_FREQUENCY_RESPONSES)
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / f"{stem}.csv"
    json_path = data_dir / f"{stem}.json"
    plot_path = plots_dir / f"{stem}.png"
    _write_csv(csv_path, payload["rows"])
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _plot(
        payload["rows"],
        plot_path,
        float(payload["scope_bw_hz"]),
        show=show,
        logx=payload.get("frequency_spacing") != "linear",
    )
    return {"csv": csv_path, "json": json_path, "png": plot_path}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sweep a sine and record V_scope / V_nominal into Frequency_responses/Data."
    )
    parser.add_argument("--f-min", type=float, default=DEFAULT_F_MIN_HZ, help="Start frequency in Hz (default: 1e3)")
    parser.add_argument("--f-max", type=float, default=DEFAULT_F_MAX_HZ, help="Stop frequency in Hz (default: 1e8)")
    parser.add_argument(
        "--points",
        type=int,
        default=DEFAULT_POINTS,
        help=f"Number of frequencies for logarithmic spacing (default: {DEFAULT_POINTS})",
    )
    parser.add_argument(
        "--spacing",
        choices=FREQUENCY_SPACINGS,
        default=DEFAULT_FREQUENCY_SPACING,
        help=f"Frequency grid (default: {DEFAULT_FREQUENCY_SPACING})",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=None,
        help="Frequency step in Hz. Required for --spacing linear; the point count is calculated.",
    )
    parser.add_argument(
        "--amplitude",
        type=float,
        default=DEFAULT_AMPLITUDE_VPP,
        help="Cap generator Vpp (default: max allowed at each frequency)",
    )
    parser.add_argument(
        "--load",
        type=_parse_load,
        default=DEFAULT_LOAD_OHM,
        help="Generator load in ohms, or inf/highz (default: 50)",
    )
    parser.add_argument(
        "--gen-channel",
        type=int,
        default=DEFAULT_GEN_CHANNEL,
        help=f"Generator output channel (default: {DEFAULT_GEN_CHANNEL})",
    )
    parser.add_argument(
        "--scope",
        type=str,
        default=None,
        choices=list_oscilloscopes() or None,
        help=(
            "Oscilloscope model id "
            f"(default: {DEFAULT_OSCILLOSCOPE}). "
            "MHO954 analog BW is 500 MHz (400 MHz with 3–4 channels on); "
            "pass --scope-bw 500e6 with that scope."
        ),
    )
    parser.add_argument(
        "--scope-channel",
        type=int,
        default=DEFAULT_SCOPE_CHANNEL,
        help=(
            f"Oscilloscope channel (default: {DEFAULT_SCOPE_CHANNEL}). "
            "DUT output when --input-channel is set."
        ),
    )
    parser.add_argument(
        "--input-channel",
        type=int,
        default=None,
        help=(
            "Scope channel on the generator input. Omit for a single-channel sweep. "
            "Must differ from --scope-channel."
        ),
    )
    parser.add_argument(
        "--scope-bw",
        type=float,
        default=DEFAULT_SCOPE_BW_HZ,
        help="Scope analog bandwidth in Hz; points above this are flagged (default: 1e8)",
    )
    parser.add_argument(
        "--averages",
        type=_parse_averages,
        default=DEFAULT_AVERAGES,
        help=(
            "Scope acquire averages (1 = normal). Wait after each timebase "
            f"change is {SETTLE_BASE_S:g} s plus {ACQ_CYCLES_PER_AVERAGE:g} "
            f"periods per average. Choices: {', '.join(str(n) for n in AVERAGE_CHOICES)}"
        ),
    )
    parser.add_argument("--no-show", action="store_true", help="Save the plot without opening a window")
    parser.add_argument(
        "--name",
        type=str,
        default=DEFAULT_RUN_NAME,
        help="Run label for output files (default: timestamp only)",
    )
    args = parser.parse_args()

    if args.amplitude is not None and args.amplitude <= 0:
        raise SystemExit("--amplitude must be positive when set")
    if args.input_channel is not None and args.input_channel == args.scope_channel:
        raise SystemExit("--input-channel and --scope-channel must differ")
    if args.spacing == "linear":
        if args.step is None or args.step <= 0:
            raise SystemExit("--spacing linear requires a positive --step in Hz")
    elif args.step is not None:
        raise SystemExit("--step is only used with --spacing linear")

    stop = threading.Event()
    previous_sigint = signal.getsignal(signal.SIGINT)

    def _request_stop(_signum, _frame) -> None:
        if stop.is_set():
            signal.signal(signal.SIGINT, previous_sigint)
            raise KeyboardInterrupt()
        stop.set()

    signal.signal(signal.SIGINT, _request_stop)
    try:
        payload = run_sweep(
            f_min_hz=args.f_min,
            f_max_hz=args.f_max,
            points=args.points,
            amplitude_vpp=args.amplitude,
            load_ohm=args.load,
            gen_channel=args.gen_channel,
            scope_channel=args.scope_channel,
            scope_bw_hz=args.scope_bw,
            scope_model=args.scope,
            averages=args.averages,
            should_stop=stop.is_set,
            input_channel=args.input_channel,
            spacing=args.spacing,
            step_hz=args.step,
        )
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
    payload["run_name"] = args.name.strip() if args.name and str(args.name).strip() else None
    if payload.get("cancelled") and not payload.get("rows"):
        raise SystemExit("Sweep stopped before any points were recorded.")
    paths = save_sweep(payload, show=not args.no_show)
    if payload.get("cancelled"):
        print("\nSweep stopped; wrote partial results")
    print(f"\nWrote {paths['csv']}")
    print(f"Wrote {paths['json']}")
    print(f"Wrote {paths['png']}")


if __name__ == "__main__":
    main()
