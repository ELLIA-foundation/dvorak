"""X-123 energy spectra: browse nested campaigns, overlay, and inspect."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lib.paths import spectra_dir, spectrum_campaign_plots
from lib.spectrum import SpectrumCapture, load_calibration, load_spectrum_file

from ..registry import FAMILY_ANALYSIS, AnalysisSpec, get, register
from ..rootexport import open_in_legacy_root, save_pdf
from ..widgets.capture_metadata import CaptureMetadataPanel, flatten_metadata
from ..widgets.line_finder import LineFinderPanel
from ..rootcanvas import RootCanvasRenderer
from ..recipes import RecipeMixin, key_path, path_key
from ..widgets.spectrum_browser import SpectrumBrowser
from ..widgets.spectrum_plot import SpectrumPlot, SpectrumTrace
from ..window import AnalysisWindow
from ..workers import WorkerHandle

X123_SPECTRA_ID = "x123_spectra"

_SELECT = (
    "Select a spectrum to plot it. "
    "Shift-click or Command-click overlays traces from any campaign. "
    "Pin that selection as a sum or a mean ± σ to compare groups."
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


def _state_keys(state: dict[str, Any]) -> list[str]:
    """Every spectrum a recipe needs: the selection first, then group members."""
    keys: list[str] = list(state.get("selection") or [])
    for group in state.get("groups") or []:
        for key in group.get("members") or []:
            if key not in keys:
                keys.append(key)
    return keys


class X123SpectraWindow(RecipeMixin, AnalysisWindow):
    recipe_kind = X123_SPECTRA_ID

    def __init__(self, spec: AnalysisSpec, controller: Any) -> None:
        self._worker = WorkerHandle()
        self._export_worker = WorkerHandle()
        self._load_gen = 0
        self._captures: dict[Path, SpectrumCapture] = {}
        self._pending_recipe: dict[str, Any] | None = None
        self._recipe_dir: Path | None = None
        super().__init__(spec, controller)
        self._controller.root.ready.connect(self._on_root_ready)
        if self._controller.root.report:
            self._on_root_ready(self._controller.root.report)
        self.resize(1280, 820)
        self._show_idle()

    def _build_menu(self) -> None:
        super()._build_menu()
        file_menu = self.menuBar().actions()[0].menu()
        assert file_menu is not None
        refresh_act = None
        for action in file_menu.actions():
            if action.text() == "Refresh catalogue":
                action.setText("Refresh spectra")
                refresh_act = action
        new_act = QAction("New session…", self)
        new_act.triggered.connect(self._new_session)
        if refresh_act is not None:
            file_menu.insertAction(refresh_act, new_act)
        close_act = next(
            action for action in file_menu.actions() if action.text() == "Close"
        )
        export_act = QAction("Export plot…", self)
        export_act.setShortcut(QKeySequence.StandardKey.Save)
        export_act.triggered.connect(self._export_plot)
        legacy_act = QAction("Legacy ROOT", self)
        legacy_act.triggered.connect(self._open_legacy_root)
        pdf_act = QAction("Export PDF…", self)
        pdf_act.triggered.connect(self._export_pdf)
        self._install_recipe_actions(file_menu, close_act)
        file_menu.insertSeparator(close_act)
        file_menu.insertAction(close_act, export_act)
        file_menu.insertAction(close_act, pdf_act)
        file_menu.insertAction(close_act, legacy_act)

        view_menu = self.menuBar().addMenu("&View")
        reset_act = QAction("Reset view", self)
        reset_act.setShortcut(QKeySequence("Home"))
        reset_act.triggered.connect(self._reset_view)
        view_menu.addAction(reset_act)
        self._view_menu = view_menu

    def _reset_view(self) -> None:
        self._plot.reset_view()

    def _new_session(self) -> None:
        self._browser.new_session()

    def _build_body(self) -> None:
        self._browser = SpectrumBrowser()
        self._browser.current_file_changed.connect(self._on_current)
        self._browser.selection_changed.connect(self._on_selection)
        self._browser.status_message.connect(self.statusBar().showMessage)

        self._plot = SpectrumPlot()
        self._plot.status_changed.connect(self.statusBar().showMessage)
        self._plot.legacy_root_requested.connect(self._open_legacy_root)
        self._plot.pdf_requested.connect(self._export_pdf)
        self._plot.generate_root_requested.connect(self._generate_root)
        self._plot.root_resized.connect(self._on_root_resized)
        self._plot.lines_requested.connect(self._show_lines)
        self._root = RootCanvasRenderer(
            self,
            self._controller.root,
            self._export_worker,
            self._plot.root_view,
            self.statusBar().showMessage,
        )

        heading = QLabel("Capture")
        self._detail = CaptureMetadataPanel(legend=True)
        self._detail.legend_fields_changed.connect(self._plot.set_legend_fields)
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

        self._lines_dock = QDockWidget("X-ray lines", self)
        self._lines_dock.setObjectName("x123_lines")
        self._lines_dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self._lines_dock.setWidget(LineFinderPanel(self._plot, self.statusBar().showMessage))
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._lines_dock)
        self._lines_dock.hide()
        lines_act = self._lines_dock.toggleViewAction()
        lines_act.setText("X-ray lines")
        lines_act.setShortcut(QKeySequence("Ctrl+L"))
        self._view_menu.addAction(lines_act)

    def _show_lines(self) -> None:
        if self._lines_dock.isVisible():
            self._lines_dock.raise_()
            return
        self._lines_dock.show()
        self.resizeDocks([self._lines_dock], [460], Qt.Orientation.Horizontal)

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
            "No X-123 sessions yet.\n\n"
            "Choose File → New session… (or New session… in the list) "
            f"to create {root}/<session>/Data/ and copy .mca files in. "
            "You can also drop .mca files onto the list."
        )
        self._detail.clear()
        self.statusBar().showMessage("No spectrum campaigns")

    def _reload(self) -> None:
        paths = self._files_to_plot()
        if not paths:
            return
        extra = []
        if self._pending_recipe is not None:
            extra = [
                key_path(k) for k in _state_keys(self._pending_recipe) if key_path(k) not in paths
            ]
        campaigns = []
        for path in paths + extra:
            campaign = self._browser.campaign_for(path) or path.parent.name
            campaigns.append(campaign)
        self._load_gen += 1
        gen = self._load_gen
        self.statusBar().showMessage("Loading…")
        self._worker.start(
            _load_files,
            [str(path) for path in paths + extra],
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
        wanted = set(self._files_to_plot())
        recipe, self._pending_recipe = self._pending_recipe, None
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
                meta=flatten_metadata(row["capture"].metadata_dict()),
            )
            for row in rows
        ]
        if recipe is not None:
            by_key = {path_key(t.path): t for t in traces}
            self._plot.set_traces([t for t in traces if t.path in wanted])
            self._plot.apply_recipe_state(recipe, by_key)
            self._detail.set_legend_keys(self._plot.legend_fields())
            missing = [k for k in _state_keys(recipe) if k not in by_key]
            if missing:
                self.statusBar().showMessage(f"Recipe: {len(missing)} spectrum file(s) not found")
        else:
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
            self._detail.show_metadata(None, str(path))
            return
        self._detail.show_metadata(capture.metadata_dict(), str(path))

    def _recipe_state(self) -> dict[str, Any] | None:
        state = self._plot.recipe_state()
        if not state["selection"] and not state["groups"]:
            QMessageBox.information(self, self.windowTitle(), "Select a spectrum first.")
            return None
        return state

    def _recipe_default_name(self) -> str:
        paths = self._files_to_plot()
        return paths[0].stem if paths else "spectrum"

    def _apply_recipe(self, state: dict[str, Any]) -> None:
        selection = [key_path(k) for k in state.get("selection") or []]
        existing = [p for p in selection if p.is_file()]
        self._pending_recipe = state
        if existing:
            self._browser.focus_paths(existing)
        else:
            self._reload_for_recipe()

    def _reload_for_recipe(self) -> None:
        # No selectable spectra (a groups-only recipe): load from the current row.
        if self._files_to_plot():
            self._reload()
        else:
            self._pending_recipe = None
            self.statusBar().showMessage("Recipe has no spectra that exist on disk")

    def _publication_spec(self) -> dict | None:
        paths = self._files_to_plot()
        name = paths[0].stem if paths else "spectrum"
        return self._plot.publication_spec(name)

    def _root_view(self):
        """The JSROOT canvas when it is showing, so its edits carry over."""
        return self._plot.root_view if self._plot.root_active() else None

    def _on_root_ready(self, report: dict) -> None:
        self._root.attach_bundle(report)

    def _on_root_resized(self) -> None:
        if self._plot.root_active():
            self._generate_root()

    def _generate_root(self) -> None:
        spec = self._publication_spec()
        if spec is None:
            QMessageBox.information(self, self.windowTitle(), "Select a spectrum first.")
            return
        self._root.render(spec, self._plot.root_size(), self._plot.show_root)

    def _open_legacy_root(self) -> None:
        spec = self._publication_spec()
        if spec is None:
            QMessageBox.information(self, self.windowTitle(), "Select a spectrum first.")
            return
        open_in_legacy_root(
            self,
            self._controller.root,
            self._export_worker,
            spec,
            view=self._root_view(),
            on_status=self.statusBar().showMessage,
        )

    def _export_pdf(self) -> None:
        spec = self._publication_spec()
        default = self._default_export_path(".pdf")
        if spec is None or default is None:
            QMessageBox.information(self, self.windowTitle(), "Select a spectrum first.")
            return
        save_pdf(
            self,
            self._controller.root,
            self._export_worker,
            spec,
            default,
            view=self._root_view(),
            on_status=self.statusBar().showMessage,
        )

    def _default_export_path(self, suffix: str) -> Path | None:
        paths = self._files_to_plot()
        if not paths:
            return None
        campaign = self._browser.campaign_for(paths[0])
        if campaign:
            default_dir = spectrum_campaign_plots(campaign)
        else:
            default_dir = paths[0].parent / "plots"
        default_dir.mkdir(parents=True, exist_ok=True)
        return default_dir / f"{paths[0].stem}{suffix}"

    def _export_plot(self) -> None:
        default = self._default_export_path(".png")
        if default is None:
            QMessageBox.information(self, self.windowTitle(), "Select a spectrum first.")
            return
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
        self._export_worker.cancel()
        super().closeEvent(event)


def _create_window(controller: Any) -> X123SpectraWindow:
    return X123SpectraWindow(get(X123_SPECTRA_ID), controller)


register(
    AnalysisSpec(
        id=X123_SPECTRA_ID,
        title="X-123 Spectra",
        description=(
            "Plot Amptek X-123 energy spectra. New session… creates "
            "Measurements/X123_Spectra/<session>/Data/ and copies .mca files "
            "into it. Overlay traces, pin sums and means, smooth them, "
            "mark U, Th, Bi, and Ra lines, query and auto-identify X-ray lines "
            "from a K/L/M line database, and open the view in ROOT."
        ),
        family=FAMILY_ANALYSIS,
        window_factory=_create_window,
    )
)
