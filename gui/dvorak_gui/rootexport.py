"""Send a figure spec to the ROOT renderer and open the result."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from .legacyroot import open_legacy_root
from .rootbridge import RootBridge
from .workers import WorkerHandle


def _unavailable(parent: QWidget, bridge: RootBridge) -> bool:
    if bridge.client.available:
        return False
    report = bridge.report or {}
    QMessageBox.warning(
        parent,
        "ROOT",
        str(report.get("error") or "Still looking for ROOT. Try again in a moment."),
    )
    return True


def open_in_legacy_root(
    parent: QWidget,
    bridge: RootBridge,
    worker: WorkerHandle,
    spec: dict,
    *,
    on_status=None,
) -> None:
    if _unavailable(parent, bridge):
        return
    folder = tempfile.mkdtemp(prefix="dvorak-legacy-")
    macro = str(Path(folder) / f"{spec.get('name') or 'figure'}.C")
    if on_status:
        on_status("Writing a ROOT macro…")

    def done(result: object) -> None:
        path = ""
        if isinstance(result, dict):
            path = str(result.get("macro") or "")
        if not path:
            QMessageBox.warning(parent, "Legacy ROOT", "Nothing was written.")
            return
        open_legacy_root(path, rootsys=bridge.client.rootsys or None, parent=parent)
        if on_status:
            on_status(f"Opened {Path(path).name}")

    def failed(message: str) -> None:
        QMessageBox.critical(parent, "Legacy ROOT", message)

    worker.start(
        bridge.client.render,
        spec,
        outputs=("macro",),
        macro_path=macro,
        on_finished=done,
        on_failed=failed,
    )


def save_pdf(
    parent: QWidget,
    bridge: RootBridge,
    worker: WorkerHandle,
    spec: dict,
    default: Path,
    *,
    on_status=None,
) -> None:
    if _unavailable(parent, bridge):
        return
    chosen, _filter = QFileDialog.getSaveFileName(
        parent,
        "Save PDF",
        str(default),
        "PDF (*.pdf)",
    )
    if not chosen:
        return
    path = Path(chosen)
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")
    if on_status:
        on_status(f"Writing {path.name}…")

    def done(result: object) -> None:
        written = path
        if isinstance(result, dict) and result.get("pdf"):
            written = Path(str(result["pdf"]))
        if on_status:
            on_status(f"Wrote {written}")

    def failed(message: str) -> None:
        QMessageBox.critical(parent, "Save PDF", message)

    worker.start(
        bridge.client.render,
        spec,
        outputs=("pdf",),
        pdf_path=str(path),
        on_finished=done,
        on_failed=failed,
    )
