# Analysis scripts

- `record_waveform.py` — download the stopped oscilloscope trace into `Data/<session>/waveform_<name>_chN.npz` plus JSON. `--session` and `--name` are required; a new session name creates the folder. `--window screen` keeps the verified 12-div view; `--window full` keeps deep memory. Pass `--scope <model_id>` to pick a registered oscilloscope.

Instrument access goes through `open_oscilloscope()`.
