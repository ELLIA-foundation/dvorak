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

Measurement tools are listed but disabled. They will ship later on lab
computers (master branch, Windows).

## Notes

- This venv must not install `pyvisa` or import `instruments`.
- Workspaces are placeholders in Phase 0. Trace viewing and spark-gap analysis
  arrive in later phases.
