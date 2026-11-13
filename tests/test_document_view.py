"""
Tests of viewing files from the filemap: a click on a file opens it in a tab beside the feed, Markdown rendered with
a switch to its raw text and code as raw text, a second click shows the open tab, binary and missing files say why
they are not shown, a changed file is read again, and the conversation's tab cannot be closed.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTabBar  # noqa: E402

from local_fox import config  # noqa: E402
from local_fox.ui.document_view import RAW, RENDERED, DocumentTabs, read_document  # noqa: E402

@pytest.fixture(autouse=True)
def scratch_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_FOX_CONFIG", str(tmp_path / "config.json"))

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])

def tabs_with_feed(tmp_path):
    from PySide6.QtWidgets import QWidget
    tabs = DocumentTabs(QWidget(), QApplication.font())
    tabs.close_all(tmp_path.resolve())
    return tabs

def test_markdown_opens_rendered_and_switches_to_raw(app, tmp_path):
    (tmp_path / "README.md").write_text("# Title\n\nSome *text*.\n", encoding="utf-8")
    tabs = tabs_with_feed(tmp_path)
    view = tabs.open_file(tmp_path / "README.md")
    assert tabs.count() == 2 and tabs.currentWidget() is view and tabs.tabText(1) == "README.md"
    assert view.mode == RENDERED and view.stack.currentWidget() is view.rendered
    assert "Title" in view.rendered.toPlainText() and "#" not in view.rendered.toPlainText()
    view.mode_buttons[RAW].click()
    assert view.stack.currentWidget() is view.raw and view.raw.toPlainText().startswith("# Title")
    assert view.path_label.text() == "README.md"

def test_code_is_raw_and_a_second_click_shows_its_tab(app, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("notes", encoding="utf-8")
    tabs = tabs_with_feed(tmp_path)
    code = tabs.open_file(tmp_path / "src" / "main.py")
    assert code.mode == RAW and not code.mode_buttons and code.raw.toPlainText() == "def main():\n    return 1\n"
    assert code.path_label.text() == "src/main.py"
    tabs.open_file(tmp_path / "notes.md")
    assert tabs.open_file(tmp_path / "src" / "main.py") is code and tabs.count() == 3 and tabs.currentIndex() == 1

def test_the_conversation_cannot_be_closed_but_files_can(app, tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    tabs = tabs_with_feed(tmp_path)
    tabs.open_file(tmp_path / "a.txt")
    assert tabs.tabBar().tabButton(0, QTabBar.RightSide) is None
    tabs.tabBar().tabButton(1, QTabBar.RightSide).click()
    assert tabs.count() == 1 and tabs.currentIndex() == 0

def test_binary_large_and_missing_files_say_why(tmp_path, monkeypatch):
    (tmp_path / "image.png").write_bytes(b"\x89PNG\0\0\0")
    assert "binary" in read_document(tmp_path / "image.png")[1]
    assert "does not exist" in read_document(tmp_path / "gone.txt")[1]
    (tmp_path / "latin.txt").write_bytes("caf\xe9\r\n".encode("latin-1"))
    assert read_document(tmp_path / "latin.txt") == ("caf�\n", "")
    monkeypatch.setattr("local_fox.ui.document_view.MAX_BYTES", 4)
    (tmp_path / "big.txt").write_text("12345", encoding="utf-8")
    assert "larger" in read_document(tmp_path / "big.txt")[1]

def test_a_changed_file_is_read_again(app, tmp_path):
    path = tmp_path / "a.py"
    path.write_text("x = 1\n", encoding="utf-8")
    view = tabs_with_feed(tmp_path).open_file(path)
    path.write_text("x = 2\n", encoding="utf-8")
    view.reload()  # What the file watcher calls
    assert view.raw.toPlainText() == "x = 2\n"

def test_a_link_to_a_project_file_opens_it(app, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("[Setup](setup.md) and [web](https://example.com)", encoding="utf-8")
    (tmp_path / "docs" / "setup.md").write_text("# Setup", encoding="utf-8")
    tabs = tabs_with_feed(tmp_path)
    view = tabs.open_file(tmp_path / "docs" / "guide.md")
    from PySide6.QtCore import QUrl
    view._link(QUrl("setup.md"))
    assert tabs.count() == 3 and tabs.currentWidget().path == (tmp_path / "docs" / "setup.md").resolve()
    view._link(QUrl("../../outside.md"))  # Outside the project: nothing opens
    assert tabs.count() == 3

def test_the_window_opens_a_clicked_file_and_sending_shows_the_conversation(app, tmp_path):
    from local_fox.ui.main_window import MainWindow
    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    window = MainWindow(dict(config.DEFAULTS))
    window.tabs.root = tmp_path.resolve()
    window.filemap.open_file.emit(str(tmp_path / "a.md"))
    assert window.tabs.count() == 2 and window.tabs.currentIndex() == 1
    window._submit("/help")
    assert window.tabs.currentIndex() == 0
    window.tabs.setCurrentIndex(1)
    window._on_decision("Allow?", ["Allow", "Skip"], 0, None)
    assert window.tabs.tabText(0) == "Conversation •"
    window._hide_notice()
    assert window.tabs.tabText(0) == "Conversation"
