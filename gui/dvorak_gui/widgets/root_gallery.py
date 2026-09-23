"""Figure list over a JSROOT canvas, with a PNG gallery when ROOT is absent."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..jsrootview import JsRootView
from ..rootbridge import RootBridge
from ..rootexport import open_in_legacy_root, save_pdf
from ..workers import WorkerHandle
from .figure_gallery import FigureGallery


class RootCanvas(QWidget):
    """One figure spec on a JSROOT view, with Legacy ROOT and PDF."""

    def __init__(
        self,
        bridge: RootBridge,
        parent: QWidget | None = None,
        empty: str = "Nothing to draw yet.",
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._empty = empty
        self._spec: dict | None = None
        self._export_spec: dict | None = None
        self._default_pdf = Path("figure.pdf")
        self._gen = 0
        self._render_worker = WorkerHandle(self)
        self._export_worker = WorkerHandle(self)

        self._view = JsRootView(bridge.jsroot, self)
        self._legacy = QPushButton("Legacy ROOT")
        self._legacy.setToolTip(
            "Open this figure in the interactive ROOT GUI (root -l)"
        )
        self._legacy.clicked.connect(self._open_legacy)
        self._pdf = QPushButton("Save PDF…")
        self._pdf.clicked.connect(self._save_pdf)
        self._status = QLabel("")
        self._status.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self._status, stretch=1)
        buttons.addWidget(self._pdf)
        buttons.addWidget(self._legacy)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view, stretch=1)
        layout.addLayout(buttons)

        bridge.ready.connect(self._on_root_ready)
        if bridge.report:
            self._on_root_ready(bridge.report)
        self._view.show_message(self._empty)
        self._update_buttons()

    def shutdown(self) -> None:
        self._gen += 1
        self._render_worker.cancel()
        self._export_worker.cancel()

    def clear(self, message: str | None = None) -> None:
        self.set_spec(None, message=message or self._empty)

    def set_pdf_default(self, path: Path) -> None:
        self._default_pdf = path

    def current_spec(self) -> dict | None:
        return self._export_spec or self._spec

    def set_spec(
        self,
        spec: dict | None,
        *,
        export_spec: dict | None = None,
        message: str | None = None,
    ) -> None:
        self._gen += 1
        self._spec = spec
        self._export_spec = export_spec or spec
        if spec is None:
            self._view.show_message(message or self._empty)
            self._status.setText("")
            self._update_buttons()
            return
        if not self._view.usable:
            report = self._bridge.report or {}
            self._view.show_message(
                str(report.get("error") or message or "Looking for a ROOT installation…")
            )
            self._update_buttons()
            return
        self._status.setText("Drawing…")
        self._view.show_message("Rendering…")
        self._update_buttons()
        gen = self._gen
        client = self._bridge.client

        def job() -> str:
            reply = client.render(spec, outputs=("json",))
            return str(reply.get("json") or "")

        def done(result: object) -> None:
            if gen != self._gen:
                return
            if isinstance(result, str) and result:
                self._view.draw(result)
                self._status.setText("")
            else:
                self._view.show_message("Empty figure.")
                self._status.setText("")

        def failed(error: str) -> None:
            if gen != self._gen:
                return
            self._status.setText("ROOT draw failed.")
            self._view.show_message(error)

        self._render_worker.start(job, on_finished=done, on_failed=failed)

    def _on_root_ready(self, report: dict) -> None:
        path = str(report.get("jsroot") or "")
        if path:
            self._view.set_bundle(path)
        elif report.get("error"):
            self._view.show_message(str(report["error"]))
        if self._spec is not None:
            self.set_spec(self._spec, export_spec=self._export_spec)

    def _update_buttons(self) -> None:
        has_spec = self.current_spec() is not None
        self._legacy.setEnabled(has_spec)
        self._pdf.setEnabled(has_spec)

    def _open_legacy(self) -> None:
        spec = self.current_spec()
        if spec is None:
            return
        open_in_legacy_root(
            self.window(),
            self._bridge,
            self._export_worker,
            spec,
            on_status=self._status.setText,
        )

    def _save_pdf(self) -> None:
        spec = self.current_spec()
        if spec is None:
            return
        name = str(spec.get("name") or "figure")
        default = self._default_pdf
        if default.name in {"figure.pdf", ""}:
            default = Path(f"{name}.pdf")
        save_pdf(
            self.window(),
            self._bridge,
            self._export_worker,
            spec,
            default,
            on_status=self._status.setText,
        )


class RootGallery(QWidget):
    """Spark-gap figures 02–08: JSROOT when it can, PNGs otherwise."""

    def __init__(self, bridge: RootBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._specs: list[dict] = []
        self._json: dict[str, str] = {}
        self._pngs: list[tuple[str, Path]] = []
        self._gen = 0
        self._render_worker = WorkerHandle()
        self._export_worker = WorkerHandle()

        self._list = QListWidget()
        self._list.setMinimumWidth(160)
        self._list.currentRowChanged.connect(self._show_row)

        self._view = JsRootView(bridge.jsroot, self)
        self._gallery = FigureGallery()
        self._stack = QStackedWidget()
        self._stack.addWidget(self._view)
        self._stack.addWidget(self._gallery)

        self._legacy = QPushButton("Legacy ROOT")
        self._legacy.setToolTip(
            "Open the selected figure in the interactive ROOT GUI (root -l)"
        )
        self._legacy.clicked.connect(self._open_legacy)
        self._pdf = QPushButton("Save PDF…")
        self._pdf.clicked.connect(self._save_pdf)
        self._status = QLabel("")
        self._status.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self._status, stretch=1)
        buttons.addWidget(self._pdf)
        buttons.addWidget(self._legacy)

        body = QHBoxLayout()
        body.addWidget(self._list)
        body.addWidget(self._stack, stretch=1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(body, stretch=1)
        layout.addLayout(buttons)

        bridge.ready.connect(self._on_root_ready)
        if bridge.report:
            self._on_root_ready(bridge.report)
        self._show_fallback()

    def shutdown(self) -> None:
        self._gen += 1
        self._render_worker.cancel()
        self._export_worker.cancel()

    def clear(self) -> None:
        self._gen += 1
        self._specs = []
        self._json = {}
        self._pngs = []
        self._list.clear()
        self._gallery.clear()
        self._view.show_message("Run full analysis to draw figures 02–08.")
        self._show_fallback()
        self._update_buttons()

    def count(self) -> int:
        return len(self._specs) or len(self._pngs)

    def set_content(self, specs: list[dict] | None, pngs: list[tuple[str, Path]]) -> None:
        self._gen += 1
        self._specs = list(specs or [])
        self._json = {}
        self._pngs = list(pngs)
        self._gallery.set_figures(self._pngs)
        self._list.blockSignals(True)
        self._list.clear()
        if self._specs:
            for spec in self._specs:
                self._list.addItem(str(spec.get("label") or spec.get("name") or "figure"))
        self._list.blockSignals(False)
        if self._specs:
            self._list.setCurrentRow(0)
        self._apply_mode()
        self._update_buttons()

    def _on_root_ready(self, report: dict) -> None:
        path = str(report.get("jsroot") or "")
        if path:
            self._view.set_bundle(path)
        elif report.get("error"):
            self._view.show_message(str(report["error"]))
        self._apply_mode()

    def _js_ready(self) -> bool:
        return self._view.usable and bool(self._specs)

    def _apply_mode(self) -> None:
        if self._js_ready():
            self._list.setVisible(True)
            self._stack.setCurrentWidget(self._view)
            self._render_specs()
            self._show_row(self._list.currentRow())
            return
        self._show_fallback()

    def _show_fallback(self) -> None:
        self._list.setVisible(False)
        self._stack.setCurrentWidget(self._gallery)
        if not self._pngs and not self._specs:
            self._gallery.clear()

    def _render_specs(self) -> None:
        specs = list(self._specs)
        if not specs:
            return
        gen = self._gen
        client = self._bridge.client
        self._status.setText("Drawing figures…")

        def job() -> dict[str, str]:
            rendered: dict[str, str] = {}
            for spec in specs:
                reply = client.render(spec, outputs=("json",))
                rendered[str(spec.get("name"))] = str(reply.get("json") or "")
            return rendered

        def done(result: object) -> None:
            if gen != self._gen or not isinstance(result, dict):
                return
            self._json = {key: value for key, value in result.items() if value}
            self._status.setText("")
            self._show_row(self._list.currentRow())

        def failed(message: str) -> None:
            if gen != self._gen:
                return
            self._status.setText("ROOT draw failed. Showing saved PNGs.")
            self._view.show_message(message)
            if self._pngs:
                self._show_fallback()

        self._render_worker.start(job, on_finished=done, on_failed=failed)

    def _show_row(self, row: int) -> None:
        if not self._js_ready():
            return
        spec = self._spec_at(row)
        if spec is None:
            self._view.show_message("Run full analysis to draw figures 02–08.")
            return
        payload = self._json.get(str(spec.get("name")))
        if payload:
            self._view.draw(payload)
        else:
            self._view.show_message("Rendering…")
        self._update_buttons()

    def _spec_at(self, row: int) -> dict | None:
        if row < 0 or row >= len(self._specs):
            return None
        return self._specs[row]

    def _selected_spec(self) -> dict | None:
        if self._js_ready():
            return self._spec_at(self._list.currentRow())
        current = self._gallery.current_item()
        if current is None:
            return self._specs[0] if self._specs else None
        title, path = current
        for spec in self._specs:
            label = str(spec.get("label") or "")
            name = str(spec.get("name") or "")
            if title == label or title == name or path.stem == name:
                return spec
        return None

    def _update_buttons(self) -> None:
        has_spec = self._selected_spec() is not None
        self._legacy.setEnabled(has_spec)
        self._pdf.setEnabled(has_spec)

    def _open_legacy(self) -> None:
        spec = self._selected_spec()
        if spec is None:
            return
        window = self.window()
        open_in_legacy_root(
            window,
            self._bridge,
            self._export_worker,
            spec,
            on_status=self._status.setText,
        )

    def _save_pdf(self) -> None:
        spec = self._selected_spec()
        if spec is None:
            return
        name = str(spec.get("name") or "figure")
        default = Path(f"{name}.pdf")
        for _title, png in self._pngs:
            if png.stem == name:
                default = png.with_suffix(".pdf")
                break
        window = self.window()
        save_pdf(
            window,
            self._bridge,
            self._export_worker,
            spec,
            default,
            on_status=self._status.setText,
        )
