"""
Tests of choosing the project before the window opens: the start dialog opens nothing by itself (no selection,
Enter does nothing), the setup question creates nothing unless Create is clicked, Back returns to the projects,
closing a dialog cancels, and a drive root can only be gone back from.
"""
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QPushButton  # noqa: E402

from local_fox import recent_projects  # noqa: E402
from local_fox.ui.project_dialog import BACK, ProjectDialog, SetupDialog, choose_project  # noqa: E402

@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    """The config, the recent projects and the tools' user folder in tmp_path."""
    from ollmcp_tools import locations
    monkeypatch.setenv("LOCAL_FOX_CONFIG", str(tmp_path / "data" / "config.json"))
    monkeypatch.setattr(locations, "USER_CONFIG_PATH", tmp_path / "home" / "user-config.json")
    monkeypatch.setattr(locations, "DEFAULT_BACKUP_ROOT", tmp_path / "home" / "backups")

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])

def make_project(folder: Path) -> Path:
    (folder / ".local-fox").mkdir(parents=True)
    (folder / ".local-fox" / "tools-config.json").write_text(json.dumps({"project_dir": ".."}), encoding="utf-8")
    return folder

def enter(dialog):
    dialog.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "\r"))

def buttons(dialog):
    return [b.text() for b in dialog.findChildren(QPushButton)]

def test_start_dialog_opens_nothing_by_itself(app, tmp_path):
    project = make_project(tmp_path / "project")
    plain = tmp_path / "plain"
    plain.mkdir()
    for folder in (tmp_path / "gone", plain, project):
        recent_projects.add(folder)
    dialog = ProjectDialog(None, startup=True)
    texts = [dialog.list.item(i).text() for i in range(dialog.list.count())]
    assert texts[0].startswith("project\n") and "(not set up)" in texts[1] and "(missing)" in texts[2]
    assert dialog.list.item(2).flags() == Qt.NoItemFlags  # A missing folder cannot be chosen
    assert dialog.list.selectedItems() == [] and not dialog.open_button.isEnabled()
    assert "Quit" in buttons(dialog) and not any(b.isDefault() or b.autoDefault() for b in dialog.findChildren(QPushButton))
    enter(dialog)
    assert dialog.result() == QDialog.Rejected and dialog.chosen is None  # Enter opened nothing
    dialog.list.item(0).setSelected(True)
    assert dialog.open_button.isEnabled()
    dialog.open_button.click()
    assert dialog.result() == QDialog.Accepted and dialog.chosen == project.resolve()

def test_setup_question(app, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    question = SetupDialog(plain)
    assert buttons(question) == ["Back", "Create .local-fox"]
    enter(question)
    assert question.result() == QDialog.Rejected and not (plain / ".local-fox").exists()
    root = SetupDialog(Path(tmp_path.anchor))
    assert buttons(root) == ["Back"]  # A drive root is never set up

def test_back_returns_to_the_projects_and_closing_cancels(app, tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    shown = []
    monkeypatch.setattr(SetupDialog, "exec", lambda self: shown.append("setup") or BACK)
    monkeypatch.setattr(ProjectDialog, "exec", lambda self: shown.append("projects") or QDialog.Rejected)
    assert choose_project(None, startup=True, folder=plain) is None  # Back, then X in the start dialog: Local Fox quits
    assert shown == ["setup", "projects"] and not (plain / ".local-fox").exists()

def test_create_sets_up_and_a_project_opens_at_once(app, tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setattr(SetupDialog, "exec", lambda self: QDialog.Accepted)
    project, report = choose_project(None, startup=True, folder=plain)
    assert project == plain.resolve() and (plain / ".local-fox" / "tools-config.json").exists() and "Created" in report
    monkeypatch.setattr(SetupDialog, "exec", lambda self: pytest.fail("A project gets no setup question"))
    assert choose_project(None, startup=True, folder=plain) == (plain.resolve(), "")

def test_window_opens_only_a_project(app, tmp_path):
    from local_fox import config
    from local_fox.ui.main_window import MainWindow
    window = MainWindow(dict(config.DEFAULTS))
    try:
        window.open_project(tmp_path)  # Not set up
        assert window.session is None and not (tmp_path / ".local-fox").exists()
    finally:
        window.close()

def test_no_recent_projects_is_a_text_not_a_row(app):
    dialog = ProjectDialog(None, startup=True)
    assert dialog.windowTitle() == "Local Fox"
    assert dialog.list.count() == 0 and dialog.list.isHidden() and not dialog.empty.isHidden()

def test_icon_from_the_logo(app, tmp_path):
    import struct
    from local_fox.ui import theme
    from local_fox.ui.logo import ICON_SIZES, dots, fox_image, write_ico
    points, width, height = dots()
    assert (width, height) == (36, 44) and points
    image = fox_image(44)  # One pixel per dot
    lit = sum(1 for x in range(44) for y in range(44) if image.pixelColor(x, y).name() == theme.ACCENT)
    assert lit == len(points) and image.pixelColor(0, 0).name() == theme.BG
    write_ico(tmp_path / "fox.ico")
    data = (tmp_path / "fox.ico").read_bytes()
    assert struct.unpack("<HHH", data[:6]) == (0, 1, len(ICON_SIZES))
