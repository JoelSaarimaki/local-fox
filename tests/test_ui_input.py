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

def _feed(app, entries, height=300):
    from local_fox.ui.feed import Feed
    feed = Feed(QApplication.font())
    feed.resize(700, height)
    feed.show()
    feed.set_entries(entries)
    app.processEvents()
    return feed

def test_streaming_writes_only_the_changed_entry(app):
    entries = [c.Entry(c.USER, text="Question"), c.Entry(c.ASSISTANT, text="An answer", streaming=True)]
    feed = _feed(app, entries)
    first_frame = feed._frames[0][1]
    first_text = first_frame.firstCursorPosition().block().text()
    changes = []
    feed.document().contentsChange.connect(lambda position, removed, added: changes.append(position))
    entries[1].text += " that grows"
    feed.render()
    assert feed._frames[0][1] is first_frame and first_frame.firstCursorPosition().block().text() == first_text
    assert changes and min(changes) >= feed._frames[1][1].firstPosition() - 1  # Nothing before the reply was touched
    assert "that grows" in feed.toPlainText()

def test_opening_an_entry_does_not_move_the_view(app):
    from PySide6.QtCore import QUrl
    entries = [c.Entry(c.USER, text=f"Line {i}") for i in range(40)]
    call = c.Entry(c.TOOL, tool="read_file_with_metadata", args={"path": "a.py"}, status=c.OK,
                   response="\n".join(f"line {i}" for i in range(80)), collapsed=True)
    entries.append(call)
    feed = _feed(app, entries)
    feed.scroll_to_bottom()
    before = feed.verticalScrollBar().value()
    feed._clicked(QUrl(f"toggle:{call.id}"))  # The user opens the last tool call
    app.processEvents()
    assert not call.collapsed and feed.verticalScrollBar().value() == before
    entries.append(c.Entry(c.ASSISTANT, text="New content from the model"))
    feed.render()  # New content still follows the end when the view is at it
    feed.scroll_to_bottom()
    assert feed.verticalScrollBar().value() == feed.verticalScrollBar().maximum()

def test_long_lines_wrap_and_an_open_tool_call_is_boxed(app):
    from local_fox.ui import theme
    call = c.Entry(c.TOOL, tool="read_file_with_metadata", args={"path": "a.py"}, status=c.OK,
                   response="```text\n" + "x" * 600 + "\n```", collapsed=False)
    feed = _feed(app, [call])
    assert feed.document().size().width() <= feed.viewport().width() + 1  # No line runs past the feed
    assert feed._frames[0][1].frameFormat().background().color().name() == theme.EXPANDED
    call.collapsed = True
    feed.render(follow=False)
    assert feed._frames[0][1].frameFormat().background().style() == Qt.NoBrush

def test_context_sizes_double_up_to_the_models_maximum():
    from local_fox.ui.settings_view import context_sizes
    assert context_sizes(65536, 262144) == [4096, 8192, 16384, 32768, 65536, 131072, 262144]
    assert context_sizes(50000, 131072) == [4096, 8192, 16384, 32768, 50000, 65536, 131072]  # A size set with /context stays
    assert context_sizes(32768, None)[-1] == 1_048_576  # The model's maximum not known
    assert context_sizes(8192, 40960)[-1] == 40960  # A maximum that is not a doubling step

def test_closing_an_entry_leaves_no_list_formatting_behind(app):
    # The instructions end with a Markdown list; written again closed, the frame must not keep a list item's format
    entry = c.Entry(c.INSTRUCTIONS, title="Project instructions", text="# Rules\n\n- one\n- two", collapsed=True)
    feed = _feed(app, [entry])
    frame = feed._frames[0][1]

    def blocks():
        block, result = frame.firstCursorPosition().block(), []
        while block.isValid() and block.position() <= frame.lastPosition():
            result.append((block.text(), block.textList() is not None, block.blockFormat().indent()))
            block = block.next()
        return result

    closed = blocks()
    entry.collapsed = False
    feed.render(follow=False)
    assert any(in_list for _, in_list, _ in blocks())  # Open: the list is there
    entry.collapsed = True
    feed.render(follow=False)
    assert blocks() == closed  # Closed again: the same as before it was opened

def test_new_entries_replace_the_feed(app):
    # A new conversation (Clear, Save and clear, a reopened one) gives the feed a new list: only it is shown
    feed = _feed(app, [c.Entry(c.INSTRUCTIONS, title="Project instructions", text="old"), c.Entry(c.USER, text="Old question")])
    feed.set_entries([c.Entry(c.INSTRUCTIONS, title="Project instructions", text="new")])
    app.processEvents()
    assert feed.toPlainText().count("Project instructions") == 1 and "Old question" not in feed.toPlainText()

def test_a_notice_can_be_dismissed_but_a_question_cannot(app):
    from local_fox.ui.main_window import DISMISS, REOPEN, MainWindow
    window = MainWindow(dict(config.DEFAULTS))
    buttons = lambda: [window.notice_buttons.itemAt(i).widget().text() for i in range(window.notice_buttons.count())]
    try:
        notice = c.Entry(c.NOTICE, text="The last conversation in this project: …", actions=[REOPEN])
        window.session = SimpleNamespace(entries=[notice], rules=None)
        window._reopen_offer = "a saved conversation"
        window._on_entry(notice.id, True)
        assert buttons() == [REOPEN, DISMISS] and not window.notice_bar.isHidden()
        window._notice_action(DISMISS)
        assert window.notice_bar.isHidden() and window._reopen_offer is None
        assert window.session.entries == [notice]  # The notice stays in the feed
        app.processEvents()
        window._show_notice("Run edit_file?", ["Allow", "Skip", "Stop"], lambda option: None)  # An approval
        assert DISMISS not in buttons()
    finally:
        window.session = None
        window.close()

def test_a_reply_with_only_tool_calls_leaves_no_gap(app):
    # The reply streams in ("…"), ends without text, and the tool call comes after it
    tool = lambda: c.Entry(c.TOOL, tool="read_file_with_metadata", args={"path": "a.py"}, response="x", status=c.OK, collapsed=True)
    height = lambda feed: feed.document().documentLayout().documentSize().height()
    plain = _feed(app, [tool(), tool()])
    reply = c.Entry(c.ASSISTANT, streaming=True)
    feed = _feed(app, [tool(), reply])
    reply.streaming = False
    feed.render()
    feed.entries.append(tool())
    feed.render()
    app.processEvents()
    assert height(feed) <= height(plain) + 3  # Only the empty frame's margins
    reply.text = "Now it has text."
    feed.render()
    app.processEvents()
    assert "Now it has text." in feed.toPlainText() and height(feed) > height(plain) + 10
