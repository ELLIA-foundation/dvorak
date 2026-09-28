"""Opt-in solenoid timing for the campaign currently selected in Video Analysis."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..campaign_import import load_video_module
from .param_form import format_number, parse_number

_FIELD_LABELS = (
    ("tube_on_s", "Tube on [s]"),
    ("current_on_s", "Current on [s]"),
    ("current_off_s", "Current off [s]"),
    ("tube_off_s", "Tube off [s]"),
)


class SolenoidPanel(QGroupBox):
    """Campaign cycle.json editor. Empty per-clip fields use the campaign times."""

    enable_requested = Signal()
    disable_requested = Signal()
    times_edited = Signal(object, dict)
    frames_requested = Signal()
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Solenoid measurement", parent)
        self._module = load_video_module("solenoid")
        self._campaign: str | None = None
        self._clip_stem: str | None = None
        self._cycle: dict[str, Any] | None = None
        self._prefer_clip = False
        self._scope_key: str | None = None
        self._frames_busy = False
        self._editors: dict[str, QLineEdit] = {}
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._build()
        self.set_context(None, None, None, None)

    def set_context(
        self,
        campaign: str | None,
        clip_stem: str | None,
        cycle: dict[str, Any] | None,
        rising_edge_s: float | None,
    ) -> None:
        self._campaign = campaign
        self._clip_stem = clip_stem
        self._cycle = None if cycle is None else _copy_cycle(cycle)
        self._scope.blockSignals(True)
        self._scope.clear()
        self._scope.addItem("Campaign times", "campaign")
        if clip_stem:
            self._scope.addItem("This clip", clip_stem)
            if self._prefer_clip:
                self._scope.setCurrentIndex(1)
        self._scope.blockSignals(False)
        self._scope_key = self._scope.currentData()
        enabled = self._cycle is not None and campaign is not None
        self._enable.setVisible(not enabled and campaign is not None)
        self._empty.setVisible(campaign is None)
        self._form.setVisible(enabled)
        self._fill_fields()
        self.set_rising_edge(rising_edge_s)
        if not enabled:
            self._summary.clear()

    def set_rising_edge(self, rising_edge_s: float | None) -> None:
        if rising_edge_s is None:
            self._edge.clear()
            self._edge.setVisible(False)
            return
        self._edge.setText(f"Detected rising edge {rising_edge_s:.4g} s")
        self._edge.setVisible(True)

    def set_summary(self, text: str) -> None:
        self._summary.setText(text)

    def set_frames_enabled(self, enabled: bool) -> None:
        self._frames_allowed = enabled
        if not self._frames_busy:
            self._frames.setEnabled(enabled)

    def set_frames_busy(self, busy: bool) -> None:
        self._frames_busy = busy
        self._frames.setText("Reading frames…" if busy else "Off / on frames")
        self._frames.setEnabled(False if busy else self._frames_allowed)

    def commit(self) -> bool:
        """Write the visible fields. Returns False when a field is not a number."""
        if self._cycle is None or self._scope_key is None or not self._form.isVisible():
            return False
        times = self._parse_fields()
        if times is None:
            return False
        self._store_local(self._scope_key, times)
        self._emit_times(self._scope_key, times)
        return True

    def _build(self) -> None:
        self._enable = QPushButton("Run as solenoid measurement")
        self._enable.setToolTip(
            "Turn solenoid timing on for this campaign. "
            "Tube on starts from the detected rising edge when the clip has one."
        )
        self._enable.clicked.connect(self.enable_requested.emit)

        self._empty = QLabel("Select a campaign.")
        self._empty.setStyleSheet("color: palette(mid);")

        self._form = QWidget()
        form = QVBoxLayout(self._form)
        form.setContentsMargins(0, 0, 0, 0)

        self._scope = QComboBox()
        self._scope.setToolTip("Campaign times apply to every clip. This clip stores only the fields you fill.")
        self._scope.currentIndexChanged.connect(self._on_scope_changed)

        self._preset = QPushButton("3 s cycle")
        self._preset.setToolTip(
            "From tube on: current off for 3 s, current on for 3 s, current off for 3 s. "
            "Fills current on, current off, and tube off. The fields stay editable."
        )
        self._preset.clicked.connect(self._on_preset)

        self._frames = QPushButton("Off / on frames")
        self._frames.setToolTip(
            "Average a short stretch in the first current-off window and the current-on window, "
            "then show those frames and their X and Y projections."
        )
        self._frames.clicked.connect(self.frames_requested.emit)
        self._frames_allowed = False
        self._frames.setEnabled(False)

        self._remove = QPushButton("Remove timing")
        self._remove.setToolTip("Delete this campaign's cycle.json.")
        self._remove.clicked.connect(self.disable_requested.emit)

        buttons = QHBoxLayout()
        buttons.addWidget(self._scope, stretch=1)
        buttons.addWidget(self._preset)
        buttons.addWidget(self._frames)
        buttons.addWidget(self._remove)
        form.addLayout(buttons)

        grid = QGridLayout()
        for column, (key, label) in enumerate(_FIELD_LABELS):
            grid.addWidget(QLabel(label), 0, column)
            editor = QLineEdit()
            editor.setMaximumWidth(120)
            editor.setToolTip("Seconds from the start of the clip.")
            editor.editingFinished.connect(self._on_field_edited)
            self._editors[key] = editor
            grid.addWidget(editor, 1, column)
        form.addLayout(grid)

        self._clip_hint = QLabel("Empty fields use the campaign times.")
        self._clip_hint.setStyleSheet("color: palette(mid);")
        form.addWidget(self._clip_hint)

        self._edge = QLabel()
        self._edge.setStyleSheet("color: palette(mid);")
        form.addWidget(self._edge)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        form.addWidget(self._summary)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.addWidget(self._enable)
        layout.addWidget(self._empty)
        layout.addWidget(self._form)

    def _on_scope_changed(self) -> None:
        new_key = self._scope.currentData()
        previous = self._scope_key
        if previous is not None and previous != new_key and self._cycle is not None:
            times = self._parse_fields()
            if times is not None:
                self._store_local(previous, times)
                self._emit_times(previous, times)
        self._scope_key = new_key
        self._prefer_clip = new_key not in (None, "campaign")
        self._fill_fields()

    def _on_field_edited(self) -> None:
        if self._cycle is None or self._scope_key is None:
            return
        times = self._parse_fields()
        if times is None:
            return
        self._store_local(self._scope_key, times)
        self._emit_times(self._scope_key, times)

    def _on_preset(self) -> None:
        if self._cycle is None or self._scope_key is None:
            return
        times = self._parse_fields()
        if times is None:
            return
        if times.get("tube_on_s") is None and self._scope_key != "campaign":
            campaign_tube = self._cycle.get("tube_on_s")
            if campaign_tube is not None:
                times["tube_on_s"] = float(campaign_tube)
        try:
            updated = self._module.apply_three_second(times)
        except ValueError as exc:
            self.status_message.emit(str(exc))
            return
        self._set_editor_text(updated)
        self._store_local(self._scope_key, updated)
        self._emit_times(self._scope_key, updated)

    def _parse_fields(self) -> dict[str, float | None] | None:
        times: dict[str, float | None] = {}
        for key, editor in self._editors.items():
            try:
                times[key] = parse_number(editor.text())
            except ValueError:
                self.status_message.emit("Enter times in seconds.")
                return None
        return times

    def _fill_fields(self) -> None:
        self._clip_hint.setVisible(self._scope_key not in (None, "campaign"))
        if self._cycle is None:
            return
        campaign_values = {key: self._cycle.get(key) for key, _label in _FIELD_LABELS}
        if self._scope_key in (None, "campaign"):
            values = campaign_values
            placeholders = {key: "" for key, _label in _FIELD_LABELS}
        else:
            override = (self._cycle.get("clips") or {}).get(self._scope_key, {})
            values = {key: override.get(key) for key, _label in _FIELD_LABELS}
            placeholders = {key: format_number(campaign_values[key]) for key, _label in _FIELD_LABELS}
        for key, editor in self._editors.items():
            editor.blockSignals(True)
            editor.setText(format_number(values.get(key)))
            editor.setPlaceholderText(placeholders[key])
            editor.blockSignals(False)

    def _set_editor_text(self, times: dict[str, float | None]) -> None:
        for key, editor in self._editors.items():
            editor.blockSignals(True)
            editor.setText(format_number(times.get(key)))
            editor.blockSignals(False)

    def _emit_times(self, scope_key: str, times: dict[str, float | None]) -> None:
        clip_stem = None if scope_key == "campaign" else scope_key
        self.times_edited.emit(clip_stem, times)

    def _store_local(self, scope: str, times: dict[str, float | None]) -> None:
        if self._cycle is None:
            return
        if scope == "campaign":
            for key, _label in _FIELD_LABELS:
                self._cycle[key] = times.get(key)
            return
        clips = self._cycle.setdefault("clips", {})
        stored = {key: value for key, value in times.items() if value is not None}
        if stored:
            clips[scope] = stored
        else:
            clips.pop(scope, None)


def _copy_cycle(cycle: dict[str, Any]) -> dict[str, Any]:
    clips = {
        stem: dict(values)
        for stem, values in (cycle.get("clips") or {}).items()
        if isinstance(values, dict)
    }
    copied = {key: cycle.get(key) for key, _label in _FIELD_LABELS}
    copied["clips"] = clips
    return copied
