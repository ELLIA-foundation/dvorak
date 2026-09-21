"""Application bootstrap: identity, Qt app, launcher."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget

from . import APP_NAME
from .macos_identity import configure as configure_macos_identity
from .registry import get
from .window import AnalysisWindow


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _ensure_repo_on_path() -> None:
    root = _repo_root()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)


class AppController:
    """Owns the launcher and any analysis windows so Qt does not garbage-collect them."""

    def __init__(self) -> None:
        self._launcher: QWidget | None = None
        self._windows: list[QWidget] = []

    def show_launcher(self) -> QWidget:
        from .launcher import LauncherWindow

        if self._launcher is None:
            launcher = LauncherWindow(self)
            launcher.destroyed.connect(self._on_launcher_destroyed)
            self._launcher = launcher
        self._launcher.show()
        self._launcher.raise_()
        self._launcher.activateWindow()
        return self._launcher

    def open_analysis(self, analysis_id: str) -> QWidget | None:
        spec = get(analysis_id)
        if not spec.enabled:
            return None

        if spec.window_factory is not None:
            window = spec.window_factory(self)
        else:
            window = AnalysisWindow(spec, self)
        self._track(window)
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def quit(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _track(self, window: QWidget) -> None:
        self._windows.append(window)
        window.destroyed.connect(lambda *_args, w=window: self._forget(w))

    def _forget(self, window: QWidget) -> None:
        if window in self._windows:
            self._windows.remove(window)

    def _on_launcher_destroyed(self, *_args: object) -> None:
        self._launcher = None


def main(argv: list[str] | None = None) -> int:
    _ensure_repo_on_path()
    from .analyses import load_plugins

    load_plugins()

    # Must run before QApplication: otherwise the Dock tooltip stays "Python".
    configure_macos_identity(APP_NAME)

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(True)

    controller = AppController()
    # Python attribute (not setProperty): keep the controller alive for the loop.
    app._dvorak_controller = controller
    controller.show_launcher()
    return app.exec()
