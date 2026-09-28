"""Off-versus-on frames and projections for one solenoid clip."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

_OFF = "#1f77b4"
_ON = "#ff7f0e"


def pack_frames(result: dict[str, Any]) -> dict[str, Any]:
    """Plain numbers and bytes, so the worker result can cross threads."""
    packed: dict[str, Any] = {"dark": bool(result["dark"])}
    for key in ("off", "on"):
        image = np.ascontiguousarray(result[key]["image"], dtype=np.float32)
        packed[key] = {
            "shape": (int(image.shape[0]), int(image.shape[1])),
            "image": image.tobytes(),
            "x": [float(value) for value in result[key]["x"]],
            "y": [float(value) for value in result[key]["y"]],
            "t0": float(result[key]["t0"]),
            "t1": float(result[key]["t1"]),
        }
    return packed


class SolenoidFramesDialog(QDialog):
    """Median frame and projections for current off and current on."""

    def __init__(self, clip_name: str, packed: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{clip_name} off / on")
        self.resize(960, 720)
        off = _as_image(packed["off"])
        on = _as_image(packed["on"])
        low, high = _display_range(off, on)

        images = QHBoxLayout()
        images.addWidget(_frame_column("Current off", packed["off"], off, low, high))
        images.addWidget(_frame_column("Current on", packed["on"], on, low, high))

        note = QLabel(
            "Dark frame subtracted, the 1 s before tube on."
            if packed.get("dark")
            else "Frames are the camera image."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")

        x_plot = _projection_plot(
            "x [px]",
            packed["off"]["x"],
            packed["on"]["x"],
        )
        y_plot = _projection_plot(
            "row from top [px]",
            packed["off"]["y"],
            packed["on"]["y"],
        )

        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(images)
        layout.addWidget(x_plot, stretch=1)
        layout.addWidget(y_plot, stretch=1)


def _as_image(payload: dict[str, Any]) -> np.ndarray:
    height, width = payload["shape"]
    flat = np.frombuffer(payload["image"], dtype=np.float32)
    return flat.reshape(height, width).copy()


def _display_range(off: np.ndarray, on: np.ndarray) -> tuple[float, float]:
    combined = np.concatenate((off.ravel(), on.ravel()))
    if combined.size == 0:
        return 0.0, 1.0
    low = float(np.percentile(combined, 2))
    high = float(np.percentile(combined, 99.5))
    if high <= low:
        high = low + 1.0
    return low, high


def _frame_column(
    title: str,
    payload: dict[str, Any],
    image: np.ndarray,
    low: float,
    high: float,
) -> QWidget:
    column = QWidget()
    layout = QVBoxLayout(column)
    layout.setContentsMargins(0, 0, 0, 0)
    caption = QLabel(f"{title}  {payload['t0']:.2f}–{payload['t1']:.2f} s")
    caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
    scaled = np.clip((image - low) / (high - low), 0.0, 1.0)
    gray = np.ascontiguousarray((scaled * 255.0).astype(np.uint8))
    height, width = gray.shape
    qimage = QImage(gray.data, width, height, width, QImage.Format.Format_Grayscale8).copy()
    picture = QLabel()
    picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
    picture.setPixmap(
        QPixmap.fromImage(qimage).scaledToHeight(280, Qt.TransformationMode.SmoothTransformation)
    )
    layout.addWidget(caption)
    layout.addWidget(picture)
    return column


def _projection_plot(xlabel: str, off: list[float], on: list[float]) -> pg.PlotWidget:
    plot = pg.PlotWidget()
    plot.setBackground("w")
    plot.showGrid(x=True, y=True, alpha=0.25)
    plot.setLabel("bottom", xlabel)
    plot.setLabel("left", "intensity")
    plot.addLegend()
    plot.plot(off, pen=pg.mkPen(_OFF, width=1.5), name="current off")
    plot.plot(on, pen=pg.mkPen(_ON, width=1.5), name="current on")
    plot.setMinimumHeight(160)
    return plot
