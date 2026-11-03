"""
Choosing the project: the start dialog with the recent projects and Choose folder, and the setup question for a
folder that is not a project yet. Local Fox starts with them, before the window, and Open project… uses them too.

Opening is always deliberate: nothing is selected at first, Enter does nothing, and a project opens only with
Open or a double click. A folder that is not a project gets the setup question, which lists what setting it up
creates, with Create .local-fox and Back (to the projects). A drive root or system folder is never set up or
opened (tools_setup.refusal). Closing a dialog (X, Esc) cancels: at the start that quits Local Fox, later it
keeps the open project.
"""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QKeyEvent
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QVBoxLayout)

from .. import APP_NAME, __version__, recent_projects, tools_setup
from ..config import config_path
from . import theme
from .logo import fox_pixmap

BACK = 2  # The setup question's result for Back; Accepted (1) is Create, Rejected (0) is closing it
DESCRIPTION = "Python-based harness for running small local models using Ollama and ollmcp-generic-tools."
def _button(text: str, slot, primary: bool = False) -> QPushButton:
    button = QPushButton(text, objectName="primary" if primary else "")
    # No default button: Enter must not open or create anything
    button.setAutoDefault(False)
    button.setDefault(False)
    button.clicked.connect(slot)
    return button

class _NoEnterDialog(QDialog):
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            return  # Opening and creating are clicks, never an Enter by accident
        super().keyPressEvent(event)

class ProjectDialog(_NoEnterDialog):
    """
    The recent projects, Choose folder, and Open config folder (the folder of config.json, e.g. to fix it before a
    project opens). After exec() returns Accepted, chosen is the folder.
    """

    def __init__(self, parent=None, startup: bool = True):
        super().__init__(parent)
        self.chosen: Path | None = None
        self.setWindowTitle(APP_NAME)
        self.resize(860, 500)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addSpacing(14)  # The fox's dots reach its left edge, so it gets room of its own from the window's side
        fox = QLabel()
        fox.setPixmap(fox_pixmap(theme.ACCENT))
        fox.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.addWidget(fox)
        header.addSpacing(16)
        about = QVBoxLayout()
        about.addStretch(1)
        name = QHBoxLayout()
        name.addWidget(QLabel(APP_NAME, objectName="title"))
        name.addSpacing(6)
        name.addWidget(QLabel(__version__, objectName="version"), 0, Qt.AlignBottom)
        name.addStretch(1)
        about.addLayout(name)
        description = QLabel(DESCRIPTION)
        description.setWordWrap(True)
        about.addWidget(description)
        about.addStretch(1)
        header.addLayout(about, 1)
        layout.addLayout(header)
        layout.addSpacing(8)
        intro = QLabel("Choose a project folder where you want the local AI to run in. Local Fox will generate .local-fox folder "
                       "to the project folder if it doesn't exist already. The generated folder is used to store the "
                       "project-specific configuration and instruction files.", objectName="dim")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.list = QListWidget()
        # Long paths are shortened in the middle, where they differ least, instead of scrolling sideways
        self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemDoubleClicked.connect(lambda item: self._open())
        self.list.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.list, 1)
        # Shown instead of the list when it is empty: a text, not a row that could be hovered or clicked
        self.empty = QLabel("No recent projects.", objectName="dim")
        self.empty.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(self.empty, 1)
        self._fill()

        buttons = QHBoxLayout()
        buttons.addWidget(_button("Quit" if startup else "Cancel", self.reject))
        buttons.addWidget(_button("Open config folder", self._open_config_folder))
        buttons.addStretch(1)
        choose = _button("Choose folder", self._choose_folder)
        buttons.addWidget(choose)
        self.open_button = _button("Open", self._open, primary=True)
        self.open_button.setEnabled(False)
        buttons.addWidget(self.open_button)
        layout.addLayout(buttons)
        # Not the list: a list with the focus marks its first row, which would look like a choice made for the user
        choose.setFocus()

    def _fill(self) -> None:
        self.list.clear()
        projects = recent_projects.load()
        for project in projects:
            if not project.is_dir():
                state = "  (missing)"
            elif not tools_setup.is_set_up(project):
                state = "  (not set up)"
            else:
                state = ""
            item = QListWidgetItem(f"{project.name or project.as_posix()}{state}\n    {project.as_posix()}")
            item.setData(Qt.UserRole, str(project))
            item.setToolTip(str(project))
            if not project.is_dir():
                item.setFlags(Qt.NoItemFlags)  # Shown so that the user sees it is gone; removed with the menu
                item.setForeground(QColor(theme.DARK))
            self.list.addItem(item)
        self.list.setVisible(bool(projects))
        self.empty.setVisible(not projects)
        self.list.clearSelection()
        self.list.setCurrentRow(-1)

    def _selected(self) -> Path | None:
        items = self.list.selectedItems()
        return Path(items[0].data(Qt.UserRole)) if items and items[0].data(Qt.UserRole) else None

    def _selection_changed(self) -> None:
        self.open_button.setEnabled(self._selected() is not None)

    def _menu(self, point) -> None:
        item = self.list.itemAt(point)
        if not item or not item.data(Qt.UserRole):
            return
        menu = QMenu(self)
        menu.addAction("Remove from the list", lambda: (recent_projects.remove(Path(item.data(Qt.UserRole))), self._fill()))
        menu.exec(self.list.viewport().mapToGlobal(point))

    def _open(self) -> None:
        project = self._selected()
        if project:
            self.chosen = project
            self.accept()

    def _open_config_folder(self) -> None:
        folder = config_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _choose_folder(self) -> None:
        start = self._selected() or (recent_projects.load() or [Path.home()])[0]
        folder = QFileDialog.getExistingDirectory(self, "Choose the project folder", str(start if start.is_dir() else Path.home()))
        if folder:
            self.chosen = Path(folder)
            self.accept()

class SetupDialog(_NoEnterDialog):
    """
    The question for a folder that is not a project: what setting it up creates, with Create .local-fox and Back.
    A folder that must not be set up gets the reason and Back only. exec() returns Accepted for Create, BACK for
    Back, and Rejected when it is closed.
    """

    def __init__(self, project: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME}: set up a project")
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        reason = tools_setup.refusal(project)
        changes: list[str] = []
        if not reason and not tools_setup.bundled_version():
            reason = "This Local Fox has no bundled tools to set it up with."
        if not reason:
            try:
                changes = tools_setup.planned(project)
            except tools_setup.SetupRefused as error:
                reason = str(error)
        title = "This folder cannot be a project" if reason else "Set up this folder as a project?"
        layout.addWidget(QLabel(title, objectName="accent"))
        path = QLabel(project.as_posix())
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path.setWordWrap(True)
        layout.addWidget(path)
        if reason:
            text = f"{reason}\n\nGo back and choose a project's own folder."
        else:
            listed = "\n".join(f"    {change}" for change in changes)
            text = (f"It is not a project yet. Setting it up creates or changes:\n\n{listed}\n\nThe AI's tools can then read and "
                    "change the files in this folder and its subfolders, except the paths you forbid. Nothing is created "
                    "unless you choose \"Create .local-fox\".")
        body = QLabel(text, objectName="dim" if not reason else "error")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(body, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(_button("Back", lambda: self.done(BACK)))
        buttons.addStretch(1)
        if not reason:
            buttons.addWidget(_button("Create .local-fox", self.accept, primary=True))
        layout.addLayout(buttons)

def choose_project(parent=None, startup: bool = True, folder: Path | None = None) -> tuple[Path, str] | None:
    """
    Asks for the project: the start dialog (or the given folder first), then the setup question for a folder that is
    not a project. Returns the project and the setup's report ('' if it was a project already), or None if the user
    closed a dialog: at the start Local Fox then quits, later the open project stays.
    """
    while True:
        if folder is None:
            dialog = ProjectDialog(parent, startup)
            if dialog.exec() != QDialog.Accepted or dialog.chosen is None:
                return None
            folder = dialog.chosen
        folder = Path(folder).resolve()
        if not folder.is_dir():
            QMessageBox.warning(parent, APP_NAME, f"{folder.as_posix()} does not exist.")
            folder = None
            continue
        if tools_setup.is_set_up(folder):
            return folder, ""
        answer = SetupDialog(folder, parent).exec()
        if answer == BACK:
            folder = None
            continue
        if answer != QDialog.Accepted:
            return None
        try:
            return folder, tools_setup.set_up(folder)
        except Exception as error:
            QMessageBox.critical(parent, APP_NAME, f"{folder.as_posix()} was not set up: {error}")
            folder = None
