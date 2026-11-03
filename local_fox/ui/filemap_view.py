"""
The filemap: the project folder as a tree, with forbidden files darker, read-only files marked, ignored folders
dimmed, and the files the AI changed in the session highlighted. It refreshes when files change. Its menu adds
paths to the prompt, and lets the user allow, forbid or make read-only a path in the project's tools config.
"""
from pathlib import Path

from PySide6.QtCore import QDir, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import QFileSystemModel, QMenu, QTreeView

from .. import tools_config
from ..filemap import FORBIDDEN, IGNORED, READ_ONLY, Rules
from . import theme

class ProjectModel(QFileSystemModel):
    def __init__(self):
        super().__init__()
        self.rules: Rules | None = None
        self.changed: set[Path] = set()
        self._states: dict[str, str] = {}
        self.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.Hidden | QDir.System)
        self.setReadOnly(True)

    def set_rules(self, rules: Rules) -> None:
        self.rules = rules
        self._states.clear()

    def state(self, path: str) -> str:
        if path not in self._states:
            self._states[path] = self.rules.state(Path(path)) if self.rules else ""
        return self._states[path]

    def _changed(self, path: Path) -> bool:
        return path in self.changed or (self.isDir(self.index(str(path))) and any(p.is_relative_to(path) for p in self.changed))

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if index.column() == 0 and role in (Qt.DisplayRole, Qt.ForegroundRole, Qt.ToolTipRole, Qt.FontRole):
            path = self.filePath(index)
            state = self.state(path)
            if role == Qt.DisplayRole:
                name = super().data(index, role)
                return f"{name}  ro" if state == READ_ONLY else name
            if role == Qt.ForegroundRole:
                if self._changed(Path(path).resolve()):
                    return QBrush(QColor(theme.ACCENT))
                if state == FORBIDDEN:
                    return QBrush(QColor(theme.DARK))
                if state == IGNORED:
                    return QBrush(QColor(theme.DIM))
                return QBrush(QColor(theme.TEXT))
            if role == Qt.FontRole and Path(path).resolve() in self.changed:
                font = QFont()
                font.setBold(True)
                return font
            if role == Qt.ToolTipRole:
                notes = {FORBIDDEN: "forbidden to the tools", IGNORED: "ignored by the tools", READ_ONLY: "read-only for the tools"}
                note = notes.get(state, "")
                if Path(path).resolve() in self.changed:
                    note = "changed by the AI in this session" + (f", {note}" if note else "")
                return f"{path}\n{note}" if note else path
        return super().data(index, role)

    def refresh_marks(self) -> None:
        self._states.clear()
        root = self.index(self.rootPath())
        self.dataChanged.emit(root, root)

class FilemapView(QTreeView):
    add_to_prompt = Signal(str)
    change_rule = Signal(str, str, bool)  # Path list of the tools config, the path, add (or remove)

    def __init__(self):
        super().__init__()
        self.model_ = ProjectModel()
        self.setModel(self.model_)
        for column in (1, 2, 3):
            self.hideColumn(column)
        self.setHeaderHidden(True)
        self.setExpandsOnDoubleClick(False)
        self.setAnimated(False)
        self.setIndentation(14)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.clicked.connect(self._clicked)
        self.full_paths = False

    def set_project(self, project: Path, rules: Rules) -> None:
        self.model_.set_rules(rules)
        self.model_.setRootPath(str(project))
        self.setRootIndex(self.model_.index(str(project)))

    def set_rules(self, rules: Rules) -> None:
        self.model_.set_rules(rules)
        self.viewport().update()

    def set_changed(self, changed: set[Path]) -> None:
        self.model_.changed = set(changed)
        self.viewport().update()

    def _clicked(self, index: QModelIndex) -> None:
        if self.model_.isDir(index):
            self.setExpanded(index, not self.isExpanded(index))

    def prompt_path(self, path: str) -> str:
        """
        Returns a path as it is added to the prompt: relative to the tools' project folder, or full.
        """
        rules = self.model_.rules
        if self.full_paths or not rules:
            return Path(path).as_posix()
        return rules.relative(Path(path))

    def _menu(self, point) -> None:
        index = self.indexAt(point)
        if not index.isValid():
            return
        path = self.model_.filePath(index)
        menu = QMenu(self)
        menu.addAction("Add path to prompt", lambda: self.add_to_prompt.emit(f"`{self.prompt_path(path)}`"))
        menu.addAction("Copy path", lambda: QGuiApplication.clipboard().setText(str(Path(path))))
        rules = self.model_.rules
        actions = tools_config.path_actions(rules.project, Path(path)) if rules else []
        if actions:
            # The user's own changes to the tools config; the model never gets these
            menu.addSeparator()
            for label, key, add in actions:
                menu.addAction(label, lambda k=key, a=add: self.change_rule.emit(k, path, a))
        menu.exec(self.viewport().mapToGlobal(point))
