"""Analyze a captured spark-gap waveform and write a diagnostic figure pack."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

for _parent in Path(__file__).resolve().parents:
    if (_parent / "lib" / "paths.py").is_file() and (_parent / "Measurements").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
else:
    raise SystemExit("Could not find repository root (expected lib/paths.py and Measurements/).")

from lib.paths import CAMPAIGN_SPARK_GAP, campaign_data, campaign_plots, infer_campaign
from lib.waveform import (
    decimate_minmax,
    latest_capture,
    load_metadata,
    pick_time_scale,
    pick_voltage_scale,
)
from spark_gap import (
    CSV_COLUMNS,
    DEFAULT_COARSE_STEP_S,
    DEFAULT_DROP_THRESHOLD_V,
    DEFAULT_DROP_WINDOW_S,
    DEFAULT_MERGE_GAP_S,
    DEFAULT_SCOPE_BW_HZ,
    EVENT_FIELDS,
    AnalysisResult,
    SparkGapEvent,
    analyze_waveform,
    pick_representatives,
    scope_limit_footer,
    typical_events,
)

HERE = Path(__file__).resolve().parent
METRICS_SRC = HERE / "SPARK_GAP_METRICS.md"
DEFAULT_MAX_POINTS = 20_000
FIGURE_DPI = 150


def _fmt_time(seconds: float | None, unit: str) -> str:
    if seconds is None or not np.isfinite(seconds):
        return "—"
    if unit == "ns":
        return f"{seconds * 1e9:.1f} ns"
    if unit == "us":
        return f"{seconds * 1e6:.1f} µs"
    if unit == "ms":
        return f"{seconds * 1e3:.3f} ms"
    return f"{seconds:.4g} s"


def _slew_text(event: SparkGapEvent) -> str:
    dv_kv = (event.v10 - event.v90) / 1000.0
    return (
        f"{dv_kv:.2f} kV in {_fmt_time(event.t_collapse_10_90, 'ns')} "
        f"({event.slew_collapse_mean / 1e12:.2f} kV/ns)"
    )


def _event_color(event: SparkGapEvent, cmap) -> tuple:
    return cmap(event.event_index % 10)


def _annotate_stats(ax, values: np.ndarray, x_scale: float, unit: str) -> None:
    finite = values[np.isfinite(values)]
    if len(finite) == 0:
        return
    mean = float(np.mean(finite))
    std = float(np.std(finite, ddof=1)) if len(finite) > 1 else 0.0
    median = float(np.median(finite))
    cv = (std / mean) if mean != 0 else float("nan")
    ax.text(
        0.98,
        0.97,
        f"n={len(finite)}\n"
        f"mean={mean * x_scale:.3g} {unit}\n"
        f"std={std * x_scale:.3g} {unit}\n"
        f"median={median * x_scale:.3g} {unit}\n"
        f"CV={100 * cv:.1f}%",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=8,
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.85},
    )


def _footer(fig, detection: dict) -> None:
    fig.text(
        0.5,
        0.01,
        scope_limit_footer(detection),
        ha="center",
        va="bottom",
        fontsize=8,
        color="0.35",
    )


def _apply_layout(fig) -> None:
    fig.tight_layout(rect=(0, 0.05, 1, 1))


def plot_overview(result: AnalysisResult, include_first: bool) -> plt.Figure:
    plot_t, plot_v = decimate_minmax(result.time_s, result.voltage_v, DEFAULT_MAX_POINTS)
    display_t, time_unit = pick_time_scale(plot_t)
    display_v, volt_unit = pick_voltage_scale(plot_v)
    if time_unit == "ms":
        t_scale = 1e3
    elif time_unit == "µs":
        t_scale = 1e6
    elif time_unit == "ns":
        t_scale = 1e9
    else:
        t_scale = 1.0
    v_scale = 1e-3 if volt_unit == "kV" else 1.0

    fig, ax = plt.subplots(figsize=(12, 5.2))
    ax.plot(display_t, display_v, linewidth=0.8, color="#1f77b4")
    ax.axhline(0.0, color="0.5", linewidth=0.8, linestyle="--")
    for event in result.events:
        color = "#d62728" if event.first_cycle else "#ff7f0e"
        ax.axvline(event.t_break * t_scale, color=color, alpha=0.35, linewidth=0.8)
        ax.plot(
            event.t_break * t_scale,
            event.v_breakdown * v_scale,
            "o",
            color=color,
            markersize=5,
            zorder=3,
        )
        ax.annotate(
            f"{event.event_index}:{event.v_breakdown / 1000.0:.1f} kV",
            (event.t_break * t_scale, event.v_breakdown * v_scale),
            textcoords="offset points",
            xytext=(4, 8 if event.event_index % 2 == 0 else -14),
            fontsize=7,
            color=color,
        )
    ax.set_xlabel(f"Time ({time_unit})")
    ax.set_ylabel(f"Voltage ({volt_unit})")
    ax.set_xlim(float(display_t[0]), float(display_t[-1]))
    ax.grid(True, alpha=0.3)
    ax.set_title(
        f"Overview — {result.detection['n_events']} breakdowns "
        f"({result.detection['n_typical']} typical)"
    )
    _footer(fig, result.detection)
    _apply_layout(fig)
    return fig


def plot_sequential(result: AnalysisResult, include_first: bool) -> plt.Figure:
    events = result.events
    indexes = np.asarray([e.event_index for e in events], dtype=float)
    v_bd = np.asarray([e.v_breakdown / 1000.0 for e in events])
    period_us = np.asarray([e.period_s * 1e6 if e.period_s is not None else np.nan for e in events])
    rate = np.asarray(
        [e.charge_rate / 1e6 if e.charge_rate is not None else np.nan for e in events]
    )
    first = np.asarray([e.first_cycle for e in events], dtype=bool)

    fig, axes = plt.subplots(3, 1, figsize=(11, 8.2), sharex=False)
    series = (
        (v_bd, "v_breakdown (kV)", axes[0]),
        (period_us, "period_s (µs)", axes[1]),
        (rate, "charge_rate (kV/ms)", axes[2]),
    )
    for values, ylabel, ax in series:
        ax.plot(indexes, values, "-o", color="#1f77b4", markersize=5, label="all")
        if first.any():
            ax.plot(indexes[first], values[first], "o", color="#d62728", markersize=7, label="first_cycle")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        ax.set_xlabel("event_index")
    axes[0].set_title("Sequential statistics (conditioning and jitter)")
    axes[0].legend(loc="best", fontsize=8)
    cond = result.summary.get("conditioning_slope")
    if cond is not None:
        axes[0].text(
            0.02,
            0.95,
            f"conditioning_slope = {cond / 1000.0:.2f} kV/event (typical)",
            transform=axes[0].transAxes,
            va="top",
            fontsize=8,
        )
    _footer(fig, result.detection)
    _apply_layout(fig)
    return fig


def plot_histograms(result: AnalysisResult, include_first: bool) -> plt.Figure:
    pool = typical_events(result.events, include_first)
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.6))
    panels = (
        (axes[0, 0], np.asarray([e.v_breakdown for e in pool]), 1e-3, "kV", "v_breakdown"),
        (axes[0, 1], np.asarray([e.period_s if e.period_s is not None else np.nan for e in pool]), 1e6, "µs", "period_s"),
        (axes[1, 0], np.asarray([e.charge_rate if e.charge_rate is not None else np.nan for e in pool]), 1e-6, "kV/ms", "charge_rate"),
        (axes[1, 1], np.asarray([e.t_collapse_10_90 for e in pool]), 1e9, "ns", "t_collapse_10_90"),
    )
    for ax, values, scale, unit, title in panels:
        finite = values[np.isfinite(values)]
        if len(finite) == 0:
            ax.set_title(title)
            continue
        ax.hist(finite * scale, bins=min(10, max(5, len(finite) // 2)), color="#1f77b4", edgecolor="white")
        ax.axvline(np.mean(finite) * scale, color="#d62728", linestyle="--", linewidth=1, label="mean")
        ax.axvline(np.median(finite) * scale, color="#2ca02c", linestyle=":", linewidth=1, label="median")
        ax.set_xlabel(f"{title} ({unit})")
        ax.set_ylabel("Count")
        ax.set_title(title)
        ax.grid(True, alpha=0.25, axis="y")
        _annotate_stats(ax, finite, scale, unit)
        ax.legend(fontsize=7, loc="upper left")
    fig.suptitle("Typical-event histograms" if not include_first else "All-event histograms")
    _footer(fig, result.detection)
    fig.tight_layout(rect=(0, 0.05, 1, 0.96))
    return fig


def plot_collapse_overlay(result: AnalysisResult, include_first: bool) -> plt.Figure:
    chosen = pick_representatives(result.events, count=5, include_first=include_first)
    fig, ax = plt.subplots(figsize=(10, 5.6))
    cmap = plt.get_cmap("tab10")
    for event in chosen:
        t_ns = event.collapse_t_s * 1e9
        ax.plot(
            t_ns,
            event.collapse_v / 1000.0,
            linewidth=1.1,
            color=_event_color(event, cmap),
            label=f"#{event.event_index}  V_bd={event.v_breakdown / 1000.0:.1f} kV",
        )
        ax.plot(0.0, event.v10 / 1000.0, "o", color=_event_color(event, cmap), markersize=4)
    if result.detection.get("scope_t1090_limit_s"):
        limit_ns = result.detection["scope_t1090_limit_s"] * 1e9
        ax.axvspan(0.0, limit_ns, color="0.8", alpha=0.35, label=f"scope 10–90 limit ({limit_ns:.1f} ns)")
    ax.axvline(0.0, color="0.4", linewidth=0.8, linestyle="--")
    ax.set_xlabel("Time after 10% crossing (ns)")
    ax.set_ylabel("Voltage (kV)")
    ax.set_title("High-resolution collapse overlay (aligned at t10)")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8, loc="best")
    _footer(fig, result.detection)
    _apply_layout(fig)
    return fig


def plot_collapse_individuals(result: AnalysisResult, include_first: bool) -> plt.Figure:
    chosen = pick_representatives(result.events, count=5, include_first=include_first)
    n = max(1, len(chosen))
    fig, axes = plt.subplots(n, 1, figsize=(10, 2.15 * n), sharex=True, squeeze=False)
    cmap = plt.get_cmap("tab10")
    for ax, event in zip(axes[:, 0], chosen):
        t_ns = event.collapse_t_s * 1e9
        ax.plot(t_ns, event.collapse_v / 1000.0, color=_event_color(event, cmap), linewidth=1.0)
        t10_rel = (event.t10 - event.t10) * 1e9
        t90_rel = (event.t90 - event.t10) * 1e9
        ax.axhline(event.v10 / 1000.0, color="0.4", linestyle=":", linewidth=0.8)
        ax.axhline(event.v90 / 1000.0, color="0.4", linestyle=":", linewidth=0.8)
        ax.axvline(t10_rel, color="#d62728", linestyle="--", linewidth=0.8)
        ax.axvline(t90_rel, color="#d62728", linestyle="--", linewidth=0.8)
        ax.set_ylabel("kV")
        ax.grid(True, alpha=0.3)
        ax.set_title(
            f"Event {event.event_index}  V_bd={event.v_breakdown / 1000.0:.2f} kV  —  {_slew_text(event)}",
            fontsize=9,
            loc="left",
        )
    axes[-1, 0].set_xlabel("Time after 10% crossing (ns)")
    fig.suptitle("Individual high-resolution discharges")
    _footer(fig, result.detection)
    fig.tight_layout(rect=(0, 0.05, 1, 0.97))
    return fig


def plot_ramp_overlay(result: AnalysisResult, include_first: bool) -> plt.Figure:
    pool = typical_events(result.events, include_first)
    fig, ax = plt.subplots(figsize=(10, 5.6))
    cmap = plt.get_cmap("tab10")
    for event in pool:
        if len(event.ramp_t_s) == 0:
            continue
        color = _event_color(event, cmap)
        t_us = event.ramp_t_s * 1e6
        ax.plot(t_us, event.ramp_v / 1000.0, color=color, linewidth=0.9, alpha=0.85)
        if event.charge_rate is not None and event.charge_intercept is not None and event.ramp_start_index is not None:
            t_abs = result.time_s[event.ramp_start_index] + event.ramp_t_s
            fit_v = event.charge_intercept + event.charge_rate * t_abs
            r2 = event.charge_r2 if event.charge_r2 is not None else float("nan")
            ax.plot(
                t_us,
                fit_v / 1000.0,
                color=color,
                linestyle="--",
                linewidth=0.8,
                alpha=0.6,
                label=f"#{event.event_index}  {event.charge_rate / 1e6:.1f} kV/ms  R²={r2:.3f}",
            )
    ax.set_xlabel("Time after ramp-fit start (µs)")
    ax.set_ylabel("Voltage (kV)")
    ax.set_title("Charging ramps (linear fit V = a + b t)")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7, loc="best", ncol=2)
    _footer(fig, result.detection)
    _apply_layout(fig)
    return fig


def plot_correlations(result: AnalysisResult, include_first: bool) -> plt.Figure:
    pool = typical_events(result.events, include_first)
    v_bd = np.asarray([e.v_breakdown / 1000.0 for e in pool])
    period = np.asarray([e.period_s * 1e6 if e.period_s is not None else np.nan for e in pool])
    rate = np.asarray([e.charge_rate / 1e6 if e.charge_rate is not None else np.nan for e in pool])
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    pairs = (
        (axes[0], period, "period_s (µs)", result.summary.get("corr_vbd_period"), "corr_vbd_period"),
        (axes[1], rate, "charge_rate (kV/ms)", result.summary.get("corr_vbd_charge_rate"), "corr_vbd_charge_rate"),
    )
    for ax, x, xlabel, corr, name in pairs:
        ax.scatter(x, v_bd, c="#1f77b4", edgecolors="white", s=45)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("v_breakdown (kV)")
        ax.grid(True, alpha=0.3)
        corr_txt = "n/a" if corr is None else f"{corr:.3f}"
        ax.set_title(f"{name} = {corr_txt}")
    fig.suptitle("Breakdown voltage correlations (typical events)")
    _footer(fig, result.detection)
    fig.tight_layout(rect=(0, 0.05, 1, 0.93))
    return fig


def plot_post_collapse(result: AnalysisResult, include_first: bool) -> plt.Figure:
    chosen = pick_representatives(result.events, count=5, include_first=include_first)
    fig, ax = plt.subplots(figsize=(10, 5.6))
    cmap = plt.get_cmap("tab10")
    for event in chosen:
        ax.plot(
            event.post_t_s * 1e9,
            event.post_v / 1000.0,
            color=_event_color(event, cmap),
            linewidth=1.0,
            label=(
                f"#{event.event_index}  V_min={event.v_undershoot / 1000.0:.1f} kV"
                + (f"  t_ring={event.t_ring * 1e9:.0f} ns" if event.t_ring else "")
            ),
        )
    ax.axhline(0.0, color="0.5", linewidth=0.8, linestyle="--")
    ax.set_xlabel("Time after t_break (ns)")
    ax.set_ylabel("Voltage (kV)")
    ax.set_title("Post-collapse window (undershoot and ring)")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8, loc="best")
    _footer(fig, result.detection)
    _apply_layout(fig)
    return fig


FIGURE_BUILDERS = (
    ("01_overview", plot_overview),
    ("02_sequential", plot_sequential),
    ("03_histograms", plot_histograms),
    ("04_collapse_overlay", plot_collapse_overlay),
    ("05_collapse_individuals", plot_collapse_individuals),
    ("06_ramp_overlay", plot_ramp_overlay),
    ("07_correlations", plot_correlations),
    ("08_post_collapse", plot_post_collapse),
)


def write_events_csv(path: Path, events: list[SparkGapEvent]) -> None:
    lines = [
        "# Spark-gap event table. SI units unless noted in SPARK_GAP_METRICS.md.",
        "# Storage units: s, V, V/s, Hz, J, C, A, H.",
    ]
    for name in CSV_COLUMNS:
        spec = EVENT_FIELDS.get(name, {})
        unit = spec.get("unit", "")
        definition = spec.get("definition", "")
        lines.append(f"# {name} [{unit}]: {definition}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for event in events:
            writer.writerow(event.to_record())


def write_summary_json(path: Path, result: AnalysisResult, source: Path) -> None:
    payload = {
        "source": str(source),
        "metadata": result.metadata,
        "detection": result.detection,
        "fields": EVENT_FIELDS,
        "events": [event.to_record() for event in result.events],
        "summary": result.summary,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def print_report(result: AnalysisResult) -> None:
    print(f"Events: {result.detection['n_events']}  typical: {result.detection['n_typical']}")
    header = (
        f"{'#':>3} {'t_ms':>9} {'V_bd':>8} {'V_res':>8} {'dV':>8} "
        f"{'T_us':>8} {'t10-90':>8} {'slew':>28} {'dV/dt':>10}"
    )
    print(header)
    print("-" * len(header))
    for event in result.events:
        flag = "*" if event.first_cycle else " "
        print(
            f"{event.event_index:3d}{flag}"
            f"{event.t_break * 1e3:9.3f} "
            f"{event.v_breakdown / 1000.0:7.2f} "
            f"{event.v_residual / 1000.0:7.2f} "
            f"{event.dv_collapse / 1000.0:7.2f} "
            f"{(event.period_s * 1e6 if event.period_s else float('nan')):8.1f} "
            f"{event.t_collapse_10_90 * 1e9:7.1f} "
            f"{_slew_text(event):>20} "
            f"{(event.charge_rate / 1e6 if event.charge_rate else float('nan')):9.1f}"
        )
    print("\nTypical-population summary")
    typical = result.summary.get("typical", {})
    for name in ("v_breakdown", "period_s", "charge_rate", "t_collapse_10_90"):
        stats = typical.get(name, {})
        if not stats or not stats.get("n"):
            continue
        scale, unit = {
            "v_breakdown": (1e-3, "kV"),
            "period_s": (1e6, "us"),
            "charge_rate": (1e-6, "kV/ms"),
            "t_collapse_10_90": (1e9, "ns"),
        }[name]
        mean = stats["mean"] * scale
        std = (stats["std"] or 0.0) * scale
        cv = stats.get("cv")
        cv_txt = f"{100 * cv:.1f}%" if cv is not None else "—"
        print(f"  {name:20s}  mean={mean:.3g} {unit}  std={std:.3g} {unit}  CV={cv_txt}")
    named = (
        ("v_bd_cv", result.summary.get("v_bd_cv"), "1"),
        ("recovery_ratio_mean", result.summary.get("recovery_ratio_mean"), "1"),
        ("period_jitter_frac", result.summary.get("period_jitter_frac"), "1"),
        ("corr_vbd_period", result.summary.get("corr_vbd_period"), "1"),
        ("corr_vbd_charge_rate", result.summary.get("corr_vbd_charge_rate"), "1"),
        ("conditioning_slope", result.summary.get("conditioning_slope"), "V/event"),
    )
    print("  figures of merit")
    for name, value, unit in named:
        if value is None:
            print(f"    {name} = —")
        elif name == "conditioning_slope":
            print(f"    {name} = {value / 1000.0:.3g} kV/event")
        else:
            print(f"    {name} = {value:.3g} {unit}")
    print(f"\n{scope_limit_footer(result.detection)}")


def save_figures(
    result: AnalysisResult,
    out_dir: Path,
    include_first: bool,
    show: bool,
) -> list[Path]:
    saved: list[Path] = []
    pdf_path = out_dir / "analysis.pdf"
    figures: list[plt.Figure] = []
    for name, builder in FIGURE_BUILDERS:
        fig = builder(result, include_first)
        png_path = out_dir / f"{name}.png"
        fig.savefig(png_path, dpi=FIGURE_DPI)
        saved.append(png_path)
        figures.append(fig)
    with PdfPages(pdf_path) as pdf:
        for fig in figures:
            pdf.savefig(fig)
    saved.append(pdf_path)
    if show:
        plt.show()
    else:
        for fig in figures:
            plt.close(fig)
    return saved


def run_analysis(
    npz_path: Path,
    out_dir: Path,
    include_first: bool,
    show: bool,
    drop_threshold_v: float,
    drop_window_s: float,
    merge_gap_s: float,
    scope_bw_hz: float,
    capacitance_f: float | None,
) -> AnalysisResult:
    data = np.load(npz_path)
    time_s = np.asarray(data["time_s"], dtype=np.float64)
    voltage_v = np.asarray(data["voltage_v"], dtype=np.float64)
    metadata = load_metadata(npz_path) or {}
    result = analyze_waveform(
        time_s,
        voltage_v,
        metadata=metadata,
        drop_threshold_v=drop_threshold_v,
        drop_window_s=drop_window_s,
        merge_gap_s=merge_gap_s,
        coarse_step_s=DEFAULT_COARSE_STEP_S,
        scope_bw_hz=scope_bw_hz,
        capacitance_f=capacitance_f,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    write_events_csv(out_dir / f"{npz_path.stem}_events.csv", result.events)
    write_summary_json(out_dir / f"{npz_path.stem}_summary.json", result, npz_path)
    if METRICS_SRC.exists():
        shutil.copy2(METRICS_SRC, out_dir / "METRICS.md")
    save_figures(result, out_dir, include_first, show)
    return result


def main() -> None:
    default_npz = latest_capture(campaign_data(CAMPAIGN_SPARK_GAP))

    parser = argparse.ArgumentParser(
        description="Detect spark-gap breakdowns in a captured NPZ waveform and write diagnostics."
    )
    parser.add_argument(
        "npz",
        nargs="?",
        type=Path,
        default=default_npz,
        help="Path to .npz file from tools/capture_waveform.py (default: latest in this campaign Data/)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: Measurements/<campaign>/Data/plots/analysis_<stem>/)",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Do not open interactive plot windows",
    )
    parser.add_argument(
        "--include-first",
        action="store_true",
        help="Include first-cycle / atypical ramps in typical histograms and overlays",
    )
    parser.add_argument(
        "--drop-threshold",
        type=float,
        default=DEFAULT_DROP_THRESHOLD_V,
        help="Coarse-pass voltage drop threshold in volts (default: 5000)",
    )
    parser.add_argument(
        "--drop-window",
        type=float,
        default=DEFAULT_DROP_WINDOW_S,
        help="Coarse-pass drop window in seconds (default: 100e-9)",
    )
    parser.add_argument(
        "--merge-gap",
        type=float,
        default=DEFAULT_MERGE_GAP_S,
        help="Merge hits closer than this many seconds (default: 5e-6)",
    )
    parser.add_argument(
        "--scope-bw",
        type=float,
        default=DEFAULT_SCOPE_BW_HZ,
        help="Scope analog bandwidth in Hz (default: 100e6)",
    )
    parser.add_argument(
        "--capacitance",
        type=float,
        default=None,
        help="Optional gap/load capacitance in farads for energy and L estimates",
    )
    args = parser.parse_args()

    if args.npz is None or not args.npz.exists():
        raise SystemExit(f"File not found: {args.npz}")

    out_dir = args.out_dir
    if out_dir is None:
        campaign = infer_campaign(args.npz) or CAMPAIGN_SPARK_GAP
        out_dir = campaign_plots(campaign) / f"analysis_{args.npz.stem}"

    result = run_analysis(
        args.npz,
        out_dir,
        include_first=args.include_first,
        show=not args.no_show,
        drop_threshold_v=args.drop_threshold,
        drop_window_s=args.drop_window,
        merge_gap_s=args.merge_gap,
        scope_bw_hz=args.scope_bw,
        capacitance_f=args.capacitance,
    )
    print_report(result)
    print(f"\nWrote analysis to {out_dir}")


if __name__ == "__main__":
    main()
