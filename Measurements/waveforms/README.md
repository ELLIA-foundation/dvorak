# waveforms

General oscilloscope traces, grouped by session.

Configure timebase and trigger on the scope, stop the acquisition, then offload
the waveform. Each session is a folder under `Data/`. A new name creates that
folder. Captures land in `Data/<session>/waveform_<name>_chN.npz` plus a JSON
sidecar.

Default scope is `DEFAULT_OSCILLOSCOPE`. Pass `--scope <model_id>` for another
registered model.

```powershell
python Measurements\waveforms\Analysis_scripts\record_waveform.py --session bench --name check
python Measurements\waveforms\Analysis_scripts\record_waveform.py --session bench --name deep --window full
python Measurements\waveforms\Analysis_scripts\record_waveform.py --session bench --name mho --scope rigol_mho954
```

The measurement GUI defaults the waveform panel to this campaign. **New session…**
creates `Data/<session>/` before capture.

`tools/capture_waveform.py --campaign waveforms --session <session> --name <name>`
writes the same files.

Use `open_oscilloscope()`, not a specific model module.

- **Data/<session>/** — raw `waveform_*.npz` plus JSON sidecars
- **Data/<session>/plots/** — overview figures from the plot tool or the GUI
- **Analysis_scripts/** — offload CLI (`record_waveform.py`)
