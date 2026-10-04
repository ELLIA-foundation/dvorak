"""Create an X-123 session folder and copy Amptek ``.mca`` files into it."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lib.paths import (
    collect_mca_files,
    copy_mca_pairs,
    ensure_spectrum_session,
    plan_mca_copy,
    resolve_spectrum_session,
    spectra_dir,
)

from .figure_gallery import open_local_path

_PATH_ROLE = Qt.ItemDataRole.UserRole


class NewSessionDialog(QDialog):
    """Name a session and choose ``.mca`` files to copy into its Data folder."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        initial_files: list[Path] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New session")
        self.setMinimumWidth(560)
        self._files: list[Path] = []

        layout = QVBoxLayout(self)
        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. 2_10_26_uranium")
        layout.addWidget(QLabel("Session name"))
        layout.addWidget(self._name)

        self._preview = QLabel()
        self._preview.setWordWrap(True)
        self._preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._preview.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._preview)

        layout.addWidget(QLabel("MCA files"))
        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self._list)

        buttons = QHBoxLayout()
        add_files = QPushButton("Add files…")
        add_files.clicked.connect(self._add_files)
        add_folder = QPushButton("Add folder…")
        add_folder.clicked.connect(self._add_folder)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_selected)
        buttons.addWidget(add_files)
        buttons.addWidget(add_folder)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._problem = QLabel()
        self._problem.setWordWrap(True)
        self._problem.setStyleSheet("color: #c0392b;")
        layout.addWidget(self._problem)

        self._box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._create = self._box.button(QDialogButtonBox.StandardButton.Ok)
        self._create.setText("Create")
        layout.addWidget(self._box)

        self._box.accepted.connect(self.accept)
        self._box.rejected.connect(self.reject)
        self._name.textChanged.connect(self._refresh)
        self._add_paths(initial_files or [])
        self._refresh()

    def session_name(self) -> str:
        return self._name.text().strip()

    def files(self) -> list[Path]:
        return list(self._files)

    def _add_files(self) -> None:
        chosen, _selected = QFileDialog.getOpenFileNames(
            self,
            "MCA files",
            "",
            "Amptek MCA (*.mca)",
        )
        self._add_paths([Path(path) for path in chosen])

    def _add_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Folder of MCA files")
        if chosen:
            self._add_paths([Path(chosen)])

    def _add_paths(self, paths: list[Path]) -> None:
        have = {path.resolve() for path in self._files}
        for path in collect_mca_files(paths):
            resolved = path.resolve()
            if resolved in have:
                continue
            have.add(resolved)
            self._files.append(path)
            item = QListWidgetItem(path.name)
            item.setData(_PATH_ROLE, path)
            item.setToolTip(str(path))
            self._list.addItem(item)
        self._refresh()

    def _remove_selected(self) -> None:
        drop = {
            item.data(_PATH_ROLE).resolve()
            for item in self._list.selectedItems()
            if isinstance(item.data(_PATH_ROLE), Path)
        }
        if not drop:
            return
        self._files = [path for path in self._files if path.resolve() not in drop]
        self._list.clear()
        for path in self._files:
            item = QListWidgetItem(path.name)
            item.setData(_PATH_ROLE, path)
            item.setToolTip(str(path))
            self._list.addItem(item)
        self._refresh()

    def _refresh(self) -> None:
        problems: list[str] = []
        name = self.session_name()
        folder = ""
        if not name:
            problems.append("A session name is required.")
            self._preview.setText(f"Folder:  {spectra_dir()}/<session>/Data/")
        else:
            try:
                folder = resolve_spectrum_session(name)
            except ValueError as exc:
                problems.append(str(exc))
                self._preview.setText("")
            else:
                dest = spectra_dir() / folder / "Data"
                exists = (spectra_dir() / folder).is_dir()
                state = "add files to the existing session" if exists else "create this session"
                self._preview.setText(f"Will {state}:\n{dest}")
        count = len(self._files)
        if folder and count == 0:
            self._preview.setText(
                self._preview.text() + "\nNo files selected — the Data folder will be created empty."
            )
        self._problem.setText("\n".join(problems))
        self._create.setEnabled(not problems)
        self._create.setText("Add files" if folder and (spectra_dir() / folder).is_dir() and count else "Create")


def run_mca_import(
    parent: QWidget,
    *,
    campaign: str | None = None,
    sources: list[Path] | None = None,
) -> tuple[str, list[Path]] | None:
    """Copy MCA files into a session. ``None`` means the user cancelled.

    ``campaign`` set imports into that existing folder. Otherwise a dialog
    asks for the session name. ``sources`` skips the file picker when dropping
    files onto an existing session.
    """
    if campaign is None:
        dialog = NewSessionDialog(parent, initial_files=sources or [])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        name = dialog.session_name()
        files = dialog.files()
    else:
        name = campaign
        if sources is None:
            chosen, _selected = QFileDialog.getOpenFileNames(
                parent,
                f"Import MCA files into {campaign}",
                "",
                "Amptek MCA (*.mca)",
            )
            if not chosen:
                return None
            files = [Path(path) for path in chosen]
        else:
            files = list(sources)

    try:
        folder = resolve_spectrum_session(name)
        existed = (spectra_dir() / folder).is_dir()
        dest = ensure_spectrum_session(folder)
    except ValueError as exc:
        QMessageBox.warning(parent, "New session", str(exc))
        return None

    if not files:
        _offer_open_folder(parent, dest, created=not existed)
        return folder, []

    fresh, collisions = plan_mca_copy(dest, files)
    pairs = list(fresh)
    if collisions:
        names = "\n".join(src.name for src, _target in collisions[:12])
        extra = ""
        if len(collisions) > 12:
            extra = f"\n… and {len(collisions) - 12} more"
        answer = QMessageBox.question(
            parent,
            "Replace existing files?",
            f"{len(collisions)} file(s) already exist in {folder}/Data:\n\n{names}{extra}\n\nReplace them?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return None
        if answer == QMessageBox.StandardButton.Yes:
            pairs.extend(collisions)
    if not pairs:
        QMessageBox.information(
            parent,
            "Import MCA files",
            "Those files are already in this session.",
        )
        return folder, []

    written, errors = copy_mca_pairs(pairs)
    if errors:
        QMessageBox.warning(
            parent,
            "Import MCA files",
            "Some files were not copied:\n\n" + "\n".join(errors),
        )
    return folder, written


def _offer_open_folder(parent: QWidget, dest: Path, *, created: bool) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle("Session created" if created else "Session")
    if created:
        box.setText("Copy .mca files into this Data folder, then click Refresh.")
    else:
        box.setText("This session already exists. Its Data folder is ready for more .mca files.")
    box.setInformativeText(str(dest))
    open_button = box.addButton("Open Data folder", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is open_button:
        open_local_path(dest)
