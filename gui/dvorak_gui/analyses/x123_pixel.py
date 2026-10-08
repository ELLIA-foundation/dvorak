"""X-123 and pixel-detector spectra overlaid on one energy axis, two y axes.

X-123 spectra come from ``Measurements/X123_Spectra`` as in the X-123 window.
Pixel-detector spectra are read, never produced, from Pixet's OPIXE store: the
``hClusterEnergy`` histograms in each measurement's ``derived.root``. Clustering,
calibration and deriving stay in Pixet; a measurement that OPIXE reports as
stale is flagged in the list, not re-processed here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lib.paths import measurements_dir, spectra_dir, spectrum_campaign_plots
from lib.pixet import ENERGY_MATCH, PixelMeasurement, spectra_from_payload
from lib.spectrum import SpectrumCapture

from ..recipes import RecipeMixin, key_path, path_key
from ..registry import FAMILY_ANALYSIS, AnalysisSpec, get, register
from ..rootcanvas import RootCanvasRenderer
from ..rootexport import open_in_legacy_root, save_pdf
from ..widgets.capture_metadata import CaptureMetadataPanel, flatten_metadata
from ..widgets.dual_spectrum_plot import DualSpectrumPlot, PixelTrace
from ..widgets.pixel_browser import PixelBrowser
from ..widgets.spectrum_browser import SpectrumBrowser
from ..widgets.spectrum_plot import SpectrumTrace
from ..window import AnalysisWindow
from ..workers import WorkerHandle
from .x123_spectra import _load_files

X123_PIXEL_ID = "x123_pixel_overlay"

_PIXEL_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Measurement", ("label", "id", "description", "created", "tags", "groups", "status")),
    (
        "Setup",
        ("distance_cm", "tube", "lid", "attenuator", "attenuator_thickness_mm"),
    ),
    ("Acquisition", ("live_time_s", "frames", "chip_id", "family", "calib_prefix")),
)


def _read_pixel(client, measurements: list[PixelMeasurement], cache: dict) -> dict[str, Any]:
    """Energy spectra for each measurement, reusing reads whose file is unchanged."""
    traces: list[PixelTrace] = []
    errors: list[str] = []
    for measurement in measurements:
        path = measurement.derived_path
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            errors.append(f"{measurement.display_name}: no derived.root")
            continue
        cached = cache.get(measurement.id)
        if cached is not None and cached[0] == stamp:
            spectra = cached[1]
        else:
            try:
                payload = client.read_hists(str(path), match=ENERGY_MATCH)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{measurement.display_name}: {str(exc).strip().splitlines()[-1]}")
                continue
            spectra = {
                s.key: s for s in spectra_from_payload(payload, measurement.live_time_s)
            }
            cache[measurement.id] = (stamp, spectra)
        if not spectra:
            errors.append(f"{measurement.display_name}: no energy histogram")
            continue
        traces.append(
            PixelTrace(
                measurement_id=measurement.id,
                label=measurement.display_name,
                spectra=spectra,
                meta=flatten_metadata(measurement.metadata_dict()),
            )
        )
    return {"traces": traces, "errors": errors}


def _x123_trace(row: dict[str, Any]) -> SpectrumTrace:
    capture: SpectrumCapture = row["capture"]
    name = capture.run_name or (capture.path.stem if capture.path else "spectrum")
    return SpectrumTrace(
        path=row["path"],
        campaign=row["campaign"],
        label=f"{row['campaign']} / {name}",
        energy_kev=capture.energy_kev,
        counts=capture.counts,
        live_time_s=capture.live_time_s,
        phase=capture.phase,
        offset_kev=capture.calibration.offset_kev,
        slope_kev=capture.calibration.slope_kev_per_channel,
        meta=flatten_metadata(capture.metadata_dict()),
    )


class X123PixelWindow(RecipeMixin, AnalysisWindow):
    recipe_kind = X123_PIXEL_ID

    def __init__(self, spec: AnalysisSpec, controller: Any) -> None:
        self._x123_worker = WorkerHandle()
        self._pixel_worker = WorkerHandle()
        self._export_worker = WorkerHandle()
        self._captures: dict[Path, SpectrumCapture] = {}
        self._pixel_cache: dict[str, tuple[int, dict]] = {}
        self._pending_recipe: dict[str, Any] | None = None
        self._meta_source = ""
        super().__init__(spec, controller)
        self.resize(1500, 880)
        self._root = RootCanvasRenderer(
            self,
            self._controller.root,
            self._export_worker,
            self._plot.root_view,
            self.statusBar().showMessage,
        )
        self._controller.root.ready.connect(self._root.attach_bundle)
        if self._controller.root.report:
            self._root.attach_bundle(self._controller.root.report)

    # -- layout ------------------------------------------------------------

    def _build_menu(self) -> None:
        super()._build_menu()
        file_menu = self.menuBar().actions()[0].menu()
        assert file_menu is not None
        for action in file_menu.actions():
            if action.text() == "Refresh catalogue":
                action.setText("Refresh lists")
        close_act = next(a for a in file_menu.actions() if a.text() == "Close")
        self._install_recipe_actions(file_menu, close_act)
        file_menu.insertSeparator(close_act)
        export_act = QAction("Export plot…", self)
        export_act.setShortcut(QKeySequence.StandardKey.Save)
        export_act.triggered.connect(self._export_png)
        pdf_act = QAction("Export PDF…", self)
        pdf_act.triggered.connect(self._export_pdf)
        legacy_act = QAction("Legacy ROOT", self)
        legacy_act.triggered.connect(self._open_legacy_root)
        for action in (export_act, pdf_act, legacy_act):
            file_menu.insertAction(close_act, action)

        view_menu = self.menuBar().addMenu("&View")
        reset_act = QAction("Reset view", self)
        reset_act.setShortcut(QKeySequence("Home"))
        reset_act.triggered.connect(lambda: self._plot.reset_view())
        view_menu.addAction(reset_act)

    def _build_body(self) -> None:
        self._x123_browser = SpectrumBrowser()
        self._x123_browser.selection_changed.connect(self._load_x123)
        self._x123_browser.current_file_changed.connect(self._show_x123_meta)
        self._x123_browser.status_message.connect(self.statusBar().showMessage)

        self._pixel_browser = PixelBrowser()
        self._pixel_browser.selection_changed.connect(self._load_pixel)
        self._pixel_browser.current_changed.connect(self._show_pixel_meta)
        self._pixel_browser.status_message.connect(self.statusBar().showMessage)

        lists = QSplitter(Qt.Orientation.Vertical)
        lists.addWidget(_titled("X-123 spectra · left axis", self._x123_browser))
        lists.addWidget(_titled("Pixel detector (OPIXE, read-only) · right axis", self._pixel_browser))
        lists.setSizes([420, 420])

        self._plot = DualSpectrumPlot()
        self._plot.status_changed.connect(self.statusBar().showMessage)
        self._plot.legacy_root_requested.connect(self._open_legacy_root)
        self._plot.pdf_requested.connect(self._export_pdf)
        self._plot.generate_root_requested.connect(self._generate_root)
        self._plot.root_resized.connect(
            lambda: self._generate_root() if self._plot.root_active() else None
        )

        self._meta_heading = QLabel("Metadata")
        self._detail = CaptureMetadataPanel(legend=True)
        self._detail.legend_fields_changed.connect(self._plot.set_legend_fields)
        meta = QWidget()
        meta_layout = QVBoxLayout(meta)
        meta_layout.addWidget(self._meta_heading)
        meta_layout.addWidget(self._detail, stretch=1)

        right = QSplitter(Qt.Orientation.Horizontal)
        right.addWidget(self._plot)
        right.addWidget(meta)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        right.setSizes([900, 300])

        splitter = QSplitter()
        splitter.addWidget(lists)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([400, 1100])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Select X-123 spectra and pixel measurements")

    def _refresh_catalogue(self) -> None:
        self._x123_browser.refresh()
        self._pixel_browser.refresh()

    # -- loading -------------------------------------------------------------

    def _load_x123(self) -> None:
        paths = self._x123_browser.selected_files()
        if not paths:
            self._captures = {}
            self._plot.set_x123([])
            self._apply_pending()
            return
        campaigns = [self._x123_browser.campaign_for(p) or p.parent.name for p in paths]
        self.statusBar().showMessage("Loading X-123 spectra…")
        self._x123_worker.start(
            _load_files,
            [str(p) for p in paths],
            campaigns,
            on_finished=self._on_x123_loaded,
            on_failed=lambda message: self.statusBar().showMessage(message),
        )

    def _on_x123_loaded(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        rows = payload.get("rows") or []
        if payload.get("errors"):
            self.statusBar().showMessage(" · ".join(payload["errors"]))
        self._captures = {row["path"]: row["capture"] for row in rows}
        self._plot.set_x123([_x123_trace(row) for row in rows])
        if self._meta_source == "x123":
            self._show_x123_meta(self._x123_browser.current_file())
        self._apply_pending()

    def _load_pixel(self) -> None:
        chosen = self._pixel_browser.selected()
        if not chosen:
            self._plot.set_pixel([])
            self._apply_pending()
            return
        self.statusBar().showMessage(f"Reading {len(chosen)} derived.root file(s)…")
        self._pixel_worker.start(
            _read_pixel,
            self._controller.root.client,
            chosen,
            self._pixel_cache,
            on_finished=self._on_pixel_loaded,
            on_failed=lambda message: QMessageBox.warning(self, "Pixel spectra", message),
        )

    def _on_pixel_loaded(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("errors"):
            self.statusBar().showMessage(" · ".join(payload["errors"]))
        self._plot.set_pixel(payload.get("traces") or [])
        self._apply_pending()

    # -- metadata ------------------------------------------------------------

    def _show_x123_meta(self, path: Path | None) -> None:
        if path is None:
            return
        self._meta_source = "x123"
        capture = self._captures.get(path)
        self._meta_heading.setText(f"X-123 · {path.stem}")
        if capture is None:
            self._detail.show_metadata(None, str(path))
            return
        self._detail.show_metadata(capture.metadata_dict(), str(path))

    def _show_pixel_meta(self, measurement: PixelMeasurement | None) -> None:
        if measurement is None:
            return
        self._meta_source = "pixel"
        self._meta_heading.setText(f"Pixel · {measurement.display_name}")
        self._detail.show_metadata(
            measurement.metadata_dict(), str(measurement.dir), sections=_PIXEL_SECTIONS
        )

    # -- recipes -------------------------------------------------------------

    def _recipe_state(self) -> dict[str, Any] | None:
        x123 = [path_key(t.path) for t in self._plot.x123_traces()]
        pixel = [t.measurement_id for t in self._plot.pixel_traces()]
        if not x123 and not pixel:
            QMessageBox.information(self, self.windowTitle(), "Select a spectrum first.")
            return None
        return {
            "x123": x123,
            "pixel": pixel,
            "pixel_data_root": str(self._pixel_browser.data_root),
            **self._plot.recipe_state(),
        }

    def _recipe_default_name(self) -> str:
        traces = self._plot.x123_traces()
        if traces:
            return f"{traces[0].path.stem}_pixel"
        pixel = self._plot.pixel_traces()
        return f"{pixel[0].measurement_id}_overlay" if pixel else "x123_pixel"

    def _apply_recipe(self, state: dict[str, Any]) -> None:
        self._pending_recipe = state
        root = Path(str(state.get("pixel_data_root") or ""))
        if root.is_dir() and root.resolve() != self._pixel_browser.data_root.resolve():
            self._pixel_browser.set_data_root(root)
        paths = [key_path(k) for k in state.get("x123") or []]
        existing = [p for p in paths if p.is_file()]
        if existing:
            self._x123_browser.focus_paths(existing)
        else:
            self._x123_browser.clear_selection()
            self._load_x123()
        self._pixel_browser.select_ids(list(state.get("pixel") or []))

    def _apply_pending(self) -> None:
        """Apply a recipe's plot settings once both lists have loaded its spectra."""
        state = self._pending_recipe
        if state is None:
            return
        want_x123 = {k for k in state.get("x123") or [] if key_path(k).is_file()}
        have_x123 = {path_key(t.path) for t in self._plot.x123_traces()}
        want_pixel = {
            mid for mid in state.get("pixel") or [] if self._pixel_browser.measurement(mid)
        }
        have_pixel = {t.measurement_id for t in self._plot.pixel_traces()}
        if want_x123 - have_x123 or want_pixel - have_pixel:
            return
        self._pending_recipe = None
        self._detail.set_legend_keys(list(state.get("legend_fields") or []))
        self._plot.apply_recipe_state(state)
        missing = len(state.get("x123") or []) - len(want_x123)
        missing += len(state.get("pixel") or []) - len(want_pixel)
        if missing:
            self.statusBar().showMessage(f"Recipe: {missing} spectrum file(s) not found")
        else:
            self.statusBar().showMessage("Recipe loaded")

    # -- ROOT and export -----------------------------------------------------

    def _figure_spec(self) -> dict | None:
        return self._plot.publication_spec(self._recipe_default_name())

    def _generate_root(self) -> None:
        spec = self._figure_spec()
        if spec is None:
            QMessageBox.information(self, self.windowTitle(), "Select a spectrum first.")
            return
        self._root.render(spec, self._plot.root_size(), self._plot.show_root)

    def _open_legacy_root(self) -> None:
        spec = self._figure_spec()
        if spec is None:
            QMessageBox.information(self, self.windowTitle(), "Select a spectrum first.")
            return
        # Drawn from the spec (the live view), not the JSROOT canvas: a zoom
        # made in JSROOT would move the frame but not the right-hand TGaxis.
        open_in_legacy_root(
            self,
            self._controller.root,
            self._export_worker,
            spec,
            on_status=self.statusBar().showMessage,
        )

    def _default_dir(self) -> Path:
        traces = self._plot.x123_traces()
        if traces:
            campaign = self._x123_browser.campaign_for(traces[0].path)
            if campaign:
                try:
                    return spectrum_campaign_plots(campaign)
                except FileNotFoundError:
                    pass
        return spectra_dir() if spectra_dir().is_dir() else measurements_dir()

    def _export_pdf(self) -> None:
        spec = self._figure_spec()
        if spec is None:
            QMessageBox.information(self, self.windowTitle(), "Select a spectrum first.")
            return
        default = self._default_dir() / f"{self._recipe_default_name()}.pdf"
        save_pdf(
            self,
            self._controller.root,
            self._export_worker,
            spec,
            default,
            on_status=self.statusBar().showMessage,
        )

    def _export_png(self) -> None:
        if self._figure_spec() is None:
            QMessageBox.information(self, self.windowTitle(), "Select a spectrum first.")
            return
        default = self._default_dir() / f"{self._recipe_default_name()}.png"
        chosen, _filter = QFileDialog.getSaveFileName(
            self, "Export plot", str(default), "PNG (*.png)"
        )
        if not chosen:
            return
        try:
            path = self._plot.export_png(Path(chosen))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Wrote {path}")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._x123_worker.cancel()
        self._pixel_worker.cancel()
        self._export_worker.cancel()
        self._pixel_browser.shutdown()
        super().closeEvent(event)


def _titled(title: str, body: QWidget) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 4, 0, 0)
    heading = QLabel(title)
    font = heading.font()
    font.setBold(True)
    heading.setFont(font)
    layout.addWidget(heading)
    layout.addWidget(body, stretch=1)
    return box


def _create_window(controller: Any) -> X123PixelWindow:
    return X123PixelWindow(get(X123_PIXEL_ID), controller)


register(
    AnalysisSpec(
        id=X123_PIXEL_ID,
        title="X-123 + Pixel Detector Overlay",
        description=(
            "Overlay X-123 spectra (left axis) with ADVACAM pixel-detector "
            "spectra (right axis) already derived by Pixet / OPIXE. Pick the "
            "chip or a region, compare in counts / keV / s, mark lines, and "
            "export through ROOT."
        ),
        family=FAMILY_ANALYSIS,
        window_factory=_create_window,
    )
)
