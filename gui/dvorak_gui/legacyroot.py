"""Launch the interactive ROOT GUI on a saved canvas macro (.C)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment
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


def legacy_environment(rootsys: str | None) -> QProcessEnvironment:
    """The GUI's environment with ROOTSYS set, as ``thisroot.sh`` would.

    A ``.rootrc`` commonly lists ``$ROOTSYS/lib`` in ``Root.DynamicPath``.
    Without ROOTSYS, ROOT cannot expand it while looking up plugins.
    """
    env = QProcessEnvironment.systemEnvironment()
    if not rootsys:
        return env
    root = Path(rootsys)
    env.insert("ROOTSYS", str(root))
    env.insert("PATH", os.pathsep.join(filter(None, [str(root / "bin"), env.value("PATH")])))
    for key in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"):
        env.insert(key, os.pathsep.join(filter(None, [str(root / "lib"), env.value(key)])))
    return env


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

    if not rootsys:
        rootsys = str(binary.resolve().parent.parent)
    process = QProcess()
    process.setProgram(str(binary))
    process.setArguments(["-l", str(path.resolve())])
    process.setWorkingDirectory(str(path.resolve().parent))
    process.setProcessEnvironment(legacy_environment(rootsys))
    started = process.startDetached()
    ok = started[0] if isinstance(started, tuple) else bool(started)
    if not ok:
        QMessageBox.critical(
            parent,
            "Legacy ROOT",
            f"Failed to launch:\n{binary} -l {path}",
        )
        return False
    return True
