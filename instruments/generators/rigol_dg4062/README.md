# Rigol DG4062

Placeholder for the DG4062 arbitrary waveform generator driver.

Campaigns should use `open_generator()` from `instruments.registry`, not this
module. Fill in `driver.py` with SCPI, then set the instrument IP in
[`instruments/lab.json`](../../lab.json).

The current IP in `lab.json` (`192.168.147.111`) is a placeholder until the
generator is on the lab network.
