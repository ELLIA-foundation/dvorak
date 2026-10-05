# Analysis GUI

Offline analysis front end for this repository. It lives on the **Analysis**
branch and does not talk to lab instruments.

## Launch (macOS)

From the repository root:

```bash
bash gui/run.sh
```

The first run creates `gui/.venv` and installs PySide6. After that the Dock /
Cmd-Tab label should read **Dvorak**, not Python.

Choose an analysis in the launcher. Each choice opens a dedicated window; the
launcher stays open so you can start more than one. **File → New analysis
window…** brings the launcher back.

## Data

The left pane lists captures under `Measurements/<Campaign>/Data/` (waveforms,
videos, and CSV / frequency-response tables). A file in `Data/<session>/` is
grouped under that session; files still in `Data/` stay directly under the
campaign. Search filters by name, campaign, session, and sidecar metadata.
Analyses that only accept waveforms still *show* camera clips and tables, but
Open / double-click is disabled for those rows.

**Use other folder…** points the catalogue at an external tree with the same
layout; **Use local** returns to this repo. Right-click a capture to reveal it
in Finder or copy its path.

New lab captures land on **master**. On this branch:

```bash
python tools/sync_measurements.py
```

That fetches `origin`, commits any GUI analysis artifacts under
`Data/plots/` and `Data/<session>/plots/` (CSV, JSON, METRICS.md,
`root_figures.json`), then checks out only the raw campaign `Data/` files
from `origin/master`. Plot outputs stay on this branch; PNG, PDF, MP4, and
MOV stay gitignored. `Measurements/X123_Spectra/` is left alone. Dirty raw
captures that do not already match the ref still block the run.
`--no-commit` skips the analysis commit. `--all` also updates
`Analysis_scripts` and the rest of `Measurements/`, but still leaves those
`plots/` folders and `X123_Spectra/` alone. `--dry-run` prints both steps
without changing files.

## Analyses

**Oscilloscope Trace Analysis** loads the NPZ on a background thread and plots
it with min-max downsampling. Hover for t / V; **Cursors** for Δt.
**View → Reset view** restores the full window. **File → Export plot…** writes
a PNG of the current view (defaults to the capture's `plots/` folder, so a
session capture uses `Data/<session>/plots/`). **Legacy ROOT** and
**Save PDF…** send that same decimated view to ROOT; the live plot stays in
Qt so zooming a multi-million-point trace stays responsive.

**Spark Gap Analysis** uses the same plot. **Detect events** marks breakdowns
(orange typical, red first-cycle). **Breakdown polarity** picks Positive (a
gap collapsing down from V > 0, the default), Negative (collapsing up from
V < 0), or Both; see `SPARK_GAP_METRICS.md`. A capture sampled more coarsely
than the drop window (e.g. 5 µs) cannot resolve a collapse, and the count
under the buttons says so. **Run full analysis** writes
`<capture>/plots/analysis_<stem>/` (CSV, JSON, METRICS.md, figures 01–08,
`analysis.pdf`, and `root_figures.json`), which is `Data/<session>/plots/`
when the waveform sits in a session and `Data/plots/` otherwise. Tabs: Overview | Figures | Compose |
Events | Summary. **Figures** has Metrics (any event scalar as a sequence or
histogram), Overlay (discharge / ramp / post-collapse for chosen events), and
Saved figures (the 02–08 pack after a full analysis). **Compose** puts several
saved measurements on one canvas. Histograms and sequences can be overlaid
on one axes or stacked as subplots; full waveforms use the same choice.
A stack of one metric takes a column count, so four histograms and 2 columns
is a 2×2 grid. Sequence overlays use each event's breakdown time.
Shift-click or Command-click pools the selected captures' saved events into
the Metrics histograms (the opened capture's live detection counts too), and
Compose → Sets pins each such selection as one pooled histogram. Detect
events is enough to open Metrics and Overlay.
**Legacy ROOT** and **Save PDF** apply to the current figure. Without ROOT,
Saved figures stays on the PNGs.

### ROOT figures

Figures, Compose, and Frequency response are ROOT canvases drawn by JSROOT.
Each is laid out for the pane it sits in and redrawn when the pane is
resized. A stack of subplots too tall for the pane scrolls instead of
shrinking; the mouse wheel then scrolls, and dragging on an axis zooms.

**Legacy ROOT** opens what the pane shows in `root -l`: the zoom, a dragged
legend, log axes, and titles or colors changed from the JSROOT menus, at the
pane's size (scaled down if it does not fit the screen). It is an ordinary
macro written by ROOT's `SaveAs(".C")` into a temporary `dvorak-legacy-*`
folder, so it can be edited and run again. **Save PDF** writes the same view.
ROOT starts with `ROOTSYS` set to the installation Dvorak found, so a
`.rootrc` that lists `$ROOTSYS/lib` keeps working.

Saved figures draws a screen copy of the 02–08 pack, with long traces
min-max decimated so every peak survives; `analysis.pdf` from Run full
analysis keeps full resolution. A panel with more than 16 labelled series
drops its legend rather than covering the data.

Labels reach ROOT as ASCII TLatex (`#mu`, `^{2}`, `#Delta`, `#ddot{a}`).
Interactive ROOT on macOS reads strings byte by byte, so raw UTF-8 such as
an em dash would show up as `‚Äî`.

**Frequency response** plots a sweep (dB ratio and Vpp, log frequency) as a
ROOT canvas with the same Legacy ROOT button. Sweeps that include
`vertical_status` mark below-floor and clipped points, and a `thd` column
adds a THD (%) panel.

**Video Analysis** plays clips stored under `Measurements/Videos/<campaign>/`
(MP4 or MOV, directly in the campaign folder). The left pane lists those
campaigns and whether each chronograph cache is fresh, stale, or missing.
Select a clip to play it and plot intensity versus time. Drag the green
rising-edge line to correct it. Shift-click or Command-click overlays those
clips, in raw time or aligned on each rising edge, as absolute intensity or
each trace divided by its own max. **Extract** decodes the current campaign
in the background. **Run as solenoid measurement** stores tube and current
times in that campaign's `cycle.json`, marks them on the chronograph,
and can show the current-off and current-on frames. Bench captures in
`Measurements/<campaign>/Data/` still show up in the other analyses'
catalogue; this window does not use that list.

**X-123 Spectra** lists sessions under
`Measurements/X123_Spectra/<session>/`. **New session…** creates
`<session>/Data/` and copies Amptek `.mca` files into it (or drop files
onto the list). **Refresh** picks up files pasted in by hand. Energy calibration is
applied on load from `calibration/energy.json`. Shift/Command-click overlays
spectra from any session. The plot has log Y, counts or cps or normalize,
a moving average (channels or keV), ROI stats, ΔE cursors, U L, Th/Bi/Ra,
common line markers, and difference vs a reference. Pin the selection
as a sum or a mean ± σ (sample standard deviation, or SEM) and overlay
those groups; **Split by phase** builds one group per pre, post, and
unlabeled set.
**File → Export plot…** writes a PNG of the current view. **Legacy ROOT** and
**Save PDF…** send that same view to ROOT; the live plot stays in Qt.

Measurement tools stay disabled. They will run on lab computers (Windows,
master branch): instrument connect plus the existing campaign / tools CLIs,
writing the same `Measurements/` tree.

## Add an analysis plugin

1. Create `gui/dvorak_gui/analyses/<id>.py`.
2. Call `register(...)`.
3. Import the module from `load_plugins()` in
   `gui/dvorak_gui/analyses/__init__.py`.
4. Do not import `instruments` or PyVISA.

```python
from ..kinds import KIND_TABLE
from ..registry import FAMILY_ANALYSIS, AnalysisSpec, register

register(
    AnalysisSpec(
        id="example_table",
        title="Example table",
        description="Opens frequency-response / CSV captures.",
        family=FAMILY_ANALYSIS,
        accepted_kinds=(KIND_TABLE,),
        # window_factory=...  # omit to use the catalogue + metadata pane
    )
)
```

Set `enabled=False` and `disabled_reason="..."` for a visible stub. A custom
window subclasses `AnalysisWindow` and implements `_workspace_panes` /
`_handle_opened` (see `trace.py`). Load campaign math with
`load_campaign_module(campaign, "module")` so you never import an acquisition
CLI.

## Add a capture kind

1. Add `KIND_*` (and its label) in `gui/dvorak_gui/kinds.py`.
2. Scan `Data/` for that pattern in `gui/dvorak_gui/catalog.py`.
3. Point the new analysis at `accepted_kinds=(KIND_*,)`.

The browser lists every kind even when no viewer exists yet.

## Notes

- This venv must not install `pyvisa` or import `instruments`.
