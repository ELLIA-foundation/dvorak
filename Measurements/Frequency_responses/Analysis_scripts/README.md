# Analysis scripts

- `sweep_frequency_response.py` — log-sweep the generator, tune the scope V/div from the trace height, measure one scope channel, and write `Data/freq_resp_*.csv` / `.json` plus a dB ratio plot with THD. `--input-channel` adds a generator-input channel scaled on its own; the sweep then also stores `V_out / V_in` and the scope phase. Pass `--scope rigol_mho954 --scope-bw 500e6` for the MHO954. `--averages` (1, 2, 4, …, 256) enables scope averaging and scales each post-timebase or V/div wait by one 8-cycle screen per average. `vertical_status` is `ok`, `below_floor`, or `clipped`. THD is a fraction from the on-screen sine fit and is NaN unless the status is `ok`. Stop from the GUI or with Ctrl+C; partial points are saved.
- `sine_metrics.py` — 1–2–5 V/div ladder and least-squares sine THD. No instrument imports.
- `frequency_plot.py` — figure spec shared by the sweep PNG and the GUI. Adds a measured-gain series and a phase panel when a dual-channel row has them, and a THD panel when any row has a finite `thd`.

Instrument access goes through `open_oscilloscope()` / `open_generator()`.
