"""Background jobs for waveform load (and later analysis). Qt only."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot


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
        self.generation = 0

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 — surface any load error in the UI
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(result)


class WorkerHandle(QObject):
    """Own a QThread + worker so the caller can cancel and wait on close.

    Results are delivered on the thread that created this object. A plain
    function has no thread affinity, so connecting it with a queued
    connection runs it on the worker. That callback then touches widgets
    while the interface thread is waiting for the GIL, and the window
    never leaves its placeholder.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: FunctionWorker | None = None
        self._on_finished: Callable[[Any], None] | None = None
        self._on_failed: Callable[[str], None] | None = None
        self._generation = 0

    def start(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_finished: Callable[[Any], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        self.cancel()
        self._generation += 1
        self._on_finished = on_finished
        self._on_failed = on_failed
        thread = QThread()
        worker = FunctionWorker(fn, *args, **kwargs)
        worker.generation = self._generation
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(
            self._relay_finished, Qt.ConnectionType.QueuedConnection
        )
        worker.failed.connect(self._relay_failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda *_args, owned=thread: self._clear_if_current(owned))
        self._thread = thread
        self._worker = worker
        thread.start()

    def _current_result(self) -> bool:
        worker = self.sender()
        return getattr(worker, "generation", None) == self._generation

    @Slot(object)
    def _relay_finished(self, result: object) -> None:
        callback = self._on_finished
        if callback is not None and self._current_result():
            callback(result)

    @Slot(str)
    def _relay_failed(self, message: str) -> None:
        callback = self._on_failed
        if callback is not None and self._current_result():
            callback(message)

    def cancel(self) -> None:
        worker = self._worker
        thread = self._thread
        self._on_finished = None
        self._on_failed = None
        if worker is not None:
            try:
                worker.finished.disconnect(self._relay_finished)
                worker.failed.disconnect(self._relay_failed)
            except RuntimeError:
                pass
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
