"""Connect to the Rigol MSO1104Z over LAN and run a basic identity check."""

import pyvisa

SCOPE_IP = "192.168.147.110"
RESOURCE_CANDIDATES = [
    f"TCPIP0::{SCOPE_IP}::5555::SOCKET",  # raw SCPI socket (works with pyvisa-py)
    f"TCPIP0::{SCOPE_IP}::INSTR",         # VXI-11
]


def open_scope():
    rm = pyvisa.ResourceManager("@py")
    last_error = None
    for resource in RESOURCE_CANDIDATES:
        try:
            scope = rm.open_resource(resource)
            scope.timeout = 5000
            if resource.endswith("SOCKET"):
                scope.write_termination = "\n"
                scope.read_termination = "\n"
            idn = scope.query("*IDN?").strip()
            return rm, scope, resource, idn
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"Could not open Rigol scope at {SCOPE_IP}: {last_error}")


def main():
    rm, scope, resource, idn = open_scope()
    try:
        print(f"Backend:  {rm}")
        print(f"Resource: {resource}")
        print(f"IDN:      {idn}")

        print("\nChannel 1 status:")
        print(f"  display: {scope.query(':CHANnel1:DISPlay?').strip()}")
        print(f"  scale:   {scope.query(':CHANnel1:SCALe?').strip()} V/div")
        print(f"  offset:  {scope.query(':CHANnel1:OFFSet?').strip()} V")

        print("\nTimebase:")
        print(f"  scale:   {scope.query(':TIMebase:MAIN:SCALe?').strip()} s/div")

        print("\nTrigger:")
        print(f"  status:  {scope.query(':TRIGger:STATus?').strip()}")
        print(f"  source:  {scope.query(':TRIGger:EDGe:SOURce?').strip()}")
    finally:
        scope.close()
        rm.close()


if __name__ == "__main__":
    main()
