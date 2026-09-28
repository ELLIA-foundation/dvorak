# Videos

Video campaigns for the Video Analysis pane. Each campaign is one folder of clips. Bench captures under `Measurements/<name>/Data/` stay there; copy a movie here when you want to watch or analyse it.

```
Measurements/Videos/
  Analysis_scripts/       chronograph extractor
  <campaign>/
    *.mp4, *.mov          local, gitignored
    data/<stem>.csv       chronograph, tracked, once extracted
    data/<stem>.json      local sidecar
    plots/                local figures
    cycle.json            solenoid timing, only if you run that analysis
```

Create a campaign by adding a folder and putting MP4 or MOV files directly in it, not inside `data/` or `plots/`. Refresh Video Analysis to see them.

Extract a chronograph (one row per frame: time and mean intensity, plus a local sidecar with the detected rising edge `t1_s`):

```bash
python3 Measurements/Videos/Analysis_scripts/extract_brightness.py Measurements/Videos/<campaign>
```

Pass `--force` to decode again. A fresh cache is skipped. The sidecar does not set tube or current on/off times. **Extract** in Video Analysis runs the same command for the campaign you are viewing.

**Run as solenoid measurement** writes `cycle.json` for that campaign only. Times are seconds from the start of the clip: tube on, current on, current off, tube off. Tube on starts from the detected rising edge when the clip has one. Per-clip fields override the campaign; an empty clip field keeps the campaign value. **3 s cycle** sets, from tube on, current on at +3 s, current off at +6 s, and tube off at +9 s. The chronograph then marks current off, current on, and current off again, and reports the mean intensity in each window (0.3 s in from each edge) and current-on minus the first current-off. **Off / on frames** averages up to 0.7 s in the middle of those two windows and shows the frames plus X and Y projections. It decodes only those stretches. When tube on is later than 0.05 s, a 1 s dark frame before tube on is subtracted.

Movies and plots are not committed. Chronograph CSVs and `cycle.json` are. JSON sidecars under `data/` stay on this machine.
