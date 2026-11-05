"""
The Ollama panel below the filemap: the address of the Ollama server, a row with the model and where it runs
("flint:40b  77 % CPU / 23 % GPU"), a short text of what the server does ("Loading the model…", "Thinking…",
"Idle", "Not running"), and a row for the "Start Ollama server" button while a server on this computer does not
answer, or "Stop Ollama server" while one answers. The window polls the server and starts it (main_window.py);
the panel only shows what it is given.

Every row is one line, cut with "…" when it is too long, and the button row keeps its height without a button, so
the panel never changes its height as its texts change, which would move the filemap above it.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedLayout, QVBoxLayout,
                               QWidget)

class ElidedLabel(QLabel):
    """
    A one-line label that cuts its text with "…" to its width instead of widening the column: a long model name or
    address would otherwise make the filemap's column as wide as the text. The whole text is in its tooltip when cut.
    """
    def __init__(self, text: str = "", object_name: str = ""):
        super().__init__(objectName=object_name)
        self._full = ""
        self._tip = ""
        # Ignored: the layout gives the label the room there is, whatever the text's width
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.set_full_text(text)

    def set_full_text(self, text: str, tip: str = "") -> None:
        """
        Sets the text, shown cut to the label's width, and the tooltip, which is the whole text when it is cut.
        """
        self._full, self._tip = text, tip
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(self.width(), 1))
        self.setText(shown)
        tips = [t for t in (self._full if shown != self._full else "", self._tip) if t]
        self.setToolTip("\n".join(tips))

def _button_page(button: QPushButton) -> QWidget:
    """
    Returns a page of the button row with the button at its left, at its own width.
    """
    page = QWidget(objectName="ollama-buttons")
    row = QHBoxLayout(page)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(button)
    row.addStretch(1)
    return page

class OllamaPanel(QFrame):
    start_clicked = Signal()
    stop_clicked = Signal()

    def __init__(self):
        super().__init__(objectName="ollama")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)
        heading = QHBoxLayout()
        heading.setSpacing(8)
        title = QLabel("Ollama", objectName="accent-text")
        self.host_label = ElidedLabel(object_name="dim")
        heading.addWidget(title)
        heading.addWidget(self.host_label, 1)
        layout.addLayout(heading)
        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        self.model_label = ElidedLabel()
        # Where the model runs is short and never cut; the name gives way to it
        self.placement_label = QLabel("", objectName="dim")
        model_row.addWidget(self.model_label, 1)
        model_row.addWidget(self.placement_label)
        layout.addLayout(model_row)
        self.status_label = ElidedLabel("Checking…")
        layout.addWidget(self.status_label)
        self.start_button = QPushButton("Start Ollama server", objectName="primary")
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.setToolTip("Run 'ollama serve' on this computer, until Local Fox closes")
        # Without the "checked" argument of clicked (see main_window._button)
        self.start_button.clicked.connect(lambda checked=False: self.start_clicked.emit())
        self.stop_button = QPushButton("Stop Ollama server")
        self.stop_button.setCursor(Qt.PointingHandCursor)
        self.stop_button.setToolTip("Stop the Ollama server of this computer, e.g. to run 'ollama serve' in a console of your own")
        self.stop_button.clicked.connect(lambda checked=False: self.stop_clicked.emit())
        # A stacked layout is as high as its highest page, so the row keeps the buttons' height also when empty
        self._buttons = QStackedLayout()
        self._empty = QWidget(objectName="ollama-buttons")
        self._buttons.addWidget(self._empty)
        self._buttons.addWidget(_button_page(self.start_button))
        self._buttons.addWidget(_button_page(self.stop_button))
        for button in (self.start_button, self.stop_button):
            # A hidden button would not count in its page's size, and the empty row would be lower
            policy = button.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            button.setSizePolicy(policy)
            button.hide()
        layout.addLayout(self._buttons)

    def show_state(self, host: str, text: str, can_start: bool, tip: str = "", can_stop: bool = False,
                   model: str = "", placement: str = "", model_tip: str = "") -> None:
        """
        Shows the address, the model and where it runs, the text of what the server does, and the start button when
        the server can be started here or the stop button when one of this computer runs (never both: one that
        answers is not started). The text is never orange: a server that is not running has the start button
        below it, which draws the eye enough.
        """
        self.host_label.set_full_text(host.removeprefix("http://"), host)
        self.model_label.set_full_text(model, model_tip)
        self.placement_label.setText(placement)
        self.placement_label.setToolTip(model_tip)
        self.status_label.set_full_text(text, tip)
        # The buttons are hidden on the pages they are not shown on, so that isHidden() tells which one shows
        self.start_button.setVisible(can_start)
        self.stop_button.setVisible(can_stop and not can_start)
        page = 1 if can_start else 2 if can_stop else 0
        self._buttons.setCurrentIndex(page)
