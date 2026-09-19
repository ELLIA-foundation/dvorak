"""Shared Rigol MSO1104Z LAN connection helpers."""

import pyvisa

SCOPE_IP = "192.168.147.110"
RESOURCE_CANDIDATES = [
    f"TCPIP0::{SCOPE_IP}::5555::SOCKET",  # raw SCPI socket (simple queries)
    f"TCPIP0::{SCOPE_IP}::INSTR",         # VXI-11 (needed for RAW waveform)
]


def open_scope(timeout_ms=5000, prefer_instr=False):
    """Open a VISA session to the scope. Returns (resource_manager, scope, resource, idn).

    Use prefer_instr=True for waveform downloads. RAW :WAVeform:DATA? hangs on
    the port-5555 SOCKET transport on this firmware; VXI-11 works.
    """
    rm = pyvisa.ResourceManager("@py")
    last_error = None
    candidates = RESOURCE_CANDIDATES
    if prefer_instr:
        candidates = list(reversed(RESOURCE_CANDIDATES))
    for resource in candidates:
        try:
            scope = rm.open_resource(resource)
            scope.timeout = timeout_ms
            if resource.endswith("SOCKET"):
                scope.write_termination = "\n"
                scope.read_termination = "\n"
            idn = scope.query("*IDN?").strip()
            return rm, scope, resource, idn
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"Could not open Rigol scope at {SCOPE_IP}: {last_error}")
