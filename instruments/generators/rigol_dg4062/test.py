"""Connect to the Rigol DG4062 over LAN and print channel status."""

from __future__ import annotations

import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "lib" / "paths.py").is_file() and (_parent / "Measurements").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
else:
    raise SystemExit("Could not find repository root (expected lib/paths.py and Measurements/).")

from instruments.generators.rigol_dg4062.driver import RigolDG4062
from instruments.registry import connection_for


def main() -> None:
    gen = RigolDG4062(connection_for("rigol_dg4062"))
    gen.connect()
    try:
        print(f"Model:    {gen.model_id}")
        print(f"Resource: {gen.resource_name}")
        print(f"IDN:      {gen.identify()}")
        for channel in (1, 2):
            status = gen.query_channel(channel)
            load = status["load_ohm"]
            load_txt = "High-Z" if load == float("inf") else f"{load:g} ohm"
            print(f"\nChannel {channel}:")
            print(f"  output:    {'ON' if status['output'] else 'OFF'}")
            print(f"  shape:     {status['shape']}")
            print(f"  frequency: {status['frequency_hz']} Hz")
            print(f"  amplitude: {status['amplitude_vpp']} Vpp")
            print(f"  offset:    {status['offset_v']} V")
            print(f"  load:      {load_txt}")
    finally:
        gen.close()


if __name__ == "__main__":
    main()
