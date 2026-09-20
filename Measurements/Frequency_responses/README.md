# Frequency_responses

Generator-plus-oscilloscope frequency-response measurements.

**Ratio:** `V_scope / V_nominal`. By default the generator uses the **maximum**
sine Vpp allowed at each frequency (`max_sine_vpp`). Pass `--amplitude` to cap
that level instead. Limits (DG4202, 50 ohm): 10 Vpp to 20 MHz, 5 Vpp to 70 MHz,
2.5 Vpp to 120 MHz, 1 Vpp to 200 MHz (High-Z is 2x). `V_scope` is peak-to-peak
on one oscilloscope channel at the DUT/output node.

Default scope is `DEFAULT_OSCILLOSCOPE` (MSO1104Z, 100 MHz). Use `--scope rigol_mho954`
for the MHO954 (500 MHz with 1–2 channels on; 400 MHz with 3–4) and pass
`--scope-bw 500e6`. Points above `--scope-bw` are stored with `scope_limited: true`.

```powershell
python Measurements\Frequency_responses\Analysis_scripts\sweep_frequency_response.py --no-show
python Measurements\Frequency_responses\Analysis_scripts\sweep_frequency_response.py --f-min 1e3 --f-max 100e6 --points 41 --load 50 --no-show
python Measurements\Frequency_responses\Analysis_scripts\sweep_frequency_response.py --amplitude 1 --no-show
python Measurements\Frequency_responses\Analysis_scripts\sweep_frequency_response.py --scope rigol_mho954 --scope-bw 500e6 --no-show
```

- **Data/** — `freq_resp_<timestamp>.csv` and `.json`
- **Data/plots/** — `freq_resp_<timestamp>.png` (dB ratio plus V_nominal / V_scope)
- **Analysis_scripts/** — sweep protocol

Use `open_oscilloscope()` and `open_generator()`, not a specific model module.
