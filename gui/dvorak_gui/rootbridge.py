"""GUI-owned handle on the ROOT renderer. This module does not import ROOT."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal

_GUI_DIR = Path(__file__).resolve().parent.parent
if str(_GUI_DIR) not in sys.path:
    sys.path.insert(0, str(_GUI_DIR))

from dvorak_root.launch import RootClient


class RootBridge(QObject):
    """Probe ROOT at startup and keep one renderer process for the session."""

    ready = Signal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.client = RootClient()
        self.client.probe_async(self._announce)

    def _announce(self, report: dict) -> None:
        self.ready.emit(dict(report))

    @property
    def report(self) -> dict | None:
        return self.client.report

    @property
    def jsroot(self) -> str:
        return self.client.jsroot

    def close(self) -> None:
        self.client.close()
