# Rigol MSO1104Z — LAN Interfacing Cheat Sheet

Quick reference for remote control of the MSO1104Z (MSO1000Z / DS1000Z series) over Ethernet using Python and SCPI.

**Lab instrument:** Rigol MSO1104Z @ `192.168.147.110`  
**Verified connection:** `TCPIP0::192.168.147.110::5555::SOCKET` via `pyvisa-py`

---

## 1. Architecture

```
PC (Python + PyVISA)  ──TCP/SCPI──►  Scope rear LAN port
                                      └── Port 5555: raw SCPI socket
                                      └── Port 111:  VXI-11 (INSTR)
```

- Protocol: **SCPI** (Standard Commands for Programmable Instruments)
- Transport: **VISA** over TCP/IP
- Python stack: `pyvisa` + `pyvisa-py` (no NI-VISA required for LAN)

---

## 2. Scope Network Setup (Front Panel)

Path: **Utility → IO Setting → LAN Conf.**

### Enable LAN remote control

Path: **Utility → IO Setting → RemoteIO**

| Condition | LAN state |
|-----------|-----------|
| USB connected + USB Device = "Computer" | LAN forced **OFF** (USB takes priority) |
| LAN only (no USB, or USB not in Computer mode) | LAN **ON** by default |

> **Tip:** Unplug USB or avoid USB Device = "Computer" when using LAN control.

### Static IP (recommended for lab use)

1. **Utility → IO Setting → LAN Conf.**
2. **Configure** → select **Static IP**
3. Turn **OFF** DHCP and Auto IP (required — see priority below)
4. Set **IP Address**, **Mask**, **Gate** (and **DNS** if needed; usually optional)
5. Press **Apply** (settings are not active until Apply is pressed)

| Field | Example (this lab) |
|-------|--------------------|
| IP Address | `192.168.147.110` |
| Subnet Mask | `255.255.255.0` |
| Gateway | `192.168.147.1` (confirm with network admin) |

### IP mode priority (if multiple modes enabled)

**DHCP → Auto IP → Static IP**

Static IP is ignored if DHCP or Auto IP wins. For reliable static setup: disable DHCP and Auto IP.

### Network status messages

| Message | Meaning |
|---------|---------|
| Net Config Success! | Ready for remote control |
| Acquire IP... | Still obtaining address |
| IP Conflict! | Duplicate IP on network |
| Unconnected! | No cable / no link |
| DHCP Fail! | No DHCP server response |

### Persist settings after power cycle

**Utility → System → Power-off Recall → Last**

Static IP/mask/gateway are stored in non-volatile memory when DHCP and Auto IP are off.

### Verify on scope

- **Current IP** and **VISA Address** shown on LAN Conf. screen
- VISA address format: `TCPIP::192.168.147.110::INSTR`
- LXI web page: open `http://192.168.147.110` in a browser

---

## 3. PC Setup

### Requirements

- PC on same subnet as scope (e.g. `192.168.147.x`, mask `255.255.255.0`)
- Python 3.9+
- Packages: `pip install pyvisa pyvisa-py`

### Quick connectivity checks

```powershell
ping 192.168.147.110
python tools\test_instruments.py --skip-generator
python instruments\oscilloscopes\rigol_mso1104\test.py
```

The bench IP is set in `instruments/lab.json` under `connections.rigol_mso1104`.

### VISA resource strings

| Type | Resource string | Notes |
|------|-----------------|-------|
| Raw SCPI socket | `TCPIP0::192.168.147.110::5555::SOCKET` | Fine for `*IDN?` / status; set `\n` terminations |
| VXI-11 / LXI (required for RAW waveform) | `TCPIP0::192.168.147.110::INSTR` | Use this for `:WAVeform:DATA?` |

---

## 4. Python Connection Pattern

See `test.py` or `tools/test_instruments.py` for a working example. Minimal pattern:

```python
import pyvisa

SCOPE_IP = "192.168.147.110"
RESOURCE = f"TCPIP0::{SCOPE_IP}::5555::SOCKET"

rm = pyvisa.ResourceManager("@py")
scope = rm.open_resource(RESOURCE)
scope.timeout = 5000
scope.write_termination = "\n"
scope.read_termination = "\n"

print(scope.query("*IDN?"))  # identity check

scope.close()
rm.close()
```

### `*IDN?` response format

```
RIGOL TECHNOLOGIES,MSO1104Z,DS1ZC170900250,00.04.02.SP4
                 model    serial number    firmware
```

---

## 5. Essential SCPI Commands

SCPI keywords are case-insensitive; Rigol uses mixed case in docs (e.g. `:CHANnel1`).

### System / transport

| Command | Description |
|---------|-------------|
| `*IDN?` | Identify instrument |
| `*RST` | Reset to default settings |
| `*CLS` | Clear status |
| `*OPC?` | Wait until operation complete (returns `1`) |

### Acquisition / run control

| Command | Description |
|---------|-------------|
| `:RUN` | Start continuous acquisition |
| `:STOP` | Stop acquisition |
| `:SINGle` | Single trigger |
| `:AUToscale` | Auto-scale all channels |
| `:CLEar` | Clear waveform data |

### Vertical (per channel N = 1..4)

| Command | Example | Description |
|---------|---------|-------------|
| `:CHANnelN:DISPlay` | `:CHANnel1:DISPlay ON` | Show/hide channel |
| `:CHANnelN:SCALe` | `:CHANnel1:SCALe 0.5` | V/div |
| `:CHANnelN:OFFSet` | `:CHANnel1:OFFSet 0` | Offset (V) |
| `:CHANnelN:COUPling` | `:CHANnel1:COUPling DC` | DC / AC / GND |
| `:CHANnelN:PROBe` | `:CHANnel1:PROBe 10` | Probe attenuation ratio |
| `:CHANnelN:DISPlay?` | | Query on/off |
| `:CHANnelN:SCALe?` | | Query V/div |

### Horizontal (timebase)

| Command | Example | Description |
|---------|---------|-------------|
| `:TIMebase:MAIN:SCALe` | `:TIMebase:MAIN:SCALe 0.001` | s/div (1 ms/div) |
| `:TIMebase:MAIN:OFFSet` | `:TIMebase:MAIN:OFFSet 0` | Time offset (s) |
| `:TIMebase:MAIN:SCALe?` | | Query s/div |

### Trigger

| Command | Example | Description |
|---------|---------|-------------|
| `:TRIGger:MODE` | `:TRIGger:MODE EDGE` | Edge trigger mode |
| `:TRIGger:EDGe:SOURce` | `:TRIGger:EDGe:SOURce CHAN1` | Trigger source |
| `:TRIGger:EDGe:SLOPe` | `:TRIGger:EDGe:SLOPe POSitive` | Rising / falling |
| `:TRIGger:EDGe:LEVel` | `:TRIGger:EDGe:LEVel 1.5` | Trigger level (V) |
| `:TRIGger:STATus?` | | STOP / RUN / T'D |

### Measurements

| Command | Example | Description |
|---------|---------|-------------|
| `:MEASure:ITEM?` | `:MEASure:ITEM? VPP,CHANnel1` | Peak-to-peak voltage |
| `:MEASure:ITEM?` | `:MEASure:ITEM? FREQuency,CHANnel1` | Frequency |
| `:MEASure:ITEM?` | `:MEASure:ITEM? VAVG,CHANnel1` | Average voltage |
| `:MEASure:ITEM?` | `:MEASure:ITEM? VRMS,CHANnel1` | RMS voltage |

### Waveform download

Use `python tools\capture_waveform.py --campaign Spark_Gap_Traces` (optional `--scope rigol_mso1104`). Verified on this instrument: **2,400,000 points at 1 ns** from a stopped single-shot, vs **600 points** from on-screen / manual CSV.

| Mode | What you get | Typical points |
|------|----------------|----------------|
| **NORM** | On-screen trace (same as manual CSV) | ~600 |
| **RAW** | Full acquisition memory | `:ACQ:MDEP?`, or `SRAT × time/div × 12` if AUTO |

Practical rules for this MSO1104Z:

- Use **VXI-11** (`TCPIP0::IP::INSTR`) for RAW downloads. Port **5555 SOCKET** works for `*IDN?` but hangs on `:WAVeform:DATA?` once RAW is active.
- Do **not** use `:WAVeform:STATus?` — it is not implemented and times out.
- Do **not** use `*OPC?` after `:STOP` — it can hang.
- `:WAVeform:MODE RAW` only sticks after you also set `:WAVeform:STARt` / `:STOP`.
- `:WAVeform:PRE?` `points` is the current start/stop **window**, not total memory.
- Request STOP from the largest plausible window (`PRE` xinc, 1 GSa/s, then `:ACQ:SRAT?`). Asking far above the real depth can clamp to 2.4 Mpts — treat that as a failed overshoot and try the next candidate.
- BYTE format max per read is **250000** points; chunk larger records.
- RAW preamble `YINCrement` / `YREFerence` / `XORigin` are untrustworthy. Always use `YINC = CHANnel:SCALe/25` and YREF=127.
- Screen window: `t_left/right = OFFSet ± 6 × time/div`. Map that onto deep memory (`:WAVeform:STARt` / `STOP`) assuming trigger-centered or screen-centered records. Default `window="screen"` downloads only that slice.
- Verification is required: RAW extrema must match `:MEASure` VMIN/VMAX (and NORM correlation if the on-screen download succeeded). Unverified data is refused, not stretched.
- Voltage: `(byte - YORigin - YREFerence) × YINCrement`
- Time: `(index - XREFerence) × XINCrement + XORigin`

Default save format is compressed **NPZ** plus a JSON sidecar (`--csv` optional) under `Measurements/<campaign>/Data/`.

### Screenshot (PNG over SCPI)

```python
data, fmt = scope.screenshot()  # this firmware returns BMP even if PNG is requested
```

### LAN configuration via SCPI (alternative to front panel)

| Command | Description |
|---------|-------------|
| `:LAN:DHCP OFF` | Disable DHCP |
| `:LAN:AUToip OFF` | Disable Auto IP |
| `:LAN:MANual ON` | Enable static IP mode |
| `:LAN:IPADdress 192.168.147.110` | Set IP |
| `:LAN:SMASk 255.255.255.0` | Set subnet mask |
| `:LAN:GATeway 192.168.147.1` | Set gateway |
| `:LAN:APPLy` | Apply network settings |
| `:LAN:STATus?` | UNLINK / INIT / IPCONFLICT / CONFIGURED / DHCPFAILED |
| `:LAN:VISA?` | Query VISA address string |
| `:LAN:MAC?` | Query MAC address |

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Connection timeout | Wrong IP, cable, or subnet | Check ping; verify scope Current IP on front panel |
| `IP Conflict!` on scope | Duplicate static IP | Assign unused IP; check with network admin |
| LAN queries fail but ping works | USB in Computer mode | Unplug USB or change USB Device setting |
| Static IP ignored | DHCP/Auto IP still on | Disable both; press Apply |
| `pyvisa` errors on import | Packages not installed | `pip install pyvisa pyvisa-py` |
| SOCKET reads hang | Missing terminations, or RAW binary on port 5555 | Use `\n` terminations for queries; use `::INSTR` for RAW waveform |
| RAW `:WAVeform:DATA?` times out on SOCKET | Firmware/transport bug on this scope | Open `TCPIP0::IP::INSTR` instead |
| INSTR fails, simple queries work | VXI-11 blocked | SOCKET is fine for `*IDN?` and status; not for RAW data |

---

## 7. Reliability Checklist

Before relying on automated scripts:

- [ ] Scope shows **Net Config Success!**
- [ ] Static IP configured; DHCP and Auto IP **off**
- [ ] **Apply** pressed after any network change
- [ ] PC and scope on same subnet
- [ ] `ping 192.168.147.110` succeeds
- [ ] `python tools\test_instruments.py --skip-generator` returns a valid `*IDN?`
- [ ] USB not overriding LAN (if using LAN only)

---

## 8. Official Documentation

Download latest from [rigol.com](https://www.rigol.com):

| Document | Contents |
|----------|----------|
| **MSO1000Z/DS1000Z User's Guide** | Ch. 15: LAN Configuration; Ch. 16: Remote Control via LAN |
| **DS1000Z Programming Guide** | Full SCPI command reference (`:LAN`, `:WAVeform`, `:MEASure`, etc.) |

---

## 9. Project Files

| File | Purpose |
|------|---------|
| `driver.py` | Oscilloscope ABC implementation (RAW waveform download) |
| `test.py` | Model-specific identity and channel status check |
| `tools/test_instruments.py` | Role-based `*IDN?` check using `instruments/lab.json` |
| `tools/capture_waveform.py` | Generic capture CLI; `--campaign` writes to `Measurements/<name>/Data/` |
| `instruments/lab.json` | Bench roles and IP addresses |
| `CHEATSHEET.md` | This reference |
