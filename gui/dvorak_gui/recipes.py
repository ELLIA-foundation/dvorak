"""Plot recipes shared by every analysis window.

A recipe is a small JSON file: ``{"version", "kind": <analysis id>, ...state}``.
Spectra and captures are named by path relative to ``Measurements/`` (absolute
when a file lives elsewhere), so recipes survive moving the repo.

``RecipeMixin`` gives a window File → Save recipe… / Open recipe… that start in
``Measurements/Plot_recipes/`` and remember the last subfolder used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMessageBox

from lib.paths import measurements_dir, plot_recipes_dir

RECIPE_VERSION = 1
RECIPE_FILTER = "Plot recipe (*.json)"


def path_key(path: Path | str) -> str:
    path = Path(path)
    try:
        return path.resolve().relative_to(measurements_dir().resolve()).as_posix()
    except ValueError:
        return str(path)


def key_path(key: str) -> Path:
    path = Path(key)
    return path if path.is_absolute() else measurements_dir() / path


def write_recipe(path: Path, kind: str, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": RECIPE_VERSION, "kind": kind, **state}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_recipe(path: Path, kind: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    found = None
    if isinstance(payload, dict):
        # Spark-gap recipes written before the shared format used "analysis".
        found = payload.get("kind") or payload.get("analysis")
    if found != kind:
        raise ValueError(
            f"{Path(path).name} is a {found or 'unrecognised'} recipe, not {kind}"
        )
    return payload


class RecipeMixin:
    """Window hooks: set ``recipe_kind`` and override the two ``_recipe_*`` methods."""

    recipe_kind = ""
    _recipe_dir: Path | None = None

    def _recipe_state(self) -> dict[str, Any] | None:
        """State to save, or ``None`` (after telling the user why) to cancel."""
        raise NotImplementedError

    def _apply_recipe(self, state: dict[str, Any]) -> None:
        raise NotImplementedError

    def _recipe_default_name(self) -> str:
        return "recipe"

    def _install_recipe_actions(self, file_menu, before) -> None:
        save = QAction("Save recipe…", self)
        save.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save.triggered.connect(self._save_recipe)
        load = QAction("Open recipe…", self)
        load.setShortcut(QKeySequence("Ctrl+Shift+O"))
        load.triggered.connect(self._open_recipe)
        file_menu.insertAction(before, save)
        file_menu.insertAction(before, load)

    def _recipe_start_dir(self) -> Path:
        last = self._recipe_dir
        if last is not None and last.is_dir():
            return last
        return plot_recipes_dir()

    def _save_recipe(self) -> None:
        state = self._recipe_state()
        if state is None:
            return
        default = self._recipe_start_dir() / f"{self._recipe_default_name()}.json"
        chosen, _filter = QFileDialog.getSaveFileName(
            self, "Save plot recipe", str(default), RECIPE_FILTER
        )
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        try:
            write_recipe(path, self.recipe_kind, state)
        except OSError as exc:
            QMessageBox.warning(self, "Save recipe", str(exc))
            return
        self._recipe_dir = path.parent
        self.statusBar().showMessage(f"Saved recipe {path}")

    def _open_recipe(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            self, "Open plot recipe", str(self._recipe_start_dir()), RECIPE_FILTER
        )
        if not chosen:
            return
        path = Path(chosen)
        try:
            state = read_recipe(path, self.recipe_kind)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Open recipe", str(exc))
            return
        self._recipe_dir = path.parent
        self.statusBar().showMessage(f"Opening {path.name}…")
        self._apply_recipe(state)
