# X-123 Spectra

Amptek X-123 Si-PIN energy spectra. There is no instrument driver: create a session in the analysis GUI (or paste `.mca` files into a session folder) and open **X-123 Spectra**.

## Add a session

```
Measurements/X123_Spectra/<SessionName>/Data/*.mca
```

In **X-123 Spectra**, choose **File → New session…** (or **New session…** beside the list). Name the session, add `.mca` files or a folder of them, and Create. The files are copied into that session's `Data/` folder and selected in the list. Dropping `.mca` files onto a session imports them there; dropping them on empty space starts a new session. Right-click a session and choose **Import MCA files…** to add more.

The same folder can be created by hand:

1. `mkdir -p Measurements/X123_Spectra/<SessionName>/Data`
2. Paste Amptek DPPMCA `.mca` files into that `Data/` folder (files dropped in the session folder itself also show up).
3. Click **Refresh** (or File → Refresh spectra).

The run label is the filename. Live time, real time, dead time, HV, and TEC come from the MCA header. Energy is applied on load from [`calibration/energy.json`](calibration/energy.json):

```
E(keV) = 0.0007 + 0.01466 × channel
```

Changing that file recalibrates every `.mca` on the next load. Do not convert MCA files to NPZ.

## Layout

```
Measurements/X123_Spectra/
  Analysis_scripts/          shared math, ODS import, overlay CLI
  calibration/energy.json    shared linear calibration
  Andras Measurements/Data/*.mca  shadowgraph geometry, glass, and vertical-detector runs
  Material_5min/Data/        ODS-only spectra (no MCA existed)
    spectrum_pre_*.npz
    spectrum_post_*.npz
```

`X123_Spectra` is a container, like `Videos`. Each subdirectory is a session (the same role as `Data/<session>/` under a spark-gap campaign).

## ODS-only import (once)

The material spreadsheet had no `.mca` files. The ore spreadsheet is the same two 1800 s spectra as the glass pre/post files in `Andras Measurements`. Re-run only if you need to rebuild the NPZ copies:

```bash
python3 Measurements/X123_Spectra/Analysis_scripts/import_andras.py \
  --source "/path/to/X-123 Andras data"
```

## Overlay CLI

```bash
python3 Measurements/X123_Spectra/Analysis_scripts/plot_spectrum.py --campaign "Andras Measurements" --smooth 5 --no-show
python3 Measurements/X123_Spectra/Analysis_scripts/plot_spectrum.py path/to/a.mca path/to/b.mca --lines --log
```

## GUI

**X-123 Spectra** lists sessions. **New session…** copies `.mca` files into the session `Data/` folder. Shift/Command-click overlays traces from any session. Controls: log Y, counts / cps / normalize, moving average (channels or keV), ROI (integral, centroid, FWHM), cursors (ΔE), U L, Th/Bi/Ra, and common line markers, difference vs a reference, PNG export. Pin the current selection as a co-added sum or a mean with a shaded sample standard deviation (or SEM) and overlay those groups. **Split by phase** makes one group for each pre, post, and unlabeled set in the selection. **Legacy ROOT** and **Save PDF…** send the current view to ROOT.
