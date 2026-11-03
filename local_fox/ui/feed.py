"""
The conversation feed: the session's entries rendered as HTML in a text browser. The HTML of each entry is
cached, and the feed is rendered again at most every 80 ms while a reply streams in. Text is selected with the
mouse and copied with Ctrl + C (Qt's own), and a selection is kept when the feed is rendered again.
"""
from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QFont, QTextCursor, QTextDocument
from PySide6.QtWidgets import QTextBrowser

from .. import conversation as c
from ..conversation import Entry
from .render import entry_html
from .theme import feed_css

class Feed(QTextBrowser):
    toggled = Signal(int)

    def __init__(self, font: QFont):
        super().__init__()
        self.setOpenLinks(False)
        self.setFont(font)
        self.document().setDefaultFont(font)
        self.document().setDefaultStyleSheet(feed_css())
        self.document().setDocumentMargin(10)
        self.anchorClicked.connect(self._clicked)
        self.entries: list[Entry] = []
        self._cache: dict[int, tuple[tuple, str]] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self.render)

    def set_entries(self, entries: list[Entry]) -> None:
        self.entries = entries
        self._cache.clear()
        self.render()

    def schedule(self) -> None:
        """
        Renders the feed soon, once for any number of changes in between.
        """
        if not self._timer.isActive():
            self._timer.start()

    @staticmethod
    def _signature(entry: Entry) -> tuple:
        return (len(entry.text), len(entry.thinking), entry.streaming, entry.stop_reason, entry.status, entry.collapsed,
                entry.answer_collapsed, len(entry.response), entry.title, entry.resolved)

    def _html(self, entry: Entry) -> str:
        signature = self._signature(entry)
        cached = self._cache.get(entry.id)
        if cached and cached[0] == signature:
            return cached[1]
        html = entry_html(entry)
        self._cache[entry.id] = (signature, html)
        return html

    def render(self) -> None:
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 30
        position = bar.value()
        cursor = self.textCursor()
        selection = (cursor.anchor(), cursor.position(), cursor.selectedText()) if cursor.hasSelection() else None
        parts = [self._html(e) for e in list(self.entries)]
        self.setHtml("<body>" + "".join(f"<div>{p}</div>" for p in parts if p) + "</body>")
        if selection:
            self._restore_selection(*selection)
        bar.setValue(bar.maximum() if at_bottom else position)

    def _restore_selection(self, anchor: int, position: int, text: str) -> None:
        """
        Selects again what the user had selected, as the feed is rendered anew while a reply streams in, so that it
        can still be copied with Ctrl + C: at the same place if the text there is the same, else the nearest same text.
        """
        cursor = QTextCursor(self.document())
        end = self.document().characterCount() - 1
        if max(anchor, position) <= end:
            cursor.setPosition(anchor)
            cursor.setPosition(position, QTextCursor.KeepAnchor)
            if cursor.selectedText() == text:
                self.setTextCursor(cursor)
                return
        if " " in text:
            return  # Over several paragraphs, which find() cannot match; the selection is let go
        found = self.document().find(text, max(0, min(anchor, position) - len(text)), QTextDocument.FindCaseSensitively)
        if found.isNull():
            found = self.document().find(text, 0, QTextDocument.FindCaseSensitively)
        if not found.isNull():
            self.setTextCursor(found)

    def scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _clicked(self, url: QUrl) -> None:
        text = url.toString()
        if text.startswith(("toggle:", "answer:")):
            part, _, entry_id = text.partition(":")
            for entry in self.entries:
                if entry.id == int(entry_id):
                    if part == "answer":
                        entry.answer_collapsed = not entry.answer_collapsed
                    else:
                        entry.collapsed = not entry.collapsed
                    break
            self.render()
        elif url.scheme() in ("http", "https", "file"):
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(url)
