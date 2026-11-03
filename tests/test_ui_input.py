"""
Tests of copying and pasting, and of sending while the model runs: pasting never sends, whatever the pasted text
contains; copying from the feed is Ctrl + C on a selection that a re-render keeps; and a message sent while the
model runs is not taken (there is no queue) and stays in the input.
"""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QKeyEvent, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from local_fox import config  # noqa: E402
from local_fox import conversation as c  # noqa: E402

@pytest.fixture(autouse=True)
def scratch_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_FOX_CONFIG", str(tmp_path / "config.json"))

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])

def key(widget, key, modifiers=Qt.NoModifier, text=""):
    widget.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, key, modifiers, text))

def test_pasting_never_sends(app):
    from local_fox.ui.prompt import PromptInput
    prompt = PromptInput()
    sent = []
    prompt.submitted.connect(sent.append)
    pasted = "/clear\nfirst line\r\nsecond\rthird\n\n"
    data = QMimeData()
    data.setText(pasted)
    prompt.insertFromMimeData(data)
    QGuiApplication.clipboard().setText("\n/export\n")
    key(prompt, Qt.Key_V, Qt.ControlModifier)  # Ctrl + V, as the user pastes
    assert sent == []
    assert "/clear" in prompt.toPlainText() and "/export" in prompt.toPlainText()

def test_enter_sends_and_the_window_decides(app):
    from local_fox.ui.prompt import PromptInput
    prompt = PromptInput()
    sent = []
    prompt.submitted.connect(sent.append)
    prompt.setPlainText("Fix the bug")
    key(prompt, Qt.Key_Return, text="\r")
    assert sent == ["Fix the bug"] and prompt.toPlainText() == "Fix the bug"  # Still there until taken
    prompt.sent("Fix the bug")
    assert prompt.toPlainText() == "" and prompt.history[-1] == "Fix the bug"

def test_copy_from_the_feed_survives_rendering(app):
    from local_fox.ui.feed import Feed
    feed = Feed(QApplication.font())
    entries = [c.Entry(c.USER, text="Explain the loader"), c.Entry(c.ASSISTANT, text="The loader reads config.json.", answer_collapsed=False)]
    feed.set_entries(entries)
    found = feed.document().find("reads config.json")
    feed.setTextCursor(found)
    entries[1].text += " More text streams in."  # A reply streaming in renders the feed again
    feed.render()
    assert feed.textCursor().selectedText() == "reads config.json"
    QGuiApplication.clipboard().clear()
    key(feed, Qt.Key_C, Qt.ControlModifier)
    assert QGuiApplication.clipboard().text() == "reads config.json"

def test_no_queue_while_the_model_runs(app):
    from local_fox.ui.main_window import MainWindow
    window = MainWindow(dict(config.DEFAULTS))
    try:
        window.session = SimpleNamespace(running=True)
        window.prompt.setPlainText("Do this next")
        window._on_prompt("Do this next")
        assert window.prompt.toPlainText() == "Do this next"  # Not taken, not lost, not queued
        assert window._turn is None
    finally:
        window.session = None
        window.close()
