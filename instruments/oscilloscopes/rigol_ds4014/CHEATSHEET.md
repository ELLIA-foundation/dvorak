# Rigol DS4014 — LAN Interfacing Cheat Sheet

Quick reference for the DS4000-series DS4014 (8-bit, 100 MHz, 4 analog channels).

**Lab instrument:** Rigol DS4014 @ `192.168.147.122`  
**Identity:** `RIGOL TECHNOLOGIES,DS4014,DS4A161550158,00.02.03`

Bench IP lives in `instruments/lab.json` under `connections.rigol_ds4014`. The default role stays the MSO1104. Select this scope with `--scope rigol_ds4014` or `OscilloscopeId.DS4014`.

Verified on firmware **00.02.03**. Waveform download needs that firmware or newer. Do not send a device clear (`*CLS` / `viClear`); this firmware can hang the session.

---

## Connection

| Transport | Resource | Notes |
|-----------|----------|-------|
| VXI-11 | `TCPIP0::192.168.147.122::INSTR` | Prefer this |
| Raw socket | `TCPIP0::192.168.147.122::5555::SOCKET` | Set `\n` read/write terminations |

```powershell
python tools\test_instruments.py --scope rigol_ds4014 --skip-generator --skip-camera
python instruments\oscilloscopes\rigol_ds4014\test.py
```

---

## Screen and bandwidth

- Graticule: **14** horizontal × **8** vertical divisions.
- `*IDN?` still says `DS4014`. The front panel is unlocked to the **DS4054** limits: timebase down to **1 ns/div** (500 ps is rejected), and bandwidth limit `20M`, `100M`, `200M`, or `OFF`. A stock DS4014 stops at 5 ns/div and only offers a 20 MHz limit. With the limit `OFF`, treat the analog bandwidth as **500 MHz** and pass `--scope-bw 500e6` on a frequency sweep. The campaign default of 100 MHz is the MSO1104.
- Sample rate at 1 ns/div with one channel on is **4.000 GSa/s** (56 points in auto memory: 4 GSa/s × 1 ns/div × 14). That is the series maximum. The unlock does not raise the ADC rate.
- Vertical at 1X: 1 mV/div to **5 V/div** into 1 MΩ, or to **1 V/div** into 50 Ω. The driver clamps to that range. An out-of-range V/div write can desync the session.
- Memory up to 140 Mpts (one channel).
- Averages: 1 (normal acquire) or a power of two from 2 to 8192.

---

## Measurements

Use the direct measure queries. `:MEASure:ITEM` is not this series.

```
:MEASure:VPP? CHANnel1
:MEASure:VMIN? CHANnel1
:MEASure:VMAX? CHANnel1
:MEASure:VAVG? CHANnel1
:MEASure:FREQuency? CHANnel1
:MEASure:RPHase? CHANnel2,CHANnel1
```

Invalid readings come back as `9.9e37`. Trigger status while running is `TD` (triggered), `WAIT`, or `AUTO`. Stopped is `STOP`.

---

## Waveform download

BYTE format (1 byte/point). YREF is **127**. YINCrement is **V/div / 32**.

Voltage: `(byte - YORigin - YREFerence) × YINCrement`

On-screen (NORMal), 1400 points, `XINCrement = time/div / 100`:

```
:WAVeform:SOURce CHANnel1
:WAVeform:MODE NORM
:WAVeform:FORMat BYTE
:WAVeform:STARt 1
:WAVeform:STOP 1400
:WAVeform:PREamble?
:WAVeform:DATA?
```

Deep memory (RAW) only while stopped. `XINCrement = 1/sample rate`. Read a window with start/stop, then the begin/status loop. Each `:WAVeform:DATA?` is one buffer; status `IDLE` means that buffer is the end of the request.

```
:STOP
:WAVeform:SOURce CHANnel1
:WAVeform:MODE RAW
:WAVeform:FORMat BYTE
:WAVeform:STARt 1
:WAVeform:STOP 1000000
:WAVeform:RESet
:WAVeform:BEGin
:WAVeform:STATus?
:WAVeform:DATA?
:WAVeform:END
```

`:WAVeform:STARt` / `:WAVeform:STOP` without `BEGin` returns an empty block. Cap each request at 1e6 points and walk the record. `capture_channel(window="screen")` keeps the 14-division window at the raw sample rate; `window="full"` reads the whole memory. `read_screen` uses the 1400-point NORMal trace and then runs again.

Programming guide for this command set: [DS4000 programming guide](https://www.batronix.com/files/Rigol/Oszilloskope/_DS&MSO4000/MSO4000&DS4000-ProgrammingGuide.pdf) (firmware 00.02.03 command set, including `:WAVeform:BEGin`).
