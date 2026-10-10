"""X-ray lines pane for the X-123 spectrum plot.

Query the line database at an energy, add an element's lines, scan the
spectrum for peaks and identify them, and manage the markers on the plot.
The database and the scan live in ``Measurements/X123_Spectra/Analysis_scripts``
(``linedb.py``, ``peaks.py``); this module is only the Qt side.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QHideEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..campaign_import import load_spectrum_module
from .spectrum_plot import SpectrumPlot

_FAMILY_BOXES = (("K", "K"), ("L", "L"), ("M", "M"), ("gamma", "γ"))
_QUERY_LIMIT = 25
_KIND_TEXT = {"line": "line", "escape": "Si escape", "pileup": "pile-up"}
_ALL = "*"  # scan source meaning every displayed spectrum
# Columns of the Markers table.
_M_SHOW, _M_LABEL, _M_ENERGY, _M_SPECTRUM, _M_COLOR = range(5)


def _swatch(color: str) -> QIcon:
    pixmap = QPixmap(12, 12)
    pixmap.fill(QColor(color or "#888888"))
    return QIcon(pixmap)


def _contrast(color: str) -> QColor:
    c = QColor(color)
    luma = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return QColor("#000000" if luma > 150 else "#ffffff")


def _item(text: str, data: Any = None, *, align_right: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if data is not None:
        item.setData(Qt.ItemDataRole.UserRole, data)
    if align_right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(22)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(True)
    return table


def _selected_rows(table: QTableWidget) -> list[int]:
    return sorted({index.row() for index in table.selectionModel().selectedRows()})


def _spin(lo: float, hi: float, value: float, step: float, decimals: int, suffix: str = "") -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(lo, hi)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setValue(value)
    if suffix:
        box.setSuffix(suffix)
    return box


class LineFinderPanel(QWidget):
    def __init__(
        self,
        plot: SpectrumPlot,
        status: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._plot = plot
        self._status = status or (lambda _text: None)
        self._linedb = load_spectrum_module("linedb")
        self._peaks = load_spectrum_module("peaks")
        self._db = self._linedb.database()
        self._loading_markers = False
        # Scan result per spectrum key, and (key, label, color) of the displayed spectra.
        self._scans: dict[str, Any] = {}
        self._source_sig: list[tuple[str, str, str]] = []
        self._build()
        plot.energy_picked.connect(self._on_picked)
        plot.curves_changed.connect(self._on_curves_changed)
        plot.markers_changed.connect(self._load_markers)
        self._refresh_sources()
        self._load_markers()

    # -- layout ---------------------------------------------------------------

    def _build(self) -> None:
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("All elements (e.g. U Th Bi Ra Pb Fe Cu)")
        self._filter.setToolTip(
            "Restrict queries and the scan to these elements or nuclides. "
            "Separate with spaces or commas; leave empty for all."
        )
        self._filter.textChanged.connect(self._on_filter_changed)
        self._filter_note = QLabel()
        self._filter_note.setStyleSheet("color: #d62728;")

        self._family_boxes: dict[str, QCheckBox] = {}
        families = QHBoxLayout()
        families.setContentsMargins(0, 0, 0, 0)
        for key, text in _FAMILY_BOXES:
            box = QCheckBox(text)
            box.setChecked(True)
            box.setToolTip("Gamma lines of common sources and U/Th chain nuclides" if key == "gamma" else f"{text} lines")
            box.toggled.connect(self._on_filter_changed)
            self._family_boxes[key] = box
            families.addWidget(box)
        self._min_rel = _spin(0.0, 100.0, 5.0, 1.0, 1, " %")
        self._min_rel.setToolTip(
            "Hide lines weaker than this % of the strongest line in their "
            "element's K, L or M family"
        )
        self._min_rel.valueChanged.connect(self._on_filter_changed)
        self._artifacts = QCheckBox("Escape / pile-up")
        self._artifacts.setChecked(True)
        self._artifacts.setToolTip(
            "Also suggest Si Kα escape peaks (line − 1.740 keV) and "
            "pile-up peaks (2 × line) of strong lines"
        )
        self._artifacts.toggled.connect(self._on_filter_changed)
        families.addSpacing(8)
        families.addWidget(QLabel("Min"))
        families.addWidget(self._min_rel)
        families.addWidget(self._artifacts)
        families.addStretch(1)

        filters = QFormLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.addRow("Elements", self._filter)
        filters.addRow("", self._filter_note)
        filters.addRow("Families", families)

        self._target = QComboBox()
        self._target.setToolTip(
            "Spectrum that markers added from Query, Elements and Markers belong to. "
            "A marker is drawn in its spectrum's color unless you set its own."
        )
        filters.addRow("Mark for", self._target)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_query(), "Query")
        self._tabs.addTab(self._build_elements(), "Elements")
        self._tabs.addTab(self._build_scan(), "Auto scan")
        self._tabs.addTab(self._build_markers(), "Markers")

        source = QLabel(f"{len(self._db.lines)} lines · {self._db.source}")
        source.setStyleSheet("color: palette(mid);")

        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(self._tabs, stretch=1)
        layout.addWidget(source)
        self._filter_note.hide()

    def _build_query(self) -> QWidget:
        self._query = QLineEdit()
        self._query.setPlaceholderText("Energies in keV, e.g. 13.6, 16.2")
        self._query.returnPressed.connect(self._run_query)
        find = QPushButton("Find")
        find.clicked.connect(self._run_query)
        self._pick = QPushButton("Pick on plot")
        self._pick.setCheckable(True)
        self._pick.setToolTip("Click the live plot to query the energy under the cursor")
        self._pick.toggled.connect(self._plot.set_pick_mode)
        self._tolerance = _spin(0.005, 2.0, 0.20, 0.01, 3, " keV")
        self._tolerance.setToolTip("Search window either side of each energy")
        self._tolerance.valueChanged.connect(self._run_query)

        row = QHBoxLayout()
        row.addWidget(self._query, stretch=1)
        row.addWidget(QLabel("±"))
        row.addWidget(self._tolerance)
        row.addWidget(find)
        row.addWidget(self._pick)

        self._query_table = _table(
            ["Query", "Candidate", "Appears at", "ΔE", "Rel %", "Line E", "IUPAC", "Kind"]
        )
        self._query_table.doubleClicked.connect(lambda _index: self._add_query_rows())
        add = QPushButton("Add selected to plot")
        add.clicked.connect(self._add_query_rows)
        hint = QLabel("Ranked by closeness × relative intensity. Double-click a row to add it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        bottom = QHBoxLayout()
        bottom.addWidget(hint, stretch=1)
        bottom.addWidget(add)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addLayout(row)
        layout.addWidget(self._query_table, stretch=1)
        layout.addLayout(bottom)
        return page

    def _build_elements(self) -> QWidget:
        self._element_edit = QLineEdit()
        self._element_edit.setPlaceholderText("Elements, e.g. U Th Bi")
        self._element_edit.textChanged.connect(self._list_element_lines)
        self._in_view = QCheckBox("Only lines in view")
        self._in_view.setChecked(True)
        self._in_view.toggled.connect(self._list_element_lines)
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Re-list after zooming the plot")
        refresh.clicked.connect(self._list_element_lines)
        row = QHBoxLayout()
        row.addWidget(self._element_edit, stretch=1)
        row.addWidget(self._in_view)
        row.addWidget(refresh)

        self._element_table = _table(["Line", "Energy", "Rel %", "IUPAC", "Family"])
        self._element_table.doubleClicked.connect(lambda _index: self._add_element_rows(selected=True))
        add_sel = QPushButton("Add selected")
        add_sel.clicked.connect(lambda: self._add_element_rows(selected=True))
        add_all = QPushButton("Add all listed")
        add_all.clicked.connect(lambda: self._add_element_rows(selected=False))
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(add_sel)
        bottom.addWidget(add_all)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addLayout(row)
        layout.addWidget(self._element_table, stretch=1)
        layout.addLayout(bottom)
        return page

    def _build_scan(self) -> QWidget:
        self._source = QComboBox()
        self._source.setToolTip(
            "Spectrum to scan (raw counts, before smoothing or Y mode), or all "
            "displayed spectra. Peaks and labels take each spectrum's color."
        )
        self._sigma = _spin(1.0, 20.0, 3.0, 0.5, 1, " σ")
        self._sigma.setToolTip(
            "Keep peaks whose net area (counts minus SNIP background) is at least "
            "this many standard deviations, σ = √(net + 2 × background)"
        )
        self._min_e = _spin(0.0, 200.0, 1.5, 0.1, 2, " keV")
        self._min_e.setToolTip("Ignore peaks below this energy (noise edge)")
        self._noise = _spin(0.05, 1.0, 0.25, 0.01, 3, " keV")
        self._noise.setToolTip(
            "Electronic noise FWHM. Expected peak FWHM is "
            "√(noise² + 2.355²·F·w·E) with F = 0.115, w = 3.62 eV. "
            "Raise it if found peaks are wider than expected."
        )
        self._tol_factor = _spin(0.25, 4.0, 1.0, 0.25, 2, " ×")
        self._tol_factor.setToolTip("Match window, in units of half the expected FWHM (+30 eV)")
        self._coverage = _spin(0.1, 1.0, 0.5, 0.05, 2)
        self._coverage.setToolTip(
            "A family (e.g. U L) is accepted when this intensity-weighted fraction "
            "of its in-range lines (≥ 10 %) match peaks"
        )
        self._visible_only = QCheckBox("Visible range only")
        self._show_peaks = QCheckBox("Mark peaks")
        self._show_peaks.setChecked(True)
        self._show_peaks.toggled.connect(self._sync_peak_marks)
        scan = QPushButton("Scan")
        scan.setDefault(True)
        scan.clicked.connect(self._run_scan)

        form = QFormLayout()
        form.addRow("Spectrum", self._source)
        params = QHBoxLayout()
        params.addWidget(QLabel("Threshold"))
        params.addWidget(self._sigma)
        params.addWidget(QLabel("From"))
        params.addWidget(self._min_e)
        params.addWidget(QLabel("Noise"))
        params.addWidget(self._noise)
        params.addStretch(1)
        form.addRow(params)
        match = QHBoxLayout()
        match.addWidget(QLabel("Tolerance"))
        match.addWidget(self._tol_factor)
        match.addWidget(QLabel("Coverage"))
        match.addWidget(self._coverage)
        match.addWidget(self._visible_only)
        match.addStretch(1)
        match.addWidget(self._show_peaks)
        match.addWidget(scan)
        form.addRow(match)

        self._scan_summary = QLabel("Scan finds peaks, then names the element families that explain them.")
        self._scan_summary.setWordWrap(True)
        self._scan_table = _table(
            ["Spectrum", "Peak keV", "Net", "σ", "FWHM", "Assignment", "Alternatives"]
        )
        self._scan_table.doubleClicked.connect(self._query_scan_row)
        self._replace_auto = QCheckBox("Replace earlier auto labels")
        self._replace_auto.setToolTip(
            "Remove the earlier auto labels of the spectra being labelled. "
            "Other spectra's labels stay."
        )
        self._replace_auto.setChecked(True)
        add = QPushButton("Add to plot")
        add.setToolTip(
            "Label the selected peaks, or every identified peak when none is selected. "
            "Each label belongs to its spectrum and takes its color."
        )
        add.clicked.connect(self._add_scan_rows)
        hint = QLabel("Scans of different spectra accumulate. Double-click a peak to query it.")
        hint.setStyleSheet("color: palette(mid);")
        bottom = QHBoxLayout()
        bottom.addWidget(hint, stretch=1)
        bottom.addWidget(self._replace_auto)
        bottom.addWidget(add)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addLayout(form)
        layout.addWidget(self._scan_summary)
        layout.addWidget(self._scan_table, stretch=1)
        layout.addLayout(bottom)
        return page

    def _build_markers(self) -> QWidget:
        self._marker_table = _table(["Show", "Label", "Energy (keV)", "Spectrum", "Color"])
        self._marker_table.itemChanged.connect(self._on_marker_edited)
        self._marker_table.cellDoubleClicked.connect(self._on_marker_double_clicked)
        self._custom_label = QLineEdit()
        self._custom_label.setPlaceholderText("Label")
        self._custom_energy = _spin(0.0, 1000.0, 10.0, 0.01, 3, " keV")
        add = QPushButton("Add marker")
        add.setToolTip("Add a free marker for the spectrum chosen in Mark for")
        add.clicked.connect(self._add_custom)
        row = QHBoxLayout()
        row.addWidget(self._custom_label, stretch=1)
        row.addWidget(self._custom_energy)
        row.addWidget(add)

        color = QPushButton("Set color…")
        color.setToolTip("Give the selected markers their own color (double-click a Color cell too)")
        color.clicked.connect(lambda: self._set_marker_color())
        follow = QPushButton("Follow spectrum")
        follow.setToolTip("Draw the selected markers in their spectrum's color again")
        follow.clicked.connect(self._follow_spectrum)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove_markers)
        clear_auto = QPushButton("Clear auto")
        clear_auto.clicked.connect(lambda: self._clear_markers("auto"))
        clear_all = QPushButton("Clear all")
        clear_all.clicked.connect(lambda: self._clear_markers(None))
        buttons = QHBoxLayout()
        buttons.addWidget(color)
        buttons.addWidget(follow)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        buttons.addWidget(clear_auto)
        buttons.addWidget(clear_all)

        hint = QLabel(
            "A marker is drawn in its spectrum's color, and follows it if that color "
            "changes, until you set its own. Labels and energies are editable. Markers "
            "are sent to ROOT and saved in recipes."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addLayout(row)
        layout.addWidget(self._marker_table, stretch=1)
        layout.addLayout(buttons)
        layout.addWidget(hint)
        return page

    # -- filters --------------------------------------------------------------

    def _families(self) -> tuple[str, ...]:
        return tuple(key for key, box in self._family_boxes.items() if box.isChecked())

    def _elements(self) -> set[str] | None:
        found, _unknown = self._linedb.parse_elements(self._filter.text(), self._db)
        return found

    def _on_filter_changed(self, *_args: object) -> None:
        _found, unknown = self._linedb.parse_elements(self._filter.text(), self._db)
        self._filter_note.setText(f"Not in the database: {', '.join(unknown)}" if unknown else "")
        self._filter_note.setVisible(bool(unknown))
        if self._query.text().strip():
            self._run_query()
        self._list_element_lines()

    # -- query ----------------------------------------------------------------

    def _on_picked(self, energy: float) -> None:
        self._query.setText(f"{energy:.3f}")
        self._tabs.setCurrentIndex(0)
        self._run_query()

    def query(self, energies: list[float]) -> None:
        self._query.setText(", ".join(f"{e:.3f}" for e in energies))
        self._tabs.setCurrentIndex(0)
        self._run_query()

    def _run_query(self, *_args: object) -> None:
        energies = self._linedb.parse_energies(self._query.text())
        table = self._query_table
        table.setRowCount(0)
        if not energies:
            return
        families = self._families()
        elements = self._elements()
        rows = 0
        for energy in energies:
            found = self._db.candidates(
                energy,
                self._tolerance.value(),
                min_rel=self._min_rel.value(),
                families=families,
                elements=elements,
                artifacts=self._artifacts.isChecked(),
            )[:_QUERY_LIMIT]
            for cand in found:
                table.insertRow(rows)
                line = cand.line
                values = [
                    _item(f"{energy:.3f}", align_right=True),
                    _item(cand.label, cand),
                    _item(f"{cand.energy_kev:.3f}", align_right=True),
                    _item(f"{cand.delta_kev:+.3f}", align_right=True),
                    _item(f"{line.rel:.1f}", align_right=True),
                    _item(f"{line.energy_kev:.4f}", align_right=True),
                    _item(line.iupac),
                    _item(_KIND_TEXT.get(cand.kind, cand.kind)),
                ]
                for column, item in enumerate(values):
                    table.setItem(rows, column, item)
                rows += 1
        if rows:
            table.selectRow(0)
        self._status(
            f"{rows} candidate(s) within ±{self._tolerance.value():.3f} keV"
            if rows
            else "No lines in range; widen the tolerance or lower Min %"
        )

    def _add_query_rows(self) -> None:
        markers = []
        for row in _selected_rows(self._query_table):
            cand = self._query_table.item(row, 1).data(Qt.ItemDataRole.UserRole)
            markers.append({"label": cand.label, "energy": cand.energy_kev, "source": "manual"})
        self._add(markers)

    # -- elements -------------------------------------------------------------

    def _list_element_lines(self, *_args: object) -> None:
        table = self._element_table
        table.setRowCount(0)
        found, _unknown = self._linedb.parse_elements(self._element_edit.text(), self._db)
        if not found:
            return
        lo, hi = (0.0, float("inf"))
        if self._in_view.isChecked():
            lo, hi = self._plot.view_x_range()
        lines = self._db.select(
            lo, hi, min_rel=self._min_rel.value(), families=self._families(), elements=found
        )
        lines.sort(key=lambda line: (line.z, line.element, line.family, -line.rel))
        for row, line in enumerate(lines):
            table.insertRow(row)
            values = [
                _item(line.name, line),
                _item(f"{line.energy_kev:.4f}", align_right=True),
                _item(f"{line.rel:.1f}", align_right=True),
                _item(line.iupac),
                _item("γ" if line.family == "gamma" else line.family),
            ]
            for column, item in enumerate(values):
                table.setItem(row, column, item)

    def _add_element_rows(self, *, selected: bool) -> None:
        table = self._element_table
        rows = _selected_rows(table) if selected else list(range(table.rowCount()))
        markers = []
        for row in rows:
            line = table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            markers.append({"label": line.name, "energy": line.energy_kev, "source": "manual"})
        self._add(markers)

    # -- auto scan ------------------------------------------------------------

    def _on_curves_changed(self) -> None:
        self._refresh_sources()
        self._paint_markers()

    def _source_label(self, key: str) -> str:
        for k, label, _color in self._source_sig:
            if k == key:
                return label
        return ""

    def _refresh_sources(self) -> None:
        sources = [
            (key, label, self._plot.source_color(key) or "")
            for key, label in self._plot.scan_sources()
        ]
        if sources == self._source_sig:
            return
        self._source_sig = sources
        old_source = self._source.currentData()
        old_target = self._target.currentData()
        self._source.blockSignals(True)
        self._target.blockSignals(True)
        self._source.clear()
        self._target.clear()
        self._source.addItem("All displayed spectra", _ALL)
        for key, label, color in sources:
            self._source.addItem(_swatch(color), label, key)
            self._target.addItem(_swatch(color), label, key)
        self._target.addItem("No spectrum", "")
        self._source.setCurrentIndex(max(0, self._source.findData(old_source)))
        found = self._target.findData(old_target) if old_target is not None else -1
        self._target.setCurrentIndex(found if found >= 0 else 0)
        self._source.blockSignals(False)
        self._target.blockSignals(False)
        keys = {key for key, _label, _color in sources}
        stale = [key for key in self._scans if key not in keys]
        if stale:
            for key in stale:
                del self._scans[key]
            self._sync_peak_marks()
        self._fill_scan_table()

    def _run_scan(self) -> None:
        choice = self._source.currentData()
        keys = [k for k, _l, _c in self._source_sig] if choice == _ALL else [choice]
        keys = [k for k in keys if k]
        if not keys:
            self._status("Select a spectrum to scan")
            return
        lo = self._min_e.value()
        hi = None
        if self._visible_only.isChecked():
            v0, v1 = self._plot.view_x_range()
            lo, hi = max(lo, v0), v1
        families = tuple(f for f in self._families() if f != "gamma") or ("K", "L", "M")
        done = 0
        for key in keys:
            data = self._plot.scan_counts(key)
            if data is None:
                continue
            energy, counts, _live = data
            self._scans[key] = self._peaks.scan(
                energy,
                counts,
                self._db,
                sigma=self._sigma.value(),
                min_energy_kev=lo,
                max_energy_kev=hi,
                noise_kev=self._noise.value(),
                tolerance_factor=self._tol_factor.value(),
                elements=self._elements(),
                families=families,
                min_coverage=self._coverage.value(),
                artifacts=self._artifacts.isChecked(),
            )
            done += 1
        self._fill_scan_table()
        self._sync_peak_marks()
        found = sum(len(self._scans[k].peaks) for k in keys if k in self._scans)
        named = sum(
            1 for k in keys if k in self._scans for p in self._scans[k].peaks if p.labels
        )
        self._status(f"{done} spectrum(s): {found} peak(s), {named} identified")

    def _fill_scan_table(self) -> None:
        table = self._scan_table
        table.setRowCount(0)
        lines = []
        row = 0
        for key, label, color in self._source_sig:
            result = self._scans.get(key)
            if result is None:
                continue
            if result.families:
                names = ", ".join(f"{m.name} ({100 * m.coverage:.0f} %)" for m in result.families)
            elif result.peaks:
                names = "peaks found, no family matched"
            else:
                names = "no peaks above threshold"
            lines.append(f"{label}: {names}" if len(self._scans) > 1 else f"Identified: {names}")
            for peak in result.peaks:
                table.insertRow(row)
                alts = "; ".join(c.label for c in peak.alternatives[:4])
                fwhm = f"{peak.fwhm_kev:.3f}" if peak.fwhm_kev else "—"
                spectrum = _item(label, (key, peak))
                spectrum.setIcon(_swatch(color))
                values = [
                    spectrum,
                    _item(f"{peak.energy_kev:.3f}", align_right=True),
                    _item(f"{peak.net_counts:.0f}", align_right=True),
                    _item(f"{peak.significance:.1f}", align_right=True),
                    _item(fwhm, align_right=True),
                    _item(peak.assignment or "?"),
                    _item(alts),
                ]
                for column, item in enumerate(values):
                    table.setItem(row, column, item)
                row += 1
        if lines:
            self._scan_summary.setText("\n".join(lines))
        else:
            self._scan_summary.setText(
                "Scan finds peaks, then names the element families that explain them."
            )
        if any(r.peaks == [] for r in self._scans.values()) and len(self._scans) == 1:
            self._scan_summary.setText(
                "No peaks above threshold. Lower the σ threshold, raise Noise, or sum spectra first."
            )

    def _sync_peak_marks(self, *_args: object) -> None:
        on = self._show_peaks.isChecked()
        self._plot.set_peak_marks(
            {k: [p.energy_kev for p in r.peaks] for k, r in self._scans.items()} if on else {}
        )

    def _scan_row_data(self, row: int) -> tuple[str, Any]:
        return self._scan_table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def _query_scan_row(self, index: Any) -> None:
        _key, peak = self._scan_row_data(index.row())
        self.query([peak.energy_kev])

    def _add_scan_rows(self) -> None:
        rows = _selected_rows(self._scan_table)
        if rows:
            chosen = [self._scan_row_data(r) for r in rows]
        else:
            chosen = [
                (key, peak)
                for key, _label, _color in self._source_sig
                if key in self._scans
                for peak in self._scans[key].peaks
                if peak.labels
            ]
        markers = [
            {
                "label": peak.assignment or f"{peak.energy_kev:.2f} keV",
                "energy": peak.energy_kev,
                "source": "auto",
                "spectrum": key,
            }
            for key, peak in chosen
        ]
        if not markers:
            self._status("Nothing to add: scan first, or select peaks")
            return
        if self._replace_auto.isChecked():
            keys = {m["spectrum"] for m in markers}
            kept = [
                m
                for m in self._plot.markers()
                if not (m["source"] == "auto" and m["spectrum"] in keys)
            ]
            self._plot.set_markers(kept + markers)
            self._status(f"Labelled {len(markers)} peak(s)")
            return
        self._add(markers)

    # -- markers --------------------------------------------------------------

    def _add(self, markers: list[dict[str, Any]]) -> None:
        if not markers:
            self._status("Select one or more rows first")
            return
        target = self._target.currentData() or ""
        for marker in markers:
            marker.setdefault("spectrum", target)
        added = self._plot.add_markers(markers)
        self._status(f"Added {added} marker(s)" if added else "Already on the plot")

    def _add_custom(self) -> None:
        energy = self._custom_energy.value()
        label = self._custom_label.text().strip() or f"{energy:.3f} keV"
        self._add([{"label": label, "energy": energy, "source": "manual"}])

    def _spectrum_text(self, key: str) -> str:
        if not key:
            return "—"
        return self._source_label(key) or "(not displayed)"

    def _paint_markers(self) -> None:
        """Refresh each row's spectrum name and color from the plot's current colors."""
        table = self._marker_table
        markers = self._plot.markers()
        if table.rowCount() != len(markers):
            return
        self._loading_markers = True
        try:
            for row, marker in enumerate(markers):
                color = self._plot.marker_color(marker)
                item = table.item(row, _M_COLOR)
                item.setText(color if marker["color"] else f"{color} (spectrum)")
                item.setBackground(QBrush(QColor(color)))
                item.setForeground(QBrush(_contrast(color)))
                table.item(row, _M_SPECTRUM).setText(self._spectrum_text(marker["spectrum"]))
        finally:
            self._loading_markers = False

    def _load_markers(self) -> None:
        self._loading_markers = True
        table = self._marker_table
        table.setRowCount(0)
        for row, marker in enumerate(self._plot.markers()):
            table.insertRow(row)
            show = QTableWidgetItem()
            show.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            show.setCheckState(Qt.CheckState.Checked if marker["show"] else Qt.CheckState.Unchecked)
            label = QTableWidgetItem(marker["label"])
            energy = QTableWidgetItem(f"{marker['energy']:.4f}")
            energy.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            cells = (show, label, energy, _item(""), _item(""))
            for column, item in enumerate(cells):
                table.setItem(row, column, item)
        self._loading_markers = False
        self._paint_markers()

    def _collect_markers(self) -> list[dict[str, Any]]:
        """Table edits applied to the plot's markers (rows are in the same order)."""
        table = self._marker_table
        old = self._plot.markers()
        out = []
        for row in range(table.rowCount()):
            if row >= len(old):
                break
            marker = dict(old[row])
            try:
                marker["energy"] = float(table.item(row, _M_ENERGY).text())
            except ValueError:
                pass
            marker["show"] = table.item(row, _M_SHOW).checkState() == Qt.CheckState.Checked
            marker["label"] = table.item(row, _M_LABEL).text().strip()
            out.append(marker)
        return out

    def _on_marker_edited(self, _item: QTableWidgetItem) -> None:
        if self._loading_markers:
            return
        self._plot.set_markers(self._collect_markers())

    def _on_marker_double_clicked(self, row: int, column: int) -> None:
        if column == _M_COLOR:
            self._set_marker_color([row])

    def _set_marker_color(self, rows: list[int] | None = None) -> None:
        rows = rows or _selected_rows(self._marker_table)
        markers = self._plot.markers()
        rows = [r for r in rows if r < len(markers)]
        if not rows:
            self._status("Select markers to color")
            return
        color = QColorDialog.getColor(QColor(self._plot.marker_color(markers[rows[0]])), self, "Marker color")
        if not color.isValid():
            return
        for row in rows:
            markers[row]["color"] = color.name()
        self._plot.set_markers(markers)

    def _follow_spectrum(self) -> None:
        rows = _selected_rows(self._marker_table)
        markers = self._plot.markers()
        if not rows:
            self._status("Select markers first")
            return
        for row in rows:
            if row < len(markers):
                markers[row]["color"] = ""
        self._plot.set_markers(markers)

    def _remove_markers(self) -> None:
        rows = set(_selected_rows(self._marker_table))
        if not rows:
            self._status("Select markers to remove")
            return
        kept = [m for i, m in enumerate(self._plot.markers()) if i not in rows]
        self._plot.set_markers(kept)

    def _clear_markers(self, source: str | None) -> None:
        kept = [] if source is None else [m for m in self._plot.markers() if m["source"] != source]
        self._plot.set_markers(kept)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        # A hidden pane must not leave the plot swallowing clicks.
        self._pick.setChecked(False)
        super().hideEvent(event)
