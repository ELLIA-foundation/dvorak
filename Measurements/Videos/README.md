# Videos

Video campaigns for the Video Analysis pane. Each campaign is one folder of clips. Bench captures under `Measurements/<name>/Data/` stay there; copy a movie here when you want to watch or analyse it.

```
Measurements/Videos/
  Analysis_scripts/       shared extractor (added later)
  <campaign>/
    *.mp4, *.mov          local, gitignored
    data/<stem>.csv       chronograph, tracked, once extracted
    data/<stem>.json      local sidecar
    plots/                local figures
    cycle.json            solenoid timing, only if you run that analysis
```

Create a campaign by adding a folder and putting MP4 or MOV files directly in it, not inside `data/` or `plots/`. Refresh Video Analysis to see them.

Movies and plots are not committed. Chronograph CSVs are. JSON sidecars under `data/` stay on this machine.
