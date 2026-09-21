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

The left pane lists captures under `Measurements/` (waveforms, videos, and
CSV tables). Search filters by name, campaign, and sidecar metadata. Analyses
that only accept waveforms still *show* camera clips and frequency-response
tables, but Open / double-click is disabled for those rows. **Use other
folder…** points the catalogue at an external tree with the same layout;
**Use local** returns to this repo.

Right-click a capture to reveal it in Finder or copy its path.

Measurement tools are listed but disabled. They will ship later on lab
computers (master branch, Windows).

## Notes

- This venv must not install `pyvisa` or import `instruments`.
- Trace plotting and spark-gap analysis arrive in later phases.
