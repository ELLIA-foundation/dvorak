"""Bench measurement: confirm instrument identity, then capture."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lib.paths import (
    CAMPAIGN_FREQUENCY_RESPONSES,
    CAMPAIGN_SPARK_GAP,
    campaign_data,
    campaign_session_data,
    list_campaigns,
    list_sessions,
    slug_name,
)
from lib.waveform import waveform_stem

from ..campaign_import import load_campaign_module
from ..registry import FAMILY_MEASUREMENT, AnalysisSpec, get, register
from ..window import AnalysisWindow
from ..workers import WorkerHandle

MEASURE_ID = "measure_run"

_ROLE_SCOPE = "oscilloscope"
_ROLE_GEN = "generator"
_ROLE_CAMERA = "camera"


def _probe(role: str, model_id: str | None) -> str:
    from instruments import open_camera, open_generator, open_oscilloscope

    openers = {
        _ROLE_SCOPE: open_oscilloscope,
        _ROLE_GEN: open_generator,
        _ROLE_CAMERA: open_camera,
    }
    instrument = openers[role](model_id)
    try:
        return f"{instrument.model_id}: {instrument.identify()}"
    finally:
        instrument.close()


def _capture_waveform(
    model_id: str | None,
    campaign: str,
    session: str,
    channel: int,
    chunk_size: int,
    window: str,
    run_name: str,
) -> str:
    acquire = load_campaign_module(CAMPAIGN_SPARK_GAP, "analyze_spark_gap")
    output_dir = campaign_session_data(campaign, session, create=True)
    npz_path, _run = acquire.acquire_waveform(
        model_id,
        channel,
        chunk_size,
        False,
        output_dir,
        window=window,
        run_name=run_name,
    )
    json_path = npz_path.with_suffix(".json")
    missing = [str(path) for path in (npz_path, json_path) if not path.is_file()]
    if missing:
        raise FileNotFoundError("Capture did not write: " + ", ".join(missing))
    return str(npz_path)


def _run_frequency(
    scope_model: str | None,
    f_min_hz: float,
    f_max_hz: float,
    points: int,
    amplitude_vpp: float | None,
    load_ohm: float,
    gen_channel: int,
    scope_channel: int,
    scope_bw_hz: float,
    averages: int,
    stop_event: Event,
    run_name: str | None,
) -> str:
    sweep = load_campaign_module(CAMPAIGN_FREQUENCY_RESPONSES, "sweep_frequency_response")
    payload = sweep.run_sweep(
        f_min_hz,
        f_max_hz,
        points,
        amplitude_vpp,
        load_ohm,
        gen_channel,
        scope_channel,
        scope_bw_hz,
        scope_model,
        averages,
        should_stop=stop_event.is_set,
    )
    payload["run_name"] = run_name
    if payload.get("cancelled") and not payload.get("rows"):
        return "cancelled"
    paths = sweep.save_sweep(payload, show=False)
    path = str(paths["csv"])
    if payload.get("cancelled"):
        return f"cancelled:{path}"
    return path


def _record_camera(
    model_id: str | None,
    campaign: str,
    duration_s: float,
    quality: str,
) -> str:
    from instruments import open_camera
    from lib.video import save_video, video_stem

    output_dir = campaign_data(campaign)
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = output_dir / f"{video_stem()}.mp4"
    camera = open_camera(model_id)
    try:
        capture = camera.record(duration_s, video_path, quality=quality)
        paths = save_video(capture, write_preview=True)
    finally:
        camera.close()
    return str(paths.get("json") or paths.get("video") or video_path)


class MeasurementWindow(AnalysisWindow):
    def __init__(self, spec: AnalysisSpec, controller: Any) -> None:
        self._workers = {
            _ROLE_SCOPE: WorkerHandle(),
            _ROLE_GEN: WorkerHandle(),
            _ROLE_CAMERA: WorkerHandle(),
            "run": WorkerHandle(),
        }
        self._ok = {_ROLE_SCOPE: False, _ROLE_GEN: False, _ROLE_CAMERA: False}
        self._busy = False
        self._sweeping = False
        super().__init__(spec, controller)
        self.resize(1100, 760)
        self.statusBar().showMessage("Test a connection before starting a measurement.")

    def _build_body(self) -> None:
        from instruments.registry import (
            list_cameras,
            list_generators,
            list_oscilloscopes,
            load_lab,
        )

        roles = load_lab().get("roles") or {}
        campaigns = list_campaigns() or [CAMPAIGN_SPARK_GAP]

        self._scope_model = self._model_combo(list_oscilloscopes(), roles.get(_ROLE_SCOPE))
        self._gen_model = self._model_combo(list_generators(), roles.get(_ROLE_GEN))
        self._camera_model = self._model_combo(list_cameras(), roles.get(_ROLE_CAMERA))
        self._scope_status = QLabel("Not tested")
        self._gen_status = QLabel("Not tested")
        self._camera_status = QLabel("Not tested")
        self._scope_btn = QPushButton("Test connection")
        self._gen_btn = QPushButton("Test connection")
        self._camera_btn = QPushButton("Test connection")
        self._scope_btn.clicked.connect(lambda: self._test(_ROLE_SCOPE))
        self._gen_btn.clicked.connect(lambda: self._test(_ROLE_GEN))
        self._camera_btn.clicked.connect(lambda: self._test(_ROLE_CAMERA))

        connections = QGroupBox("Instruments")
        form = QFormLayout(connections)
        form.addRow("Oscilloscope", self._role_row(self._scope_model, self._scope_btn, self._scope_status))
        form.addRow("Generator", self._role_row(self._gen_model, self._gen_btn, self._gen_status))
        form.addRow("Camera", self._role_row(self._camera_model, self._camera_btn, self._camera_status))

        self._wave_campaign = self._campaign_combo(campaigns, CAMPAIGN_SPARK_GAP)
        self._wave_campaign.currentTextChanged.connect(self._on_wave_campaign_changed)
        self._wave_session = QComboBox()
        self._wave_session.currentIndexChanged.connect(self._update_wave_preview)
        new_session_btn = QPushButton("New session…")
        new_session_btn.clicked.connect(self._new_session)
        session_row = QWidget()
        session_layout = QHBoxLayout(session_row)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.addWidget(self._wave_session, stretch=1)
        session_layout.addWidget(new_session_btn)
        self._wave_channel = QSpinBox()
        self._wave_channel.setRange(1, 4)
        self._wave_channel.setValue(1)
        self._wave_channel.valueChanged.connect(self._update_wave_preview)
        self._wave_chunk = QSpinBox()
        self._wave_chunk.setRange(1_000, 5_000_000)
        self._wave_chunk.setSingleStep(50_000)
        self._wave_chunk.setValue(250_000)
        self._wave_window = QComboBox()
        self._wave_window.addItems(["screen", "full"])
        self._wave_name = QLineEdit()
        self._wave_name.setPlaceholderText("required measurement name")
        self._wave_name.textChanged.connect(self._update_wave_preview)
        self._wave_preview = QLabel("")
        self._wave_preview.setWordWrap(True)
        self._wave_preview.setStyleSheet("color: palette(mid);")
        self._wave_btn = QPushButton("Capture waveform")
        self._wave_btn.clicked.connect(self._capture)
        wave = QGroupBox("Waveform")
        wave_form = QFormLayout(wave)
        wave_form.addRow("Campaign", self._wave_campaign)
        wave_form.addRow("Session", session_row)
        wave_form.addRow("Channel", self._wave_channel)
        wave_form.addRow("Chunk size", self._wave_chunk)
        wave_form.addRow("Window", self._wave_window)
        wave_form.addRow("Measurement name", self._wave_name)
        wave_form.addRow("Will write", self._wave_preview)
        wave_form.addRow(self._wave_btn)

        self._freq_min = self._hz_box(1e3)
        self._freq_max = self._hz_box(1e8)
        self._freq_points = QSpinBox()
        self._freq_points.setRange(2, 401)
        self._freq_points.setValue(41)
        self._freq_amp = QLineEdit()
        self._freq_amp.setPlaceholderText("blank = generator maximum")
        self._freq_load = QLineEdit("50")
        self._freq_gen_ch = QSpinBox()
        self._freq_gen_ch.setRange(1, 2)
        self._freq_scope_ch = QSpinBox()
        self._freq_scope_ch.setRange(1, 4)
        self._freq_bw = self._hz_box(1e8)
        self._freq_averages = self._averages_combo()
        self._freq_name = QLineEdit()
        self._freq_name.setPlaceholderText("optional run name")
        self._freq_btn = QPushButton("Run frequency sweep")
        self._freq_btn.clicked.connect(self._sweep)
        self._freq_stop_btn = QPushButton("Stop sweep")
        self._freq_stop_btn.clicked.connect(self._stop_sweep)
        freq_actions = QWidget()
        freq_actions_layout = QHBoxLayout(freq_actions)
        freq_actions_layout.setContentsMargins(0, 0, 0, 0)
        freq_actions_layout.addWidget(self._freq_btn)
        freq_actions_layout.addWidget(self._freq_stop_btn)
        freq = QGroupBox("Frequency response")
        freq_form = QFormLayout(freq)
        freq_form.addRow("f min (Hz)", self._freq_min)
        freq_form.addRow("f max (Hz)", self._freq_max)
        freq_form.addRow("Points", self._freq_points)
        freq_form.addRow("Amplitude Vpp", self._freq_amp)
        freq_form.addRow("Load (ohm)", self._freq_load)
        freq_form.addRow("Generator channel", self._freq_gen_ch)
        freq_form.addRow("Scope channel", self._freq_scope_ch)
        freq_form.addRow("Scope BW (Hz)", self._freq_bw)
        freq_form.addRow("Averages", self._freq_averages)
        freq_form.addRow("Run name", self._freq_name)
        freq_form.addRow(freq_actions)

        self._cam_campaign = self._campaign_combo(campaigns, "Camera_Check")
        self._cam_duration = QDoubleSpinBox()
        self._cam_duration.setRange(0.5, 120.0)
        self._cam_duration.setValue(3.0)
        self._cam_duration.setSuffix(" s")
        self._cam_quality = QComboBox()
        self._cam_quality.addItems(["preview", "full"])
        self._cam_btn = QPushButton("Record clip")
        self._cam_btn.clicked.connect(self._record)
        camera = QGroupBox("Camera")
        cam_form = QFormLayout(camera)
        cam_form.addRow("Campaign", self._cam_campaign)
        cam_form.addRow("Duration", self._cam_duration)
        cam_form.addRow("Quality", self._cam_quality)
        cam_form.addRow(self._cam_btn)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Connection and capture messages appear here.")

        actions = QWidget()
        actions_layout = QVBoxLayout(actions)
        actions_layout.addWidget(wave)
        actions_layout.addWidget(freq)
        actions_layout.addWidget(camera)
        actions_layout.addStretch(1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(connections)
        left_layout.addWidget(actions, stretch=1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self._log)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)
        self._fill_sessions(self._wave_campaign.currentText())
        self._sync_actions()
        self._update_wave_preview()

    def closeEvent(self, event) -> None:  # noqa: N802
        for worker in self._workers.values():
            worker.cancel()
        super().closeEvent(event)

    def _model_combo(self, models: list[str], selected: str | None) -> QComboBox:
        combo = QComboBox()
        combo.addItem("lab default", "")
        for model in models:
            combo.addItem(model, model)
        if selected:
            index = combo.findData(selected)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.currentIndexChanged.connect(self._on_model_changed)
        return combo

    def _campaign_combo(self, campaigns: list[str], preferred: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(campaigns)
        index = combo.findText(preferred)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def _hz_box(self, value: float) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(1.0, 1e10)
        box.setDecimals(0)
        box.setValue(value)
        return box

    def _averages_combo(self) -> QComboBox:
        sweep = load_campaign_module(CAMPAIGN_FREQUENCY_RESPONSES, "sweep_frequency_response")
        combo = QComboBox()
        for count in sweep.AVERAGE_CHOICES:
            label = "1 (normal)" if count == 1 else str(count)
            combo.addItem(label, count)
        index = combo.findData(sweep.DEFAULT_AVERAGES)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def _role_row(self, combo: QComboBox, button: QPushButton, status: QLabel) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        status.setWordWrap(True)
        layout.addWidget(combo)
        layout.addWidget(button)
        layout.addWidget(status, stretch=1)
        return row

    def _on_model_changed(self, _index: int) -> None:
        if not hasattr(self, "_scope_status"):
            return
        sender = self.sender()
        if sender is self._scope_model:
            self._mark(_ROLE_SCOPE, False, "Not tested")
        elif sender is self._gen_model:
            self._mark(_ROLE_GEN, False, "Not tested")
        elif sender is self._camera_model:
            self._mark(_ROLE_CAMERA, False, "Not tested")

    def _model_id(self, combo: QComboBox) -> str | None:
        value = combo.currentData()
        if not value:
            return None
        return str(value)

    def _test(self, role: str) -> None:
        combos = {
            _ROLE_SCOPE: self._scope_model,
            _ROLE_GEN: self._gen_model,
            _ROLE_CAMERA: self._camera_model,
        }
        self._mark(role, False, "Testing…")
        self._set_busy(True)
        self._workers[role].start(
            _probe,
            role,
            self._model_id(combos[role]),
            on_finished=lambda result, role=role: self._probe_ok(role, result),
            on_failed=lambda message, role=role: self._probe_fail(role, message),
        )

    def _probe_ok(self, role: str, result: object) -> None:
        text = str(result)
        self._mark(role, True, text)
        self._log.appendPlainText(f"{role}: {text}")
        self._set_busy(False)

    def _probe_fail(self, role: str, message: str) -> None:
        self._mark(role, False, message)
        self._log.appendPlainText(f"{role} failed: {message}")
        self._set_busy(False)
        self.statusBar().showMessage(f"{role} connection failed")

    def _mark(self, role: str, ok: bool, text: str) -> None:
        self._ok[role] = ok
        labels = {
            _ROLE_SCOPE: self._scope_status,
            _ROLE_GEN: self._gen_status,
            _ROLE_CAMERA: self._camera_status,
        }
        labels[role].setText(text)
        self._sync_actions()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._sync_actions()
        if busy:
            self.statusBar().showMessage("Working…")

    def _sync_actions(self) -> None:
        if not hasattr(self, "_wave_btn"):
            return
        idle = not self._busy
        self._scope_btn.setEnabled(idle)
        self._gen_btn.setEnabled(idle)
        self._camera_btn.setEnabled(idle)
        self._wave_btn.setEnabled(idle and self._ok[_ROLE_SCOPE] and self._wave_ready())
        self._freq_btn.setEnabled(idle and self._ok[_ROLE_SCOPE] and self._ok[_ROLE_GEN])
        if hasattr(self, "_freq_stop_btn"):
            self._freq_stop_btn.setEnabled(self._sweeping)
        self._cam_btn.setEnabled(idle and self._ok[_ROLE_CAMERA])

    def _fill_sessions(self, campaign: str, selected: str | None = None) -> None:
        sessions = list_sessions(campaign) if campaign else []
        self._wave_session.blockSignals(True)
        self._wave_session.clear()
        self._wave_session.addItem("Select a session", "")
        prefer = selected
        for name in sessions:
            self._wave_session.addItem(name, name)
            if prefer is None and name != "Legacy":
                prefer = name
        if prefer:
            index = self._wave_session.findData(prefer)
            if index >= 0:
                self._wave_session.setCurrentIndex(index)
        self._wave_session.blockSignals(False)
        self._update_wave_preview()

    def _on_wave_campaign_changed(self, campaign: str) -> None:
        self._fill_sessions(campaign)

    def _new_session(self) -> None:
        campaign = self._wave_campaign.currentText()
        if not campaign:
            return
        name, ok = QInputDialog.getText(self, "New session", "Session name:")
        if not ok:
            return
        slug = slug_name(name)
        if not slug:
            QMessageBox.warning(self, "Session", "Enter a name that can be used as a folder.")
            return
        campaign_session_data(campaign, slug, create=True)
        self._fill_sessions(campaign, selected=slug)
        self._log.appendPlainText(f"Session folder: {campaign_session_data(campaign, slug)}")

    def _wave_session_name(self) -> str:
        return str(self._wave_session.currentData() or "")

    def _wave_destination(self) -> Path | None:
        campaign = self._wave_campaign.currentText()
        session = self._wave_session_name()
        name = slug_name(self._wave_name.text())
        if not campaign or not session or not name:
            return None
        stem = waveform_stem(int(self._wave_channel.value()), name)
        return campaign_session_data(campaign, session, create=True) / f"{stem}.npz"

    def _wave_ready(self) -> bool:
        dest = self._wave_destination()
        return dest is not None and not dest.exists() and not dest.with_suffix(".json").exists()

    def _update_wave_preview(self) -> None:
        if not hasattr(self, "_wave_preview"):
            return
        name = self._wave_name.text().strip()
        session = self._wave_session_name()
        if not session:
            self._wave_preview.setText("Choose or create a session.")
        elif not slug_name(name):
            self._wave_preview.setText("Enter a measurement name.")
        else:
            dest = self._wave_destination()
            assert dest is not None
            if dest.exists() or dest.with_suffix(".json").exists():
                self._wave_preview.setText(f"{dest.name} already exists in this session.")
            else:
                self._wave_preview.setText(str(dest))
        self._sync_actions()

    def _capture(self) -> None:
        dest = self._wave_destination()
        session = self._wave_session_name()
        name = self._wave_name.text().strip()
        if dest is None:
            QMessageBox.warning(
                self,
                "Capture",
                "Choose a session and a measurement name before capturing.",
            )
            return
        if dest.exists() or dest.with_suffix(".json").exists():
            QMessageBox.warning(
                self,
                "Capture",
                f"{dest.name} already exists. Pick another measurement name.",
            )
            return
        self._set_busy(True)
        self._log.appendPlainText(f"Capturing {name} → {dest}")
        self._workers["run"].start(
            _capture_waveform,
            self._model_id(self._scope_model),
            self._wave_campaign.currentText(),
            session,
            int(self._wave_channel.value()),
            int(self._wave_chunk.value()),
            str(self._wave_window.currentText()),
            name,
            on_finished=self._run_ok,
            on_failed=self._run_fail,
        )

    def _sweep(self) -> None:
        amp_text = self._freq_amp.text().strip()
        amplitude: float | None
        if not amp_text:
            amplitude = None
        else:
            try:
                amplitude = float(amp_text)
            except ValueError:
                QMessageBox.warning(self, "Amplitude", "Amplitude must be a number, or blank.")
                return
            if amplitude <= 0:
                QMessageBox.warning(self, "Amplitude", "Amplitude must be positive.")
                return
        load_text = self._freq_load.text().strip().lower()
        if load_text in {"inf", "highz", "high-z"}:
            load = float("inf")
        else:
            try:
                load = float(load_text)
            except ValueError:
                QMessageBox.warning(self, "Load", "Load must be ohms, or inf.")
                return
        name = self._freq_name.text().strip() or None
        stop_event = Event()
        self._sweeping = True
        self._set_busy(True)
        self._log.appendPlainText("Running frequency sweep…")
        self._workers["run"].start(
            _run_frequency,
            self._model_id(self._scope_model),
            float(self._freq_min.value()),
            float(self._freq_max.value()),
            int(self._freq_points.value()),
            amplitude,
            load,
            int(self._freq_gen_ch.value()),
            int(self._freq_scope_ch.value()),
            float(self._freq_bw.value()),
            int(self._freq_averages.currentData()),
            stop_event,
            name,
            cancel_event=stop_event,
            on_finished=self._run_ok,
            on_failed=self._run_fail,
        )

    def _stop_sweep(self) -> None:
        self._workers["run"].request_stop()
        self._log.appendPlainText("Stopping frequency sweep…")
        self.statusBar().showMessage("Stopping…")
        self._freq_stop_btn.setEnabled(False)

    def _record(self) -> None:
        self._set_busy(True)
        self._log.appendPlainText("Recording…")
        self._workers["run"].start(
            _record_camera,
            self._model_id(self._camera_model),
            self._cam_campaign.currentText(),
            float(self._cam_duration.value()),
            str(self._cam_quality.currentText()),
            on_finished=self._run_ok,
            on_failed=self._run_fail,
        )

    def _run_ok(self, result: object) -> None:
        self._sweeping = False
        text = str(result)
        if text == "cancelled":
            self._log.appendPlainText("Frequency sweep stopped.")
            self._set_busy(False)
            self.statusBar().showMessage("Sweep stopped")
            return
        if text.startswith("cancelled:"):
            path = Path(text.split(":", 1)[1])
            self._log.appendPlainText(f"Sweep stopped; wrote {path}")
            self._set_busy(False)
            self.statusBar().showMessage(f"Sweep stopped; wrote {path.name}")
            return
        path = Path(text)
        json_path = path.with_suffix(".json")
        if path.suffix.lower() == ".npz":
            missing = [str(item) for item in (path, json_path) if not item.is_file()]
            if missing:
                self._log.appendPlainText("Capture missing files: " + ", ".join(missing))
                self._set_busy(False)
                self.statusBar().showMessage("Capture files missing")
                QMessageBox.warning(
                    self,
                    "Capture incomplete",
                    "Expected files were not written:\n" + "\n".join(missing),
                )
                self._update_wave_preview()
                return
            self._log.appendPlainText(f"Wrote {path}")
            self._log.appendPlainText(f"Wrote {json_path}")
            self._update_wave_preview()
        else:
            self._log.appendPlainText(f"Wrote {path}")
        self._set_busy(False)
        self.statusBar().showMessage(f"Wrote {path.name}")

    def _run_fail(self, message: str) -> None:
        self._sweeping = False
        self._log.appendPlainText(f"Measurement failed: {message}")
        self._set_busy(False)
        self.statusBar().showMessage("Measurement failed")
        QMessageBox.warning(self, "Measurement failed", message)


def _create_window(controller: Any) -> MeasurementWindow:
    return MeasurementWindow(get(MEASURE_ID), controller)


register(
    AnalysisSpec(
        id=MEASURE_ID,
        title="Run measurement",
        description=(
            "Confirm oscilloscope, generator, and camera connections, then "
            "capture a waveform, frequency sweep, or video clip."
        ),
        family=FAMILY_MEASUREMENT,
        accepted_kinds=(),
        window_factory=_create_window,
    )
)
