"""Connect to the Rigol MSO1104Z over LAN and run a basic identity check."""

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

from instruments.oscilloscopes.rigol_mso1104.driver import RigolMSO1104
from instruments.registry import connection_for


def main() -> None:
    scope = RigolMSO1104(connection_for("rigol_mso1104"))
    scope.connect()
    try:
        visa = scope.visa
        print(f"Model:    {scope.model_id}")
        print(f"Resource: {scope.resource_name}")
        print(f"IDN:      {scope.identify()}")

        print("\nChannel 1 status:")
        print(f"  display: {visa.query(':CHANnel1:DISPlay?').strip()}")
        print(f"  scale:   {visa.query(':CHANnel1:SCALe?').strip()} V/div")
        print(f"  offset:  {visa.query(':CHANnel1:OFFSet?').strip()} V")

        print("\nTimebase:")
        print(f"  scale:   {visa.query(':TIMebase:MAIN:SCALe?').strip()} s/div")

        print("\nTrigger:")
        print(f"  status:  {visa.query(':TRIGger:STATus?').strip()}")
        print(f"  source:  {visa.query(':TRIGger:EDGe:SOURce?').strip()}")
    finally:
        scope.close()


if __name__ == "__main__":
    main()
