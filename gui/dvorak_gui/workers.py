"""Background jobs for waveform load (and later analysis). Qt only."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Qt, Signal


class FunctionWorker(QObject):
    """Run ``fn(*args, **kwargs)`` on a QThread and emit the return value."""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 — surface any load error in the UI
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(result)


class WorkerHandle:
    """Own a QThread + worker so the caller can cancel and wait on close."""

    def __init__(self) -> None:
        self._thread: QThread | None = None
        self._worker: FunctionWorker | None = None

    def start(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_finished: Callable[[Any], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        self.cancel()
        thread = QThread()
        worker = FunctionWorker(fn, *args, **kwargs)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        if on_finished is not None:
            worker.finished.connect(
                on_finished, Qt.ConnectionType.QueuedConnection
            )
        if on_failed is not None:
            worker.failed.connect(on_failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda *_args, owned=thread: self._clear_if_current(owned))
        self._thread = thread
        self._worker = worker
        thread.start()

    def cancel(self) -> None:
        worker = self._worker
        thread = self._thread
        if worker is not None:
            try:
                worker.finished.disconnect()
                worker.failed.disconnect()
            except RuntimeError:
                pass
        if thread is None:
            self._clear()
            return
        thread.requestInterruption()
        thread.quit()
        thread.wait(200)
        self._clear()

    def _clear_if_current(self, thread: QThread) -> None:
        if self._thread is thread:
            self._clear()

    def _clear(self) -> None:
        self._thread = None
        self._worker = None
