"""
Tests of the settings: the checks of config.json, saving it, and the settings view's form, which must give
back the settings it was given.
"""
import json
import os

import pytest

from local_fox import config

def test_every_setting_has_a_description():
    assert set(config.DESCRIPTIONS) == set(config.DEFAULTS)

def test_validate_rejects_wrong_values():
    for values in ({"num_ctx": 100}, {"temperature": "hot"}, {"bundled_tools": "yes"},
                   {"auto_approve_tools": "read_file"}, {"model": ""}, {"unknown": 1}):
        with pytest.raises(config.ConfigError):
            config.validate(values)
    assert config.validate({"think": "high", "temperature": 0.2})["think"] == "high"

def test_save_writes_all_settings_in_order(tmp_path):
    path = tmp_path / "config.json"
    config.save({**config.DEFAULTS, "model": "gemma4:26b"}, path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert list(written) == list(config.DEFAULTS) and written["model"] == "gemma4:26b"
    assert config.load(path)["model"] == "gemma4:26b"

@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

def test_form_gives_back_the_settings(app):
    from local_fox.ui.settings_view import AppSettingsForm
    values = {**config.DEFAULTS, "think": "high", "temperature": 0.3, "seed": 7, "max_steps": None,
              "auto_approve_tools": ["read_file_with_metadata", "get_outline"], "bundled_tools": False}
    assert AppSettingsForm(values, models=["flint:40b"]).values() == values
    assert AppSettingsForm(config.DEFAULTS).values() == config.DEFAULTS

def test_context_size_is_chosen_from_the_sizes_of_the_sessions_dialog(app):
    from local_fox.ui.settings_view import AppSettingsForm
    form = AppSettingsForm({**config.DEFAULTS, "model": "flint:40b", "num_ctx": 65536}, max_contexts={"flint:40b": 262144})
    field = form.fields["num_ctx"]
    assert [field.itemData(i) for i in range(field.count())] == [4096, 8192, 16384, 32768, 65536, 131072, 262144]
    assert field.currentText() == "65 536 tokens"
    field.setCurrentIndex(field.findData(131072))
    assert form.values()["num_ctx"] == 131072
    # A size of its own in config.json stays as it is; another model's maximum is not known
    form = AppSettingsForm({**config.DEFAULTS, "model": "gemma4:26b", "num_ctx": 50000}, max_contexts={"flint:40b": 262144})
    assert form.values()["num_ctx"] == 50000 and form.fields["num_ctx"].itemData(form.fields["num_ctx"].count() - 1) == 1_048_576

def test_the_api_key_is_a_secret(app):
    from local_fox.ui.settings_view import AppSettingsForm
    from PySide6.QtWidgets import QLineEdit
    with pytest.raises(config.ConfigError, match="ollama_api_key"):
        config.validate({"ollama_api_key": None})
    assert config.without_secrets({"model": "m", "ollama_api_key": "secret"}) == {"model": "m", "ollama_api_key": "(set)"}
    assert config.without_secrets({"ollama_api_key": ""})["ollama_api_key"] == ""
    form = AppSettingsForm({**config.DEFAULTS, "ollama_api_key": "secret"})
    assert form.fields["ollama_api_key"].echoMode() != QLineEdit.Normal and form.values()["ollama_api_key"] == "secret"

def test_form_reports_wrong_numbers(app):
    from local_fox.ui.settings_view import AppSettingsForm, SettingsError
    form = AppSettingsForm(config.DEFAULTS)
    form.fields["seed"].setText("abc")
    with pytest.raises(SettingsError, match="seed"):
        form.values()

def test_removed_settings_are_dropped():
    assert not set(config.validate({"session_log": True, "session_log_days": 30, "tool_calls_collapsed": True})) & config.REMOVED

def test_the_wheel_passes_over_dropdowns_and_numbers_to_the_scrolled_settings(app):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QComboBox, QSpinBox
    from local_fox.ui.settings_view import AppSettingsForm
    from local_fox.ui.wheel_guard import guard_wheel
    assert guard_wheel(app) is guard_wheel(app)  # Installed once
    form = AppSettingsForm({**config.DEFAULTS, "num_ctx": 65536, "num_thread": 4}, max_contexts={"flint:40b": 262144})
    assert isinstance(form.fields["font_size"], QSpinBox) and isinstance(form.fields["num_thread"], QComboBox)
    for key in ("num_ctx", "num_thread", "font_size"):
        before = form.values()[key]
        for delta in (-120, 120):
            event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, delta),
                                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            QApplication.sendEvent(form.fields[key], event)
            # Ignored, so Qt hands a real wheel event on to the scroll area (a sent one is not handed on)
            assert not event.isAccepted(), key
        assert form.values()[key] == before, key

def test_each_setting_can_be_set_back_to_its_default(app):
    from local_fox.ui.settings_view import AppSettingsForm
    changed = {**config.DEFAULTS, "ollama_host": "http://192.168.1.20:11434", "num_ctx": 131072, "num_thread": 4,
               "think": "high", "temperature": 0.3, "auto_approve_tools": ["get_outline"], "font_size": 12,
               "bundled_tools": False, "ollama_api_key": "secret", "model": "gemma4:26b"}
    form = AppSettingsForm(changed)
    assert all(form.default_buttons[key].isEnabled() for key in changed if changed[key] != config.DEFAULTS[key])
    assert not form.default_buttons["seed"].isEnabled()  # Already the default
    form.default_buttons["ollama_host"].click()
    assert form.values()["ollama_host"] == config.DEFAULTS["ollama_host"] and not form.default_buttons["ollama_host"].isEnabled()
    assert form.values()["model"] == "gemma4:26b"  # The others stay
    # "Restore defaults" sets them all but the API key, which is hard to get back
    form.restore_defaults()
    assert form.values() == {**config.DEFAULTS, "ollama_api_key": "secret"}
    form.default_buttons["ollama_api_key"].click()
    assert form.values() == config.DEFAULTS

def test_the_model_field_lists_the_models_at_the_address_typed(app):
    from local_fox.ui.settings_view import AppSettingsForm
    form = AppSettingsForm({**config.DEFAULTS, "model": "flint:40b"}, models=["flint:40b", "gemma4:26b"])
    wanted = []
    form.models_wanted.connect(wanted.append)
    form.want_models()
    assert wanted == ["http://127.0.0.1:11434"]
    form.fields["ollama_host"].setText("192.168.1.20")
    form.want_models()  # As after the pause in typing
    assert wanted[-1] == "http://192.168.1.20:11434" and "Listing" in form.models_note.text()
    form.show_models("http://127.0.0.1:11434", ["old:1b"])  # A late answer of the address before: dropped
    field = form.fields["model"]
    assert [field.itemText(i) for i in range(field.count())] == ["flint:40b", "gemma4:26b"]
    form.show_models("http://192.168.1.20:11434", ["llama3.1:latest", "qwen3.6:35b"])
    assert [field.itemText(i) for i in range(field.count())] == ["llama3.1:latest", "qwen3.6:35b"]
    assert form.values()["model"] == "flint:40b"  # Kept as it was, and the note says the server lacks it
    assert "does not have flint:40b" in form.models_note.text()
    field.setCurrentText("llama3.1")
    assert "2 models installed at http://192.168.1.20:11434" in form.models_note.text()
    form.fields["ollama_host"].setText("http://192.168.1.30:11434")
    form.want_models()
    form.show_models("http://192.168.1.30:11434", None, "it did not answer in time")
    assert "did not answer" in form.models_note.text() and field.count() == 0 and form.values()["model"] == "llama3.1"
    form.fields["ollama_host"].setText("http://")
    form.want_models()
    assert len(wanted) == 3 and "not complete" in form.models_note.text()

def test_the_address_is_saved_complete_and_checked(app, tmp_path, monkeypatch):
    from local_fox.ui.settings_view import SettingsDialog
    path = tmp_path / "config.json"
    monkeypatch.setenv("LOCAL_FOX_CONFIG", str(path))
    dialog = SettingsDialog(None)
    saved = []
    dialog.saved.connect(lambda settings, clear, tools: saved.append((settings, clear)))
    dialog.app_form.fields["ollama_host"].setText("192.168.1.20")
    dialog._save(True)
    assert saved[-1] == ({**config.DEFAULTS, "ollama_host": "http://192.168.1.20:11434"}, True)
    assert json.loads(path.read_text(encoding="utf-8"))["ollama_host"] == "http://192.168.1.20:11434"
    dialog = SettingsDialog(None)
    dialog.app_form.fields["ollama_host"].setText("not an address")
    dialog._save(False)
    assert "is not an address" in dialog.error.text() and len(saved) == 1
