"""Edit the base legend name of each overlaid spectrum."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_KEY_ROLE = Qt.ItemDataRole.UserRole


class LegendNamesDialog(QDialog):
    """``rows`` are ``(key, default label, current name)``; selected metadata
    fields are appended to the name on the plot."""

    def __init__(
        self,
        rows: list[tuple[str, str, str]],
        suffix_hint: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Legend names")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        hint = "Edit the base name for each trace. Metadata chosen from the Capture panel is appended."
        if suffix_hint:
            hint += f"\nAppended now: {suffix_hint}"
        label = QLabel(hint)
        label.setWordWrap(True)
        layout.addWidget(label)

        self._table = QTableWidget(len(rows), 2)
        self._table.setHorizontalHeaderLabels(["Spectrum", "Legend name"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._defaults: dict[str, str] = {}
        for row, (key, default, name) in enumerate(rows):
            self._defaults[key] = default
            first = QTableWidgetItem(default)
            first.setFlags(Qt.ItemFlag.ItemIsEnabled)
            first.setData(_KEY_ROLE, key)
            self._table.setItem(row, 0, first)
            self._table.setItem(row, 1, QTableWidgetItem(name or default))
        layout.addWidget(self._table, stretch=1)

        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._reset)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reset(self) -> None:
        for row in range(self._table.rowCount()):
            key = self._table.item(row, 0).data(_KEY_ROLE)
            self._table.item(row, 1).setText(self._defaults[key])

    def names(self) -> dict[str, str]:
        """Only names that differ from the default label."""
        out: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            key = self._table.item(row, 0).data(_KEY_ROLE)
            text = self._table.item(row, 1).text().strip()
            if text and text != self._defaults[key]:
                out[key] = text
        return out
