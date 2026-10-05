"""Send a figure to the ROOT renderer and open the result.

With a JSROOT view, Legacy ROOT and Save PDF start from the canvas the user
is looking at: its zoom, dragged legend, log axes, and anything edited from
the JSROOT menus. The renderer rebuilds that canvas and writes it with
ROOT's own ``SaveAs``. If the page cannot hand its canvas back, the figure is
drawn again from the spec at the same size.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from dvorak_root.layout import safe_stem, screen_fit
from dvorak_root.text import root_text

from .jsrootview import JsRootView
from .legacyroot import open_legacy_root
from .rootbridge import RootBridge
from .workers import WorkerHandle

# ROOT's canvas window adds a title bar and a menu bar around the pad area.
_WINDOW_CHROME = (24, 72)


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


def _screen_room(parent: QWidget | None) -> tuple[int, int]:
    """Largest canvas that fits on the screen the window is on."""
    screen = parent.screen() if parent is not None else None
    screen = screen or QGuiApplication.primaryScreen()
    if screen is None:
        return (1600, 1000)
    geometry = screen.availableGeometry()
    return (
        max(320, geometry.width() - _WINDOW_CHROME[0]),
        max(240, geometry.height() - _WINDOW_CHROME[1]),
    )


def _spec_size(spec: dict) -> tuple[int, int]:
    return int(spec.get("width") or 900), int(spec.get("height") or 560)


def _shown_size(state: dict) -> tuple[int, int] | None:
    try:
        width, height = int(state.get("width") or 0), int(state.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width < 50 or height < 50:
        return None
    return width, height


def open_in_legacy_root(
    parent: QWidget,
    bridge: RootBridge,
    worker: WorkerHandle,
    spec: dict,
    *,
    view: JsRootView | None = None,
    on_status=None,
) -> None:
    """Open ``spec`` (or the JSROOT canvas showing it) in ``root -l``."""
    if _unavailable(parent, bridge):
        return
    status = on_status or (lambda _text: None)
    stem = safe_stem(str(spec.get("name") or "figure"))
    title = root_text(spec.get("label") or spec.get("title") or stem)
    macro_path = Path(tempfile.mkdtemp(prefix="dvorak-legacy-")) / f"{stem}.C"
    room = _screen_room(parent)

    def launch(result: object) -> None:
        path = str(result.get("macro") or "") if isinstance(result, dict) else ""
        if not path:
            QMessageBox.warning(parent, "Legacy ROOT", "Nothing was written.")
            status("")
            return
        if open_legacy_root(path, rootsys=bridge.client.rootsys or None, parent=parent):
            status(f"Opened {Path(path).name} in ROOT")
        else:
            status("")

    def failed(message: str) -> None:
        status("")
        QMessageBox.critical(parent, "Legacy ROOT", message)

    def from_spec(size: tuple[int, int] | None = None) -> None:
        status("Writing a ROOT macro…")
        worker.start(
            bridge.client.render,
            spec,
            outputs=("macro",),
            macro_path=str(macro_path),
            size=screen_fit(size or _spec_size(spec), room),
            on_finished=launch,
            on_failed=failed,
        )

    def from_view(payload: dict | None) -> None:
        if not payload:
            from_spec()
            return
        state = payload.get("view") or {}
        shown = _shown_size(state)

        def retry(_message: str) -> None:
            from_spec(shown)

        status("Writing a ROOT macro…")
        worker.start(
            bridge.client.legacy,
            str(payload["canvas"]),
            name=stem,
            title=title,
            view=state,
            outputs=("macro",),
            macro_path=str(macro_path),
            size=screen_fit(shown, room) if shown else None,
            on_finished=launch,
            on_failed=retry,
        )

    if view is not None and view.usable:
        status("Reading the JSROOT canvas…")
        view.export_canvas(from_view)
        return
    from_spec()


def save_pdf(
    parent: QWidget,
    bridge: RootBridge,
    worker: WorkerHandle,
    spec: dict,
    default: Path,
    *,
    view: JsRootView | None = None,
    on_status=None,
) -> None:
    """Write ``spec`` (or the JSROOT canvas showing it) as a ROOT PDF."""
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
    status = on_status or (lambda _text: None)
    stem = safe_stem(str(spec.get("name") or path.stem))
    status(f"Writing {path.name}…")

    def done(result: object) -> None:
        written = path
        if isinstance(result, dict) and result.get("pdf"):
            written = Path(str(result["pdf"]))
        status(f"Wrote {written}")

    def failed(message: str) -> None:
        status("")
        QMessageBox.critical(parent, "Save PDF", message)

    def from_spec(size: tuple[int, int] | None = None) -> None:
        worker.start(
            bridge.client.render,
            spec,
            outputs=("pdf",),
            pdf_path=str(path),
            size=size,
            on_finished=done,
            on_failed=failed,
        )

    def from_view(payload: dict | None) -> None:
        if not payload:
            from_spec()
            return
        state = payload.get("view") or {}
        shown = _shown_size(state)
        worker.start(
            bridge.client.legacy,
            str(payload["canvas"]),
            name=stem,
            title=root_text(spec.get("label") or spec.get("title") or stem),
            view=state,
            outputs=("pdf",),
            pdf_path=str(path),
            on_finished=done,
            on_failed=lambda _message: from_spec(shown),
        )

    if view is not None and view.usable:
        view.export_canvas(from_view)
        return
    from_spec()
