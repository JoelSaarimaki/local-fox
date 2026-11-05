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
    assert activity(State(True), "flint:40b", WAITING) == "Loading the model…"
    assert activity(None, "flint:40b", WAITING) == "Loading the model…"
    loaded = State(True, [Loaded("flint:40b", 22 * GIB, 22 * GIB)])
    assert activity(loaded, "flint:40b", WAITING) == "Reading the prompt…"
    assert activity(loaded, "flint:40b", THINKING) == "Thinking…"
    assert activity(loaded, "flint:40b", WRITING) == "Writing…"

def test_activity_and_placement_between_requests():
    assert activity(None, "m", None) == "Checking…" and ollama_server.placement(None, "m") == ""
    assert activity(State(False), "m", None) == "Not running" and ollama_server.placement(State(False), "m") == ""
    assert activity(State(True), "m", None) == "Idle" and ollama_server.placement(State(True), "m") == "not loaded"
    split = State(True, [Loaded("llama3.1:latest", 100, 23), Loaded("other:1b", 10, 10)])
    assert activity(split, "llama3.1", None) == "Idle"
    assert ollama_server.placement(split, "llama3.1") == "77 % CPU / 23 % GPU"
    assert ollama_server.others_loaded(split, "llama3.1") == ["other:1b"]
    assert ollama_server.placement(split, "flint:40b") == "not loaded"
    assert ollama_server.others_loaded(split, "flint:40b") == ["llama3.1:latest", "other:1b"]

def test_the_panel_keeps_its_height_and_cuts_a_long_model_name(app):
    from local_fox.ui.ollama_panel import OllamaPanel
    panel = OllamaPanel()
    panel.resize(240, panel.sizeHint().height())
    panel.show()
    app.processEvents()
    panel.show_state("http://127.0.0.1:11434", "Idle", can_start=False, model="m")
    app.processEvents()
    narrowest = panel.minimumSizeHint().width()
    heights = []
    long_name = "hf.co/someone/a-very-long-model-repository-name-GGUF:Q4_K_M"
    for can_start, can_stop, text in ((True, False, "Not running"), (False, True, "Idle"), (False, False, "Not reachable")):
        panel.show_state("http://127.0.0.1:11434", text, can_start=can_start, can_stop=can_stop,
                         model=long_name, placement="77 % CPU / 23 % GPU")
        app.processEvents()
        heights.append(panel.sizeHint().height())
        # The long name does not widen the panel, which would widen the filemap's column
        assert panel.minimumSizeHint().width() == narrowest and panel.status_label.full_text() == text
    assert len(set(heights)) == 1
    assert panel.model_label.text() != long_name and long_name in panel.model_label.toolTip()
    assert panel.placement_label.text() == "77 % CPU / 23 % GPU"
    panel.close()

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
        assert panel.status_label.full_text() == "Not running" and not panel.start_button.isHidden()
        assert panel.stop_button.isHidden()
        panel.start_button.click()
        assert started == [("ollama", "http://127.0.0.1:11434")]
        assert panel.status_label.full_text() == "Starting…" and panel.start_button.isHidden()
        # It answers: the poll clears the start, and the panel shows the server's state
        window._ollama_state = State(True)
        window._check_serve()
        window._show_ollama()
        assert panel.status_label.full_text() == "Idle" and window._serve_started == 0
        assert "stopped when Local Fox closes" in panel.status_label.toolTip()
        # Its own server can be stopped, e.g. to run Ollama in a console instead
        process = window._serve
        assert not panel.stop_button.isHidden()
        panel.stop_button.click()
        assert process.returncode == 1 and window._serve is None
        assert panel.stop_button.isHidden() and panel.status_label.full_text() == "Checking…"
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
        assert panel.status_label.full_text() == "Not reachable" and panel.start_button.isHidden()
        # A poll can time out while the server is busy with Local Fox's own request
        window.session = fake_session(phase=WAITING)
        window._show_ollama()
        assert panel.status_label.full_text() == "Loading the model…" and panel.start_button.isHidden()
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
        assert stopped == [23720] and panel.status_label.full_text() == "Checking…"
    finally:
        window.session = None
        window.close()

def test_stopping_a_server_that_is_already_gone_offers_to_start_one(app, monkeypatch):
    # Started and stopped outside Local Fox since the last check: the panel still had "Stop Ollama server"
    import time as clock

    from local_fox.ui.main_window import MainWindow
    window = MainWindow(dict(config.DEFAULTS))
    errors = []
    monkeypatch.setattr(window, "_local_error", errors.append)
    monkeypatch.setattr(ollama_server, "other_server", lambda host: None)
    answers = {"state": State(False)}

    async def server_state():
        return answers["state"]
    panel = window.ollama_panel

    def wait_for(condition):
        end = clock.monotonic() + 5
        while not condition() and clock.monotonic() < end:
            app.processEvents()
            clock.sleep(0.01)
    try:
        window.session = fake_session()
        window.session.server_state = server_state

        async def server_back():
            pass
        window.session.server_back = server_back
        window._ollama_state = State(True)
        window._show_ollama()
        assert not panel.stop_button.isHidden()
        panel.stop_button.click()
        wait_for(lambda: panel.status_label.full_text() == "Not running")
        assert panel.status_label.full_text() == "Not running" and not panel.start_button.isHidden()
        assert panel.stop_button.isHidden() and errors == []
        # One that still answers but cannot be found (on Linux, a system service of another user) is an error
        answers["state"] = State(True)
        window._ollama_state = State(True)
        window._show_ollama()
        panel.stop_button.click()
        wait_for(lambda: errors)
        assert len(errors) == 1 and "No Ollama server was found" in errors[0]
    finally:
        window.session = None
        window.close()

# --- Ollama on another computer: /host and the setting ---

def test_an_address_is_completed_as_typed():
    normalize = ollama_server.normalize_host
    assert normalize("192.168.1.20") == "http://192.168.1.20:11434"
    assert normalize("192.168.1.20:11500") == "http://192.168.1.20:11500"
    assert normalize(" http://gpu-box.local/ ") == "http://gpu-box.local:11434"
    assert normalize("https://ollama.example.org") == "https://ollama.example.org"  # A proxy's own port
    for text in ("", "not an address", "ftp://x", "192.168.1.20:port", "http://x/api", "http://"):
        assert normalize(text) is None, text

def test_host_shows_and_sets_the_address_and_keeps_the_conversation(app, tmp_path, monkeypatch):
    import json
    import time as clock

    from local_fox.ui.main_window import MainWindow
    window = MainWindow(dict(config.DEFAULTS))
    notices, errors, hosts = [], [], []
    monkeypatch.setattr(window, "_local_notice", notices.append)
    monkeypatch.setattr(window, "_local_error", errors.append)
    monkeypatch.setattr(window, "_poll_ollama", lambda then=None: None)

    def wait_for(condition):
        end = clock.monotonic() + 5
        while not condition() and clock.monotonic() < end:
            app.processEvents()
            clock.sleep(0.01)
    try:
        session = fake_session()

        async def set_host(host):
            hosts.append(host)
            session.config["ollama_host"] = host
            return None
        session.set_host = set_host
        window.session = session
        window._submit("/host")
        assert "The Ollama address is http://127.0.0.1:11434" in notices[-1]
        window._submit("/host 192.168.1.20")
        wait_for(lambda: hosts and "now" in notices[-1])
        assert hosts == ["http://192.168.1.20:11434"] and notices[-1] == "The Ollama address is now http://192.168.1.20:11434."
        saved = json.loads(Path(os.environ["LOCAL_FOX_CONFIG"]).read_text(encoding="utf-8"))
        assert saved["ollama_host"] == "http://192.168.1.20:11434" and window.settings["ollama_host"] == saved["ollama_host"]
        # The panel shows the other computer's server, which has neither button
        window._ollama_state = State(True)
        window._show_ollama()
        assert window.ollama_panel.host_label.full_text() == "192.168.1.20:11434"
        assert window.ollama_panel.start_button.isHidden() and window.ollama_panel.stop_button.isHidden()
        window._submit("/host not an address")
        assert "is not an address" in errors[-1] and len(hosts) == 1
        session.running = True
        window._submit("/host 192.168.1.30")
        assert "Stop the model" in errors[-1] and len(hosts) == 1
        session.running = False
        # The settings view's plain "Save" applies a new address at once too, without a clear
        window._settings_saved({**window.settings, "ollama_host": "http://192.168.1.40:11434"}, False, False)
        wait_for(lambda: len(hosts) == 2)
        assert hosts[-1] == "http://192.168.1.40:11434"
    finally:
        window.session = None
        window.close()

def test_a_check_of_the_old_address_does_not_overwrite_the_new_ones(app):
    # The panel said "Not running" for a server that answered: a check sent before /host returned after it
    import asyncio
    import time as clock

    from local_fox.ui.main_window import MainWindow
    window = MainWindow(dict(config.DEFAULTS))
    asked = []

    def server_state():
        # The address is the one when the check is sent, as the session's client has it then
        asked.append(window.session.config["ollama_host"])
        first = len(asked) == 1

        async def check():
            if first:
                await asyncio.sleep(0.2)  # On its way while the address changes
                return State(False)
            return State(True)
        return check()

    async def server_back():
        pass
    try:
        session = fake_session()
        session.server_state, session.server_back = server_state, server_back
        window.session = session
        window._poll_ollama()
        session.config["ollama_host"] = "http://127.0.0.1:11500"
        end = clock.monotonic() + 5
        while len(asked) < 2 and clock.monotonic() < end or window._poll_pending:
            app.processEvents()
            clock.sleep(0.01)
        assert asked == ["http://127.0.0.1:11434", "http://127.0.0.1:11500"]
        assert window._ollama_state == State(True) and window.ollama_panel.status_label.full_text() == "Idle"
    finally:
        window.session = None
        window.close()
