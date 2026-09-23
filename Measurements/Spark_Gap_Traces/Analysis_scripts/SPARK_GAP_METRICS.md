# Spark-gap waveform metrics

Glossary of every quantity computed by `analyze_spark_gap.py` from a captured
Rigol NPZ (`time_s`, `voltage_v`). Field names match the event CSV, the
`fields` object in `*_summary.json`, and the `SparkGapEvent` dataclass.

Storage units in files are SI (`s`, `V`, `V/s`, `Hz`, `J`, `C`, `A`, `H`).
Plots and the printed table use convenient prefixes (ms, ns, kV, kV/ms).

## How an event is found

A **two-pass** search is used. Local maxima are not used: on this instrument
the breakdown “peak” is a short plateau plus 200 V LSB chatter.

1. **Coarse pass.** On a 50 ns stride, flag samples where voltage falls by at
   least `drop_threshold_v` (default 1.5 kV) inside `drop_window_s` (default
   100 ns). Hits within `merge_gap_s` (default 5 µs) become one candidate.
2. **Full-resolution refine.** In a ±2 µs window, the collapse index is the
   start of the steepest ~20 ns drop. All times and voltages below are taken
   from that refined index, not from the coarse seed.

The first cycle of a capture is often a long high-voltage plateau rather than
a typical recharge. Those shots stay in the event table and the overview
plot, but they are excluded from “typical” histograms, overlays, and named
summary figures unless `--include-first` is set.

## Instrument limits

The MSO1104Z analog bandwidth is 100 MHz, so the scope-limited 10–90% edge is
about `0.35 / BW ≈ 3.5 ns`. Observed collapse times of ~8–17 ns are only
**partially resolved**. Treat every slew number as a **lower bound** on the
true gap collapse rate. Vertical resolution is `y_increment_v` (200 V on the
reference capture: 5 kV/div × 1000× probe). Collapse and slew figures carry
the footer:

`100 MHz scope, 1 GS/s, 200 V LSB; slew is a lower bound`

## Per-event time and voltage

- **`event_index`** (1) — 0-based order of detected collapses. Used to plot
  conditioning (does `v_breakdown` drift shot-to-shot?).
- **`first_cycle`** (bool) — True when the preceding ramp is much flatter
  than the later population (startup / long plateau).
- **`t_break`** (s) — Collapse-start time: first sample of the steepest ~20 ns
  drop. This is the event timestamp for all intervals. Not the 1-sample
  voltage maximum.
- **`v_breakdown` / V_bd** (V) — Median voltage over the ~20–50 ns plateau
  immediately before `t_break`. Repetitive breakdown voltage for that shot.
  Median, not max, so 200 V LSB chatter does not inflate the peak.
- **`v_undershoot` / V_min** (V) — Minimum in the ~200 ns after `t_break`.
  Includes inductive kick / probe-cable ringing, not necessarily the true gap
  residual.
- **`v_residual` / V_res** (V) — Median voltage after ringing has settled
  (~1–5 µs post-edge), at the start of the next charging ramp. Recovery /
  restart voltage seen by the next cycle.
- **`dv_collapse`** (V) — `v_breakdown - v_undershoot`. Total observed swing
  of that shot, including undershoot. Distinct from the 10–90 window used for
  slew.

## Repetition and jitter

- **`period_s` / T** (s) — `t_break[i] - t_break[i-1]`. Undefined for event 0.
  Relaxation-oscillator period (charge time plus collapse).
- **`rep_rate_hz`** (Hz) — `1 / period_s` per interval. Summary also reports
  `rep_rate_mean_hz = 1 / mean(T)` on typical events.
- **`period_jitter_s`** (s) — Sample standard deviation of `period_s` over the
  typical population.
- **`period_jitter_frac`** (1) — `std(T) / mean(T)`. Timing jitter figure for
  a repetitive gap; high values mean the gap is not a stable clock.

## Collapse dynamics (high-resolution window)

Collapse amplitude for the edge is `ΔV = v_pre - v_post`, where `v_pre` is
the pre-edge plateau and `v_post` is the first local minimum after the main
fall (or the 200 ns minimum if no clear local min).

- **`v10`**, **`v90`** (V) — `v_pre - 0.1 ΔV` and `v_pre - 0.9 ΔV`.
- **`t10`**, **`t90`** (s) — First crossings of those levels near `t_break`.
- **`t_collapse_10_90`** (s) — `t90 - t10`. Reported as “the discharge occurs
  over N ns”. IEC/scope convention for edge time; less sensitive to ringing
  than 0–100%.
- **`slew_collapse_mean`** (V/s) — `0.8 * ΔV / t_collapse_10_90`. The
  “X kV in Y ns” number, expressed as a rate. A lower bound set by bandwidth.
- **`slew_collapse_peak`** (V/s) — Maximum falling `|dV/dt|` over a 5 ns
  window on the edge. More aggressive than the 10–90 mean; still limited by
  scope rise time.
- **`t_ring`** (s) — Dominant post-collapse ring period in the 50–400 ns
  window (median peak spacing of the residual after a short moving mean). If
  capacitance `C` is later known, `L ≈ 1 / ((2π f_ring)² C)`.

## Charging ramp (medium-resolution interval)

Fit `V(t) = a + b t` by least squares on samples from **after residual
settle** (~5 µs after the previous breakdown, or the start of the record) to
**~100 ns before** this `t_break`. Do not use 1 µs first differences: 200 V
quantization makes those 0 or 200 V and invents a 200 kV/ms slope.

- **`charge_rate` / dV/dt_charge** (V/s) — Slope `b`. Source current into the
  stray/load capacitance is `I ≈ C * b` if `C` is known.
- **`charge_intercept`** (V) — Fit intercept `a`, used for overlay plots.
- **`charge_r2`** (1) — Coefficient of determination. Low R² flags a
  non-linear or interrupted ramp (first cycle, mid-ramp glitch, missed event).
- **`v_charge_start`**, **`v_charge_end`** (V) — Voltage at the fit-window
  ends (should track the previous `v_residual` and this `v_breakdown`).
- **`recovery_s`** (s) — Time from `t_break` until a short local slope matches
  the next-cycle charge rate. How long the gap/circuit stays in the post-spark
  transient.

## Population / summary statistics

Computed twice where relevant: **all** events and **typical** events
(`first_cycle == false`). Each scalar quantity `x` gets:

- **`n`**, **`mean`**, **`std`** (sample), **`median`**, **`min`**, **`max`**
- **`p05`**, **`p95`** — robust range without one-shot outliers
- **`cv`** — `std / mean` when `mean ≠ 0`

Applied to: `v_breakdown`, `v_residual`, `v_undershoot`, `dv_collapse`,
`period_s`, `charge_rate`, `t_collapse_10_90`, `slew_collapse_mean`.

Named summary fields (standard spark-gap figures of merit):

- **`v_bd_cv`** — `std(V_bd) / mean(V_bd)` on typical events. Gap stability:
  a well-conditioned, uniform gap has a small CV (a few percent); large CV
  means surface, pressure, or recovery scatter.
- **`recovery_ratio_mean`** — `mean(V_res / V_bd)`. How completely the
  voltage collapses before recharge. Near 0 is a hard short; a large fraction
  means the gap did not fully discharge or the probe/circuit offset is high.
- **`corr_vbd_period`** — Pearson correlation of `V_bd` vs preceding
  `period_s`. Positive: longer wait → higher voltage (weak source / statistical
  time lag). Near zero: breakdown is voltage-driven on this timescale.
- **`corr_vbd_charge_rate`** — Pearson of `V_bd` vs preceding `charge_rate`.
  Tests a charging-rate effect on apparent breakdown voltage.
- **`conditioning_slope`** (V/event) — Linear slope of `V_bd` versus
  `event_index` on typical events. Positive: gap is conditioning up;
  negative: degrading or heating.

## Optional energy / circuit parameters

Only when `--capacitance` (farads) is passed. Omitted rather than guessed.

- **`energy_j`** (J) — `0.5 * C * v_breakdown²`. Energy stored at breakdown,
  not necessarily energy in the arc.
- **`charge_c`** (C) — `C * (v_breakdown - v_residual)` if that ΔV is dumped.
- **`source_current_a`** (A) — `C * charge_rate` during the linear ramp.
- **`L_est_h`** (H) — `1 / ((2π / t_ring)² * C)` if `t_ring` is finite.

## Detection / quality metadata

Stored under `detection` in the summary JSON so a run is reproducible.

- **`drop_threshold_v`**, **`drop_window_s`**, **`merge_gap_s`**,
  **`coarse_step_s`** — coarse-pass settings.
- **`n_events`**, **`n_typical`**
- **`sample_rate_hz`**, **`sample_interval_s`**, **`y_increment_v`**,
  **`lsb_v`**
- **`scope_bw_hz`** (default 100e6), **`scope_t1090_limit_s`** (`0.35 / BW`)
- **`capacitance_f`** — user-supplied or `null`

## Figures

1. **Overview** — min-max decimated full trace with event markers and V_bd.
2. **Sequential stats** — V_bd, period, and charge rate vs event / time.
3. **Histograms** — V_bd, period, charge rate, t_10-90 with mean/std/CV.
4. **High-res overlay** — representative collapses aligned at the 10% crossing.
5. **High-res individuals** — the same events with 10/90 markers and slew text.
6. **Ramp overlay** — charging segments aligned at ramp start, plus the fit.
7. **Correlations** — V_bd vs period and V_bd vs charge rate.
8. **Post-collapse window** — aligned 400 ns after the edge (undershoot + ring).
