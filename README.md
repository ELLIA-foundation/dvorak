# Rigol MSO1104Z — Spark Gap Analysis

Python tooling for capturing and analyzing waveforms from a Rigol MSO1104Z oscilloscope over LAN, with a focus on spark gap discharge metrics.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

See [RIGOL_MSO1104/CHEATSHEET.md](RIGOL_MSO1104/CHEATSHEET.md) for scope connection and capture commands.

- `capture_waveform.py` — acquire waveform data from the scope
- `plot_waveform.py` — visualize captured waveforms
- `analyze_spark_gap.py` — compute spark gap metrics (see [SPARK_GAP_METRICS.md](RIGOL_MSO1104/SPARK_GAP_METRICS.md))

## Requirements

- Python 3.12+
- Rigol MSO1104Z on the lab network
