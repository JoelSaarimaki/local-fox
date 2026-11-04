"""
The PATH of the user's login shell, for Local Fox started from the macOS Finder or Dock.

An application started there gets only the system's PATH (/usr/bin:/bin:/usr/sbin:/sbin), not the one the user's
shell sets up, so programs installed with e.g. Homebrew (git, pytest, npm, uvx) were not found: neither the
commands the tools run nor the MCP servers of .mcp.json. The login shell is asked for its PATH once at the start,
and its folders are added after the ones Local Fox already has. Started from a terminal, the PATH is already the
shell's, and nothing new is added.
"""
import os
import subprocess
import sys

# A shell that waits on something (a prompt in a profile) must not hold up the start
SHELL_SECONDS = 5
MARK = "__LOCAL_FOX_PATH__"  # Around the PATH in the shell's output, which profiles may add lines to

def login_shell_path(shell: str | None = None) -> str | None:
    """
    Returns the PATH of the user's login shell, or None if it could not be read.
    """
    shell = shell or os.environ.get("SHELL") or "/bin/zsh"
    try:
        result = subprocess.run([shell, "-l", "-c", f'printf "{MARK}%s{MARK}" "$PATH"'], capture_output=True,
                                text=True, stdin=subprocess.DEVNULL, timeout=SHELL_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = result.stdout.split(MARK)
    return parts[1] if len(parts) >= 3 and parts[1] else None

def merged(current: str, shell: str) -> str:
    """
    Returns the current PATH with the shell's folders that it lacks added after it, in the shell's order.
    """
    folders = [f for f in current.split(os.pathsep) if f]
    return os.pathsep.join(folders + [f for f in dict.fromkeys(shell.split(os.pathsep)) if f and f not in folders])

def add_login_shell_path() -> None:
    """
    On macOS, adds the login shell's PATH to this process's environment, which the MCP servers and the commands
    they run inherit. Elsewhere it does nothing: on Windows the PATH comes from the system, and on Linux desktops
    applications get the session's PATH.
    """
    if sys.platform != "darwin":
        return
    shell = login_shell_path()
    if shell:
        os.environ["PATH"] = merged(os.environ.get("PATH", ""), shell)
