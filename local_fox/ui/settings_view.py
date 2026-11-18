"""
The settings view: the settings of config.json as a form, with the help text of each, in a dialog that the
≡ menu and /settings open. It edits the same file as a text editor would: Save writes all the settings to
config.json after checking them as a start does (config.validate); the model, context size and Ollama address
apply at once, as from their menus and /host, and the others from the next clear, as the settings read from the
file do. Save and clear applies them all at once.

The model field lists the models installed at the address in the ollama_host field: the window lists them
(models_wanted) when the view opens and again when the address is edited, as another computer's Ollama often has
other models. Each setting has a "Default" button beside it, and "Restore defaults" sets the whole tab to the
defaults but the API key; neither saves anything until "Save" (e.g. back to this computer's Ollama after another's).

The form is built from config.DEFAULTS, so a new setting shows up in it without changes here; only a setting
whose default does not tell its type (null) needs a kind in KINDS, or it is edited as JSON.

The second tab is the open project's tools config (tools_config.py), which only the user changes. Saving it
does not restart the tools: the feed offers that, as it starts a new conversation; Save and clear does it at once.
"""
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import config as config_module
from ..ollama_server import normalize_host, processors
from .. import tools_config
from ..conversation import format_number

# Settings whose default is null, by what they take
KINDS = {"think": "think", "temperature": "float?", "seed": "int?", "max_steps": "int?", "num_thread": "int?"}
# The smallest and largest of the whole-number settings
RANGES = {"font_size": (6, 40), "filemap_max_tokens": (1, 1_000_000)}
THINK_CHOICES = ["", "true", "false", "low", "medium", "high"]
AUTO_THREADS = "auto"  # Ollama chooses the CPU threads itself, as the top bar's "threads auto"
HOST_PAUSE_MS = 700  # The pause in typing the address after which its models are listed

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

def context_sizes(current: int, maximum: int | None) -> list[int]:
    """
    Returns the context sizes to choose from, in the settings and for the session: 4 096 tokens doubled up to the
    model's maximum (or 1 048 576 if it is not known), the maximum itself, and the current size, which may be
    anything set with /context or in config.json. Doubling steps are the ones that make a difference in memory and
    in what fits; finer ones are for /context and config.json.
    """
    limit = maximum or 1_048_576
    sizes = {4096 * 2 ** i for i in range(9) if 4096 * 2 ** i <= limit} | {limit, current}
    return sorted(sizes)

class AppSettingsForm(QWidget):
    """
    The settings of config.json as a form. values() returns them as they would be saved. Emits models_wanted with
    the address in the ollama_host field when its models should be listed; show_models shows them.
    """
    models_wanted = Signal(str)

    def __init__(self, values: dict, models: list[str] | None = None, tools: list[str] | None = None,
                 max_contexts: dict[str, int] | None = None):
        super().__init__()
        self.fields: dict[str, QWidget] = {}
        self.default_buttons: dict[str, QPushButton] = {}
        # The largest context of the configured model, if known (from the open session): the last size offered
        self.max_context = (max_contexts or {}).get(values.get("model"))
        self.models_note = QLabel("", objectName="dim")
        self.models_note.setWordWrap(True)
        self.models_note.hide()
        self._models: list[str] | None = list(models) if models else None
        self._models_host = ""  # The address whose models were last asked for
        # The models are listed after a pause in typing the address, not at every key
        self._host_timer = QTimer(self, singleShot=True, interval=HOST_PAUSE_MS)
        self._host_timer.timeout.connect(self.want_models)
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setVerticalSpacing(4)
        for key in config_module.DEFAULTS:
            field = self._field(key, values.get(key, config_module.DEFAULTS[key]), models, tools)
            self.fields[key] = field
            button = QPushButton("Default")
            button.setToolTip(f"Set to the default: {_shown_default(key)}. Saved only by \"Save\".")
            button.clicked.connect(lambda checked=False, k=key: self.set_value(k, config_module.DEFAULTS[k]))
            self.default_buttons[key] = button
            _row(form, key, field, config_module.DESCRIPTIONS.get(key, ""), button,
                 self.models_note if key == "model" else None)
        for key, field in self.fields.items():
            _on_change(field, lambda k=key: self._changed(k))
            self._show_default_button(key)

    def _show_default_button(self, key: str) -> None:
        """
        Offers a setting's "Default" button only when its field differs from the default.
        """
        try:
            differs = self.value_of(key) != config_module.DEFAULTS[key]
        except SettingsError:
            differs = True
        self.default_buttons[key].setEnabled(differs)

    def _changed(self, key: str) -> None:
        """
        A field changed: its "Default" button follows, a changed address has its models listed after a pause in
        typing, and a changed model is checked against the models listed.
        """
        self._show_default_button(key)
        if key == "ollama_host":
            self._host_timer.start()
        elif key == "model":
            self._show_models_note()

    def want_models(self) -> None:
        """
        Asks for the models of the address in the field, if it is an address and not the one last asked for.
        """
        host = normalize_host(self.fields["ollama_host"].text())
        if not host:
            self._models_host, self._models = "", None
            self._note("The address is not complete, so its models are not listed.")
            return
        if host == self._models_host:
            return
        self._models_host = host
        self._note(f"Listing the models at {host}…")
        self.models_wanted.emit(host)

    def show_models(self, host: str, models: list[str] | None, error: str = "") -> None:
        """
        Shows the models installed at host in the model field, keeping the model the field has, or that they could
        not be listed (models None). A list of an address the field no longer has is dropped.
        """
        if host != self._models_host:
            return
        field: QComboBox = self.fields["model"]
        model = field.currentText()
        field.blockSignals(True)
        field.clear()
        field.addItems(models or [])
        field.setCurrentText(model)
        field.blockSignals(False)
        self._models = models
        if models is None:
            self._note(f"Ollama at {host} did not answer, so its models are not listed" + (f": {error}" if error else "."))
        else:
            self._show_models_note()

    def _show_models_note(self) -> None:
        """
        Tells under the model field whether the server at the address has the model.
        """
        if self._models is None or not self._models_host:
            return
        model = self.fields["model"].currentText().strip()
        if not self._models:
            self._note(f"Ollama at {self._models_host} has no models installed.")
        elif model and model not in self._models and f"{model}:latest" not in self._models:
            self._note(f"Ollama at {self._models_host} does not have {model}: choose one of its {len(self._models)} models.")
        else:
            self._note(f"The list has the {len(self._models)} models installed at {self._models_host}.")

    def _note(self, text: str) -> None:
        self.models_note.setText(text)
        self.models_note.setVisible(bool(text))

    def set_value(self, key: str, value) -> None:
        """
        Shows a value in a setting's field, as the form shows it when opened (the "Default" buttons).
        """
        field = self.fields[key]
        if isinstance(field, QCheckBox):
            field.setChecked(bool(value))
        elif isinstance(field, QSpinBox):
            field.setValue(value)
        elif key in ("num_ctx", "num_thread"):
            index = field.findData(value) if value is not None else 0
            if index < 0:
                field.addItem(f"{format_number(value)} tokens" if key == "num_ctx" else str(value), value)
                index = field.count() - 1
            field.setCurrentIndex(index)
        elif isinstance(field, QComboBox):
            field.setCurrentText(_text_of(kind_of(key), value))
        else:
            field.setText(_text_of(kind_of(key), value))

    def restore_defaults(self, keep=frozenset(config_module.SECRETS)) -> None:
        """
        Shows the defaults in every field but those of keep: the API key, which is hard to get back once cleared.
        """
        for key, value in config_module.DEFAULTS.items():
            if key not in keep:
                self.set_value(key, value)

    def _field(self, key: str, value, models, tools) -> QWidget:
        kind = kind_of(key)
        if kind == "bool":
            field = QCheckBox()
            field.setChecked(bool(value))
            return field
        if key == "num_ctx" and isinstance(value, int):
            # The same sizes as the session's context dialog; a size of its own is kept in config.json as it is
            field = QComboBox()
            field.setMinimumWidth(160)
            for size in context_sizes(value, self.max_context):
                field.addItem(f"{format_number(size)} tokens", size)
            field.setCurrentIndex(field.findData(value))
            return field
        if key == "num_thread":
            # Ollama's choice, or a number up to this computer's processors (or the value, set for another computer)
            field = QComboBox()
            field.setMinimumWidth(160)
            field.addItem(AUTO_THREADS, None)
            count = processors()
            for threads in range(1, max(count, value or 0) + 1):
                field.addItem(f"{threads} (all)" if threads == count else str(threads), threads)
            field.setCurrentIndex(max(field.findData(value), 0))
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
            field.setCurrentText(_text_of(kind, value))
            return field
        if key == "model":
            # A list of the models at the address when they are known (show_models), and a name can be typed
            field = QComboBox()
            field.setMinimumWidth(320)
            field.setEditable(True)
            field.addItems(models or [])
            field.setCurrentText(str(value))
            return field
        field = QLineEdit()
        if key in config_module.SECRETS:
            field.setEchoMode(QLineEdit.PasswordEchoOnEdit)  # Hidden, except while it is typed or pasted
        if key == "auto_approve_tools" and tools:
            field.setToolTip("The tools of this project: " + ", ".join(tools))
        field.setText(_text_of(kind, value))
        field.setPlaceholderText({"int?": "empty: none", "float?": "empty: the model's own", "str?": "empty: none"}.get(kind, ""))
        return field

    def values(self) -> dict:
        """
        Returns the settings as the form shows them. Raises SettingsError for a value that is not of its kind.
        """
        return {key: self.value_of(key) for key in self.fields}

    def value_of(self, key: str):
        """
        Returns one setting as its field shows it. Raises SettingsError for a value that is not of its kind.
        """
        field = self.fields[key]
        if isinstance(field, QCheckBox):
            return field.isChecked()
        if isinstance(field, QSpinBox):
            return field.value()
        if key == "num_thread":
            return field.currentData()  # None for Ollama's choice
        if isinstance(field, QComboBox) and field.currentData() is not None:
            return field.currentData()  # A choice with its value, e.g. the context size
        text = (field.currentText() if isinstance(field, QComboBox) else field.text()).strip()
        try:
            return _parse(kind_of(key), text)
        except ValueError as error:
            raise SettingsError(f"{key}: {error}") from None

def _text_of(kind: str, value) -> str:
    """
    Returns a value as a text field shows it, the reverse of _parse.
    """
    if kind == "list":
        return ", ".join(str(v) for v in value) if isinstance(value, list) else json.dumps(value)
    if kind == "think":
        return "" if value is None else json.dumps(value) if isinstance(value, bool) else str(value)
    if kind == "json" or not isinstance(value, (str, int, float)) and value is not None:
        return json.dumps(value)
    return "" if value is None else str(value)

def _shown_default(key: str) -> str:
    """
    Returns a setting's default as the tooltip of its "Default" button names it.
    """
    value = config_module.DEFAULTS[key]
    if value is None or value == "" or value == []:
        return "empty"
    if isinstance(value, bool):
        return "on" if value else "off"
    return format_number(value) if isinstance(value, int) else str(value)

def _on_change(field: QWidget, callback) -> None:
    """
    Calls callback whenever the field's value changes, whatever kind of field it is.
    """
    if isinstance(field, QCheckBox):
        field.toggled.connect(lambda _: callback())
    elif isinstance(field, QSpinBox):
        field.valueChanged.connect(lambda _: callback())
    elif isinstance(field, QComboBox):
        field.currentTextChanged.connect(lambda _: callback())
    else:
        field.textChanged.connect(lambda *_: callback())

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

def _row(form: QFormLayout, key: str, field: QWidget, help_text: str, button: QWidget | None = None,
         status: QLabel | None = None) -> None:
    """
    Adds a setting to a form: its name, its field with the button beside it, if any, and under them the status,
    if any, and the help text, which spans the row.
    """
    note = QLabel(help_text, objectName="dim")
    note.setWordWrap(True)
    box = QWidget()
    # Expanding, so that the help text spans the row also under a narrow field
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 8)
    column.setSpacing(2)
    wide = isinstance(field, (QLineEdit, QPlainTextEdit))
    if button:
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(field, 1 if wide else 0)
        line.addWidget(button)
        if not wide:
            line.addStretch(1)
        column.addLayout(line)
    else:
        column.addWidget(field, 0, Qt.Alignment() if wide else Qt.AlignLeft)
    if status:
        column.addWidget(status)
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
    The settings view. Emits saved with the saved settings, whether to clear now, and whether the tools config
    changed, and models_wanted with an address whose models the model field should list (show_models).
    """
    saved = Signal(dict, bool, bool)
    open_folder = Signal()
    models_wanted = Signal(str)

    def __init__(self, parent, models: list[str] | None = None, tools: list[str] | None = None, project: Path | None = None,
                 max_contexts: dict[str, int] | None = None):
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
        self.app_form = AppSettingsForm(values, models, tools, max_contexts)
        self.app_form.models_wanted.connect(self.models_wanted.emit)
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
        defaults = QPushButton("Restore defaults")
        defaults.setToolTip("Set the Local Fox settings in the form to their defaults, all but the API key. Nothing is saved "
                            "until \"Save\", and \"Cancel\" keeps the settings as they were.")
        defaults.clicked.connect(self._restore_defaults)
        buttons.addWidget(defaults)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setToolTip("Write config.json; the model, context size, CPU threads and Ollama address apply now, the others from the next clear")
        save.clicked.connect(lambda: self._save(False))
        save_clear = QPushButton("Save and clear", objectName="primary")
        save_clear.setToolTip("Write config.json and start a new session with the settings")
        save_clear.clicked.connect(lambda: self._save(True))
        for button in (cancel, save, save_clear):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        # The models of the address, once the window has connected models_wanted
        QTimer.singleShot(0, self.app_form.want_models)

    def show_models(self, host: str, models: list[str] | None, error: str = "") -> None:
        self.app_form.show_models(host, models, error)

    def _restore_defaults(self) -> None:
        self.tabs.setCurrentIndex(0)  # Where they show
        self.app_form.restore_defaults()

    def _save(self, clear: bool) -> None:
        """
        Checks both tabs before writing either, so that nothing is half saved. The address is saved as /host
        saves it (with http:// and the port), so that the same server has the same address wherever it was set.
        """
        try:
            app_values = self.app_form.values()
            host = normalize_host(app_values["ollama_host"])
            if not host:
                raise SettingsError(f"ollama_host: '{app_values['ollama_host']}' is not an address. Give it as e.g. "
                                    "192.168.1.20, 192.168.1.20:11434 or http://192.168.1.20:11434.")
            app_values["ollama_host"] = host
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
