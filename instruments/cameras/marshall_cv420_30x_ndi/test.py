"""Connect to the Marshall CV420-30X-NDI and print identity plus NDI sources."""

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

from instruments.cameras.marshall_cv420_30x_ndi.ndi import find_sources
from instruments.registry import connection_for, open_camera


def main() -> None:
    camera = open_camera()
    try:
        print(f"Model: {camera.model_id}")
        print(f"IDN:   {camera.identify()}")
        conn = connection_for(camera.model_id)
        print(f"IP:    {conn.get('ip')}")
        sources = find_sources(extra_ips=str(conn.get("ip", "")), timeout_s=4.0)
        if not sources:
            print("NDI:   (no sources)")
            return
        print("NDI sources:")
        for source in sources:
            print(f"  {source.name}  {source.url}")
    finally:
        camera.close()


if __name__ == "__main__":
    main()
