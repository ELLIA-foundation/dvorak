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

`--averages` (GUI: **Averages**) sets scope acquire averages (`1` = normal). After
each timebase or V/div change the sweep waits `0.15 s + averages × 8 / f` so one
on-screen window can complete per average before the trace is judged.

At each frequency the scope walks its 1–2–5 V/div ladder until the sine spans
about 4–6 divisions, and centers the offset on the measured average. The first
point seeds that scale from `V_nominal / 5`; later points reuse the last good
scope Vpp. A below-floor point seeds the next point at that same floor scale. Each row stores `v_div` and `vertical_status`:

- `ok` — span is about 4–6 divisions, or already 1 mV/div with at least 2 divisions
- `below_floor` — finite span still under 2 divisions at 1 mV/div
- `clipped` — still on the screen rails at 10 V/div, or the extrema stay invalid there

An invalid `VMIN`/`VMAX` (the scope's `9.9e37`) is not treated as a small signal. The sweep reads the visible trace once: samples on the rails coarsen, and a short finite trace still fines. Repeated off-screen readings then take larger steps up the ladder. A below-floor seed is armed only when that point has a finite Vpp.

On `ok` points the sweep downloads the visible trace and stores sine **THD** as a
fraction: `sqrt(V2² + … + VH²) / V1`, fit at the commanded frequency. `H` is at
most 10 and stops below 40% of the screen sample rate. Clipped and below-floor
points store NaN so noise is not reported as distortion.

Stop a running sweep from the GUI (**Stop sweep**) or with Ctrl+C on the CLI.
The generator output is turned off; any points already measured are written as
a partial run (`cancelled: true` in the JSON).

```powershell
python Measurements\Frequency_responses\Analysis_scripts\sweep_frequency_response.py --no-show
python Measurements\Frequency_responses\Analysis_scripts\sweep_frequency_response.py --f-min 1e3 --f-max 100e6 --points 41 --load 50 --no-show
python Measurements\Frequency_responses\Analysis_scripts\sweep_frequency_response.py --amplitude 1 --no-show
python Measurements\Frequency_responses\Analysis_scripts\sweep_frequency_response.py --scope rigol_mho954 --scope-bw 500e6 --no-show
python Measurements\Frequency_responses\Analysis_scripts\sweep_frequency_response.py --averages 16 --no-show
```

- **Data/** — `freq_resp_<timestamp>.csv` and `.json`
- **Data/plots/** — `freq_resp_<timestamp>.png` (dB ratio, V_nominal / V_scope, and THD when present)
- **Analysis_scripts/** — sweep protocol

Use `open_oscilloscope()` and `open_generator()`, not a specific model module.
