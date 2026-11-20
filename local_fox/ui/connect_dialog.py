"""
The dialog of the Ollama panel's "Connect to Ollama server": the address of an Ollama server that already runs, e.g.
on another computer, with the current address filled in. "Connect" checks that Ollama answers there while the
dialog stays open ("Connecting to …"), and closes it only when it does; otherwise the dialog says why, below the
address, so that it can be corrected and tried again. The window switches to the address after the dialog
(MainWindow._connect_ollama); the dialog itself changes nothing.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton, QVBoxLayout

from .. import ollama_server

WIDTH = 440  # Pixels: wide enough for an address and a short line of help, which wraps

class ConnectDialog(QDialog):
    """
    Asks for an Ollama server's address and checks it with check(host, done): check runs the check without
    blocking the window and calls done(error) in the window's thread, error None when Ollama answers. After an
    accepted dialog, host is the address, as /host saves it.
    """

    def __init__(self, parent, current: str, check):
        super().__init__(parent)
        self.setWindowTitle("Connect to Ollama server")
        self.current, self.check = current, check
        self.host = ""
        self._asked = ""  # The address being checked; an answer for another one (or after Cancel) is dropped
        layout = QVBoxLayout(self)
        # The dialog is as large as its contents, also when the line below the address comes and goes
        layout.setSizeConstraint(QLayout.SetFixedSize)
        margins = layout.contentsMargins()
        text_width = WIDTH - margins.left() - margins.right()  # The texts wrap at this width, which sets the dialog's
        intro = QLabel("The address of an Ollama server that runs, e.g. 192.168.1.20 or 192.168.1.20:11434. On another "
                       "computer, Ollama must listen on the network there (OLLAMA_HOST=0.0.0.0).", objectName="dim")
        intro.setWordWrap(True)
        intro.setFixedWidth(text_width)
        layout.addWidget(intro)
        self.address = QLineEdit(current)
        self.address.selectAll()
        self.address.textEdited.connect(lambda _: self._show(""))
        layout.addWidget(self.address)
        self.status = QLabel("", objectName="error")
        self.status.setWordWrap(True)
        self.status.setFixedWidth(text_width)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.hide()
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(lambda checked=False: self.reject())
        self.connect_button = QPushButton("Connect", objectName="primary")
        self.connect_button.setDefault(True)  # Enter in the address connects
        self.connect_button.clicked.connect(lambda checked=False: self.try_connect())
        buttons.addWidget(cancel)
        buttons.addWidget(self.connect_button)
        layout.addLayout(buttons)

    def try_connect(self) -> None:
        """
        Checks the address in the field: one that is not an address, or the one in use, is told at once; any other
        is checked, and the dialog closes when Ollama answers there.
        """
        text = self.address.text().strip()
        host = ollama_server.normalize_host(text)
        if not host:
            self._show(f"'{text}' is not an address. Give it as e.g. 192.168.1.20, 192.168.1.20:11434 or "
                       "http://192.168.1.20:11434." if text else "Give the address of the Ollama server.")
            return
        if host == self.current:
            self._show(f"Local Fox already uses the Ollama server at {host}.")
            return
        self._asked = host
        self._busy(True)
        self._show(f"Connecting to {host}…", error=False)
        self.check(host, lambda error: self.checked(host, error))

    def checked(self, host: str, error: str | None) -> None:
        """
        Takes the check's answer: closes the dialog with the address if Ollama answered, else says why.
        """
        if host != self._asked or not self.isVisible():
            return  # Cancelled, or an earlier address
        self._asked = ""
        self._busy(False)
        if error is None:
            self.host = host
            self.accept()
            return
        self._show(f"Ollama at {host} could not be reached: {error}. Check that Ollama runs there and listens on the "
                   f"network, and that the firewall lets port {ollama_server.port_of(host)} in.")
        self.address.setFocus()

    def _busy(self, busy: bool) -> None:
        self.address.setEnabled(not busy)
        self.connect_button.setEnabled(not busy)

    def _show(self, text: str, error: bool = True) -> None:
        """
        Shows a line below the address: an error in the error color, or the progress dimmed.
        """
        self.status.setObjectName("error" if error else "dim")
        self.status.style().unpolish(self.status)  # The object name's style applies only when polished again
        self.status.style().polish(self.status)
        self.status.setText(text)
        self.status.setVisible(bool(text))
