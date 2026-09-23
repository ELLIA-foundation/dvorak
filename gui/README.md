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
videos, and CSV / frequency-response tables). Search filters by name, campaign,
and sidecar metadata. Analyses that only accept waveforms still *show* camera
clips and tables, but Open / double-click is disabled for those rows.

**Use other folder…** points the catalogue at an external tree with the same
layout; **Use local** returns to this repo. Right-click a capture to reveal it
in Finder or copy its path.

New lab captures land on **master**. On this branch:

```bash
python tools/sync_measurements.py
```

That fetches `origin` and checks out each campaign `Data/` folder from
`origin/master`. NPZ and JSON are tracked in git; PNG, PDF, and MP4 stay
gitignored. The script refuses to run if those paths have local changes.
`--all` replaces the entire `Measurements/` tree (including
`Analysis_scripts`) and removes files that are not on that ref.
`--dry-run` prints the diff only.

## Analyses

**Oscilloscope Trace Analysis** loads the NPZ on a background thread and plots
it with min-max downsampling. Hover for t / V; **Cursors** for Δt.
**View → Reset view** restores the full window. **File → Export plot…** writes
a PNG of the current view (defaults to `Data/plots/`). **Legacy ROOT** and
**Save PDF…** send that same decimated view to ROOT; the live plot stays in
Qt so zooming a multi-million-point trace stays responsive.

**Spark Gap Analysis** uses the same plot. **Detect events** marks breakdowns
(orange typical, red first-cycle). **Run full analysis** writes
`Data/plots/analysis_<stem>/` (CSV, JSON, METRICS.md, figures 01–08,
`analysis.pdf`, and `root_figures.json`). Tabs: Overview | Figures | Compose |
Events | Summary. **Figures** has Metrics (any event scalar as a sequence or
histogram), Overlay (discharge / ramp / post-collapse for chosen events), and
Saved figures (the 02–08 pack after a full analysis). **Compose** puts the
same metric figure for several saved measurements on one canvas (for example
slew-rate histograms). Detect events is enough to open Metrics and Overlay.
**Legacy ROOT** and **Save PDF** apply to the current figure. Without ROOT,
Saved figures stays on the PNGs.

**Frequency response** plots a sweep (dB ratio and Vpp, log frequency) as a
ROOT canvas with the same Legacy ROOT button. **Camera clip** is listed but
disabled — the catalogue already indexes videos; a viewer comes later.

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
