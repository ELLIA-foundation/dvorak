"""Sweep a sine on the generator and record V_scope / V_nominal vs frequency."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

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
CSV_COLUMNS = (
    "frequency_hz",
    "v_requested_vpp",
    "v_nominal_vpp",
    "v_scope_vpp",
    "f_scope_hz",
    "ratio",
    "ratio_db",
    "scope_limited",
)


def _logspace(f_min: float, f_max: float, points: int) -> np.ndarray:
    if f_min <= 0 or f_max <= 0:
        raise ValueError("Frequencies must be positive")
    if f_max < f_min:
        raise ValueError("--f-max must be >= --f-min")
    if points < 2:
        raise ValueError("--points must be at least 2")
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


def _settle(frequency_hz: float) -> None:
    time.sleep(SETTLE_BASE_S + SETTLE_PERIODS / frequency_hz)


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


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = {}
            for name in CSV_COLUMNS:
                value = row[name]
                if name == "v_requested_vpp" and value is None:
                    out[name] = "max"
                else:
                    out[name] = value
            writer.writerow(out)


def _plot(rows: list[dict], output_path: Path, scope_bw_hz: float, show: bool) -> None:
    freq = np.asarray([row["frequency_hz"] for row in rows], dtype=float)
    ratio_db = np.asarray([row["ratio_db"] for row in rows], dtype=float)
    v_nom = np.asarray([row["v_nominal_vpp"] for row in rows], dtype=float)
    v_scope = np.asarray([row["v_scope_vpp"] for row in rows], dtype=float)
    limited = np.asarray([row["scope_limited"] for row in rows], dtype=bool)

    fig, axes = plt.subplots(2, 1, figsize=(10, 7.2), sharex=True)
    ax_db, ax_v = axes
    ax_db.semilogx(freq, ratio_db, "-o", color="#1f77b4", markersize=4, label="20 log10(V_scope / V_nominal)")
    if limited.any():
        ax_db.axvline(scope_bw_hz, color="#d62728", linestyle="--", linewidth=1, label=f"scope BW {scope_bw_hz/1e6:.0f} MHz")
    ax_db.set_ylabel("Ratio (dB)")
    ax_db.set_title("Frequency response  V_scope / V_nominal")
    ax_db.grid(True, which="both", alpha=0.3)
    ax_db.legend(loc="best", fontsize=8)

    ax_v.semilogx(freq, v_nom, "-o", color="#2ca02c", markersize=4, label="V_nominal (generator)")
    ax_v.semilogx(freq, v_scope, "-s", color="#ff7f0e", markersize=4, label="V_scope")
    if limited.any():
        ax_v.axvline(scope_bw_hz, color="#d62728", linestyle="--", linewidth=1)
    ax_v.set_xlabel("Frequency (Hz)")
    ax_v.set_ylabel("Amplitude (Vpp)")
    ax_v.grid(True, which="both", alpha=0.3)
    ax_v.legend(loc="best", fontsize=8)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    if show:
        plt.show()
    else:
        plt.close(fig)


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
) -> dict:
    frequencies = _logspace(f_min_hz, f_max_hz, points)
    gen = open_generator()
    scope = None
    gen_idn = ""
    scope_idn = ""
    rows: list[dict] = []
    try:
        gen_idn = gen.identify()
        scope = open_oscilloscope(scope_model)
        scope_idn = scope.identify()
        gen.set_load(gen_channel, load_ohm)
        gen.output(gen_channel, True)

        for frequency_hz in frequencies:
            v_max = gen.max_sine_vpp(float(frequency_hz))
            v_nominal = _nominal_vpp(amplitude_vpp, v_max)
            gen.set_waveform(gen_channel, "sine", float(frequency_hz), v_nominal)
            _settle(float(frequency_hz))
            scope.prepare_sine(scope_channel, float(frequency_hz), v_nominal)
            _settle(float(frequency_hz))
            v_scope = scope.measure_vpp(scope_channel)
            f_scope = scope.measure_frequency(scope_channel)
            ratio = (
                v_scope / v_nominal
                if math.isfinite(v_scope) and v_nominal > 0
                else float("nan")
            )
            rows.append(
                {
                    "frequency_hz": float(frequency_hz),
                    "v_requested_vpp": amplitude_vpp,
                    "v_nominal_vpp": v_nominal,
                    "v_scope_vpp": v_scope,
                    "f_scope_hz": f_scope,
                    "ratio": ratio,
                    "ratio_db": _ratio_db(ratio),
                    "scope_limited": bool(frequency_hz > scope_bw_hz),
                }
            )
            print(
                f"{frequency_hz:12.4g} Hz  "
                f"Vnom={v_nominal:.4g} Vpp  "
                f"Vscope={v_scope:.4g} Vpp  "
                f"ratio={_ratio_db(ratio):.2f} dB"
            )
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
        "oscilloscope_model": scope.model_id,
        "gen_channel": gen_channel,
        "scope_channel": scope_channel,
        "load_ohm": None if math.isinf(load_ohm) else load_ohm,
        "load_highz": math.isinf(load_ohm),
        "f_min_hz": f_min_hz,
        "f_max_hz": f_max_hz,
        "points": points,
        "amplitude_requested_vpp": amplitude_vpp,
        "amplitude_mode": "max" if amplitude_vpp is None else "fixed",
        "scope_bw_hz": scope_bw_hz,
        "ratio_definition": (
            "V_scope / V_nominal, where V_nominal is the generator sine Vpp "
            "(max at each frequency by default, or min(requested, max) when --amplitude is set)"
        ),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log-sweep a sine and record V_scope / V_nominal into Frequency_responses/Data."
    )
    parser.add_argument("--f-min", type=float, default=DEFAULT_F_MIN_HZ, help="Start frequency in Hz (default: 1e3)")
    parser.add_argument("--f-max", type=float, default=DEFAULT_F_MAX_HZ, help="Stop frequency in Hz (default: 1e8)")
    parser.add_argument("--points", type=int, default=DEFAULT_POINTS, help="Log-spaced points (default: 41)")
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
        help=f"Oscilloscope read channel (default: {DEFAULT_SCOPE_CHANNEL})",
    )
    parser.add_argument(
        "--scope-bw",
        type=float,
        default=DEFAULT_SCOPE_BW_HZ,
        help="Scope analog bandwidth in Hz; points above this are flagged (default: 1e8)",
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
    )
    payload["run_name"] = args.name.strip() if args.name and str(args.name).strip() else None
    stem = _run_stem(payload["run_name"])
    data_dir = campaign_data(CAMPAIGN_FREQUENCY_RESPONSES)
    plots_dir = campaign_plots(CAMPAIGN_FREQUENCY_RESPONSES)
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / f"{stem}.csv"
    json_path = data_dir / f"{stem}.json"
    plot_path = plots_dir / f"{stem}.png"
    _write_csv(csv_path, payload["rows"])
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _plot(payload["rows"], plot_path, args.scope_bw, show=not args.no_show)
    print(f"\nWrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"Wrote {plot_path}")


if __name__ == "__main__":
    main()
