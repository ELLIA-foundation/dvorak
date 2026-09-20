# Lab measurement repository

Python tooling for lab instruments and measurement campaigns: interchangeable
oscilloscope and generator drivers, capture/plot CLIs, and campaign analysis
plus data.

## Layout

```
lib/                  Shared path helpers and waveform NPZ I/O
instruments/          Hardware by role, then model
  lab.json            Which model is on the bench, plus IPs
  oscilloscope.py     Oscilloscope ABC
  generator.py        SignalGenerator ABC
  registry.py         open_oscilloscope() / open_generator()
  oscilloscopes/      One folder per scope model
  generators/         One folder per generator model
tools/                Capture, plot, and connection tests (not model-specific)
Measurements/         One folder per campaign
  <Campaign>/
    Analysis_scripts/
    Data/             waveform_*.npz + .json
      plots/          Derived PNG/PDF and analysis_<stem>/
```

Campaigns and tools import **roles** (`open_oscilloscope()`, `open_generator()`),
never a specific model module. Swap the scope on the bench by editing
[`instruments/lab.json`](instruments/lab.json) or passing `--scope <model_id>`.

## Setup

```bash
pip install -r requirements.txt
```

Python 3.12+. Edit `instruments/lab.json` so the `oscilloscope` / `generator`
roles match the hardware on the network.

## Usage

```powershell
python tools\test_instruments.py
python tools\capture_waveform.py --campaign Spark_Gap_Traces
python tools\plot_waveform.py --campaign Spark_Gap_Traces --no-show
python Measurements\Spark_Gap_Traces\Analysis_scripts\analyze_spark_gap.py --no-show
```

MSO1104 connection quirks: [instruments/oscilloscopes/rigol_mso1104/CHEATSHEET.md](instruments/oscilloscopes/rigol_mso1104/CHEATSHEET.md).

Spark-gap metrics: [Measurements/Spark_Gap_Traces/Analysis_scripts/SPARK_GAP_METRICS.md](Measurements/Spark_Gap_Traces/Analysis_scripts/SPARK_GAP_METRICS.md).

A later generate-then-capture script looks like:

```python
from instruments import open_generator, open_oscilloscope

gen = open_generator()
scope = open_oscilloscope()
gen.set_waveform(1, "sine", 1e3, 1.0)
gen.output(1, True)
capture = scope.capture_channel(1)
```

DG4062 control: [instruments/generators/rigol_dg4062/README.md](instruments/generators/rigol_dg4062/README.md).

## Adding an oscilloscope

1. Create `instruments/oscilloscopes/<model_id>/driver.py` implementing `Oscilloscope`.
2. Register the class in [`instruments/registry.py`](instruments/registry.py).
3. Add a `connections.<model_id>.ip` block (and optionally switch `roles.oscilloscope`) in `lab.json`.
4. Keep model quirks and a CHEATSHEET in that folder. Capture still returns `time_s` / `voltage_v`.

## Adding a campaign

Create `Measurements/<Name>/Analysis_scripts/` and `Measurements/<Name>/Data/plots/`.
Point capture at it with `--campaign <Name>`.
