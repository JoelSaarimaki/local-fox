"""
The settings view: the settings of config.json as a form, with the help text of each, in a dialog that the
≡ menu and /settings open. It edits the same file as a text editor would: Save writes all the settings to
config.json after checking them as a start does (config.validate); the model and context size apply at once,
as from their menus, and the others from the next clear, as the settings read from the file do. Save and
clear applies them all at once.

The form is built from config.DEFAULTS, so a new setting shows up in it without changes here; only a setting
whose default does not tell its type (null) needs a kind in KINDS, or it is edited as JSON.

The second tab is the open project's tools config (tools_config.py), which only the user changes. Saving it
does not restart the tools: the feed offers that, as it starts a new conversation; Save and clear does it at once.
"""
import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import config as config_module
from .. import tools_config

# Settings whose default is null, by what they take
KINDS = {"think": "think", "temperature": "float?", "seed": "int?", "max_steps": "int?"}
# The smallest and largest of the whole-number settings
RANGES = {"num_ctx": (512, 10_000_000), "font_size": (6, 40), "filemap_max_tokens": (1, 1_000_000)}
THINK_CHOICES = ["", "true", "false", "low", "medium", "high"]

def kind_of(key: str) -> str:
    """
    Returns how a setting is edited: from KINDS, or from the type of its default.
    """
    if key in KINDS:
        return KINDS[key]
    default = config_module.DEFAULTS[key]
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int):
        return "int"
    if isinstance(default, str):
        return "str"
    if isinstance(default, list):
        return "list"
    return "json"

class SettingsError(Exception):
    pass

class AppSettingsForm(QWidget):
    """
    The settings of config.json as a form. values() returns them as they would be saved.
    """

    def __init__(self, values: dict, models: list[str] | None = None, tools: list[str] | None = None):
        super().__init__()
        self.fields: dict[str, QWidget] = {}
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setVerticalSpacing(4)
        for key in config_module.DEFAULTS:
            field = self._field(key, values.get(key, config_module.DEFAULTS[key]), models, tools)
            self.fields[key] = field
            _row(form, key, field, config_module.DESCRIPTIONS.get(key, ""))

    def _field(self, key: str, value, models, tools) -> QWidget:
        kind = kind_of(key)
        if kind == "bool":
            field = QCheckBox()
            field.setChecked(bool(value))
            return field
        if kind == "int" and isinstance(value, int):
            field = QSpinBox()
            field.setMinimumWidth(160)
            low, high = RANGES.get(key, (0, 1_000_000_000))
            field.setRange(low, high)
            field.setGroupSeparatorShown(True)
            field.setValue(min(max(value, low), high))
            return field
        if kind == "think":
            field = QComboBox()
            field.setMinimumWidth(160)
            field.setEditable(True)
            field.addItems(THINK_CHOICES)
            field.setCurrentText("" if value is None else json.dumps(value) if isinstance(value, bool) else str(value))
            return field
        if key == "model" and models:
            field = QComboBox()
            field.setMinimumWidth(320)
            field.setEditable(True)
            field.addItems(models)
            field.setCurrentText(str(value))
            return field
        field = QLineEdit()
        if kind == "list":
            field.setText(", ".join(str(v) for v in value) if isinstance(value, list) else json.dumps(value))
            if key == "auto_approve_tools" and tools:
                field.setToolTip("The tools of this project: " + ", ".join(tools))
        elif kind == "json" or not isinstance(value, (str, int, float)) and value is not None:
            field.setText(json.dumps(value))
        else:
            field.setText("" if value is None else str(value))
        field.setPlaceholderText({"int?": "empty: none", "float?": "empty: the model's own", "str?": "empty: none"}.get(kind, ""))
        return field

    def values(self) -> dict:
        """
        Returns the settings as the form shows them. Raises SettingsError for a value that is not of its kind.
        """
        values = {}
        for key, field in self.fields.items():
            kind = kind_of(key)
            if isinstance(field, QCheckBox):
                values[key] = field.isChecked()
                continue
            if isinstance(field, QSpinBox):
                values[key] = field.value()
                continue
            text = (field.currentText() if isinstance(field, QComboBox) else field.text()).strip()
            try:
                values[key] = _parse(kind, text)
            except ValueError as error:
                raise SettingsError(f"{key}: {error}") from None
        return values

def _parse(kind: str, text: str):
    """
    Turns the text of a field into the setting's value.
    """
    if kind == "list":
        return [part.strip() for part in text.split(",") if part.strip()]
    if kind in ("int?", "float?", "str?", "think") and not text:
        return None
    if kind in ("int?", "int"):
        try:
            return int(text.replace(" ", "").replace("_", ""))
        except ValueError:
            raise ValueError(f"'{text}' is not a whole number.") from None
    if kind == "float?":
        try:
            return float(text.replace(",", "."))
        except ValueError:
            raise ValueError(f"'{text}' is not a number.") from None
    if kind == "think":
        return {"true": True, "false": False}.get(text.lower(), text)
    if kind == "json":
        try:
            return json.loads(text) if text else None
        except json.JSONDecodeError as error:
            raise ValueError(f"not valid JSON: {error}") from None
    return text

def _row(form: QFormLayout, key: str, field: QWidget, help_text: str) -> None:
    """
    Adds a setting to a form: its name, and under its field the help text, which spans the row.
    """
    note = QLabel(help_text, objectName="dim")
    note.setWordWrap(True)
    box = QWidget()
    # Expanding, so that the help text spans the row also under a narrow field
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 8)
    column.setSpacing(2)
    column.addWidget(field, 0, Qt.Alignment() if isinstance(field, (QLineEdit, QPlainTextEdit)) else Qt.AlignLeft)
    column.addWidget(note)
    form.addRow(QLabel(key), box)

class ToolsConfigForm(QWidget):
    """
    The project's tools config as a form. values() returns the file's settings with the form's changes; the
    settings Local Fox does not know are kept as they are.
    """

    def __init__(self, path: Path, values: dict, tools: list[str] | None = None):
        super().__init__()
        self.path = path
        self.original = values
        self.fields: dict[str, QWidget] = {}
        layout = QVBoxLayout(self)
        intro = QLabel(f"{path.as_posix()}\nThe tools read it when they start. Only you change it: the tools never let "
                       "the AI change its own limits. The details are in the README of ollmcp-generic-tools.", objectName="dim")
        intro.setWordWrap(True)
        intro.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(intro)
        unknown = [key for key in values if key not in tools_config.SETTINGS]
        if unknown:
            note = QLabel(f"Also in the file, kept as they are: {', '.join(unknown)}", objectName="dim")
            note.setWordWrap(True)
            layout.addWidget(note)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setVerticalSpacing(4)
        layout.addLayout(form)
        for key, (kind, help_text) in tools_config.SETTINGS.items():
            value = values.get(key)
            if kind in ("paths", "names?"):
                field = QPlainTextEdit()
                field.setFixedHeight(76)
                field.setPlainText("\n".join(str(v) for v in value) if isinstance(value, list) else "" if value is None else json.dumps(value))
                if key == "disabled_tools" and tools:
                    field.setToolTip("The tools offered now: " + ", ".join(tools))
            else:
                field = QLineEdit("" if value is None else str(value))
                field.setPlaceholderText("empty: the default" if kind != "str" else "")
            self.fields[key] = field
            _row(form, key, field, help_text)
        layout.addStretch(1)

    def values(self) -> dict:
        """
        Returns the settings to write. Raises SettingsError for a value that is not of its kind.
        """
        values = dict(self.original)
        for key, (kind, _) in tools_config.SETTINGS.items():
            field = self.fields[key]
            text = field.toPlainText() if isinstance(field, QPlainTextEdit) else field.text().strip()
            if kind in ("paths", "names?"):
                value = [line.strip() for line in text.splitlines() if line.strip()]
                if kind == "names?" and not value:
                    value = None
            elif not text:
                value = None
            elif kind == "int?":
                try:
                    value = int(text.replace(" ", "").replace("_", ""))
                except ValueError:
                    raise SettingsError(f"{key}: '{text}' is not a whole number.") from None
            else:
                value = text
            if key not in values and value in (None, []):
                continue  # Not written when it was not in the file and is still empty
            values[key] = value
        return values

    def changed(self) -> bool:
        return self.values() != self.original

class SettingsDialog(QDialog):
    """
    The settings view. Emits saved with the saved settings, whether to clear now, and whether the tools config changed.
    """
    saved = Signal(dict, bool, bool)
    open_folder = Signal()

    def __init__(self, parent, models: list[str] | None = None, tools: list[str] | None = None, project: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(820, 760)
        path = config_module.config_path()
        values, problem = dict(config_module.DEFAULTS), ""
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            values.update(raw if isinstance(raw, dict) else {})
            config_module.validate(raw if isinstance(raw, dict) else [], str(path))
        except (OSError, json.JSONDecodeError, config_module.ConfigError) as error:
            problem = f"config.json has a problem: {error}\nSaving writes the settings shown here."

        layout = QVBoxLayout(self)
        location = QLabel(f"{path.as_posix()}", objectName="dim")
        location.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(location)
        if problem:
            warning = QLabel(problem, objectName="error")
            warning.setWordWrap(True)
            layout.addWidget(warning)

        self.tabs = QTabWidget()
        self.app_form = AppSettingsForm(values, models, tools)
        self.tabs.addTab(_scrolled(self.app_form), "Local Fox")
        self.tools_form: ToolsConfigForm | None = None
        if project:
            try:
                tools_path, tools_values = tools_config.read(project)
                self.tools_form = ToolsConfigForm(tools_path, tools_values, tools)
                self.tabs.addTab(_scrolled(self.tools_form), "Project tools")
            except tools_config.ToolsConfigError as error:
                message = QLabel(str(error), objectName="dim")
                message.setWordWrap(True)
                message.setAlignment(Qt.AlignTop | Qt.AlignLeft)
                self.tabs.addTab(message, "Project tools")
        layout.addWidget(self.tabs, 1)

        self.error = QLabel("", objectName="error")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        folder = QPushButton("Open config folder")
        folder.clicked.connect(self.open_folder.emit)
        buttons.addWidget(folder)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setToolTip("Write config.json; the model and context size apply now, the others from the next clear")
        save.clicked.connect(lambda: self._save(False))
        save_clear = QPushButton("Save and clear", objectName="primary")
        save_clear.setToolTip("Write config.json and start a new session with the settings")
        save_clear.clicked.connect(lambda: self._save(True))
        for button in (cancel, save, save_clear):
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def _save(self, clear: bool) -> None:
        """
        Checks both tabs before writing either, so that nothing is half saved.
        """
        try:
            app_values = self.app_form.values()
            config_module.validate(app_values, "The settings")
            tools_values = self.tools_form.values() if self.tools_form and self.tools_form.changed() else None
            if tools_values is not None:
                tools_config.check(tools_values)
            settings = config_module.save(app_values)
            if tools_values is not None:
                tools_config.write(self.tools_form.path, tools_values)
        except (SettingsError, config_module.ConfigError, tools_config.ToolsConfigError, OSError) as error:
            self.error.setText(f"Not saved: {error}")
            self.error.show()
            return
        self.saved.emit(settings, clear, tools_values is not None)
        self.accept()

def _scrolled(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setWidget(widget)
    return area
