"""Camera role: model-agnostic video capture interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from lib.video import VideoCapture


class Camera(ABC):
    """A bench camera that can identify itself and record a video clip."""

    model_id: str

    def __init__(self, connection: dict | None = None) -> None:
        self.connection = dict(connection or {})

    def __enter__(self) -> Camera:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @abstractmethod
    def connect(self) -> None:
        """Open a session to the instrument."""

    @abstractmethod
    def close(self) -> None:
        """Release the session."""

    @abstractmethod
    def identify(self) -> str:
        """Return a human-readable identity string."""

    @abstractmethod
    def record(self, duration_s: float, output_path: Path, **kwargs) -> VideoCapture:
        """Record video for ``duration_s`` seconds into ``output_path``."""
