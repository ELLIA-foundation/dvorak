"""Send a figure spec to the ROOT renderer and open the result."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from dvorak_root.text import sanitize_root_json

from .jsrootview import JsRootView
from .legacyroot import open_legacy_root, write_canvas_macro
from .rootbridge import RootBridge
from .workers import WorkerHandle


_COLOR_KEYS = ("fFillColor", "fLineColor", "fMarkerColor", "fTextColor")


def missing_custom_colors(payload: str) -> bool:
    """True when a custom color index is used and no ``TColor`` defines it.

    Indices at or above 1000 come from ``TColor::GetColor`` and exist only in
    the process that allocated them. Without those records Legacy ROOT draws
    the axes and skips the fills.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return True
    defined: set[int] = set()
    used: set[int] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("_typename") == "TColor" and isinstance(node.get("fNumber"), int):
                defined.add(node["fNumber"])
            for key in _COLOR_KEYS:
                value = node.get(key)
                if isinstance(value, int) and value >= 1000:
                    used.add(value)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return bool(used - defined)


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
    view: JsRootView | None = None,
    on_status=None,
) -> None:
    if _unavailable(parent, bridge):
        return
    name = str(spec.get("name") or "figure")

    def launch(canvas_json: str) -> None:
        folder = Path(tempfile.mkdtemp(prefix="dvorak-legacy-"))
        macro = write_canvas_macro(folder, name, sanitize_root_json(canvas_json))
        if not open_legacy_root(macro, rootsys=bridge.client.rootsys or None, parent=parent):
            return
        if on_status:
            on_status(f"Opened {macro.name}")

    def failed(message: str) -> None:
        QMessageBox.critical(parent, "Legacy ROOT", message)

    def from_spec() -> None:
        if on_status:
            on_status("Writing a ROOT canvas…")

        def done(result: object) -> None:
            text = ""
            if isinstance(result, dict):
                text = str(result.get("json") or "")
            if not text:
                QMessageBox.warning(parent, "Legacy ROOT", "Nothing was written.")
                return
            launch(text)

        worker.start(
            bridge.client.render,
            spec,
            outputs=("json",),
            on_finished=done,
            on_failed=failed,
        )

    def from_view(text: str) -> None:
        source = view.source_json if view is not None else ""
        # The page export can drop the color table while keeping the bins.
        # The JSON originally drawn still has that table, so the bars survive.
        if text.strip().startswith("{") and not missing_custom_colors(text):
            if on_status:
                on_status("Opening the edited canvas…")
            launch(text)
            return
        if source.strip().startswith("{") and not missing_custom_colors(source):
            if on_status:
                on_status("Opening the rendered canvas…")
            launch(source)
            return
        from_spec()

    if view is not None and view.usable:
        if on_status:
            on_status("Reading the JSROOT canvas…")
        view.export_json(from_view)
        return
    from_spec()


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
