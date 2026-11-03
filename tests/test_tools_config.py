"""
Tests of the user's changes to the project's tools config: the filemap's Allow, Read-only and Forbid, the
checks before writing, and the settings view's form, which keeps the settings it does not know.
"""
import json
import os

import pytest

from local_fox import tools_config

def make_project(tmp_path, **settings):
    (tmp_path / ".ollmcp-tools").mkdir()
    config = {"project_dir": "..", "forbidden_paths": [".ollmcp-tools", "secrets"], "allowed_paths": [".ollmcp-tools/context"],
              "read_only_files": [], "max_output_chars": 40000, "future_setting": 1, **settings}
    (tmp_path / ".ollmcp-tools" / "tools-config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "key.txt").write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x", encoding="utf-8")
    return tmp_path

def saved(project):
    return json.loads((project / ".ollmcp-tools" / "tools-config.json").read_text(encoding="utf-8"))

def test_actions_follow_the_paths_state(tmp_path):
    project = make_project(tmp_path)
    labels = lambda path: [label.split("  ")[0] for label, _, _ in tools_config.path_actions(project, path)]
    assert labels(project / "src" / "app.py") == ["Read-only", "Forbid"]
    assert labels(project / "src") == ["Forbid"]
    assert labels(project / "secrets" / "key.txt") == ["Allow", "Read-only", "Forbid"]
    assert labels(project / "secrets") == ["Remove from forbidden paths"]

def test_change_adds_and_removes_relative_paths(tmp_path):
    project = make_project(tmp_path)
    text = tools_config.change_path(project, "forbidden_paths", project / "src" / "app.py", True)
    assert "Added `src/app.py` to forbidden_paths" in text
    assert saved(project)["forbidden_paths"] == [".ollmcp-tools", "secrets", "src/app.py"]
    assert saved(project)["future_setting"] == 1  # Kept as it was
    tools_config.change_path(project, "forbidden_paths", project / "secrets", False)
    assert saved(project)["forbidden_paths"] == [".ollmcp-tools", "src/app.py"]
    assert "nothing was changed" in tools_config.change_path(project, "forbidden_paths", project / "secrets", False)

def test_no_actions_without_a_tools_config(tmp_path):
    assert tools_config.path_actions(tmp_path, tmp_path / "a.py") == []

def test_check_catches_wrong_kinds():
    good = {"project_dir": ".."}
    tools_config.check(good)
    for bad in ({}, {"project_dir": "..", "forbidden_paths": "secrets"}, {"project_dir": "..", "max_output_chars": "many"},
                {"project_dir": "..", "ignored_dirs": ["src/build"]}):
        with pytest.raises(tools_config.ToolsConfigError):
            tools_config.check(bad)

@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

def test_form_keeps_the_file_and_its_changes(app, tmp_path):
    from local_fox.ui.settings_view import ToolsConfigForm
    project = make_project(tmp_path)
    path, values = tools_config.read(project)
    form = ToolsConfigForm(path, values)
    assert form.values() == values and not form.changed()
    form.fields["read_only_files"].setPlainText("README.md\n\n docs/a.md ")
    form.fields["disabled_toolsets"].setPlainText("web")
    form.fields["max_output_chars"].setText("12 000")
    changed = form.values()
    assert changed["read_only_files"] == ["README.md", "docs/a.md"]
    assert changed["disabled_toolsets"] == ["web"] and changed["max_output_chars"] == 12000
    assert "backup_folder" not in changed and changed["future_setting"] == 1
