# Rigol MHO954 — LAN Interfacing Cheat Sheet

Quick reference for the MHO900-series MHO954 (12-bit, 500 MHz, 4 analog channels).

**Lab instrument:** Rigol MHO954 @ `192.168.147.154`  
**Identity:** `RIGOL TECHNOLOGIES,MHO954,MHO9B280501047,00.01.00`

Bench IP lives in `instruments/lab.json` under `connections.rigol_mho954`. Default role stays the MSO1104; switch with `DEFAULT_OSCILLOSCOPE` in `instruments/registry.py` or `--scope rigol_mho954`.

---

## Connection

| Transport | Resource | Notes |
|-----------|----------|-------|
| VXI-11 | `TCPIP0::192.168.147.154::INSTR` | Prefer this (same as MSO1104) |
| Raw socket | `TCPIP0::192.168.147.154::5555::SOCKET` | Set `\n` read/write terminations |

```powershell
python tools\test_instruments.py --scope rigol_mho954 --skip-generator
python instruments\oscilloscopes\rigol_mho954\test.py
```

`*OPT?` is not implemented on firmware `00.01.00` (command error / timeout).

---

## Screen and bandwidth

- Graticule: **10** horizontal × **8** vertical divisions (MSO1104Z is 12×8).
- Analog BW: **500 MHz** with 1–2 channels on; **400 MHz** with 3–4 channels on.
- Timebase: 500 ps/div to 500 s/div. Vertical (1 MΩ, 1X): 1 mV/div to 10 V/div.

For frequency-response sweeps use `--scope rigol_mho954 --scope-bw 500e6` (or `400e6` if three or four channels are enabled).

---

## Measurements

Use `:MEASure:ITEM`, not `:MEASure:VPP?` (the latter is a command error on this firmware).

```
:MEASure:ITEM VPP,CHANnel1
:MEASure:ITEM? VPP,CHANnel1
:MEASure:ITEM? FREQuency,CHANnel1
```

Invalid readings come back as `9.9e37`. Trigger source write form is `CHANnelN` (query returns `CHANN`).

---

## Waveform download

RAW memory, **WORD** format (2 bytes/point, little-endian uint16). YREF is **32768**.

Voltage: `(word - YORigin - YREFerence) × YINCrement`  
In WORD mode YINCrement is typically `V/div / 7500`.

```
:STOP
:WAVeform:SOURce CHANnel1
:WAVeform:MODE RAW
:WAVeform:FORMat WORD
:WAVeform:STARt 1
:WAVeform:STOP <n>
:WAVeform:PREamble?
:WAVeform:DATA?
```

- `:WAVeform:DATA?` returns an empty IEEE block (`#9000000000`) if memory was never filled. **RUN or SINGLE**, then STOP, then download.
- Do not raise memory depth in the capture path; download whatever `:ACQuire:MDEPth?` currently is.
- Chunk large records (driver default 250000 points ≈ 500 kB/chunk).
- Programming guide: [MHO900 Programming Guide](https://www.batronix.com/files/Rigol/Oszilloskope/MHO900/Manual/English/MHO900_ProgrammingGuide_EN.pdf)
