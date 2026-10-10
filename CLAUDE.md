# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Branches

- **master**: lab computers (Windows). Instrument drivers, capture CLIs, raw data capture.
- **Analysis**: offline PySide6 analysis GUI (`gui/`). Must never import `instruments` or `pyvisa`; it uses its own venv (`gui/.venv`, no PyVISA).

New raw captures arrive from master via `python tools/sync_measurements.py` (`--dry-run`, `--no-commit`, `--all`). It commits GUI analysis artifacts under `Data/plots/` and `Data/<session>/plots/` (CSV/JSON/METRICS.md/`root_figures.json`), then checks out only raw `Data/` files from `origin/master`. PNG/PDF/MP4/MOV are gitignored; `Measurements/X123_Spectra/` is left alone.

## Commands

```bash
pip install -r requirements.txt          # repo root (pyvisa, numpy, matplotlib); Python 3.12+
bash gui/run.sh                          # launch GUI; bootstraps gui/.venv on first run
python tools/test_instruments.py         # bench connection test (master, needs hardware)
python tools/capture_waveform.py --campaign <Name>
python tools/plot_waveform.py --campaign <Name> --no-show
python tools/capture_video.py --campaign <Name> --duration 3
python Measurements/Spark_Gap_Traces/Analysis_scripts/analyze_spark_gap.py --no-show
```

There is no test suite or linter configured.

## Architecture

- `instruments/`: hardware by **role** then **model**. Campaign and `tools/` code must call `open_oscilloscope()` / `open_generator()` / `open_camera()` from `instruments`, never import a model-specific driver. Which model fills each role, and the IPs, live in `instruments/lab.json`; defaults are flipped in `instruments/registry.py`. Scopes return `lib.waveform.WaveformCapture` (`time_s`, `voltage_v`, vendor fields in `extra`). Adding a model = driver + registry entry + `lab.json` block (see README).
- `lib/`: shared helpers (paths, waveform NPZ I/O, video sidecars, X-123 `spectrum.py`, Pixet/OPIXE reader `pixet.py`).
- `Measurements/<Campaign>/{Analysis_scripts,Data}`: raw captures are NPZ+JSON (waveforms) or MP4+JSON (video) in `Data/`; optional `Data/<session>/` subfolders; derived output goes in `plots/`. Never write campaign data next to a driver.
- `gui/dvorak_gui/`: the Qt app.
  - `registry.py`: `AnalysisSpec` + `register()`; plugins live in `analyses/` and are imported from `load_plugins()` in `analyses/__init__.py`. Custom windows subclass `AnalysisWindow` (`_workspace_panes` / `_handle_opened`; see `analyses/trace.py`).
  - `kinds.py` + `catalog.py`: capture kinds and the scan of `Data/` for each. Adding a kind = `KIND_*` in kinds.py, a scan pattern in catalog.py, then `accepted_kinds=` on the analysis.
  - Campaign-specific math is loaded with `load_campaign_module(campaign, "module")` from `Analysis_scripts/`, not by importing acquisition CLIs.
  - `widgets/`: reusable panes (trace/spectrum/dual-spectrum plots, galleries, browsers). Live plots are Qt with min-max downsampling.
- `gui/dvorak_root/`: ROOT figure generation (`figures.py`, `histread.py`, `style.py`, `macro.py`, `text.py`). Figures/Compose/Frequency response are ROOT canvases rendered by JSROOT in the GUI (`jsrootview.py`, `rootbridge.py`, `rootexport.py`); "Legacy ROOT" / "Save PDF" export the same view via `root -l`. Labels sent to ROOT must be ASCII TLatex (`#mu`, `^{2}`), since interactive ROOT on macOS mangles raw UTF-8.
- X-123 + Pixel overlay reads pixel spectra from OPIXE (`opixe-core list`, `derived.root`); it never produces them. Located via `$OPIXE_DATA_DIR` / `$PIXET_DIR` / `$OPIXE_CORE`.

Full user-facing behaviour of each analysis is documented in [gui/README.md](gui/README.md); spark-gap metric definitions in `Measurements/Spark_Gap_Traces/Analysis_scripts/SPARK_GAP_METRICS.md`; scope quirks in `instruments/oscilloscopes/*/CHEATSHEET.md`.
