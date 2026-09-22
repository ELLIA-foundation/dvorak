"""Launch the interactive ROOT GUI on a saved canvas macro (.C)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QMessageBox, QWidget


def find_root_binary(rootsys: str | None = None) -> Path | None:
    """Locate the interactive ``root`` executable."""
    candidates: list[Path] = []
    if rootsys:
        candidates.append(Path(rootsys) / "bin" / "root")
    env = os.environ.get("ROOTSYS", "")
    if env:
        candidates.append(Path(env) / "bin" / "root")
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    which = shutil.which("root")
    return Path(which) if which else None


def open_legacy_root(
    macro_path: str | Path,
    *,
    rootsys: str | None = None,
    parent: QWidget | None = None,
) -> bool:
    """Detach-launch ``root -l macro.C``. Returns True when the process started."""
    path = Path(macro_path)
    if not path.is_file():
        QMessageBox.critical(parent, "Legacy ROOT", f"Macro not found:\n{path}")
        return False

    binary = find_root_binary(rootsys)
    if binary is None:
        QMessageBox.critical(
            parent,
            "Legacy ROOT",
            "Cannot find the interactive ROOT executable.\n\n"
            "Set ROOTSYS (or put `root` on PATH) and restart Dvorak.",
        )
        return False

    ok = QProcess.startDetached(str(binary), ["-l", str(path.resolve())])
    if not ok:
        QMessageBox.critical(
            parent,
            "Legacy ROOT",
            f"Failed to launch:\n{binary} -l {path}",
        )
        return False
    return True
