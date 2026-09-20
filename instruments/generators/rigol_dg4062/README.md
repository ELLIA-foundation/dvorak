# Rigol DG4062

LAN driver for the DG4000-series dual-channel function generator.

Campaigns and `tools/` should use `open_generator()`, not this module.

**Bench IP:** `192.168.147.153` in [`instruments/lab.json`](../../lab.json)

## Quick start

```powershell
python tools\test_instruments.py --skip-scope
python instruments\generators\rigol_dg4062\test.py
```

```python
from instruments import open_generator

gen = open_generator()
try:
    print(gen.identify())
    gen.set_load(1, 50)                          # amplitude assumes 50 ohm
    gen.set_waveform(1, "sine", 1e3, 1.0, 0.0)  # 1 kHz, 1 Vpp, 0 V offset
    gen.output(1, True)
    print(gen.query_channel(1))
    gen.output(1, False)
finally:
    gen.close()
```

## Interface

| Method | What it does |
|--------|----------------|
| `identify()` | `*IDN?` |
| `set_waveform(ch, shape, f_hz, vpp, offset_v=0, phase_deg=0)` | Basic waveform via `:SOURceN:APPLy:...` |
| `set_load(ch, ohms)` | Output load; `math.inf` is High-Z |
| `output(ch, enabled)` | Front-panel output on/off |
| `query_channel(ch)` | Current shape, frequency, amplitude, offset, load, output |

Shapes: `sine`, `square`, `ramp` (triangle), `pulse`, `noise`, `dc`.

Noise ignores frequency. DC uses `offset_v` as the DC level. Pulse does not send a width (instrument default).

## Notes

- Displayed Vpp depends on the load setting. Set `set_load` to match the cable/termination before trusting amplitude.
- Amplitude range is about 10 Vpp to 20 MHz and 5 Vpp to 60 MHz (into High-Z; half of that into 50 Ω).
- Connection tries `TCPIP0::IP::INSTR` first, then `TCPIP0::IP::5555::SOCKET`. Use INSTR; SOCKET desyncs because `:OUTPutN?` returns an extra newline.
- This bench unit identifies as **DG4202** (`*IDN?`). Same DG4000 SCPI as the DG4062.
