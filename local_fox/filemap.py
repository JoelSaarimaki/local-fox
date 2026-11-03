"""
The project's files as the tools see them: which paths are forbidden, read-only or ignored, read from the
project's tools config (.local-fox/tools-config.json) with the same rules as ollmcp-generic-tools, and the
compact filemap given to the model at the start of a session.

The project's Local Fox folder is the folder of its tools config, wherever .mcp.json points: .local-fox in a
project set up by Local Fox, .ollmcp-tools in one set up by the tools' own init. It holds the instructions, the
tools config, the commands, the AI's plans (context) and the saved conversations.
"""
import fnmatch
import json
import os
from pathlib import Path

from .conversation import estimate_tokens

PROJECT_FOLDER = ".local-fox"      # The folder Local Fox's setup creates
LEGACY_FOLDER = ".ollmcp-tools"    # The folder of the tools' own init, still used in projects set up with it
CONFIG_NAME = "tools-config.json"
INSTRUCTIONS_NAME = "instructions.md"
PROTECTED_FILE_NAMES = {".mcp.json"}
# As the tools' DEFAULT_IGNORED_DIRS: folder names skipped at any depth unless the tools config sets its own
DEFAULT_IGNORED_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "build", ".next",
    ".venv", "venv", "env", ".pytest_cache", ".idea", ".vscode",
    "target", "out", ".mypy_cache", ".ruff_cache"
}

# How a path is shown
SHOWN, FORBIDDEN, READ_ONLY, IGNORED = "shown", "forbidden", "read_only", "ignored"

def tools_config_path(project: Path) -> Path:
    """
    Returns the path of the project's tools config: the MCP_TOOLS_CONFIG of a server in .mcp.json if that file
    exists, else the one in .local-fox or .ollmcp-tools that exists, else where Local Fox's setup creates it. A
    .mcp.json left from an earlier setup can name a config that is gone; it does not decide where a new one goes.
    """
    named = mcp_json_config(project)
    if named and named.is_file():
        return named
    for folder in (PROJECT_FOLDER, LEGACY_FOLDER):
        if (project / folder / CONFIG_NAME).exists():
            return project / folder / CONFIG_NAME
    return project / PROJECT_FOLDER / CONFIG_NAME

def mcp_json_config(project: Path) -> Path | None:
    """
    Returns the tools config that a server of the project's .mcp.json names (MCP_TOOLS_CONFIG), whether or not it
    exists, or None.
    """
    try:
        servers = json.loads((project / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers", {})
        for server in servers.values():
            value = (server.get("env") or {}).get("MCP_TOOLS_CONFIG")
            if value:
                path = Path(value)
                return path if path.is_absolute() else (project / path).resolve()
    except (OSError, ValueError, AttributeError):
        pass
    return None

def project_folder(project: Path) -> Path:
    """
    Returns the project's Local Fox folder: the folder of its tools config.
    """
    return tools_config_path(project).parent

def instructions_path(project: Path) -> Path:
    """
    Returns the path of the project instructions: the tools config's instructions_file, relative to its project_dir
    as the tools' other paths, or else instructions.md next to the tools config (projects set up before the setting).
    """
    config_path = tools_config_path(project)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    value = config.get("instructions_file") if isinstance(config, dict) else None
    if isinstance(value, str) and value.strip():
        project_dir = config.get("project_dir")
        base = (config_path.parent / project_dir).resolve() if isinstance(project_dir, str) else Path(project).resolve()
        path = Path(value.strip())
        return path if path.is_absolute() else (base / path).resolve()
    return config_path.parent / INSTRUCTIONS_NAME

class Rules:
    """
    The tools' rules for the paths of a project. Without a tools config, nothing is forbidden and the
    default folders are ignored.
    """

    def __init__(self, project: Path):
        self.project = project.resolve()
        self.config_path = tools_config_path(self.project)
        config = {}
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        config = config if isinstance(config, dict) else {}
        # The folder the tools take paths relative to
        project_dir = config.get("project_dir")
        self.base = (self.config_path.parent / project_dir).resolve() if isinstance(project_dir, str) else self.project
        if not self.base.is_dir():
            self.base = self.project
        self.forbidden = self._paths(config.get("forbidden_paths"))
        self.allowed = self._paths(config.get("allowed_paths"))
        self.read_only = self._paths(config.get("read_only_files"))
        ignored = config.get("ignored_dirs")
        self.ignored_dirs = set(ignored) if isinstance(ignored, list) else set(DEFAULT_IGNORED_DIRS)
        self.protected = {self.config_path.resolve()}
        registry = config.get("command_registry")
        if isinstance(registry, str):
            self.protected.add((self.base / registry).resolve())
        gitignore = config.get("gitignore_path")
        self.gitignore = self._load_gitignore((self.base / gitignore) if isinstance(gitignore, str) else self.base / ".gitignore")

    def _paths(self, values) -> list[Path]:
        if not isinstance(values, list):
            return []
        return [(Path(v) if Path(v).is_absolute() else self.base / v.strip()).resolve() for v in values if isinstance(v, str) and v.strip()]

    @staticmethod
    def _load_gitignore(path: Path) -> list[str]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]

    @staticmethod
    def _deepest(path: Path, roots: list[Path]) -> int:
        return max((len(root.parts) for root in roots if path.is_relative_to(root)), default=-1)

    def is_forbidden(self, path: Path) -> bool:
        if path.name.lower() in PROTECTED_FILE_NAMES or path in self.protected:
            return True
        return self._deepest(path, self.forbidden) > self._deepest(path, self.allowed)

    def _matches_ignored(self, path: Path, base: Path) -> bool:
        try:
            parts = path.relative_to(base).parts
        except ValueError:
            return False
        if any(part in self.ignored_dirs for part in parts):
            return True
        rel = "/".join(parts)
        return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(f"{rel}/", p) or any(fnmatch.fnmatch(part, p) for part in parts)
                   for p in self.gitignore)

    def state(self, path: Path) -> str:
        """
        Returns how a path is shown: READ_ONLY, FORBIDDEN, IGNORED (in ignored_dirs or .gitignore) or SHOWN.
        """
        path = Path(os.path.abspath(path))
        if path in self.read_only:
            return READ_ONLY
        if self.is_forbidden(path):
            return FORBIDDEN
        roots = [allowed for allowed in self.allowed if path.is_relative_to(allowed)]
        base = max(roots, key=lambda root: len(root.parts)) if roots else self.base
        return IGNORED if self._matches_ignored(path, base) else SHOWN

    def leads_to_visible(self, folder: Path) -> bool:
        """
        Checks if a hidden folder contains an allowed path or a read-only file, which the tools show.
        """
        return any(p != folder and p.is_relative_to(folder) for p in self.allowed + self.read_only)

    def relative(self, path: Path) -> str:
        """
        Returns a path as the tools take it: relative to their project folder, with forward slashes.
        """
        try:
            return Path(os.path.abspath(path)).relative_to(self.base).as_posix() or "."
        except ValueError:
            return Path(path).as_posix()

def visible_tree(rules: Rules) -> dict:
    """
    Returns the files the tools show, as nested dicts: a folder is a dict of its entries, a file is None.
    Forbidden and ignored paths are left out, except the allowed paths and read-only files in them.
    """
    def walk(folder: Path, hidden: bool) -> dict:
        tree = {}
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        except OSError:
            return tree
        for entry in entries:
            path = Path(entry.path)
            state = rules.state(path)
            is_dir = entry.is_dir(follow_symlinks=False)
            visible = state == READ_ONLY or (state == SHOWN and (not hidden or any(path.is_relative_to(a) for a in rules.allowed)))
            if is_dir:
                if visible:
                    tree[entry.name] = walk(path, False)
                elif rules.leads_to_visible(path):
                    sub = walk(path, True)
                    if sub:
                        tree[entry.name] = sub
            elif visible:
                tree[entry.name] = None
        return tree
    return walk(rules.base, False)

def count_files(tree: dict) -> int:
    return sum(1 if sub is None else count_files(sub) for sub in tree.values())

def _lines(tree: dict, depth: int, folders_only: bool, max_depth: int | None) -> list[str]:
    indent = "  " * depth
    lines = [f"{indent}{name}" for name, sub in tree.items() if sub is None] if not folders_only else []
    for name, sub in tree.items():
        if sub is None:
            continue
        if folders_only:
            lines.append(f"{indent}{name}/ ({count_files(sub)} files)")
        else:
            lines.append(f"{indent}{name}/")
        if max_depth is None or depth + 1 < max_depth:
            lines += _lines(sub, depth + 1, folders_only, max_depth)
    return lines

def model_filemap(rules: Rules, max_tokens: int) -> tuple[str, int, bool]:
    """
    Returns the filemap for the model, the number of files in it and whether it shows the folders only:
    the project folder once, then each folder and file by its name, indented under its folder. A project
    larger than max_tokens is shown with its folders only, with their file counts, as deep as fits.
    """
    tree = visible_tree(rules)
    files = count_files(tree)
    header = f"Files in the project folder {rules.base.as_posix()}:"
    text = "\n".join([header, *_lines(tree, 0, False, None)])
    if estimate_tokens(text) <= max_tokens:
        return text, files, False
    header = (f"Folders in the project folder {rules.base.as_posix()}, with their file counts ({files} files in all; "
              f"outline a folder with get_outline to see its files):")
    for max_depth in (None, 4, 3, 2, 1):
        text = "\n".join([header, *_lines(tree, 0, True, max_depth)])
        if estimate_tokens(text) <= max_tokens or max_depth == 1:
            return text, files, True
    return text, files, True
