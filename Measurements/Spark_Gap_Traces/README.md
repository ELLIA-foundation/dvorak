# Spark_Gap_Traces

High-voltage spark-gap captures from the bench oscilloscope.

Configure timebase and trigger on the scope, stop the acquisition, then run
the campaign script. It downloads the waveform through `open_oscilloscope()`,
saves NPZ + JSON, and writes the diagnostic figure pack.

Default scope is `DEFAULT_OSCILLOSCOPE` (MSO1104Z, 100 MHz). Use
`--scope rigol_mho954` for the MHO954 and pass `--scope-bw 500e6`. Edit the
`DEFAULT_*` block at the top of `analyze_spark_gap.py`, or override on the CLI.

```powershell
python Measurements\Spark_Gap_Traces\Analysis_scripts\analyze_spark_gap.py --no-show
python Measurements\Spark_Gap_Traces\Analysis_scripts\analyze_spark_gap.py --window full --no-show
python Measurements\Spark_Gap_Traces\Analysis_scripts\analyze_spark_gap.py --scope rigol_mho954 --scope-bw 500e6 --no-show
python Measurements\Spark_Gap_Traces\Analysis_scripts\analyze_spark_gap.py --npz --no-show
```

Default `--window screen` downloads only the 12-div view after matching `:MEASure` VMIN/VMAX (and the on-screen NORM trace when available). `--window full` keeps all deep memory but still requires that the screen slice verify.

`--npz` skips the instrument and re-analyzes an existing capture (latest in
`Data/` if no path is given). Generic `tools/capture_waveform.py` remains
available for other campaigns.

Use `open_oscilloscope()`, not a specific model module.

- **Data/** — raw `waveform_*.npz` plus JSON sidecars
- **Data/plots/** — overview PNGs and `analysis_<stem>/` figure packs
- **Analysis_scripts/** — campaign CLI (`analyze_spark_gap.py`) and event detection (`spark_gap.py`)

Metrics glossary: [SPARK_GAP_METRICS.md](Analysis_scripts/SPARK_GAP_METRICS.md).
