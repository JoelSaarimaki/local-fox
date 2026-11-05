"""
Tests of the Ollama panel: what it says the server does, when it offers "Start Ollama server", and that the
button starts 'ollama serve' for the address of the settings (with the start itself faked, so no server runs).
"""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from local_fox import config, ollama_server  # noqa: E402
from local_fox.ollama_server import THINKING, WAITING, WRITING, Loaded, State, activity  # noqa: E402

GIB = 1024 ** 3

def test_activity_while_local_fox_waits_for_the_model():
    # A model that is still loading is not in 'ollama ps', so a waiting request whose model is missing loads it
    assert activity(State(True), "flint:40b", WAITING) == "Loading flint:40b…"
    assert activity(None, "flint:40b", WAITING) == "Loading flint:40b…"
    loaded = State(True, [Loaded("flint:40b", 22 * GIB, 22 * GIB)])
    assert activity(loaded, "flint:40b", WAITING) == "Reading the prompt for flint:40b…"
    assert activity(loaded, "flint:40b", THINKING) == "flint:40b is thinking…"
    assert activity(loaded, "flint:40b", WRITING) == "flint:40b is writing…"

def test_activity_between_requests():
    assert activity(None, "m", None) == "Checking…"
    assert activity(State(False), "m", None) == "Not running"
    assert activity(State(True), "m", None) == "Running, no model loaded"
    split = State(True, [Loaded("llama3.1:latest", 100, 23), Loaded("other:1b", 10, 10)])
    assert activity(split, "llama3.1", None) == "llama3.1 loaded, 77 % CPU / 23 % GPU, and 1 more"
    assert activity(split, "flint:40b", None) == "Loaded: llama3.1:latest, other:1b"

def test_poll_reads_ps_and_a_failure_is_not_reachable():
    class Client:
        async def ps(self):
            return SimpleNamespace(models=[SimpleNamespace(model="gemma4:26b", name="gemma4:26b", size=10, size_vram=10)])

    class Down:
        async def ps(self):
            raise ConnectionError("Failed to connect to Ollama")
    class Hanging:
        async def ps(self):
            await asyncio.sleep(30)
    state = asyncio.run(ollama_server.poll(Client()))
    assert state.reachable and state.find("gemma4:26b").placement() == "100 % GPU"
    assert asyncio.run(ollama_server.poll(Down())) == State(False)
    # A poll that never returned left the panel's text as it was for good
    hanging = asyncio.run(ollama_server.poll(Hanging(), timeout=0.05))
    assert hanging.reachable and not hanging.answering and activity(hanging, "m", None) == "Not answering"

def test_only_this_computer_can_be_started():
    for host in ("http://127.0.0.1:11434", "http://localhost:11500", "127.0.0.1", "http://0.0.0.0:11434"):
        assert ollama_server.is_local(host), host
    for host in ("http://192.168.1.20:11434", "https://ollama.com"):
        assert not ollama_server.is_local(host), host

def test_the_server_gets_the_address_of_the_settings():
    assert ollama_server.serve_env("http://127.0.0.1:11500")["OLLAMA_HOST"] == "127.0.0.1:11500"
    assert ollama_server.serve_env("http://localhost")["OLLAMA_HOST"] == "localhost:11434"

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_FOX_CONFIG", str(tmp_path / "config.json"))
    return QApplication.instance() or QApplication([])

def fake_session(host="http://127.0.0.1:11434", phase=None):
    return SimpleNamespace(config={"ollama_host": host}, model="flint:40b", phase=phase, running=False,
                           used_tokens=0, num_ctx=65536, changed_files=set(),
                           log=SimpleNamespace(write=lambda *a, **k: None))

def test_the_panel_offers_to_start_a_local_server_that_does_not_answer(app, monkeypatch):
    from local_fox.ui.main_window import MainWindow
    window = MainWindow(dict(config.DEFAULTS))
    started = []

    class Process:
        pid, returncode = 1234, None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 1

        def wait(self, timeout=None):
            return self.returncode
    monkeypatch.setattr(ollama_server, "find_ollama", lambda: "ollama")
    monkeypatch.setattr(ollama_server, "start", lambda program, host, logs: started.append((program, host)) or Process())
    monkeypatch.setattr(window, "_poll_ollama", lambda: None)
    panel = window.ollama_panel
    try:
        window.session = fake_session()
        window._ollama_state = State(False)
        window._show_ollama()
        assert panel.status_label.text() == "Not running" and not panel.start_button.isHidden()
        assert panel.stop_button.isHidden()
        panel.start_button.click()
        assert started == [("ollama", "http://127.0.0.1:11434")]
        assert panel.status_label.text() == "Starting…" and panel.start_button.isHidden()
        # It answers: the poll clears the start, and the panel shows the server's state
        window._ollama_state = State(True)
        window._check_serve()
        window._show_ollama()
        assert panel.status_label.text() == "Running, no model loaded" and window._serve_started == 0
        assert "stopped when Local Fox closes" in panel.status_label.toolTip()
        # Its own server can be stopped, e.g. to run Ollama in a console instead
        process = window._serve
        assert not panel.stop_button.isHidden()
        panel.stop_button.click()
        assert process.returncode == 1 and window._serve is None
        assert panel.stop_button.isHidden() and panel.status_label.text() == "Checking…"
        window._ollama_state = State(False)
        window._show_ollama()
        assert not panel.start_button.isHidden()
    finally:
        window._serve = None
        window.session = None
        window.close()

def test_the_server_is_checked_often_only_while_its_answer_is_about_to_change(app, monkeypatch):
    from local_fox.ui.main_window import FAST_POLL_MS, SLOW_POLL_MS, MainWindow
    window = MainWindow(dict(config.DEFAULTS))
    polls = []
    monkeypatch.setattr(window, "_poll_ollama", lambda: polls.append(window.ollama_timer.interval()))
    try:
        window.session = fake_session()
        window._ollama_state = State(True)
        window._show_ollama()
        assert window.ollama_timer.interval() == SLOW_POLL_MS and polls == []  # Idle: seldom, each poll a console line
        window.session.phase = WAITING  # A request waits: is its model loading?
        window._show_ollama()
        assert window.ollama_timer.interval() == FAST_POLL_MS and polls == [FAST_POLL_MS]
        window.session.phase = WRITING
        window._show_ollama()
        assert window.ollama_timer.interval() == SLOW_POLL_MS and polls == [FAST_POLL_MS, SLOW_POLL_MS]
        window.session.phase = None
        window._serve_started = 1.0  # A server Local Fox started, until it answers
        window._show_ollama()
        assert window.ollama_timer.interval() == FAST_POLL_MS
    finally:
        window._serve_started = 0.0
        window.session = None
        window.close()

def test_no_start_button_for_another_computer_or_while_a_request_runs(app, monkeypatch):
    from local_fox.ui.main_window import MainWindow
    window = MainWindow(dict(config.DEFAULTS))
    monkeypatch.setattr(window, "_poll_ollama", lambda: None)
    panel = window.ollama_panel
    try:
        # Another computer's server can be neither started nor stopped from here
        window.session = fake_session("http://192.168.1.20:11434")
        window._ollama_state = State(True)
        window._show_ollama()
        assert panel.stop_button.isHidden() and panel.start_button.isHidden()
        window.session = fake_session("http://192.168.1.20:11434")
        window._ollama_state = State(False)
        window._show_ollama()
        assert panel.status_label.text() == "Not reachable" and panel.start_button.isHidden()
        # A poll can time out while the server is busy with Local Fox's own request
        window.session = fake_session(phase=WAITING)
        window._show_ollama()
        assert panel.status_label.text() == "Loading flint:40b…" and panel.start_button.isHidden()
    finally:
        window.session = None
        window.close()

def test_a_server_that_stops_at_once_is_reported_with_its_output(app, tmp_path, monkeypatch):
    from local_fox.ui.main_window import MainWindow
    window = MainWindow(dict(config.DEFAULTS))
    errors = []
    monkeypatch.setattr(window, "_local_error", errors.append)
    logs = Path(os.environ["LOCAL_FOX_CONFIG"]).parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / ollama_server.LOG_NAME).write_text("Error: listen tcp 127.0.0.1:11434: bind: address already in use\n",
                                               encoding="utf-8")
    try:
        # Hidden, as on Linux without a display: its log tells why
        window._serve = SimpleNamespace(poll=lambda: 1, returncode=1, visible=False)
        window._serve_started = 1.0
        window._ollama_state = State(False)
        window._check_serve()
        assert len(errors) == 1 and "exit code 1" in errors[0] and "address already in use" in errors[0]
        assert window._serve_started == 0
        # In a console window of its own, as on Windows: the window closed with it, so a terminal tells why
        window._serve = SimpleNamespace(poll=lambda: 1, returncode=1, visible=True)
        window._serve_started = 1.0
        window._check_serve()
        assert "exit code 1" in errors[1] and "run 'ollama serve' in a terminal" in errors[1]
        # In a terminal (macOS, Linux) that could not be opened: the server never answered
        launcher = SimpleNamespace(poll=lambda: 1, returncode=1)
        window._serve = ollama_server.TerminalServer(54321, launcher)
        monkeypatch.setattr(ollama_server, "listening_pid", lambda port: None)
        window._serve_started = 1.0
        window._check_serve()
        assert "terminal window for 'ollama serve' could not be opened" in errors[2]
    finally:
        window._serve = None
        window.close()

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1180
  TCP    127.0.0.1:53347        127.0.0.1:11434        TIME_WAIT       0
  TCP    127.0.0.1:11434        0.0.0.0:0              LISTENING       23720
  TCP    127.0.0.1:11434        127.0.0.1:53350        ESTABLISHED     23720
  TCP    [::]:11500             [::]:0                 ABHOEREN        4242
"""

def test_the_server_is_found_by_the_port_it_listens_on():
    assert ollama_server.listening_pid(11434, NETSTAT) == 23720
    assert ollama_server.listening_pid(11500, NETSTAT) == 4242  # IPv6, and the state in another language
    assert ollama_server.listening_pid(53347, NETSTAT) is None  # Only a client's port there
    assert ollama_server.port_of("http://127.0.0.1:11500") == 11500 and ollama_server.port_of("localhost") == 11434

def test_only_an_ollama_program_is_offered_for_stopping(monkeypatch):
    monkeypatch.setattr(ollama_server, "listening_pid", lambda port: 23720)
    monkeypatch.setattr(ollama_server, "process_name", lambda pid: "ollama.exe")
    assert ollama_server.other_server("http://127.0.0.1:11434") == ollama_server.Process(23720, "ollama.exe")
    monkeypatch.setattr(ollama_server, "process_name", lambda pid: "postgres.exe")
    assert ollama_server.other_server("http://127.0.0.1:11434") is None
    assert ollama_server.other_server("http://192.168.1.20:11434") is None

def test_a_server_local_fox_did_not_start_is_stopped_after_confirming(app, monkeypatch):
    from local_fox.ui import main_window
    window = main_window.MainWindow(dict(config.DEFAULTS))
    stopped, questions, answers = [], [], [main_window.QMessageBox.No, main_window.QMessageBox.Yes]
    monkeypatch.setattr(ollama_server, "other_server", lambda host: ollama_server.Process(23720, "ollama.exe"))
    monkeypatch.setattr(ollama_server, "stop_other", lambda server: stopped.append(server.pid))
    monkeypatch.setattr(main_window.QMessageBox, "question",
                        lambda parent, title, text, *args: questions.append(text) or answers.pop(0))
    monkeypatch.setattr(window, "_poll_ollama", lambda: None)
    panel = window.ollama_panel
    try:
        # A hidden server, e.g. one started earlier from a console that is gone: running, and stoppable
        window.session = fake_session()
        window._ollama_state = State(True)
        window._show_ollama()
        assert not panel.stop_button.isHidden() and panel.start_button.isHidden()
        panel.stop_button.click()  # "No"
        assert stopped == [] and "ollama.exe, process 23720" in questions[0] and "did not start it" in questions[0]
        panel.stop_button.click()  # "Yes"
        assert stopped == [23720] and panel.status_label.text() == "Checking…"
    finally:
        window.session = None
        window.close()
