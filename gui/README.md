# Analysis and measurement GUI

PySide6 desktop app for this repository. It browses captures under
`Measurements/`, plots them with pyqtgraph and matplotlib, and can start a
bench measurement after the instrument answers `identify()`.

CERN ROOT is not used.

## Launch (Windows)

From the repository root:

```powershell
gui\bootstrap.cmd
gui\run.cmd
```

`bootstrap.cmd` and `run.cmd` do not depend on PowerShell execution policy.
If you prefer the PowerShell bootstrap:

```powershell
powershell -ExecutionPolicy Bypass -File gui\bootstrap.ps1
gui\run.cmd
```

The first run creates `gui/.venv` and installs PySide6, pyqtgraph, matplotlib,
and the bench packages (`pyvisa`, `pyvisa-py`, `numpy`).

Choose a tool in the launcher. Each choice opens its own window; the launcher
stays open. **File → New analysis window…** brings the launcher back.

## Data

The left pane lists captures under `Measurements/<Campaign>/Data/` (waveforms,
videos, and CSV / frequency-response tables). Search filters by name, campaign,
and sidecar metadata. Analyses that only accept waveforms still show camera
clips and tables, but Open / double-click is disabled for those rows.

**Use other folder…** points the catalogue at an external tree with the same
layout; **Use local** returns to this repo. Right-click a capture to reveal it
in Explorer or copy its path.

## Analyses

**Oscilloscope Trace Analysis** loads the NPZ on a background thread and plots
it with min-max downsampling. Hover for t / V; **Cursors** for Δt.
**View → Reset view** restores the full window. **File → Export plot…** writes
a PNG of the current view (defaults to `Data/plots/`).

**Spark Gap Analysis** uses the same plot. **Detect events** marks breakdowns
(orange typical, red first-cycle). **Run full analysis** writes
`Data/plots/analysis_<stem>/` (CSV, JSON, METRICS.md, figures 01–08, and
`analysis.pdf`). Tabs: Overview | Figures | Compose | Events | Summary.
**Figures** has Metrics (any event scalar as a sequence or histogram), Overlay
(discharge / ramp / post-collapse for chosen events), and Saved figures (the
PNG pack). **Compose** puts the same metric figure for several saved
measurements on one canvas. Detect events is enough to open Metrics and Overlay.
Metrics, Overlay, Compose, and frequency plots are drawn with matplotlib from
the same JSON figure specs the analysis code builds.

**Frequency response** plots a sweep (dB ratio and Vpp, log frequency).

**Video Analysis** plays clips stored under `Measurements/Videos/<campaign>/`
(MP4 or MOV, directly in the campaign folder). The left pane lists those
campaigns and whether each chronograph cache is fresh, stale, or missing.
Select a clip to play it and plot intensity versus time. Drag the green
rising-edge line to correct it. Shift-click overlays those clips. **Extract**
decodes the current campaign in the background. ffmpeg must be on `PATH`.

## Measurement

**Run measurement** lists the oscilloscope, generator, and camera from
`instruments/lab.json`. **Test connection** opens the instrument, calls
`identify()`, and closes it.

- Waveform capture stays disabled until the oscilloscope probe succeeds.
- The frequency sweep stays disabled until both the oscilloscope and the
  generator succeed.
- Camera record stays disabled until the camera probe succeeds. The NDI
  runtime is required only for that probe and for recording.

Captures use the same writers as the campaign CLIs and land in
`Measurements/<campaign>/Data/`. Refresh the catalogue in an analysis window
to open the new file.

## Add an analysis plugin

1. Create `gui/dvorak_gui/analyses/<id>.py`.
2. Call `register(...)`.
3. Import the module from `load_plugins()` in
   `gui/dvorak_gui/analyses/__init__.py`.
4. Do not import `instruments` or PyVISA from an analysis plugin. The
   measurement window is the place that talks to the bench.
