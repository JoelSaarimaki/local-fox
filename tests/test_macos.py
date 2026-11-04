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
