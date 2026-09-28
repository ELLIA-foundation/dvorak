# X-123 Spectra

Amptek X-123 Si-PIN energy spectra. There is no instrument driver: paste `.mca` files into a nested campaign and open **X-123 Spectra** in the analysis GUI.

## Add a campaign

```
Measurements/X123_Spectra/<CampaignName>/Data/*.mca
```

1. `mkdir -p Measurements/X123_Spectra/<CampaignName>/Data`
2. Paste Amptek DPPMCA `.mca` files into that `Data/` folder (files dropped in the campaign folder itself also show up).
3. Open **X-123 Spectra** and click **Refresh** (or File → Refresh spectra).

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
  Glass_1800s/Data/*.mca     1800 s pre/post glass (from Andras)
  Material_5min/Data/        ODS-only spectra (no MCA existed)
    spectrum_pre_*.npz
    spectrum_post_*.npz
```

`X123_Spectra` is a container, like `Videos`. Each subdirectory is a campaign.

## ODS-only import (once)

The material spreadsheet had no `.mca` files. The ore spreadsheet is the same two spectra as `Glass_1800s`. Re-run only if you need to rebuild the NPZ copies:

```bash
python3 Measurements/X123_Spectra/Analysis_scripts/import_andras.py \
  --source "/path/to/X-123 Andras data"
```

## Overlay CLI

```bash
python3 Measurements/X123_Spectra/Analysis_scripts/plot_spectrum.py --campaign Glass_1800s --smooth 5 --no-show
python3 Measurements/X123_Spectra/Analysis_scripts/plot_spectrum.py path/to/a.mca path/to/b.mca --lines --log
```

## GUI

**X-123 Spectra** lists nested campaigns. Shift/Command-click overlays traces from any campaign. Controls: log Y, counts / cps / normalize, moving average (channels or keV), ROI (integral, centroid, FWHM), cursors (ΔE), U L and common line markers, mean ± std by phase, difference vs a reference, PNG export.
