"""Frequency-response viewer: two-panel matplotlib canvas."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from lib.paths import CAMPAIGN_FREQUENCY_RESPONSES

from ..campaign_import import load_campaign_module
from ..catalog import CaptureRecord
from ..kinds import KIND_TABLE
from ..registry import FAMILY_ANALYSIS, AnalysisSpec, get, register
from ..widgets.spec_canvas import SpecCanvas
from ..window import AnalysisWindow, _format_record

FREQ_ID = "frequency_response"
_plot = load_campaign_module(CAMPAIGN_FREQUENCY_RESPONSES, "frequency_plot")


class FrequencyWindow(AnalysisWindow):
    def __init__(self, spec: AnalysisSpec, controller: Any) -> None:
        self._view: SpecCanvas | None = None
        self._meta: dict[str, Any] = {}
        super().__init__(spec, controller)

    def _workspace_panes(self) -> list[QWidget]:
        self._view = SpecCanvas(self, empty="Open a frequency-response table.")

        heading = QLabel("Capture")
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("Select a frequency-response table.")
        meta = QWidget()
        meta_layout = QVBoxLayout(meta)
        meta_layout.addWidget(heading)
        meta_layout.addWidget(self._detail, stretch=1)
        return [self._view, meta]

    def _handle_opened(self, record: CaptureRecord) -> None:
        self._meta = dict(record.metadata)
        rows = _rows_for(record)
        self._detail.setPlainText(_format_record(record, accepted=True))
        if self._view is None:
            return
        if not rows:
            self._view.clear("This table has no frequency rows.")
            self.statusBar().showMessage("This table has no frequency rows.")
            return
        spec = _plot.frequency_spec(rows, self._meta.get("scope_bw_hz"))
        spec["name"] = record.stem
        png = _png_for(record)
        if png is not None:
            self._view.set_pdf_default(png.with_suffix(".pdf"))
        self._view.set_spec(spec)
        self.statusBar().showMessage(record.stem)


def _rows_for(record: CaptureRecord) -> list[dict]:
    rows = record.metadata.get("rows")
    if isinstance(rows, list) and rows:
        return [row for row in rows if isinstance(row, dict)]
    path = record.path
    if path.suffix.lower() == ".json" and path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        listed = payload.get("rows")
        if isinstance(listed, list):
            return [row for row in listed if isinstance(row, dict)]
    if path.suffix.lower() == ".csv" and path.is_file():
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        except OSError:
            return []
    return []


def _png_for(record: CaptureRecord | None) -> Path | None:
    if record is None:
        return None
    return record.path.parent / "plots" / f"{record.stem}.png"


def _create_window(controller: Any) -> FrequencyWindow:
    return FrequencyWindow(get(FREQ_ID), controller)


register(
    AnalysisSpec(
        id=FREQ_ID,
        title="Frequency response",
        description="Plot V_scope / V_nominal, measured gain, phase, and sine THD versus frequency.",
        family=FAMILY_ANALYSIS,
        accepted_kinds=(KIND_TABLE,),
        window_factory=_create_window,
    )
)
