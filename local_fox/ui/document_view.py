"""
The files the user opened from the filemap, in tabs beside the conversation feed. A Markdown file is shown
rendered, with a switch to its raw text; every other text file is shown as raw text. They are only for the user
to read: nothing here goes to the model, and nothing can be edited. An open file is read again when it changes on
disk, so a file the model edits shows its new text.
"""
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont, QTextOption
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QStackedWidget, QTabBar,
                               QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from . import theme
from .render import md

MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd"}
MAX_BYTES = 2_000_000  # Larger files are not shown: a text view of megabytes is slow and rarely what is wanted
RENDERED, RAW = "Rendered", "Raw"

def is_markdown(path: Path) -> bool:
    return path.suffix.lower() in MARKDOWN_SUFFIXES

def read_document(path: Path) -> tuple[str, str]:
    """
    Reads a file for viewing. Returns its text and an empty string, or an empty text and why it is not shown
    (missing, too large, binary). Text that is not UTF-8 is shown with replacement characters.
    """
    try:
        size = path.stat().st_size
        if size > MAX_BYTES:
            return "", f"{path.name} is {size / 1_000_000:.1f} MB, larger than the {MAX_BYTES // 1_000_000} MB shown here."
        data = path.read_bytes()
    except FileNotFoundError:
        return "", f"{path.as_posix()} does not exist any more."
    except OSError as e:
        return "", f"{path.as_posix()} could not be read: {e}"
    if b"\0" in data[:8192]:
        return "", f"{path.name} is a binary file, which is not shown here."
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n"), ""

class DocumentView(QWidget):
    """
    One open file: its path and, for Markdown, the "Rendered" / "Raw" switch above the text.
    """
    open_file = Signal(str)  # A link in rendered Markdown to another file of the project

    def __init__(self, path: Path, root: Path | None, font: QFont):
        super().__init__()
        self.path = path
        self.root = root
        self.markdown = is_markdown(path)
        self.mode = RENDERED if self.markdown else RAW

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        shown = path.relative_to(root).as_posix() if root and path.is_relative_to(root) else path.as_posix()
        self.path_label = QLabel(shown, objectName="dim")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(self.path_label, 1)
        self.mode_buttons: dict[str, QPushButton] = {}
        if self.markdown:
            for mode in (RENDERED, RAW):
                button = QPushButton(mode, objectName="flat")
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(lambda checked=False, m=mode: self.set_mode(m))
                bar.addWidget(button)
                self.mode_buttons[mode] = button

        self.rendered = QTextBrowser()
        self.rendered.setOpenLinks(False)
        self.rendered.setFont(font)
        self.rendered.document().setDefaultFont(font)
        self.rendered.document().setDefaultStyleSheet(theme.feed_css())
        self.rendered.document().setDocumentMargin(10)
        self.rendered.setSearchPaths([str(path.parent)])  # Images of the Markdown, by their relative paths
        self.rendered.anchorClicked.connect(self._link)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setFont(font)
        self.raw.setWordWrapMode(QTextOption.NoWrap)  # Code keeps its lines; long ones scroll sideways
        self.raw.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.rendered)
        self.stack.addWidget(self.raw)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self.stack, 1)

        # Editors and the tools often replace a file instead of writing it, which ends the watch: it is set again
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(lambda _p: self._reload_timer.start())
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(200)
        self._reload_timer.timeout.connect(self.reload)
        self.text = ""
        self.reload()
        self.set_mode(self.mode)

    def reload(self) -> None:
        """
        Reads the file again and shows it, keeping where the user had scrolled to.
        """
        text, problem = read_document(self.path)
        if self.path.exists() and str(self.path) not in self.watcher.files():
            self.watcher.addPath(str(self.path))
        bars = [(view.verticalScrollBar(), view.verticalScrollBar().value()) for view in (self.rendered, self.raw)]
        self.text = text
        self.problem = problem
        if problem:
            self.rendered.setHtml(f"<p class=error>{problem}</p>")
            self.raw.setPlainText(problem)
        else:
            self.raw.setPlainText(text)
            if self.markdown:
                self.rendered.setHtml(md(text))
        for bar, value in bars:
            bar.setValue(value)

    def set_mode(self, mode: str) -> None:
        """
        Shows the file rendered or as raw text; only Markdown is rendered.
        """
        self.mode = mode if self.markdown else RAW
        rendered = self.mode == RENDERED or bool(self.problem)
        self.stack.setCurrentWidget(self.rendered if rendered else self.raw)
        for name, button in self.mode_buttons.items():
            button.setStyleSheet(f"color: {theme.ACCENT if name == self.mode else theme.DIM};")

    def _link(self, url: QUrl) -> None:
        """
        Follows a link of rendered Markdown the user clicked: a web link opens in the browser, a link to a file of
        the project opens in a tab of its own.
        """
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        if url.scheme() not in ("", "file") or not url.path():
            return
        target = Path(url.toLocalFile()) if url.scheme() == "file" else (self.path.parent / url.path())
        target = target.resolve()
        if target.is_file() and (self.root is None or target.is_relative_to(self.root)):
            self.open_file.emit(str(target))

class DocumentTabs(QTabWidget):
    """
    The conversation feed as the first tab, which cannot be closed, and the opened files after it, each with an
    "×" button. The tab bar is hidden while only the conversation is open, so the window looks as before.
    """
    def __init__(self, conversation: QWidget, font: QFont):
        super().__init__()
        self.font_ = font
        self.root: Path | None = None
        self.setDocumentMode(True)
        self.setTabBarAutoHide(True)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.ElideMiddle)
        self.addTab(conversation, "Conversation")
        self.tabBar().setTabToolTip(0, "The conversation with the model")
        self.currentChanged.connect(lambda _i: self.mark_conversation(False) if self.currentIndex() == 0 else None)

    def documents(self) -> list[DocumentView]:
        return [self.widget(i) for i in range(1, self.count())]

    def open_file(self, path: str | Path) -> DocumentView:
        """
        Shows a file in its tab, opening one if it is not open yet.
        """
        path = Path(path).resolve()
        for view in self.documents():
            if view.path == path:
                self.setCurrentWidget(view)
                return view
        view = DocumentView(path, self.root, self.font_)
        view.open_file.connect(self.open_file)
        index = self.addTab(view, path.name)
        self.tabBar().setTabToolTip(index, view.path_label.text())
        close = QToolButton(objectName="flat")
        close.setText("×")
        close.setToolTip("Close")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(lambda checked=False, v=view: self.close_document(v))
        self.tabBar().setTabButton(index, QTabBar.RightSide, close)
        self.setCurrentIndex(index)
        return view

    def close_document(self, view: DocumentView) -> None:
        index = self.indexOf(view)
        if index > 0:
            self.removeTab(index)
            view.deleteLater()

    def close_all(self, root: Path | None = None) -> None:
        """
        Closes every opened file, as when another project opens, and shows the conversation.
        """
        for view in self.documents():
            self.close_document(view)
        self.root = root
        self.show_conversation()

    def show_conversation(self) -> None:
        self.setCurrentIndex(0)

    def mark_conversation(self, waiting: bool) -> None:
        """
        Marks the conversation's tab while a question waits there and a file is shown instead.
        """
        waiting = waiting and self.currentIndex() != 0
        self.tabBar().setTabText(0, "Conversation •" if waiting else "Conversation")
