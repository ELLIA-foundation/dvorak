"""Render a figure spec with ROOT into a JSROOT view, ignoring stale replies.

Shared by every analysis whose live plot can be swapped for the ROOT canvas
(the Generate ROOT button).
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from dvorak_root.layout import fitted_size

from .jsrootview import JsRootView
from .rootbridge import RootBridge
from .workers import WorkerHandle


class RootCanvasRenderer:
    def __init__(
        self,
        parent: QWidget,
        bridge: RootBridge,
        worker: WorkerHandle,
        view: JsRootView,
        status=None,
    ) -> None:
        self._parent = parent
        self._bridge = bridge
        self._worker = worker
        self._view = view
        self._status = status or (lambda _text: None)
        self._gen = 0

    def attach_bundle(self, report: dict) -> None:
        """Hand the JSROOT bundle path to the view once ROOT has been probed."""
        path = str(report.get("jsroot") or "")
        if path:
            self._view.set_bundle(path)

    def render(self, spec: dict, room: tuple[int, int], on_drawn) -> None:
        """Draw ``spec`` fitted to ``room``; ``on_drawn(json, height)`` follows."""
        if not self._bridge.client.available or not self._view.usable:
            report = self._bridge.report or {}
            QMessageBox.warning(
                self._parent,
                "ROOT",
                str(report.get("error") or "ROOT is not ready yet. Try again in a moment."),
            )
            return
        size = fitted_size(spec, *room)
        self._gen += 1
        gen = self._gen
        self._status("Generating ROOT plot…")
        client = self._bridge.client

        def job() -> str:
            return str(client.render(spec, outputs=("json",), size=size).get("json") or "")

        def done(result: object) -> None:
            if gen != self._gen:
                return
            if isinstance(result, str) and result:
                on_drawn(result, size[1])
                self._status("ROOT plot")
            else:
                self._status("ROOT returned no canvas")

        def failed(message: str) -> None:
            if gen != self._gen:
                return
            self._status("")
            QMessageBox.warning(self._parent, "Generate ROOT", message)

        self._worker.start(job, on_finished=done, on_failed=failed)
