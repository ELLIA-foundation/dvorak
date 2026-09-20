# Frequency_responses

Campaign folder for generator-plus-oscilloscope frequency-response work.

- Put capture files in `Data/`
- Put derived figures in `Data/plots/`
- Put analysis programs in `Analysis_scripts/`

Capture once the generator driver is implemented:

```powershell
python tools\capture_waveform.py --campaign Frequency_responses
```

Scripts in this campaign should call `open_oscilloscope()` and
`open_generator()`, not a specific model module.
