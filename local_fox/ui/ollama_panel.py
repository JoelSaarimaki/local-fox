"""
The Ollama panel below the filemap: the address of the Ollama server, a short text of what it is doing
("Loading flint:40b…", "flint:40b loaded, 100 % GPU", "Not running"), the "Start Ollama server" button
while a server on this computer does not answer, and "Stop Ollama server" while one answers. The window polls the server and starts it (main_window.py);
the panel only shows what it is given.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

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
        self.host_label = QLabel("", objectName="dim")
        heading.addWidget(title)
        heading.addWidget(self.host_label, 1)
        layout.addLayout(heading)
        self.status_label = QLabel("Checking…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.start_button = QPushButton("Start Ollama server", objectName="primary")
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.setToolTip("Run 'ollama serve' on this computer, until Local Fox closes")
        # Without the "checked" argument of clicked (see main_window._button)
        self.start_button.clicked.connect(lambda checked=False: self.start_clicked.emit())
        self.start_button.hide()
        layout.addWidget(self.start_button, 0, Qt.AlignLeft)
        self.stop_button = QPushButton("Stop Ollama server")
        self.stop_button.setCursor(Qt.PointingHandCursor)
        self.stop_button.setToolTip("Stop the Ollama server of this computer, e.g. to run 'ollama serve' in a console of your own")
        self.stop_button.clicked.connect(lambda checked=False: self.stop_clicked.emit())
        self.stop_button.hide()
        layout.addWidget(self.stop_button, 0, Qt.AlignLeft)

    def show_state(self, host: str, text: str, can_start: bool, tip: str = "", can_stop: bool = False) -> None:
        """
        Shows the address, the text of what the server does, the start button when the server can be started
        here, and the stop button when one of this computer runs. The text is never orange: a server that is not
        running has the start button below it, which draws the eye enough.
        """
        self.stop_button.setVisible(can_stop)
        self.host_label.setText(host.removeprefix("http://"))
        self.host_label.setToolTip(host)
        self.status_label.setText(text)
        self.status_label.setToolTip(tip)
        self.start_button.setVisible(can_start)
