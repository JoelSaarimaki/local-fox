"""
The project's tools config (.local-fox/tools-config.json, or where .mcp.json's MCP_TOOLS_CONFIG points), as
the user changes it in Local Fox: in the settings view, and with Allow, Read-only and Forbid in the filemap's menu.

Only the user changes it, never the model: the tools protect the file from their own tools, so that the AI
cannot lift its own limits, and Local Fox writes it only for the user's actions. The tools read it when they
start, so a change applies when Local Fox restarts them, which starts a new conversation.

The checks here are of the kinds of the values only, so that a typo is caught before the file is written. The
tools' own rules (what a path may be, which names exist) stay in ollmcp-generic-tools: a server that does not
start with the new config shows its error in the feed. Settings Local Fox does not know are kept as they are.
"""
import json
from pathlib import Path

from .filemap import Rules, tools_config_path

# The settings of the tools config, in the order of the tools' README, with how they are edited and what they do
SETTINGS = {
    "project_dir": ("str", "The project folder the tools can access, relative to the config file's own folder. "
                           "Every other path here is relative to it. Required."),
    "forbidden_paths": ("paths", "Folders and files the tools never access, one per line."),
    "allowed_paths": ("paths", "Folders and files inside forbidden folders that the tools may use again, one per line."),
    "read_only_files": ("paths", "Files the AI can read anywhere but never change, move or delete, one per line."),
    "max_output_chars": ("int?", "The largest tool response in characters (about 4 per token). Empty: 40 000; at least 2 000."),
    "gitignore_path": ("str?", "The .gitignore used by the outline and search tools. Empty: .gitignore in the project folder."),
    "command_registry": ("str?", "The command registry of the commands toolset; always protected."),
    "command_suggestions": ("str?", "Where suggest_command saves the AI's suggested commands for you to review."),
    "backup_folder": ("str?", "Where restore_file keeps its backups. Empty: a folder for the project in ~/.ollmcp-tools/backups."),
    "ignored_dirs": ("names?", "Folder names the outline and search tools skip at any depth, one per line. "
                               "Empty: node_modules, .git, build and other common build and cache folders."),
    "disabled_toolsets": ("names?", "Toolsets that are not loaded, one per line: filesystem, outline, search, git, commands, web."),
    "disabled_tools": ("names?", "Single tools that are not offered to the AI, one per line."),
}
PATH_LISTS = ("allowed_paths", "read_only_files", "forbidden_paths")

class ToolsConfigError(Exception):
    pass

def read(project: Path) -> tuple[Path, dict]:
    """
    Returns the path of the project's tools config and its settings. Raises ToolsConfigError if it cannot be read.
    """
    path = tools_config_path(Path(project).resolve())
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ToolsConfigError(f"The project has no tools config: {path.as_posix()}. Set the project up with /setup.") from None
    except (OSError, ValueError) as error:
        raise ToolsConfigError(f"{path.as_posix()} cannot be read: {error}") from None
    if not isinstance(values, dict):
        raise ToolsConfigError(f"{path.as_posix()} must contain a JSON object.")
    return path, values

def check(values: dict) -> None:
    """
    Checks the kinds of the known settings. Raises ToolsConfigError with what is wrong.
    """
    for key, (kind, _) in SETTINGS.items():
        value = values.get(key)
        if kind == "str" and (not isinstance(value, str) or not value.strip()):
            raise ToolsConfigError(f"'{key}' is required: a path.")
        if kind == "str?" and value is not None and not isinstance(value, str):
            raise ToolsConfigError(f"'{key}' must be a path, or empty.")
        if kind == "int?" and value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ToolsConfigError(f"'{key}' must be a whole number, or empty.")
        if kind in ("paths", "names?") and value is not None and (not isinstance(value, list) or not all(isinstance(v, str) for v in value)):
            raise ToolsConfigError(f"'{key}' must be a list of {'paths' if kind == 'paths' else 'names'}.")
    for name in values.get("ignored_dirs") or []:
        if "/" in name or "\\" in name:
            raise ToolsConfigError(f"'ignored_dirs' takes folder names, not paths: {name}. Forbid one folder in 'forbidden_paths' instead.")

def write(path: Path, values: dict) -> None:
    """
    Writes the tools config after checking it, keeping the order of its settings.
    """
    check(values)
    path.write_text(json.dumps(values, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")

def _listed(rules: Rules, values: list, target: Path) -> str | None:
    """
    Returns the entry of a path list that names the target, as it is written in the file, or None.
    """
    for value in values if isinstance(values, list) else []:
        if isinstance(value, str) and value.strip():
            path = Path(value.strip())
            if (path if path.is_absolute() else rules.base / path).resolve() == target:
                return value
    return None

def path_actions(project: Path, path: Path) -> list[tuple[str, str, bool]]:
    """
    Returns what the filemap's menu offers for a path: (label, path list, add) for each list the path can be
    added to or removed from. Nothing for a project without a tools config, or a path outside the tools' folder.
    """
    try:
        _, values = read(project)
    except ToolsConfigError:
        return []
    rules = Rules(Path(project).resolve())
    target = Path(path).resolve()
    if not target.is_relative_to(rules.base) or target == rules.base:
        return []
    actions = []
    labels = {"allowed_paths": ("Allow", "Remove from allowed paths"),
              "read_only_files": ("Read-only", "Remove from read-only files"),
              "forbidden_paths": ("Forbid", "Remove from forbidden paths")}
    for key in PATH_LISTS:
        if key == "read_only_files" and target.is_dir():
            continue
        listed = _listed(rules, values.get(key), target) is not None
        if key == "allowed_paths" and not listed and (not rules.is_forbidden(target) or _listed(rules, values.get("forbidden_paths"), target)):
            # Allowing is for paths inside forbidden folders; a forbidden path itself is removed from forbidden_paths
            # instead, as the tools keep a path forbidden when it is both forbidden and allowed
            continue
        add_label, remove_label = labels[key]
        actions.append((remove_label if listed else f"{add_label}  (add to {key})", key, not listed))
    return actions

def change_path(project: Path, key: str, path: Path, add: bool) -> str:
    """
    Adds a path to one of the path lists of the tools config, or removes it, and returns what was done, for the feed.
    """
    config_path, values = read(project)
    rules = Rules(Path(project).resolve())
    target = Path(path).resolve()
    entries = list(values.get(key) or [])
    listed = _listed(rules, entries, target)
    rel = rules.relative(target)
    if add and listed is None:
        entries.append(rel)
        done = f"Added `{rel}` to {key}"
    elif not add and listed is not None:
        entries.remove(listed)
        done = f"Removed `{listed}` from {key}"
    else:
        return f"`{rel}` is already {'in' if add else 'not in'} {key}; nothing was changed."
    values[key] = entries
    write(config_path, values)
    return f"{done} in {rules.relative(config_path)}. The tools apply it when they restart."
