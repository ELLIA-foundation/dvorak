"""Schema-driven parameter form (OPIXE-style)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from ..registry import Option


def format_number(value: object) -> str:
    if value is None:
        return ""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)
    return f"{number:g}"


def parse_number(text: str) -> float | None:
    cleaned = (text or "").strip().replace(" ", "")
    if not cleaned:
        return None
    return float(cleaned)


class ParamForm(QWidget):
    """Build editors from ``Option`` rows; ``values()`` / ``set_values()`` round-trip."""

    def __init__(
        self,
        options: tuple[Option, ...] | list[Option],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._options = tuple(options)
        self._widgets: dict[str, tuple[Option, QWidget]] = {}
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for option in self._options:
            widget = self._make_widget(option)
            if option.help:
                widget.setToolTip(option.help)
            label = option.label
            if option.unit:
                label = f"{label} ({option.unit})"
            layout.addRow(label, widget)
            self._widgets[option.key] = (option, widget)

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, (option, widget) in self._widgets.items():
            out[key] = self._read(option, widget)
        return out

    def set_values(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            pair = self._widgets.get(key)
            if pair is None:
                continue
            option, widget = pair
            self._write(option, widget, value)

    def reset_defaults(self) -> None:
        for option, widget in self._widgets.values():
            self._write(option, widget, option.default)

    def _make_widget(self, option: Option) -> QWidget:
        if option.type == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(option.default))
            return widget
        if option.type == "integer":
            widget = QSpinBox()
            widget.setRange(-1_000_000_000, 1_000_000_000)
            widget.setValue(int(option.default or 0))
            return widget
        if option.type == "choice":
            widget = QComboBox()
            for choice in option.choices:
                widget.addItem(choice, choice)
            if option.default is not None:
                index = widget.findData(option.default)
                if index < 0:
                    index = widget.findText(str(option.default))
                if index >= 0:
                    widget.setCurrentIndex(index)
            return widget
        widget = QLineEdit(format_number(option.default))
        if option.type == "number":
            widget.setPlaceholderText("empty" if option.optional else "e.g. 1e-7")
        return widget

    def _read(self, option: Option, widget: QWidget) -> Any:
        if option.type == "bool":
            assert isinstance(widget, QCheckBox)
            return widget.isChecked()
        if option.type == "integer":
            assert isinstance(widget, QSpinBox)
            return int(widget.value())
        if option.type == "choice":
            assert isinstance(widget, QComboBox)
            data = widget.currentData()
            return data if data is not None else widget.currentText()
        assert isinstance(widget, QLineEdit)
        text = widget.text()
        if option.type != "number":
            return text
        parsed = parse_number(text)
        if parsed is None:
            if option.optional:
                return None
            raise ValueError(f"{option.label} needs a number")
        return parsed

    def _write(self, option: Option, widget: QWidget, value: Any) -> None:
        if option.type == "bool":
            assert isinstance(widget, QCheckBox)
            widget.setChecked(bool(value))
            return
        if option.type == "integer":
            assert isinstance(widget, QSpinBox)
            widget.setValue(int(value or 0))
            return
        if option.type == "choice":
            assert isinstance(widget, QComboBox)
            index = widget.findData(value)
            if index < 0:
                index = widget.findText(str(value))
            if index >= 0:
                widget.setCurrentIndex(index)
            return
        assert isinstance(widget, QLineEdit)
        widget.setText(format_number(value))
