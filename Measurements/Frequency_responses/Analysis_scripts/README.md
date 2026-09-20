# Analysis scripts

- `sweep_frequency_response.py` — log-sweep the generator, measure one scope channel, write `Data/freq_resp_*.csv` / `.json` and a dB ratio plot. Pass `--scope rigol_mho954 --scope-bw 500e6` for the MHO954.

Instrument access goes through `open_oscilloscope()` / `open_generator()`.
