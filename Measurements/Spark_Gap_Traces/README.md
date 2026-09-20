# Spark_Gap_Traces

High-voltage spark-gap captures from the bench oscilloscope.

- **Data/** — raw `waveform_*.npz` plus JSON sidecars
- **Data/plots/** — overview PNGs and `analysis_<stem>/` figure packs
- **Analysis_scripts/** — event detection (`spark_gap.py`, `analyze_spark_gap.py`)

Capture into this campaign:

```powershell
python tools\capture_waveform.py --campaign Spark_Gap_Traces
python Measurements\Spark_Gap_Traces\Analysis_scripts\analyze_spark_gap.py --no-show
```

Metrics glossary: [SPARK_GAP_METRICS.md](Analysis_scripts/SPARK_GAP_METRICS.md).
