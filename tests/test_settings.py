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
