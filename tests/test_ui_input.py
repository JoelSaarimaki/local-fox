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

def test_own_changes_to_the_tools_config_are_not_reported_as_outside(app, tmp_path):
    from types import SimpleNamespace
    from local_fox.ui.main_window import MainWindow
    (tmp_path / ".local-fox").mkdir()
    config_file = tmp_path / ".local-fox" / "tools-config.json"
    config_file.write_text('{"project_dir": ".."}', encoding="utf-8")
    window = MainWindow(dict(config.DEFAULTS))
    reported, cleared = [], []
    window._tools_config_changed = reported.append
    window._clear = lambda settings=None, restart_tools=False: cleared.append(restart_tools)
    try:
        window.session = SimpleNamespace(project=tmp_path, servers=object(), rules=None, running=False,
                                         tools_config_text='{"project_dir": ".."}',
                                         read_tools_config=lambda: config_file.read_text(encoding="utf-8"))
        config_file.write_text('{"project_dir": "..", "max_output_chars": 30000}', encoding="utf-8")  # "Save and clear"
        window._settings_saved(dict(config.DEFAULTS), True, True)
        window._tools_config_touched()  # The watcher's signal for that write
        assert cleared == [True] and reported == []
        config_file.write_text('{"project_dir": "..", "max_output_chars": 20000}', encoding="utf-8")  # Then by hand
        window._tools_config_touched()
        assert len(reported) == 1 and "outside Local Fox" in reported[0]
    finally:
        window.session = None
        window.close()
