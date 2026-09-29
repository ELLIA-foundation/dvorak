"""Interactive matplotlib view for a JSON figure spec."""

from __future__ import annotations

from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..mpl_spec import figure_from_spec


class SpecCanvas(QWidget):
    """One figure spec on a Qt matplotlib canvas, with PDF export."""

    def __init__(
        self,
        parent: QWidget | None = None,
        empty: str = "Nothing to draw yet.",
    ) -> None:
        super().__init__(parent)
        self._empty = empty
        self._spec: dict | None = None
        self._export_spec: dict | None = None
        self._default_pdf = Path("figure.pdf")

        self._figure = Figure()
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)
        self._message = QLabel(empty)
        self._message.setWordWrap(True)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._message)
        self._stack.addWidget(self._canvas)

        self._pdf = QPushButton("Save PDF…")
        self._pdf.clicked.connect(self._save_pdf)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self._status, stretch=1)
        buttons.addWidget(self._pdf)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._stack, stretch=1)
        layout.addLayout(buttons)
        self._show_message(self._empty)

    def shutdown(self) -> None:
        return

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
        self._spec = spec
        self._export_spec = export_spec or spec
        if spec is None:
            self._show_message(message or self._empty)
            self._status.setText("")
            self._pdf.setEnabled(False)
            return
        try:
            figure_from_spec(spec, self._figure)
        except Exception as exc:  # noqa: BLE001 — show the draw error in the pane
            self._show_message(str(exc))
            self._status.setText("Draw failed.")
            self._pdf.setEnabled(self.current_spec() is not None)
            return
        self._canvas.draw()
        self._stack.setCurrentWidget(self._canvas)
        self._toolbar.setVisible(True)
        self._status.setText("")
        self._pdf.setEnabled(True)

    def _show_message(self, text: str) -> None:
        self._message.setText(text)
        self._stack.setCurrentWidget(self._message)
        self._toolbar.setVisible(False)
        self._pdf.setEnabled(False)

    def _save_pdf(self) -> None:
        spec = self.current_spec()
        if spec is None:
            return
        name = str(spec.get("name") or "figure")
        default = self._default_pdf
        if default.name in {"figure.pdf", ""}:
            default = Path(f"{name}.pdf")
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            "Save PDF",
            str(default),
            "PDF (*.pdf)",
        )
        if not chosen:
            return
        path = Path(chosen)
        try:
            fig = figure_from_spec(spec)
            path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(path)
        except Exception as exc:  # noqa: BLE001
            self._status.setText("PDF export failed.")
            QMessageBox.warning(self.window(), "Save PDF failed", str(exc))
            return
        self._status.setText(f"Wrote {path}")
