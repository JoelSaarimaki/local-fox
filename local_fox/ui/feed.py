"""
The conversation feed: the session's entries as HTML in a text browser, each entry in a frame of its own.

While a reply streams in, only the frames of the entries that changed are written again, at most every 80 ms; the
rest of the document stays as it is. Writing the whole document each time made the feed flash, lost the user's
selection, and grew slower with the conversation. A whole render is done only when the entries are replaced (a
new conversation, a reopened one) or no longer match the frames.

An expanded collapsible entry (a tool call with its arguments and response, the instructions, the filemap, the
tools) gets a background and a border, so that it is clear where it starts and ends. The feed follows the end of
the conversation when new content arrives and the user is at the end, but never because the user opened or
closed an entry: the view then stays where it is. Text is selected with the mouse and copied with Ctrl + C.
"""
from PySide6.QtCore import QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextFrameFormat
from PySide6.QtWidgets import QTextBrowser

from .. import conversation as c
from ..conversation import Entry
from . import theme
from .render import entry_html
from .theme import feed_css

# The entries that are shown in a box when expanded
BOXED = (c.TOOL, c.INSTRUCTIONS, c.FILEMAP, c.TOOLS)
# How a frame looks (Feed._look): all but an empty one have the same geometry, so that nothing moves when one opens
BOXED_LOOK, PLAIN_LOOK, EMPTY_LOOK = "boxed", "plain", "empty"
# Around every entry: these space the entries, as the empty lines Qt keeps between frames are hidden
FRAME_MARGIN = 1
FRAME_PADDING = 6

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
        self._frames: list[tuple[int, object, str, str]] = []  # Entry id, its frame, the HTML written in it, its look
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self.render)

    def set_entries(self, entries: list[Entry]) -> None:
        self.entries = entries
        self._cache.clear()
        # The document is written again: forgetting only the frames would leave the old conversation's text in it,
        # and the new entries would be added after it (Save and clear showed the start entries again and again)
        self._render_all(list(entries))
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

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

    @staticmethod
    def _boxed(entry: Entry) -> bool:
        return entry.kind in BOXED and not entry.collapsed

    @classmethod
    def _look(cls, entry: Entry, html: str) -> str:
        """
        Returns how an entry's frame looks: BOXED_LOOK for an expanded collapsible entry, EMPTY_LOOK for an entry
        that shows nothing, else PLAIN_LOOK.
        """
        if not html:
            return EMPTY_LOOK
        return BOXED_LOOK if cls._boxed(entry) else PLAIN_LOOK

    @staticmethod
    def _frame_format(look: str) -> QTextFrameFormat:
        """
        Returns the format of a frame of the look. A boxed and a plain frame have the same margins, border and
        padding, the plain one's border and background only not drawn, so that the heading of an entry stays in its
        place when the entry is opened or closed (with a box only around an open entry, the heading jumped).
        An empty frame takes no space.
        """
        fmt = QTextFrameFormat()
        if look == EMPTY_LOOK:
            fmt.setMargin(0)
            fmt.setBorder(0)
            fmt.setPadding(0)
            fmt.setBackground(QBrush(Qt.NoBrush))
            return fmt
        fmt.setMargin(0)
        fmt.setTopMargin(FRAME_MARGIN)
        fmt.setBottomMargin(FRAME_MARGIN)
        fmt.setBorder(1)
        fmt.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
        fmt.setPadding(FRAME_PADDING)
        if look == BOXED_LOOK:
            fmt.setBackground(QBrush(QColor(theme.EXPANDED)))
            fmt.setBorderBrush(QBrush(QColor(theme.BORDER)))
        else:
            fmt.setBackground(QBrush(Qt.NoBrush))
            fmt.setBorderBrush(QBrush(Qt.NoBrush))
        return fmt

    @staticmethod
    def _hide(block) -> None:
        """
        Hides a block of the document and lays it out again.
        """
        if block.isValid() and block.isVisible():
            block.setVisible(False)
            block.document().markContentsDirty(block.position(), block.length())

    @staticmethod
    def _write(frame, html: str) -> None:
        """
        Replaces the content of a frame with the HTML.
        """
        cursor = frame.firstCursorPosition()
        cursor.setPosition(frame.lastPosition(), QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        # Removing leaves one block, which keeps the old content's format: e.g. a list item's, when an opened entry
        # ended with a list. The new HTML would start in it, and a closed entry would get a bullet and an indent.
        text_list = cursor.currentList()
        if text_list:
            text_list.remove(cursor.block())
        cursor.setBlockFormat(QTextBlockFormat())
        cursor.setCharFormat(QTextCharFormat())
        # An entry that shows nothing (a reply with only tool calls) keeps its frame, as frames follow the entries,
        # but its empty block is hidden: shown, it left a blank space between the tool calls
        block = cursor.block()
        if block.isVisible() != bool(html):
            block.setVisible(bool(html))
            frame.document().markContentsDirty(block.position(), block.length())  # Laid out again
        if html:
            cursor.insertHtml(html)

    def render(self, follow: bool = True) -> None:
        """
        Brings the document up to date with the entries: the frames of changed entries are written again, new entries
        get frames at the end. With follow, the view stays at the end of the conversation if it was there.
        """
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 30
        position = bar.value()
        selected = self.textCursor()
        selection = (selected.anchor(), selected.position(), selected.selectedText()) if selected.hasSelection() else None
        entries = list(self.entries)
        known = [frame[0] for frame in self._frames]
        if known != [e.id for e in entries[:len(known)]]:
            self._render_all(entries)  # Entries removed, replaced or reordered
        else:
            document = self.document()
            cursor = QTextCursor(document)
            cursor.beginEditBlock()
            for i, entry in enumerate(entries):
                html = self._html(entry)
                if i < len(self._frames):
                    entry_id, frame, written, look = self._frames[i]
                    if html != written:
                        self._write(frame, html)
                    # The format only when it changes: setting it lays the document out again
                    if self._look(entry, html) != look:
                        frame.setFrameFormat(self._frame_format(self._look(entry, html)))
                        # Else a frame that became empty kept its earlier height
                        document.markContentsDirty(frame.firstPosition(), frame.lastPosition() - frame.firstPosition() + 1)
                    self._frames[i] = (entry_id, frame, html, self._look(entry, html))
                else:
                    self._append(cursor, entry, html)
            cursor.endEditBlock()
        if selection and not self.textCursor().hasSelection():
            self._restore_selection(*selection)
        bar.setValue(bar.maximum() if follow and at_bottom else position)

    def _restore_selection(self, anchor: int, position: int, text: str) -> None:
        """
        Selects again what the user had selected in a frame that was written again (e.g. in the reply streaming in),
        so that it can still be copied with Ctrl + C: at the same place if the text there is the same (a reply only
        grows at its end), else the nearest same text.
        """
        document = self.document()
        cursor = QTextCursor(document)
        if max(anchor, position) <= document.characterCount() - 1:
            cursor.setPosition(anchor)
            cursor.setPosition(position, QTextCursor.KeepAnchor)
            if cursor.selectedText() == text:
                self.setTextCursor(cursor)
                return
        if " " in text:
            return  # Over several paragraphs, which find() cannot match; the selection is let go
        found = document.find(text, max(0, min(anchor, position) - len(text)), QTextDocument.FindCaseSensitively)
        if found.isNull():
            found = document.find(text, 0, QTextDocument.FindCaseSensitively)
        if not found.isNull():
            self.setTextCursor(found)

    def _append(self, cursor: QTextCursor, entry: Entry, html: str) -> None:
        cursor.movePosition(QTextCursor.End)
        # Qt keeps an empty line between frames; hidden, as the frames' own margins and padding space the entries
        self._hide(cursor.block())
        frame = cursor.insertFrame(self._frame_format(self._look(entry, html)))
        self._write(frame, html)
        self._hide(self.document().findBlock(frame.lastPosition() + 1))
        self._frames.append((entry.id, frame, html, self._look(entry, html)))

    def _render_all(self, entries: list[Entry]) -> None:
        """
        Writes the whole document again, a frame per entry.
        """
        self.document().clear()
        self._frames = []
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        for entry in entries:
            self._append(cursor, entry, self._html(entry))
        cursor.endEditBlock()

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
            self.render(follow=False)  # Opening or closing an entry never moves the view
        elif url.scheme() in ("http", "https", "file"):
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(url)
