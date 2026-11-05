"""
The Ollama server as the window's Ollama panel shows it: whether it answers, which models it has loaded and where
they run, and what it is doing for Local Fox's request. It also starts 'ollama serve' when the user asks for it
("Start Ollama server"), never on its own: a server Local Fox started runs in a console or terminal window of its
own (hidden, logged to logs/ollama-serve.log, only without a terminal), and is stopped when Local Fox closes. "Stop Ollama server" stops any Ollama server of this
computer at the address, also one started elsewhere and running hidden, so that the user can run it in a console
of their own: it is found by the port it listens on (other_server), and stopped only if the program is Ollama.

Ollama tells nothing of a request in progress, so the activity comes from Local Fox's own request (Session.phase)
and 'ollama ps': a model that is still loading is not listed there (checked with Ollama 0.40, 2026-11-05), so a
request that waits for its first reply chunk while its model is not listed is loading it, and one whose model is
listed is reading the prompt.
"""
import asyncio
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PORT = 11434
LOG_NAME = "ollama-serve.log"
POLL_TIMEOUT = 5  # Seconds for 'ollama ps', which answers at once also while a model loads

# The phases of Local Fox's request (Session.phase)
WAITING = "waiting"      # Sent, no reply chunk yet: loading the model or reading the prompt
THINKING = "thinking"
WRITING = "writing"

@dataclass
class Loaded:
    """
    A model Ollama has loaded, as 'ollama ps' lists it.
    """
    name: str
    size: int = 0
    size_vram: int = 0

    def placement(self) -> str:
        """
        Where the model runs, as 'ollama ps' shows it: '100 % GPU', '77 % CPU / 23 % GPU' or '100 % CPU'.
        """
        if not self.size:
            return "loaded"
        gpu = round(100 * (self.size_vram or 0) / self.size)
        if gpu >= 100:
            return "100 % GPU"
        return "100 % CPU" if gpu <= 0 else f"{100 - gpu} % CPU / {gpu} % GPU"

@dataclass
class State:
    """
    What a poll of the server found: whether it answered and what it has loaded.
    """
    reachable: bool
    loaded: list[Loaded] = field(default_factory=list)
    answering: bool = True  # False: it took the connection but did not answer in time (POLL_TIMEOUT)

    def find(self, model: str) -> Loaded | None:
        """
        Returns the loaded model of that name; 'llama3.1' finds 'llama3.1:latest', as Ollama names it.
        """
        for item in self.loaded:
            if item.name == model or item.name == f"{model}:latest":
                return item
        return None

async def poll(client, timeout: float = POLL_TIMEOUT) -> State:
    """
    Asks the server what it has loaded ('ollama ps'). A server that refuses the connection is not reachable; one
    that takes it but does not answer in time is reachable but not answering. The ollama client has no timeout of
    its own, and one poll that never returned left the panel at its old text for good.
    """
    try:
        models = (await asyncio.wait_for(client.ps(), timeout)).models
    except TimeoutError:
        return State(True, answering=False)
    except Exception:
        return State(False)
    return State(True, [Loaded(m.model or m.name or "", m.size or 0, m.size_vram or 0) for m in models])

def activity(state: State | None, model: str, phase: str | None) -> str:
    """
    Returns the panel's short text of what the server does: Local Fox's request if one runs, otherwise what is
    loaded. A request in progress counts as reachable, as the poll of a busy server can time out.
    """
    if phase == WAITING:
        if state and state.reachable and state.find(model):
            return f"Reading the prompt for {model}…"
        return f"Loading {model}…"
    if phase == THINKING:
        return f"{model} is thinking…"
    if phase == WRITING:
        return f"{model} is writing…"
    if state is None:
        return "Checking…"
    if not state.reachable:
        return "Not running"
    if not state.answering:
        return "Not answering"
    if not state.loaded:
        return "Running, no model loaded"
    current = state.find(model)
    if current:
        others = len(state.loaded) - 1
        return f"{model} loaded, {current.placement()}" + (f", and {others} more" if others else "")
    return "Loaded: " + ", ".join(item.name for item in state.loaded)

def is_local(host: str) -> bool:
    """
    Whether the address is this computer, where 'ollama serve' can be started.
    """
    name = (urlsplit(host if "://" in host else f"http://{host}").hostname or "").lower()
    return name in ("127.0.0.1", "localhost", "::1", "0.0.0.0")

def find_ollama() -> str | None:
    """
    Returns the path of the ollama program: on the PATH, or where its installer puts it.
    """
    found = shutil.which("ollama")
    if found:
        return found
    if sys.platform == "win32":
        candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"]
    elif sys.platform == "darwin":
        candidates = [Path("/Applications/Ollama.app/Contents/Resources/ollama"), Path("/usr/local/bin/ollama"),
                      Path("/opt/homebrew/bin/ollama")]
    else:
        candidates = [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    return next((str(path) for path in candidates if path.is_file()), None)

def serve_env(host: str) -> dict:
    """
    The environment of 'ollama serve': OLLAMA_HOST set to the address Local Fox uses, so that a port other than
    the default one is served too.
    """
    parts = urlsplit(host if "://" in host else f"http://{host}")
    env = dict(os.environ)
    env["OLLAMA_HOST"] = f"{parts.hostname or '127.0.0.1'}:{parts.port or DEFAULT_PORT}"
    return env

# Linux terminals, in the order tried, with the option after which the command and its arguments follow.
# gnome-terminal before x-terminal-emulator, whose -e takes one string with some of the terminals behind it.
LINUX_TERMINALS = [("gnome-terminal", "--"), ("konsole", "-e"), ("xfce4-terminal", "-x"), ("x-terminal-emulator", "-e"),
                   ("xterm", "-e")]
# What a Linux terminal shows when the server stops, so that an error stays readable instead of the window closing
STOPPED_NOTE = "The Ollama server stopped. Press Enter to close this window."

def terminal_command(program: str, host: str, platform: str | None = None, which=shutil.which,
                     environ: dict | None = None) -> list[str] | None:
    """
    Returns the command that opens a terminal window running 'ollama serve' for the address: Terminal on macOS
    (through osascript), the first terminal found on Linux with a display. None where there is none, e.g. on a
    Linux server without a display; the server then runs hidden with a log (start).
    """
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    serve = f"OLLAMA_HOST={shlex.quote(serve_env(host)['OLLAMA_HOST'])} {shlex.quote(program)} serve"
    if platform == "darwin":
        script = serve.replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", f'tell application "Terminal" to do script "{script}"',
                "-e", 'tell application "Terminal" to activate']
    if platform.startswith("linux") and (environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY")):
        for terminal, option in LINUX_TERMINALS:
            path = which(terminal)
            if path:
                return [path, option, "sh", "-c", f"{serve}; echo; echo '{STOPPED_NOTE}'; read _"]
    return None

def _alive(pid: int) -> bool:
    """
    Whether a process of that id runs (macOS, Linux).
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # It runs, as another user
        return True
    return True

class TerminalServer:
    """
    An 'ollama serve' that Local Fox started in a terminal window (macOS, Linux). The terminal runs it, not Local
    Fox, so it is found by the port it listens on once it answers, and followed and stopped by that process id;
    it then works as the Popen of a server Local Fox runs itself (poll, terminate, wait, kill).
    """
    visible = True

    def __init__(self, port: int, launcher: subprocess.Popen):
        self.port = port
        self.launcher = launcher  # The terminal, or osascript, which can end at once: only a failure counts
        self.pid: int | None = None
        self.returncode: int | None = None

    def poll(self) -> int | None:
        """
        None while the server starts or runs; its exit code (0 when unknown) when it stopped, or the launcher's
        when the terminal could not be opened (e.g. macOS did not allow Local Fox to control Terminal).
        """
        if self.returncode is not None:
            return self.returncode
        if self.pid is None:
            pid = listening_pid(self.port)
            if pid and process_name(pid).lower().startswith("ollama"):
                self.pid = pid
            elif self.launcher.poll():
                self.returncode = self.launcher.returncode
            return self.returncode  # None: still starting; a server that never answers is timed out by the window
        if not _alive(self.pid):
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self._signal(signal.SIGTERM)

    def kill(self) -> None:
        self._signal(signal.SIGKILL)

    def _signal(self, number: int) -> None:
        if self.pid is not None and self.returncode is None:
            try:
                os.kill(self.pid, number)
            except OSError:
                pass

    def wait(self, timeout: float | None = None) -> int:
        end = time.monotonic() + (timeout if timeout is not None else 3600)
        while self.poll() is None and self.pid is not None:
            if time.monotonic() > end:
                raise subprocess.TimeoutExpired("ollama serve", timeout)
            time.sleep(0.1)
        return self.returncode or 0

def start(program: str, host: str, log_dir: Path):
    """
    Starts 'ollama serve' for the address, where the user sees it: on Windows in a console window of its own, on
    macOS and Linux in a terminal window (terminal_command), where they can stop it with Ctrl+C or by closing
    the window; Local Fox stops it when it closes. Without a terminal (a Linux server without a display) it runs
    hidden, with its output in logs/ollama-serve.log. Returns a Popen, or a TerminalServer that works as one;
    'visible' tells whether it has a window.
    """
    if sys.platform == "win32":
        # Not CREATE_NEW_PROCESS_GROUP, which would turn Ctrl+C off in the new console. A console of its own
        # already keeps Ctrl+C in a console that started Local Fox from reaching it.
        process = subprocess.Popen([program, "serve"], env=serve_env(host), creationflags=subprocess.CREATE_NEW_CONSOLE)
        process.visible = True
        return process
    command = terminal_command(program, host)
    if command:
        try:
            launcher = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)
            return TerminalServer(port_of(host), launcher)
        except OSError:
            pass  # Hidden, as without a terminal
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / LOG_NAME, "ab") as log:
        process = subprocess.Popen([program, "serve"], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   env=serve_env(host), start_new_session=True)
    process.visible = False
    return process

@dataclass
class Process:
    """
    An Ollama server that Local Fox did not start, found by its port.
    """
    pid: int
    name: str

def _run(command: list[str]) -> str:
    """
    Runs a small system command for its output, with no console window, and returns "" if it fails.
    """
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=10, creationflags=flags,
                              encoding="utf-8", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return ""

def port_of(host: str) -> int:
    """
    The port of the address, Ollama's default when it has none.
    """
    return urlsplit(host if "://" in host else f"http://{host}").port or DEFAULT_PORT

def listening_pid(port: int, netstat: str | None = None) -> int | None:
    """
    Returns the process that listens on the TCP port of this computer: from 'netstat -ano' on Windows, 'lsof'
    elsewhere. The netstat text can be given, for the tests.
    """
    if sys.platform == "win32" or netstat is not None:
        text = netstat if netstat is not None else _run(["netstat", "-ano", "-p", "TCP"])
        for line in text.splitlines():
            parts = line.split()
            # TCP  127.0.0.1:11434  0.0.0.0:0  LISTENING  23720 (the state is in the system's language, so it is
            # not read: a listening socket is the one whose remote address is all zeros)
            if len(parts) == 5 and parts[0] == "TCP" and parts[1].rsplit(":", 1)[-1] == str(port) \
                    and parts[2].rsplit(":", 1)[-1] == "0" and parts[4].isdigit():
                return int(parts[4])
        return None
    text = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]).split()
    if text and text[0].isdigit():
        return int(text[0])
    # lsof is missing from some Linux systems; ss is there: ... users:(("ollama",pid=1234,fd=3))
    match = re.search(r"pid=(\d+)", _run(["ss", "-ltnpH", f"sport = :{port}"]))
    return int(match.group(1)) if match else None

def not_found_hint(platform: str | None = None) -> str:
    """
    What to try when the server at the address cannot be found or stopped: on Linux it is often the system
    service, which runs as the user 'ollama' and is hidden from the user's own processes.
    """
    if (platform or sys.platform).startswith("linux"):
        return ("If it runs as a system service, as Ollama's Linux installer sets it up, stop it with "
                "'sudo systemctl stop ollama'.")
    return "It may run under another user, or another program answers there."

def process_name(pid: int) -> str:
    """
    Returns the program name of the process, e.g. 'ollama.exe', or "" if it is not found.
    """
    if sys.platform == "win32":
        line = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]).strip()
        return line.split('","')[0].strip('"') if line.startswith('"') else ""
    return Path(_run(["ps", "-p", str(pid), "-o", "comm="]).strip()).name

def other_server(host: str) -> Process | None:
    """
    Returns the Ollama server that listens at the address on this computer, if the listening program is Ollama.
    Another program on the port is never returned, so it cannot be stopped by mistake.
    """
    if not is_local(host):
        return None
    pid = listening_pid(port_of(host))
    if not pid:
        return None
    name = process_name(pid)
    return Process(pid, name) if name.lower().startswith("ollama") else None

def stop_other(server: Process) -> str | None:
    """
    Stops an Ollama server that Local Fox did not start. Returns why it could not, or None. On Windows it is
    ended at once (taskkill /F), as a server without a console does not take a polite request to close.
    """
    if sys.platform == "win32":
        result = subprocess.run(["taskkill", "/PID", str(server.pid), "/F"], capture_output=True, text=True,
                                creationflags=subprocess.CREATE_NO_WINDOW, encoding="utf-8", errors="replace")
        return None if result.returncode == 0 else (result.stderr or result.stdout).strip() or "taskkill failed"
    try:
        import signal
        os.kill(server.pid, signal.SIGTERM)
    except OSError as error:
        return str(error)
    return None

def stop(process: subprocess.Popen | None) -> None:
    """
    Stops a server Local Fox started, when Local Fox closes.
    """
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

def log_tail(log_dir: Path, lines: int = 5) -> str:
    """
    Returns the end of logs/ollama-serve.log, for a server that did not start.
    """
    try:
        text = (log_dir / LOG_NAME).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])
