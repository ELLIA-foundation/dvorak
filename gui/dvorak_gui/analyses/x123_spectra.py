"""X-123 energy spectra: browse nested campaigns, overlay, and inspect."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lib.paths import spectra_dir, spectrum_campaign_plots
from lib.spectrum import SpectrumCapture, load_calibration, load_spectrum_file

from ..registry import FAMILY_ANALYSIS, AnalysisSpec, get, register
from ..widgets.spectrum_browser import SpectrumBrowser
from ..widgets.spectrum_plot import SpectrumPlot, SpectrumTrace
from ..window import AnalysisWindow
from ..workers import WorkerHandle

X123_SPECTRA_ID = "x123_spectra"

_SELECT = (
    "Select a spectrum to plot it. "
    "Shift-click or Command-click overlays traces from any campaign."
)


def _load_files(paths: list[str], campaigns: list[str]) -> dict[str, Any]:
    cal = load_calibration()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for path_s, campaign in zip(paths, campaigns):
        path = Path(path_s)
        try:
            capture = load_spectrum_file(path, cal)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.name}: {exc}")
            continue
        rows.append({"path": path, "campaign": campaign, "capture": capture})
    return {"rows": rows, "errors": errors}


class X123SpectraWindow(AnalysisWindow):
    def __init__(self, spec: AnalysisSpec, controller: Any) -> None:
        self._worker = WorkerHandle()
        self._load_gen = 0
        self._captures: dict[Path, SpectrumCapture] = {}
        super().__init__(spec, controller)
        self.resize(1280, 820)
        self._show_idle()

    def _build_menu(self) -> None:
        super()._build_menu()
        file_menu = self.menuBar().actions()[0].menu()
        assert file_menu is not None
        for action in file_menu.actions():
            if action.text() == "Refresh catalogue":
                action.setText("Refresh spectra")
        close_act = next(
            action for action in file_menu.actions() if action.text() == "Close"
        )
        export_act = QAction("Export plot…", self)
        export_act.setShortcut(QKeySequence.StandardKey.Save)
        export_act.triggered.connect(self._export_plot)
        file_menu.insertAction(close_act, export_act)

        view_menu = self.menuBar().addMenu("&View")
        reset_act = QAction("Reset view", self)
        reset_act.setShortcut(QKeySequence("Home"))
        reset_act.triggered.connect(self._reset_view)
        view_menu.addAction(reset_act)

    def _reset_view(self) -> None:
        self._plot.reset_view()

    def _build_body(self) -> None:
        self._browser = SpectrumBrowser()
        self._browser.current_file_changed.connect(self._on_current)
        self._browser.selection_changed.connect(self._on_selection)

        self._plot = SpectrumPlot()
        self._plot.status_changed.connect(self.statusBar().showMessage)

        heading = QLabel("Capture")
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("Select a spectrum.")
        meta = QWidget()
        meta_layout = QVBoxLayout(meta)
        meta_layout.addWidget(heading)
        meta_layout.addWidget(self._detail, stretch=1)

        right = QSplitter(Qt.Orientation.Horizontal)
        right.addWidget(self._plot)
        right.addWidget(meta)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        right.setSizes([860, 280])

        splitter = QSplitter()
        splitter.addWidget(self._browser)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([360, 920])
        self.setCentralWidget(splitter)

    def _refresh_catalogue(self) -> None:
        self._browser.refresh()

    def _on_current(self, path: Path | None) -> None:
        self._show_metadata(path)
        if path is None and not self._browser.selected_files():
            self._show_idle()

    def _on_selection(self) -> None:
        paths = self._files_to_plot()
        if not paths:
            self._show_idle()
            return
        self._reload()

    def _files_to_plot(self) -> list[Path]:
        selected = self._browser.selected_files()
        if selected:
            return selected
        current = self._browser.current_file()
        return [current] if current is not None else []

    def _show_idle(self) -> None:
        if self._browser.has_campaigns():
            self._plot.show_message(_SELECT)
            self.statusBar().showMessage("Select a spectrum")
            return
        root = spectra_dir()
        self._plot.show_message(
            "No X-123 campaigns yet.\n\n"
            f"Create {root}/<campaign>/Data/ and paste .mca files, "
            "then choose File → Refresh spectra."
        )
        self._detail.clear()
        self.statusBar().showMessage("No spectrum campaigns")

    def _reload(self) -> None:
        paths = self._files_to_plot()
        if not paths:
            return
        campaigns = []
        for path in paths:
            campaign = self._browser.campaign_for(path) or path.parent.name
            campaigns.append(campaign)
        self._load_gen += 1
        gen = self._load_gen
        self.statusBar().showMessage("Loading…")
        self._worker.start(
            _load_files,
            [str(path) for path in paths],
            campaigns,
            on_finished=lambda payload, generation=gen: self._on_loaded(generation, payload),
            on_failed=lambda message, generation=gen: self._on_failed(generation, message),
        )

    def _on_loaded(self, generation: int, payload: dict[str, Any]) -> None:
        if generation != self._load_gen:
            return
        rows = payload.get("rows") or []
        errors = payload.get("errors") or []
        if errors:
            self.statusBar().showMessage(" · ".join(errors))
        if not rows:
            self._plot.show_message("Could not load the selected spectra.")
            return
        self._captures = {row["path"]: row["capture"] for row in rows}
        traces = [
            SpectrumTrace(
                path=row["path"],
                campaign=row["campaign"],
                label=self._label(row["campaign"], row["capture"]),
                energy_kev=row["capture"].energy_kev,
                counts=row["capture"].counts,
                live_time_s=row["capture"].live_time_s,
                phase=row["capture"].phase,
                offset_kev=row["capture"].calibration.offset_kev,
                slope_kev=row["capture"].calibration.slope_kev_per_channel,
            )
            for row in rows
        ]
        self._plot.set_traces(traces)
        current = self._browser.current_file()
        self._show_metadata(current if current in self._captures else rows[0]["path"])

    def _on_failed(self, generation: int, message: str) -> None:
        if generation != self._load_gen:
            return
        QMessageBox.warning(self, self.windowTitle(), message)
        self.statusBar().showMessage(message)

    def _label(self, campaign: str, capture: SpectrumCapture) -> str:
        name = capture.run_name or (capture.path.stem if capture.path else "spectrum")
        return f"{campaign} / {name}"

    def _show_metadata(self, path: Path | None) -> None:
        if path is None:
            self._detail.clear()
            return
        capture = self._captures.get(path)
        if capture is None:
            self._detail.setPlainText(str(path))
            return
        payload = capture.metadata_dict()
        payload["path"] = str(path)
        self._detail.setPlainText(json.dumps(payload, indent=2, default=str))

    def _export_plot(self) -> None:
        paths = self._files_to_plot()
        if not paths:
            QMessageBox.information(self, self.windowTitle(), "Select a spectrum first.")
            return
        campaign = self._browser.campaign_for(paths[0])
        if campaign:
            default_dir = spectrum_campaign_plots(campaign)
        else:
            default_dir = paths[0].parent / "plots"
        default = default_dir / f"{paths[0].stem}.png"
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            "Export plot",
            str(default),
            "PNG (*.png)",
        )
        if not chosen:
            return
        path = Path(chosen)
        try:
            self._plot.export_png(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Wrote {path}")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._load_gen += 1
        self._worker.cancel()
        super().closeEvent(event)


def _create_window(controller: Any) -> X123SpectraWindow:
    return X123SpectraWindow(get(X123_SPECTRA_ID), controller)


register(
    AnalysisSpec(
        id=X123_SPECTRA_ID,
        title="X-123 Spectra",
        description=(
            "Plot Amptek X-123 energy spectra from nested campaigns. "
            "Paste .mca files into Measurements/X123_Spectra/<campaign>/Data/ "
            "and Refresh. Overlay traces, smooth them, and mark U L lines."
        ),
        family=FAMILY_ANALYSIS,
        window_factory=_create_window,
    )
)
