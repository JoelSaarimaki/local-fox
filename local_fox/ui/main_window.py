"""
The window: the top bar, the filemap and the Ollama panel on the left, the feed in the middle, and the notice bar
and the prompt input at the bottom.
"""
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QSplitter, QToolTip,
                               QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from .. import config as config_module
from .. import conversation as c
from .. import ollama_server, recent_projects, saved_conversations, tools_config, tools_setup
from ..conversation import format_number
from ..export import to_markdown
from ..filemap import Rules, tools_config_path
from ..session import RESTART_TOOLS, Session, auto_choice
from ..session_log import LOG_NAME
from . import theme
from .bridge import AsyncRunner, Bridge, QtFrontend
from .feed import Feed
from .filemap_view import FilemapView
from .ollama_panel import OllamaPanel
from .project_dialog import choose_project
from .prompt import PromptInput
from .settings_view import SettingsDialog, context_sizes

HELP = f"""Commands:
/clear            start a new session, with the instructions and the filemap again
/export           save the conversation as a Markdown file
/instructions     read the project instructions (.local-fox/instructions.md) again after editing them
/model [name]     show the installed models, or switch to one
/context [size]   show the context size and how much the conversation uses, or set the size for this session
/host [address]   show the Ollama address, or set it, e.g. /host 192.168.1.20 for Ollama on another computer
/settings         show the settings of config.json
/config           open the folder of config.json; a clear reads it again
/log              open the folder of the log of this run
/project          open another project: the recent projects, or choose a folder
/reopen           reopen the last saved conversation of the project (all of them are in ≡ > Conversations)
/setup            create the open project's missing files (.mcp.json, .local-fox), after confirming them
/help             show this help

Keys: Enter sends, Shift+Enter adds a line, Up and Down recall sent messages, Esc stops the model.
Right-click a file in the filemap to add its path to the prompt."""

REOPEN = "Reopen it"
# Closes a notice's bar without its actions. Only notices get it: a question the session waits on (an approval, the
# step limit) is answered with one of its own options, and the notice stays in the feed either way.
DISMISS = "Dismiss"
# How often the Ollama panel asks the server what it has loaded ('ollama ps', a line in the server's console each
# time): often only while the answer is about to change, a server starting or a model loading for a request, and
# otherwise seldom, with a check whenever the window becomes active again (_pace_polls)
FAST_POLL_MS = 2000
SLOW_POLL_MS = 10000

def _button(text: str, slot, name: str = "", tip: str = "") -> QPushButton:
    button = QPushButton(text)
    if name:
        button.setObjectName(name)
    if tip:
        button.setToolTip(tip)
    button.setCursor(Qt.PointingHandCursor)
    # The slot is called without the "checked" argument of clicked: PySide passed it to a slot with optional
    # parameters, so "Clear" called _clear(False), which failed silently in the windowed executable
    button.clicked.connect(lambda checked=False: slot())
    return button

class MainWindow(QMainWindow):
    def __init__(self, settings: dict, config_error: str | None = None):
        """
        Builds the window. The project is opened with open_project, after the start dialog chose it (app.py).
        """
        super().__init__()
        self.settings = settings
        self.runner = AsyncRunner()
        self.bridge = Bridge()
        self.session: Session | None = None
        self._decision = None   # The future of the decision the notice bar shows
        self._decision_options: list[str] = []
        self._choose = None     # Answers the decision the notice bar shows
        self._turn = None       # The future of the turn sent last, so that a second Enter cannot send before it starts
        self._reopen_offer: Path | None = None  # The conversation the notice bar offers to reopen
        self._poll_pending = False
        self._after_poll = []  # Called with the result of the poll on its way (_poll_ollama's then)
        self._ollama_state: ollama_server.State | None = None  # The latest poll of the Ollama server
        self._serve = None               # The 'ollama serve' Local Fox started on the user's click, stopped at the close
        self._serve_started = 0.0        # When it was started, while it has not answered yet (0 when it has)
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)

        font = QFont()
        font.setFamilies(theme.FONT_FAMILIES)
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(settings.get("font_size", 10))
        self.font_ = font
        self.setFont(font)
        self.setStyleSheet(theme.stylesheet(settings.get("font_size", 10)))

        # The top bar
        bar = QFrame(objectName="bar")
        top = QHBoxLayout(bar)
        top.setContentsMargins(8, 4, 8, 4)
        # The name in orange and the version in grey, as in the start dialog
        title = QLabel(f"<span style='color: {theme.ACCENT}; font-weight: bold'>{APP_NAME}</span>"
                       f"&nbsp;<span style='color: {theme.DIM}'>{__version__}</span>")
        self.model_button = QToolButton(objectName="flat")
        self.model_button.setPopupMode(QToolButton.InstantPopup)
        self.model_button.setToolTip("The model: click to switch")
        self.model_menu = QMenu(self)
        self.model_menu.aboutToShow.connect(self._fill_model_menu)
        self.model_button.setMenu(self.model_menu)
        self.context_button = _button("", self._ask_context, "flat", "The context size: click to change it for this session")
        self.usage_label = QLabel("", objectName="dim")
        self.usage_label.setToolTip("How much of the context the conversation uses")
        self.project_label = QLabel("", objectName="dim")
        for widget in (title, self.model_button, self.context_button):
            top.addWidget(widget)
            top.addSpacing(10)
        top.addStretch(1)
        top.addWidget(self.project_label)
        top.addSpacing(10)
        menu_button = QToolButton(objectName="flat")
        menu_button.setText("≡")
        menu_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self)
        menu.addAction("Open project", self._open_project)
        self.conversations_menu = menu.addMenu("Conversations")
        self.conversations_menu.aboutToShow.connect(self._fill_conversations_menu)
        menu.addAction("Reload instructions", self._reload_instructions)
        menu.addAction("Set up project", lambda: self._set_up())
        menu.addAction("Context size", self._ask_context)
        menu.addAction("Settings", self._open_settings)
        menu.addAction("Open config folder", self._open_config)
        menu_button.setMenu(menu)
        top.addWidget(_button("Export", self._export, tip="Save the conversation as Markdown (/export)"))
        top.addWidget(menu_button)

        # The filemap and the Ollama panel on the left, down to the bottom bar, and the feed, the notice bar and the
        # prompt on the right
        self.filemap = FilemapView()
        self.filemap.full_paths = settings.get("prompt_full_paths", False)
        self.ollama_panel = OllamaPanel()
        self.ollama_panel.start_clicked.connect(self._start_ollama)
        self.ollama_panel.stop_clicked.connect(self._stop_ollama)
        files = QWidget()
        files_column = QVBoxLayout(files)
        files_column.setContentsMargins(0, 0, 0, 0)
        files_column.setSpacing(0)
        files_column.addWidget(self.filemap, 1)
        files_column.addWidget(self.ollama_panel)
        self.feed = Feed(font)

        # The notice bar, for approvals and other decisions
        self.notice_bar = QFrame(objectName="notice")
        # The text above its buttons: a button can be wide ("Restart tools and clear"), and beside a long text it
        # would squeeze it
        notice_layout = QVBoxLayout(self.notice_bar)
        notice_layout.setContentsMargins(8, 6, 8, 6)
        notice_layout.setSpacing(6)
        self.notice_label = QLabel("")
        self.notice_label.setWordWrap(True)
        notice_layout.addWidget(self.notice_label)
        self.notice_buttons = QHBoxLayout()
        button_row = QHBoxLayout()
        button_row.addLayout(self.notice_buttons)
        button_row.addStretch(1)
        notice_layout.addLayout(button_row)
        self.notice_bar.hide()

        # The prompt
        self.prompt = PromptInput()
        self.prompt.setFont(font)
        self.prompt.show_lines()  # PROMPT_LINES; fitted again when the stylesheet applies
        self.prompt.submitted.connect(self._on_prompt)
        self.prompt.escape.connect(self._stop)
        conversation = QWidget()
        column = QVBoxLayout(conversation)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.feed, 1)
        column.addWidget(self.notice_bar)
        column.addWidget(self.prompt)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(files)
        splitter.addWidget(conversation)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 1100])

        # The bottom bar: how much of the context the conversation uses, and Clear and Send (Stop while the model runs)
        bottom = QFrame(objectName="bar")
        bottom_row = QHBoxLayout(bottom)
        bottom_row.setContentsMargins(8, 4, 8, 4)
        # "Clear" in the other corner from "Send", so that it is not pressed by mistake instead of it
        bottom_row.addWidget(_button("Clear", self._clear, tip="Start a new conversation (/clear)"))
        bottom_row.addWidget(_button("Help", lambda: self._local_notice(HELP), tip="Show the commands and keys (/help)"))
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.usage_label)
        bottom_row.addSpacing(24)
        # Auto mode, off at every start: the model works without pausing for approvals and "Continue" questions
        self.auto_check = QCheckBox("Auto mode")
        self.auto_check.setCursor(Qt.PointingHandCursor)
        self.auto_check.setToolTip(
            "Run tool calls without asking, and answer \"Continue\" when the model would pause, e.g. at the step limit.\n"
            "The turn still ends when you stop it, on an error, or when the context is full. Never sends a message.")
        self.auto_check.toggled.connect(lambda on: self._set_auto(on))
        bottom_row.addWidget(self.auto_check)
        bottom_row.addSpacing(12)
        self.send_button = _button("Send", self._send_clicked, "primary")
        bottom_row.addWidget(self.send_button)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(bar)
        layout.addWidget(splitter, 1)
        layout.addWidget(bottom)
        self.setCentralWidget(central)

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._stop)
        self.filemap.add_to_prompt.connect(self.prompt.insert_text)
        self.filemap.change_rule.connect(self._change_rule)
        self.bridge.reset.connect(self._on_reset)
        self.bridge.entry_changed.connect(self._on_entry)
        self.bridge.status.connect(self._update_status)
        self.bridge.decision.connect(self._on_decision)
        self.bridge.decision_done.connect(self._on_decision_done)

        # The Ollama panel's checks of the server, paced by _pace_polls
        self.ollama_timer = QTimer(self)
        self.ollama_timer.setInterval(SLOW_POLL_MS)
        self.ollama_timer.timeout.connect(self._poll_ollama)
        self.ollama_timer.start()

        # The open project's tools config, edited by hand: the feed offers to restart the tools
        self._config_notified: str | None = None
        self.config_watcher = QFileSystemWatcher(self)
        self.config_watcher.fileChanged.connect(self._tools_config_touched)
        self.config_watcher.directoryChanged.connect(self._tools_config_touched)

        self._update_status()
        self._config_error = config_error  # Shown in the feed of the first project
        self.prompt.setFocus()

    # --- Running things in the session's loop ---

    def _later(self, fn) -> None:
        """
        Calls fn in the window's thread.
        """
        self.bridge.invoke.emit(fn)

    def _run(self, coro, done=None):
        """
        Runs a coroutine in the session's loop and calls done with its result in the window's thread. Returns the
        concurrent future of the coroutine.
        """
        future = self.runner.submit(coro)

        def finished(f):
            try:
                result = f.result()
            except BaseException as error:  # Shown, not raised in the loop's thread
                self._later(lambda: self._local_error(f"{type(error).__name__}: {error}"))
                return
            if done:
                self._later(lambda: done(result))
        future.add_done_callback(finished)
        return future

    # --- Project and session ---

    def open_project(self, project: Path, report: str = "") -> None:
        """
        Opens a project chosen in the project dialogs: starts its servers and a session. Only a set-up project is
        opened, so the model is never prompted outside one. The report of a setup is shown when the session has started.
        """
        project = Path(project).resolve()
        if not tools_setup.is_set_up(project):
            self._local_error(f"{project.as_posix()} is not set up as a project, so it was not opened.")
            return
        old = self.session
        if old:
            self.runner.submit(old.close())
            self.session = None
        self.session = Session(self.settings, project, QtFrontend(self.bridge))
        self.session.auto = self.auto_check.isChecked()
        self.project_label.setText(project.name)
        self.project_label.setToolTip(str(project))
        self.setWindowTitle(f"{APP_NAME} — {project.name}")
        self._hide_notice()
        recent_projects.add(project)
        self._config_notified = None
        self._watch_tools_config()
        session = self.session
        self.filemap.set_project(project, Rules(project))
        self.feed.set_entries([c.Entry(c.NOTICE, text=f"Starting the MCP servers of {project.as_posix()}…")])

        def started(_):
            self._update_status()
            self._poll_ollama()  # Not after the slow interval: the panel would say "Checking…" until then
            if self._config_error:
                self._local_error(f"config.json: {self._config_error}\nThe defaults are used until it is fixed.")
                self._config_error = None
            if report:
                self._local_notice(report)
            else:
                self._offer_reopen(session)
        self._run(session.start(), started)

    def _open_project(self) -> None:
        """
        Opens another project with the project dialogs. Closing them keeps the open project.
        """
        chosen = choose_project(self, startup=False)
        if chosen:
            self.open_project(*chosen)

    def _set_up(self) -> None:
        """
        Creates the open project's missing files (e.g. a deleted instructions.md), after the user confirmed them, and
        opens the project again so that its servers start with them.
        """
        project = self._project()
        if not project:
            return
        if self._busy():
            self._local_error("Stop the model before setting up the project.")
            return
        try:
            changes = tools_setup.planned(project)
        except tools_setup.SetupRefused as error:
            self._local_error(f"Not set up: {error}")
            return
        if not changes:
            self._local_notice(f"{project.as_posix()} is set up: nothing is missing.")
            return
        listed = "\n".join(f"  {change}" for change in changes)
        answer = QMessageBox.question(
            self, "Create the missing files?",
            f"The project {project.as_posix()} is missing some of its files. This creates or changes:\n{listed}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        try:
            report = tools_setup.set_up(project)
        except Exception as error:
            self._local_error(f"Not set up: {error}")
            return
        self.open_project(project, report=report)

    def _project(self) -> Path | None:
        """
        Returns the open project folder.
        """
        return self.session.project if self.session else None

    # --- Signals of the session ---

    def _on_reset(self) -> None:
        if self.session:
            self.feed.set_entries(self.session.entries)
            self.filemap.set_rules(self.session.rules) if self.session.rules else None

    def _on_entry(self, entry_id: int, new: bool) -> None:
        if not self.session:
            return
        if self.feed.entries is not self.session.entries:
            self.feed.set_entries(self.session.entries)
        if new:
            entry = next((e for e in reversed(self.session.entries) if e.id == entry_id), None)
            if entry and entry.kind == c.NOTICE and entry.actions:
                self._show_notice(entry.text, entry.actions + [DISMISS], self._notice_action)
        self.feed.schedule()

    def _update_status(self) -> None:
        s = self.session
        model = s.model if s else self.settings["model"]
        num_ctx = s.num_ctx if s else self.settings["num_ctx"]
        self.model_button.setText(f"{model} ▾")
        self.context_button.setText(f"ctx {format_number(num_ctx)}")
        if s:
            self.usage_label.setText(f"{format_number(s.used_tokens)} / {format_number(num_ctx)} tokens")
            self.filemap.set_changed(s.changed_files)
            running = s.running
        else:
            self.usage_label.setText("")
            running = False
        self.send_button.setText("Stop" if running else "Send")
        self.send_button.setToolTip("Stop the model (Esc)" if running else "Send (Enter)")
        self._show_ollama()

    # --- The Ollama panel ---

    def _poll_ollama(self, then=None) -> None:
        """
        Asks the Ollama server what it has loaded, in the session's loop, and shows it in the panel. then, if
        given, is called with the result, also when a poll already on its way gives it.
        """
        if then:
            self._after_poll.append(then)
        if not self.session or self._poll_pending:
            return
        self._poll_pending = True
        asked = self.session.config["ollama_host"]

        def done(state):
            self._poll_pending = False
            if self.session and self.session.config["ollama_host"] != asked:
                # The address changed (/host) while this was on its way: it tells of the old server
                self._poll_ollama()
                return
            before = self._ollama_state
            self._ollama_state = state
            if state.reachable and state.answering and not (before and before.reachable) and self.session:
                self._run(self.session.server_back())  # A notice under the error, if one was shown
            self._check_serve()
            self._show_ollama()
            callbacks, self._after_poll = self._after_poll, []
            for callback in callbacks:
                callback(state)
        future = self.runner.submit(self.session.server_state())
        future.add_done_callback(lambda f: self._later(
            lambda: done(f.result() if not f.exception() else ollama_server.State(False))))

    def _show_ollama(self) -> None:
        """
        Shows the server's address and what it does: Local Fox's request while one runs, otherwise the poll's
        result, with the start button for a server of this computer that does not answer, and the stop button
        for one that does (or that Local Fox started).
        """
        s = self.session
        host = s.config["ollama_host"] if s else self.settings["ollama_host"]
        model = s.model if s else self.settings["model"]
        state = self._ollama_state
        text = ollama_server.activity(state, model, s.phase if s else None)
        down = state is not None and not state.reachable and not (s and s.phase)
        tip = ""
        own = self._serve is not None and self._serve.poll() is None
        if own:
            tip = "Started by Local Fox ('ollama serve'), stopped when Local Fox closes. " + (
                "Its output is in its window." if getattr(self._serve, "visible", False) else
                f"Its output: {config_module.data_dir() / 'logs' / ollama_server.LOG_NAME}")
        if down and self._serve_started:
            text, down = "Starting…", False
        elif down and not ollama_server.is_local(host):
            text = "Not reachable"
        local = ollama_server.is_local(host)
        answers = state is not None and state.reachable
        others = ollama_server.others_loaded(state, model)
        model_tip = "Also loaded: " + ", ".join(others) if others else ""
        self.ollama_panel.show_state(host, text, tip=tip,
                                     can_start=down and not own and local, can_stop=own or (answers and local),
                                     model=model, placement=ollama_server.placement(state, model), model_tip=model_tip)
        self._pace_polls()

    def _pace_polls(self) -> None:
        """
        Checks the server every FAST_POLL_MS while a server Local Fox started has not answered yet or Local Fox's
        request waits for its first reply (a model loading shows only in 'ollama ps'), and every SLOW_POLL_MS
        otherwise. A change of pace checks at once: the request that ends has just loaded its model.
        """
        s = self.session
        fast = bool(self._serve_started) or bool(s and s.phase == ollama_server.WAITING)
        interval = FAST_POLL_MS if fast else SLOW_POLL_MS
        if interval != self.ollama_timer.interval():
            self.ollama_timer.setInterval(interval)  # Starts the interval again
            self._poll_ollama()

    def changeEvent(self, event) -> None:
        """
        Checks the Ollama server when the window becomes active again: between the seldom checks, a server may
        have been started or stopped elsewhere while the user was away.
        """
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._poll_ollama()

    def _stop_ollama(self) -> None:
        """
        Stops the Ollama server at the address, on the user's click on "Stop Ollama server", e.g. to run it in a
        console of their own: the one Local Fox started at once, any other (the Ollama app's, a console's, or one
        running hidden) after the user confirmed it. A request in progress then fails, and the feed says so.
        """
        serve, self._serve, self._serve_started = self._serve, None, 0.0
        log = self.session.log if self.session else None
        if serve and serve.poll() is None:
            ollama_server.stop(serve)
            if log:
                self.runner.call(lambda: log.write("ollama_serve_stopped", pid=serve.pid))
        else:
            host = self.session.config["ollama_host"] if self.session else self.settings["ollama_host"]
            other = ollama_server.other_server(host)
            if not other:
                # Most often it was stopped elsewhere since the last check: the panel is checked again and then
                # offers to start one. Only a server that still answers is one that could not be found.
                def recheck(state):
                    if state.reachable:
                        self._local_error(f"No Ollama server was found listening at {host} on this computer, so "
                                          f"nothing was stopped. {ollama_server.not_found_hint()}")
                self._ollama_state = None  # "Checking…", without buttons, until the check tells
                self._show_ollama()
                self._poll_ollama(recheck)
                return
            answer = QMessageBox.question(
                self, "Stop the Ollama server?",
                f"Stop the Ollama server at {host} ({other.name}, process {other.pid})?\n\nLocal Fox did not start it, "
                "so other programs that use it lose it too. If the Ollama app started it, the app may start it again: "
                "quit the app from its icon instead.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
            error = ollama_server.stop_other(other)
            if log:
                self.runner.call(lambda: log.write("ollama_server_stopped", pid=other.pid, program=other.name, error=error))
            if error:
                self._local_error(f"The Ollama server (process {other.pid}) was not stopped: {error}\n"
                                  f"{ollama_server.not_found_hint()}")
                return
        self._ollama_state = None  # "Checking…" until the next poll tells
        self._show_ollama()
        self._poll_ollama()

    def _start_ollama(self) -> None:
        """
        Starts 'ollama serve' for the address of the settings, on the user's click on "Start Ollama server".
        """
        host = self.session.config["ollama_host"] if self.session else self.settings["ollama_host"]
        program = ollama_server.find_ollama()
        if not program:
            self._local_error("Ollama was not found on this computer. Install it from ollama.com, then choose "
                              "\"Start Ollama server\" again.")
            return
        logs = config_module.data_dir() / "logs"
        try:
            self._serve = ollama_server.start(program, host, logs)
        except OSError as error:
            self._local_error(f"'ollama serve' did not start: {error}")
            return
        self._serve_started = time.monotonic()
        if self.session:
            log, pid = self.session.log, self._serve.pid
            self.runner.call(lambda: log.write("ollama_serve", program=program, host=host, pid=pid))
        self._show_ollama()
        self._poll_ollama()

    def _check_serve(self) -> None:
        """
        Follows a server Local Fox started until it answers, and tells if it stopped or did not answer in time.
        """
        if not self._serve_started:
            return
        state = self._ollama_state
        if state and state.reachable:
            self._serve_started = 0.0
            return
        serve = self._serve
        terminal = isinstance(serve, ollama_server.TerminalServer)
        if serve.poll() is not None:
            if terminal and serve.pid is None:
                self._serve_started = 0.0
                self._local_error("The terminal window for 'ollama serve' could not be opened" + (
                    ". Allow Local Fox to control Terminal in System Settings, Privacy & Security, Automation, and "
                    "try again, or run 'ollama serve' in a terminal yourself." if sys.platform == "darwin" else
                    ": run 'ollama serve' in a terminal yourself."))
                return
            reason = f"it stopped with exit code {serve.returncode}"
        elif time.monotonic() - self._serve_started > 30:
            reason = "it did not answer within 30 seconds"
        else:
            return
        self._serve_started = 0.0
        if terminal:
            # The terminal window stays open with what the server said
            self._local_error(f"'ollama serve' did not start: {reason}. Its terminal window tells why.")
            return
        if getattr(serve, "visible", False):
            # Its console window closed with it, so what it said is gone; a terminal of the user's own keeps it
            self._local_error(f"'ollama serve' did not start: {reason}. To see why, run 'ollama serve' in a terminal.")
            return
        logs = config_module.data_dir() / "logs"
        tail = ollama_server.log_tail(logs)
        self._local_error(f"'ollama serve' did not start: {reason}. Its output is in {logs / ollama_server.LOG_NAME}"
                          + (f":\n{tail}" if tail else "."))

    # --- The notice bar ---

    def _show_notice(self, text: str, options: list[str], on_choice) -> None:
        self._clear_notice_buttons()
        self.notice_label.setText(text)
        for option in options:
            button = _button(option, lambda checked=False, o=option: on_choice(o), "primary" if option in ("Allow", "Continue") else "")
            self.notice_buttons.addWidget(button)
        self.notice_bar.show()

    def _clear_notice_buttons(self) -> None:
        while self.notice_buttons.count():
            widget = self.notice_buttons.takeAt(0).widget()
            if widget:
                widget.deleteLater()

    def _hide_notice(self) -> None:
        self._clear_notice_buttons()
        self.notice_bar.hide()
        self._decision = None
        self._choose = None

    def _on_decision(self, question: str, options: list, entry_id: int, future) -> None:
        self._decision = future
        # The call asked about is open in the feed as the latest one, so the user sees its arguments

        def choose(option: str):
            if not future.done():
                self.runner.call(lambda: future.done() or future.set_result(option))
            self._hide_notice()
        self._show_notice(question, options, choose)
        self._decision_options, self._choose = list(options), choose
        self.feed.scroll_to_bottom()

    def _set_auto(self, on: bool) -> None:
        """
        Turns auto mode on or off for the session. Turned on while a question it would answer waits, it answers it.
        """
        if self.session:
            self.session.auto = on
        if on and self._decision is not None and self._choose:
            choice = auto_choice(self._decision_options)
            if choice:
                self._choose(choice)

    def _on_decision_done(self, future) -> None:
        if self._decision is future:
            self._hide_notice()

    def _notice_action(self, action: str) -> None:
        self._hide_notice()
        if action == DISMISS:
            self._reopen_offer = None  # The conversations stay in the ≡ menu
        elif action == "Clear":
            self._clear()
        elif action == "Export":
            self._export()
        elif action == "Larger context" and self.session:
            session = self.session

            def larger():
                error = session.larger_context()
                session.notice(error or f"The context size is {format_number(session.num_ctx)} tokens for this session. "
                                        "Ollama reloads the model with it on the next request. Ask again.")
            self.runner.call(larger)
        elif action == "Open project":
            self._open_project()
        elif action == REOPEN and self._reopen_offer:
            self._reopen(self._reopen_offer)
        elif action == RESTART_TOOLS:
            self._clear(restart_tools=True)

    # --- The prompt and the commands ---

    def _busy(self) -> bool:
        """
        Checks if a turn runs, or was sent and has not started yet in the session's loop.
        """
        return bool(self.session and (self.session.running or (self._turn and not self._turn.done())))

    def _send_clicked(self) -> None:
        if self._busy():
            self._stop()
        else:
            text = self.prompt.toPlainText().strip()
            if text:
                self._on_prompt(text)

    def _on_prompt(self, text: str) -> None:
        """
        Sends the prompt's text, or runs it as a command. A message that is not taken (no project is set up, or the
        model runs: there is no queue) stays in the input, and a tooltip says why.
        """
        refused = self._submit(text)
        if refused is None:
            self.prompt.sent(text)
        else:
            QToolTip.showText(self.prompt.mapToGlobal(self.prompt.rect().topLeft()), refused, self.prompt)

    def _submit(self, text: str) -> str | None:
        """
        Runs a command or sends a message. Returns None if it was taken, or why the message was not sent.
        """
        if text.startswith("/"):
            command, _, argument = text.partition(" ")
            handler = {
                "/clear": lambda a: self._clear(), "/export": lambda a: self._export(),
                "/instructions": lambda a: self._reload_instructions(), "/model": self._model_command,
                "/context": self._context_command, "/host": self._host_command,
                "/config": lambda a: self._open_config(), "/log": lambda a: self._open_log(),
                "/settings": lambda a: self._open_settings(), "/setup": lambda a: self._set_up(),
                "/reopen": lambda a: self._reopen_latest(),
                "/project": lambda a: self._open_project(), "/help": lambda a: self._local_notice(HELP),
            }.get(command.lower())
            if handler:
                handler(argument.strip())
                return None
        if not self.session:
            # No session exists outside a set-up project, so nothing can reach the model
            if self._project():
                return "This folder is not set up as a project, so the model is not prompted here. Set it up with /setup."
            return 'Open a project first: "Open project" in the ≡ menu, or /project.'
        if self._busy():
            return 'The model is running. Stop it with Esc or the "Stop" button to send this, or wait until it has finished.'
        if self.notice_bar.isVisible() and self._decision is None:
            self._hide_notice()
        self._turn = self._run(self.session.send(text))
        self.feed.scroll_to_bottom()
        return None

    def _local_notice(self, text: str) -> None:
        if self.session:
            self.runner.call(self.session.notice, text)
        else:
            self.feed.set_entries(self.feed.entries + [c.Entry(c.NOTICE, text=text)])

    def _local_error(self, text: str) -> None:
        if self.session:
            self.runner.call(self.session.error, text)
        else:
            self.feed.set_entries(self.feed.entries + [c.Entry(c.ERROR, text=text)])

    def _stop(self) -> None:
        if self.session and self.session.running:
            self.runner.call(self.session.stop)

    def _clear(self, settings: dict | None = None, restart_tools: bool = False) -> None:
        """
        Starts a new conversation with the settings read again from config.json, keeping the session's model and
        context size, or with the given settings as they are (after the settings view saved them). With
        restart_tools, the MCP servers are restarted first, so that they read a changed tools config.
        """
        if not self.session:
            return
        session = self.session
        if settings is None:
            try:
                settings = config_module.load()
            except config_module.ConfigError as error:
                self._local_error(str(error))
                settings = self.settings
            settings = {**settings, "model": session.model, "num_ctx": session.num_ctx}
        self.settings = dict(settings)
        self.filemap.full_paths = self.settings.get("prompt_full_paths", False)

        async def clear():
            await session.stop_and_wait()
            await session.apply_settings(self.settings)
            if restart_tools:
                await session.restart_servers()
            else:
                await session.new_conversation()
        self._hide_notice()
        self._run(clear(), lambda _: self._poll_ollama())  # The settings may have another Ollama address

    def _reload_instructions(self) -> None:
        if self.session:
            self.runner.call(self.session.reload_instructions)

    def _export(self) -> None:
        if not self.session:
            return
        default = self.session.project / f"conversation-{datetime.now().strftime('%Y%m%d-%H%M')}.md"
        path, _ = QFileDialog.getSaveFileName(self, "Export the conversation", str(default), "Markdown (*.md)")
        if not path:
            return
        text = to_markdown(list(self.session.entries), self.session.model, str(self.session.project))
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as error:
            self._local_error(f"The conversation was not saved: {error}")
            return
        self._local_notice(f"The conversation was saved to {Path(path).as_posix()}.")

    def _open_config(self) -> None:
        folder = config_module.config_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        self._local_notice(f"config.json is in {folder.as_posix()}. A clear (/clear) reads it again; /settings shows it as a form.")

    def _open_settings(self) -> None:
        """
        Opens the settings view, with the installed models in the model field when Ollama answers in time.
        """
        session = self.session
        models = []
        if session:
            try:
                models = self.runner.submit(session.list_models()).result(timeout=2)
            except Exception:
                pass  # The model is typed instead
        tools = [t.exposed for t in session.servers.tools] if session and session.servers else []
        # The session's model's largest context, for the context sizes offered if config.json names the same model
        known = {session.model: session.max_context} if session and session.max_context else {}
        dialog = SettingsDialog(self, models, tools, self._project(), known)
        dialog.open_folder.connect(self._open_config)
        dialog.saved.connect(self._settings_saved)
        dialog.exec()

    def _settings_saved(self, settings: dict, clear: bool, tools_changed: bool) -> None:
        self.filemap.full_paths = settings.get("prompt_full_paths", False)
        # The tools take the API key when they start, as they read their config
        key_changed = settings.get("ollama_api_key", "") != self.settings.get("ollama_api_key", "")
        if tools_changed:
            self._wrote_tools_config()
        if clear and self.session:
            self._clear(settings, restart_tools=tools_changed or key_changed)
            return
        if tools_changed:
            self._tools_config_changed("The tools config was saved. The tools apply it when they restart.")
        elif key_changed and self.session:
            session = self.session
            self.runner.call(lambda: session.notice("The Ollama API key was saved. The tools take it when they restart, "
                                                    "which starts a new conversation.", [RESTART_TOOLS]))
        previous, self.settings = self.settings, dict(settings)
        if self.session and settings["model"] != self.session.model:
            self._switch_model(settings["model"])  # As the model menu does, so that a clear keeps it
        if self.session and settings["num_ctx"] != previous.get("num_ctx"):
            self._set_context(settings["num_ctx"])
        if self.session and settings["ollama_host"] != self.session.config["ollama_host"]:
            self._set_host(settings["ollama_host"])  # At once, as the conversation does not depend on the server
        self._local_notice("The settings were saved to config.json. The model and context size apply now, the others "
                           "from the next clear (/clear), and the font size from the next start.")

    def _change_rule(self, key: str, path: str, add: bool) -> None:
        """
        Adds a path of the filemap to a path list of the tools config, or removes it, as the user chose in its menu.
        """
        project = self._project()
        if not project:
            return
        try:
            text = tools_config.change_path(project, key, Path(path), add)
        except tools_config.ToolsConfigError as error:
            self._local_error(str(error))
            return
        self._tools_config_changed(text)

    def _tools_config_changed(self, text: str) -> None:
        """
        Tells in the feed what changed in the tools config, with the action that restarts the tools to apply it.
        """
        session = self.session
        if session:
            self._wrote_tools_config()
            self.runner.call(lambda: session.notice(f"{text} Restarting them starts a new conversation.", [RESTART_TOOLS]))
        else:
            self._local_notice(text)

    def _wrote_tools_config(self) -> None:
        """
        Local Fox itself just wrote the tools config (the settings view, the filemap's menu): the watcher must not
        report the change as made outside Local Fox. Called right after the write, before the watcher's signal.
        """
        if self.session:
            self._config_notified = self.session.read_tools_config()

    def _watch_tools_config(self) -> None:
        """
        Watches the open project's tools config, and its folder, as editors often save by replacing the file, which
        ends a watch on the file itself.
        """
        if self.config_watcher.files() or self.config_watcher.directories():
            self.config_watcher.removePaths(self.config_watcher.files() + self.config_watcher.directories())
        if self.session:
            path = tools_config_path(self.session.project)
            self.config_watcher.addPaths([str(p) for p in (path, path.parent) if p.exists()])

    def _tools_config_touched(self, _path: str = "") -> None:
        """
        The tools config or its folder changed, e.g. edited by hand. The tools read it only when they start, so a
        change they have not started with is told in the feed, with the action that restarts them; Local Fox does
        not restart them on its own, as that starts a new conversation.
        """
        session = self.session
        if not session or session.servers is None:
            return  # Not started yet: the servers read the config as it is when they start
        path = tools_config_path(session.project)
        if path.exists() and str(path) not in self.config_watcher.files():
            self.config_watcher.addPath(str(path))  # Watched again after an editor replaced it
        text = session.read_tools_config()
        if text is None or text == session.tools_config_text or text == self._config_notified:
            return
        self._config_notified = text
        self._tools_config_changed(f"The tools config ({session.rules.relative(path) if session.rules else path.name}) was "
                                   "changed outside Local Fox. The tools apply it when they restart.")
        self._config_notified = text

    # --- Saved conversations ---

    def _offer_reopen(self, session: Session) -> None:
        """
        Offers to reopen the project's last saved conversation, when a project has just opened.
        """
        latest = saved_conversations.list_saved(session.project)
        if not latest:
            return
        last = latest[0]
        self._reopen_offer = last.path
        when = last.updated.replace("T", " ")[:16]
        self.runner.call(lambda: session.notice(f"The last conversation in this project: “{last.title}” ({when}, "
                                                f"{last.messages} messages). Older ones are in the ≡ menu under \"Conversations\".", [REOPEN]))

    def _fill_conversations_menu(self) -> None:
        self.conversations_menu.clear()
        project = self._project()
        conversations = saved_conversations.list_saved(project) if project else []
        if not conversations:
            self.conversations_menu.addAction("No saved conversations").setEnabled(False)
        current = self.session.saved_path if self.session else None
        for item in conversations[:30]:
            label = f"{item.updated.replace('T', ' ')[:16]}   {item.title}   ({item.messages} messages)"
            action = self.conversations_menu.addAction(label, lambda p=item.path: self._reopen(p))
            action.setCheckable(True)
            action.setChecked(item.path == current)
        if project:
            self.conversations_menu.addSeparator()
            self.conversations_menu.addAction("Open folder", lambda: self._open_folder(saved_conversations.folder(project)))

    def _reopen(self, path: Path) -> None:
        if not self.session:
            return
        self._hide_notice()
        self._reopen_offer = None
        self._run(self.session.reopen(path))

    def _reopen_latest(self) -> None:
        if not self.session:
            return
        current = self.session.saved_path
        others = [s for s in saved_conversations.list_saved(self.session.project) if s.path != current]
        if others:
            self._reopen(others[0].path)
        else:
            self._local_notice("This project has no other saved conversation.")

    def _open_folder(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _open_log(self) -> None:
        """
        Opens the logs folder: the log of this run, and the servers' output.
        """
        folder = self.session.log_dir if self.session else config_module.data_dir() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        self._local_notice(f"The log of this run is {(folder / LOG_NAME).as_posix()}, and the output of the MCP servers is in "
                           "server-<key>.log next to it. The next start of Local Fox empties them.")

    # --- Model and context ---

    def _fill_model_menu(self) -> None:
        self.model_menu.clear()
        if not self.session:
            self.model_menu.addAction("Open a project first").setEnabled(False)
            return
        loading = self.model_menu.addAction("Loading the models…")
        loading.setEnabled(False)

        def fill(models):
            self.model_menu.clear()
            for name in models:
                action = self.model_menu.addAction(name, lambda n=name: self._switch_model(n))
                action.setCheckable(True)
                action.setChecked(name == self.session.model)
        self._run(self.session.list_models(), fill)

    def _switch_model(self, name: str) -> None:
        if not self.session:
            return
        if self.session.running:
            self._local_error("Stop the model before switching to another.")
            return
        session = self.session

        def switched(error):
            if error:
                self._local_error(error)
            else:
                config_module.save_setting("model", name)
                self.settings["model"] = name
                self._local_notice(f"The model is now {name}" + (f", which takes at most {format_number(session.max_context)} tokens of context." if session.max_context else "."))
        self._run(session.set_model(name), switched)

    def _model_command(self, argument: str) -> None:
        if not self.session:
            return
        if argument:
            self._switch_model(argument)
        else:
            self._run(self.session.list_models(),
                      lambda models: self._local_notice(f"The model is {self.session.model}. Installed: {', '.join(models)}. Switch with /model <name>."))

    def _host_command(self, argument: str) -> None:
        """
        /host shows the Ollama address; /host <address> sets it, e.g. to a server on another computer.
        """
        if not self.session:
            return
        if argument:
            self._set_host(argument)
        else:
            self._local_notice(f"The Ollama address is {self.session.config['ollama_host']}. Change it with /host "
                               "<address>, e.g. /host 192.168.1.20 for Ollama on another computer.")

    def _set_host(self, text: str) -> None:
        """
        Talks to Ollama at another address from now on and saves it to config.json, keeping the conversation:
        from /host, and from the settings view's "Save". The panel shows the new server, and the model is checked
        there again.
        """
        if not self.session:
            return
        host = ollama_server.normalize_host(text)
        if not host:
            self._local_error(f"{text} is not an address. Give it as e.g. 192.168.1.20, 192.168.1.20:11434 or "
                              "http://192.168.1.20:11434.")
            return
        if self.session.running:
            self._local_error("Stop the model before changing the Ollama address.")
            return
        session = self.session

        def changed(error):
            config_module.save_setting("ollama_host", host)
            self.settings["ollama_host"] = host
            self._local_notice(f"The Ollama address is now {host}.")
            if error:
                self._local_error(error)
            self._show_ollama()
            self._poll_ollama()
        self._ollama_state = None  # "Checking…" for the new address, not the old server's state
        self._run(session.set_host(host), changed)

    def _context_command(self, argument: str) -> None:
        if not self.session:
            return
        if argument:
            try:
                size = int(argument.replace(" ", "").replace("_", ""))
            except ValueError:
                self._local_error("Give the context size as a number of tokens, e.g. /context 65536.")
                return
            self._set_context(size)
        else:
            s = self.session
            largest = f" {s.model} takes at most {format_number(s.max_context)}." if s.max_context else ""
            self._local_notice(f"The context size is {format_number(s.num_ctx)} tokens, and the conversation uses "
                               f"{format_number(s.used_tokens)}.{largest}")

    def _set_context(self, size: int) -> None:
        session = self.session

        def set_size():
            error = session.set_context(size)
            if error:
                session.error(error)
            else:
                session.notice(f"The context size is {format_number(size)} tokens for this session. Ollama reloads the model with it "
                               "on the next request, which can take a while; a larger size needs more memory and can move more of the model to the CPU.")
        self.runner.call(set_size)

    def _ask_context(self) -> None:
        if not self.session:
            return
        sizes = context_sizes(self.session.num_ctx, self.session.max_context)
        labels = [f"{format_number(size)} tokens" for size in sizes]
        label, ok = QInputDialog.getItem(self, "Context size", "Context size for this session (/context sets any other size):",
                                         labels, sizes.index(self.session.num_ctx), False)
        if ok and sizes[labels.index(label)] != self.session.num_ctx:
            self._set_context(sizes[labels.index(label)])

    # --- Closing ---

    def closeEvent(self, event) -> None:
        self.ollama_timer.stop()
        ollama_server.stop(self._serve)
        if self.session:
            future = self.runner.submit(self.session.close())
            try:
                future.result(timeout=15)
            except Exception:
                pass
        self.runner.stop()
        super().closeEvent(event)
