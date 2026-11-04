"""
The prompt input: always multiline, Enter sends and Shift + Enter adds a line break. Up and Down recall the
messages sent earlier while the input is empty or shows a recalled message. Copying and pasting are Qt's own
(Ctrl + C, Ctrl + X, Ctrl + V), and pasting never sends. A message the window does not take (the model is still
running) stays in the input.
"""
import json
import math

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QKeyEvent
from PySide6.QtWidgets import QPlainTextEdit

from ..config import data_dir

HISTORY_SIZE = 200
# The lines of text the input shows; a longer message scrolls. Five were too few for a message of several lines.
PROMPT_LINES = 7

class PromptInput(QPlainTextEdit):
    submitted = Signal(str)
    escape = Signal()

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("Message, or /help for the commands  (Enter sends, Shift+Enter adds a line)")
        self.setTabChangesFocus(True)
        self.history: list[str] = self._load_history()
        self._position = len(self.history)  # The recalled message, len(history) for none
        self._lines = PROMPT_LINES
        self._fit_height()

    def show_lines(self, lines: int = PROMPT_LINES) -> None:
        """
        Makes the input as high as the given number of lines in its font, so that it follows the font size.
        """
        self._lines = lines
        self._fit_height()

    def _fit_height(self) -> None:
        # A line as the input lays it out: the font's line spacing rounded up (QFontMetrics rounds it down)
        line = math.ceil(QFontMetricsF(self.font()).lineSpacing())
        height = line * self._lines + 2 * self.document().documentMargin() + 2 * self.frameWidth()
        self.setFixedHeight(int(height) + 2)  # A little room, so that the last line is not cut by rounding

    def changeEvent(self, event) -> None:
        """
        Fits the height again when the font or the style changes: the window's stylesheet, which sets the border,
        is applied after the input is created.
        """
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit_height()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_height()

    @staticmethod
    def _history_path():
        return data_dir() / "history.json"

    def _load_history(self) -> list[str]:
        try:
            values = json.loads(self._history_path().read_text(encoding="utf-8"))
            return [v for v in values if isinstance(v, str)][-HISTORY_SIZE:]
        except (OSError, ValueError):
            return []

    def _save_history(self) -> None:
        try:
            self._history_path().parent.mkdir(parents=True, exist_ok=True)
            self._history_path().write_text(json.dumps(self.history[-HISTORY_SIZE:], ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _recalled(self) -> bool:
        text = self.toPlainText()
        return not text or (self._position < len(self.history) and text == self.history[self._position])

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key, modifiers = event.key(), event.modifiers()
        # Only a typed Enter sends: pasted text goes through insertFromMimeData, never through here
        if key in (Qt.Key_Return, Qt.Key_Enter) and not modifiers & Qt.ShiftModifier:
            text = self.toPlainText().strip()
            if text:
                self.submitted.emit(text)  # The window calls sent() if it takes the message; otherwise it stays here
            return
        if key == Qt.Key_Escape:
            self.escape.emit()
            return
        if key in (Qt.Key_Up, Qt.Key_Down) and self.history and self._recalled():
            if key == Qt.Key_Up:
                self._position = max(0, self._position - 1)
            else:
                self._position = min(len(self.history), self._position + 1)
            self.setPlainText(self.history[self._position] if self._position < len(self.history) else "")
            self.moveCursor(self.textCursor().MoveOperation.End)
            return
        super().keyPressEvent(event)

    def sent(self, text: str) -> None:
        """
        The window took the message: it is recalled with Up from now on, and the input is emptied.
        """
        if not self.history or self.history[-1] != text:
            self.history.append(text)
            self._save_history()
        self._position = len(self.history)
        self.clear()

    def insertFromMimeData(self, source) -> None:
        """
        Pastes and drops as plain text at the cursor, whatever the text contains (line breaks, a leading '/'):
        pasting only edits the input and never sends it. Only Ctrl + V (or the context menu) pastes.
        """
        if source.hasText():
            self.insertPlainText(source.text())

    def insert_text(self, text: str) -> None:
        cursor = self.textCursor()
        before = self.toPlainText()[:cursor.position()]
        if before and not before.endswith((" ", "\n")):
            text = " " + text
        cursor.insertText(text + " ")
        self.setFocus()
