"""
Tests of the parts of Local Fox that differ on macOS: the login shell's PATH, the config folder, the folders that
are not set up, and the application's icon. They run on any system, with macOS simulated where it matters.
"""
import os
import struct
import sys
from pathlib import Path

import pytest

from local_fox import config, shell_path, tools_setup

def test_the_shells_folders_come_after_the_current_ones():
    sep = os.pathsep
    merged = shell_path.merged(sep.join(["/usr/bin", "/bin"]), sep.join(["/opt/homebrew/bin", "/usr/bin", "/opt/homebrew/bin", "/usr/local/bin"]))
    assert merged.split(sep) == ["/usr/bin", "/bin", "/opt/homebrew/bin", "/usr/local/bin"]

def test_the_login_shells_path_is_added_only_on_macos(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(shell_path, "login_shell_path", lambda: "/opt/homebrew/bin")
    monkeypatch.setattr(sys, "platform", "win32")
    shell_path.add_login_shell_path()
    assert os.environ["PATH"] == "/usr/bin"
    monkeypatch.setattr(sys, "platform", "darwin")
    shell_path.add_login_shell_path()
    assert os.environ["PATH"] == os.pathsep.join(["/usr/bin", "/opt/homebrew/bin"])

def test_a_shell_that_cannot_run_gives_no_path():
    assert shell_path.login_shell_path("/no/such/shell") is None

@pytest.mark.skipif(os.name == "nt", reason="Needs a POSIX shell")
def test_the_path_is_read_from_the_shells_output_only():
    assert shell_path.login_shell_path("/bin/sh")

def test_config_is_in_application_support_on_macos(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCAL_FOX_CONFIG", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert config.config_path() == tmp_path / "Library" / "Application Support" / "LocalFox" / "config.json"

@pytest.mark.skipif(os.name == "nt", reason="POSIX paths")
def test_disk_roots_and_the_library_folder_are_not_set_up():
    assert "root of a disk" in tools_setup.refusal(Path("/Volumes/External"))
    assert "Library folder" in tools_setup.refusal(Path.home() / "Library" / "Caches")

def test_the_icns_icon_has_every_size(tmp_path):
    from PySide6.QtWidgets import QApplication
    from local_fox.ui import logo
    QApplication.instance() or QApplication([])
    path = tmp_path / "local_fox.icns"
    logo.write_icns(path)
    data = path.read_bytes()
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    kinds, offset = [], 8
    while offset < len(data):
        kinds.append(data[offset:offset + 4])
        offset += struct.unpack(">I", data[offset + 4:offset + 8])[0]
    assert kinds == list(logo.ICNS_TYPES.values()) and offset == len(data)

# --- The Ollama panel's server on macOS and Linux (ollama_server) ---

def test_macos_runs_ollama_serve_in_terminal():
    from local_fox import ollama_server
    command = ollama_server.terminal_command("/Applications/Ollama.app/Contents/Resources/ollama",
                                             "http://127.0.0.1:11500", platform="darwin")
    assert command[0] == "osascript"
    assert command[2] == ('tell application "Terminal" to do script '
                          '"OLLAMA_HOST=127.0.0.1:11500 /Applications/Ollama.app/Contents/Resources/ollama serve"')
    # A path with a space is quoted for the shell, and the quotes survive AppleScript's string
    spaced = ollama_server.terminal_command("/Users/a b/ollama", "http://localhost", platform="darwin")
    assert "OLLAMA_HOST=localhost:11434 '/Users/a b/ollama' serve" in spaced[2]

def test_linux_runs_ollama_serve_in_the_first_terminal_found_with_a_display():
    from local_fox import ollama_server
    found = {"konsole": "/usr/bin/konsole", "xterm": "/usr/bin/xterm"}
    command = ollama_server.terminal_command("/usr/local/bin/ollama", "http://127.0.0.1:11434", platform="linux",
                                             which=found.get, environ={"WAYLAND_DISPLAY": "wayland-0"})
    assert command[:4] == ["/usr/bin/konsole", "-e", "sh", "-c"]
    assert command[4].startswith("OLLAMA_HOST=127.0.0.1:11434 /usr/local/bin/ollama serve; ")
    assert ollama_server.STOPPED_NOTE in command[4]  # An error stays readable instead of the window closing
    # No display (a server over SSH), or no terminal: none, and the server runs hidden with its log
    assert ollama_server.terminal_command("ollama", "http://127.0.0.1:11434", platform="linux", which=found.get,
                                          environ={}) is None
    assert ollama_server.terminal_command("ollama", "http://127.0.0.1:11434", platform="linux", which=lambda name: None,
                                          environ={"DISPLAY": ":0"}) is None

def test_a_server_in_a_terminal_is_followed_by_its_port(monkeypatch):
    from types import SimpleNamespace

    from local_fox import ollama_server
    launcher = SimpleNamespace(poll=lambda: 0, returncode=0)  # osascript and gnome-terminal end at once
    server = ollama_server.TerminalServer(11434, launcher)
    listening = {"pid": None}
    alive = {7: True}
    monkeypatch.setattr(ollama_server, "listening_pid", lambda port: listening["pid"])
    monkeypatch.setattr(ollama_server, "process_name", lambda pid: "ollama")
    monkeypatch.setattr(ollama_server, "_alive", lambda pid: alive.get(pid, False))
    assert server.poll() is None  # Starting: the launcher's exit 0 is no failure
    listening["pid"] = 7
    assert server.poll() is None and server.pid == 7
    signals = []
    monkeypatch.setattr(ollama_server.os, "kill", lambda pid, number: signals.append((pid, number)) or alive.pop(pid))
    ollama_server.stop(server)
    assert signals == [(7, ollama_server.signal.SIGTERM)] and server.poll() == 0

def test_linux_finds_the_server_with_ss_when_lsof_is_missing(monkeypatch):
    from local_fox import ollama_server
    monkeypatch.setattr(sys, "platform", "linux")
    outputs = {"lsof": "", "ss": 'LISTEN 0 4096 127.0.0.1:11434 0.0.0.0:* users:(("ollama",pid=1234,fd=3))\n'}
    monkeypatch.setattr(ollama_server, "_run", lambda command: outputs[command[0]])
    assert ollama_server.listening_pid(11434) == 1234
    assert "sudo systemctl stop ollama" in ollama_server.not_found_hint("linux")
    assert "systemctl" not in ollama_server.not_found_hint("darwin")
