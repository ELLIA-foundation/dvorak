"""Background jobs for waveform load, analysis, and ROOT renders. Qt only.

A job cannot be stopped once it runs (a ROOT render blocks in a pipe read),
so a newer request only stops listening to the old one. Each job's thread
and worker therefore stay referenced in ``_RUNNING`` until the thread has
actually finished, and are released on the interface thread.

Earlier versions released them as soon as a new job started, or from the
worker thread when it finished. Python could then delete a ``QThread`` that
was still running (Qt aborts the process) or a worker whose result was still
queued for the interface thread, so the result never arrived and the figure
stayed on "Rendering…".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Slot


class FunctionWorker(QObject):
    """Run ``fn(*args, **kwargs)`` on a QThread and emit the return value.

    Both signals carry the job's generation, so the receiver can tell a
    current result from a stale one without asking which object sent it.
    """

    finished = Signal(int, object)
    failed = Signal(int, str)

    def __init__(
        self,
        generation: int,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.generation = generation
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 — surface any load error in the UI
            self.failed.emit(self.generation, f"{type(exc).__name__}: {exc}")
        else:
            self.finished.emit(self.generation, result)
        finally:
            # Hand the object back to the interface thread, which releases it.
            app = QCoreApplication.instance()
            if app is not None:
                self.moveToThread(app.thread())


class _Reaper(QObject):
    """Lives on the interface thread and releases jobs whose thread has ended."""

    @Slot()
    def reap(self) -> None:
        _RUNNING[:] = [job for job in _RUNNING if not job[0].isFinished()]


_RUNNING: list[tuple[QThread, FunctionWorker]] = []
_REAPER: _Reaper | None = None


def _reaper() -> _Reaper:
    global _REAPER
    if _REAPER is None:
        _REAPER = _Reaper()
    return _REAPER


def wait_for_jobs(timeout_ms: int = 5000) -> bool:
    """Block until every background thread has ended. Call before the app exits.

    Python tears down Qt objects at exit, and destroying a running QThread
    aborts the process. Returns False if a job is still running at the timeout.
    """
    done = True
    for thread, _worker in list(_RUNNING):
        thread.quit()
        if thread.wait(timeout_ms):
            continue
        # Still busy (a long video decode, say). Stopping it is safer at exit
        # than letting Qt destroy a running thread, which aborts the process.
        done = False
        thread.terminate()
        thread.wait(1000)
    _reaper().reap()
    return done


class WorkerHandle(QObject):
    """Start background jobs and deliver the latest one's result.

    Results arrive on the thread that created this object. A plain function
    has no thread affinity, so connecting it with a queued connection runs it
    on the worker. That callback then touches widgets while the interface
    thread is waiting for the GIL, and the window never leaves its placeholder.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on_finished: Callable[[Any], None] | None = None
        self._on_failed: Callable[[str], None] | None = None
        self._generation = 0
        self._current: QThread | None = None

    def start(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_finished: Callable[[Any], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        self.cancel()
        self._on_finished = on_finished
        self._on_failed = on_failed
        thread = QThread()
        worker = FunctionWorker(self._generation, fn, *args, **kwargs)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._relay_finished)
        worker.failed.connect(self._relay_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(_reaper().reap)
        _RUNNING.append((thread, worker))
        self._current = thread
        thread.start()

    def is_running(self) -> bool:
        return self._current is not None and self._current.isRunning()

    @Slot(int, object)
    def _relay_finished(self, generation: int, result: object) -> None:
        callback = self._on_finished
        if generation == self._generation and callback is not None:
            self._on_finished = None
            self._on_failed = None
            callback(result)

    @Slot(int, str)
    def _relay_failed(self, generation: int, message: str) -> None:
        callback = self._on_failed
        if generation == self._generation and callback is not None:
            self._on_finished = None
            self._on_failed = None
            callback(message)

    def cancel(self) -> None:
        """Ignore the running job's result. The job itself runs to completion."""
        self._generation += 1
        self._on_finished = None
        self._on_failed = None
        if self._current is not None and self._current.isRunning():
            self._current.requestInterruption()
        self._current = None
