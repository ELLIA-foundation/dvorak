# Frequency_responses

Campaign folder for generator-plus-oscilloscope frequency-response work.

- Put capture files in `Data/`
- Put derived figures in `Data/plots/`
- Put analysis programs in `Analysis_scripts/`

Capture with the bench generator and scope:

```powershell
python tools\test_instruments.py --skip-scope
python tools\capture_waveform.py --campaign Frequency_responses
```

Scripts in this campaign should call `open_oscilloscope()` and
`open_generator()`, not a specific model module.
