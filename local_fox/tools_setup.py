"""
The tools of ollmcp-generic-tools bundled with Local Fox: the ollmcp_tools package is installed into Local
Fox's environment and packed into the executable, so that neither needs a Python with the tools installed.

- A project's .mcp.json starts the tools with 'python -m ollmcp_tools', as ollmcp and other clients do. Local
  Fox runs such a server with its own copy instead (setting bundled_tools): from the source, with the Python
  that runs Local Fox; from the executable, by starting LocalFox.exe again with --tools-server. The file
  itself stays as it is, so the project keeps working in the other clients.
- A folder is set up as the tools' 'python -m ollmcp_tools init' does, from the templates of the bundled version,
  but in a .local-fox folder, with Local Fox's own instructions (templates/instructions.md) and a folder for the
  saved conversations (see set_up). Existing files are never overwritten.

Each server is a process of its own, also when bundled: the tools read their config when they are imported,
so one process serves one project.
"""
import importlib.util
import json
import os
import sys
from importlib.resources import files
from pathlib import Path

from .filemap import CONFIG_NAME, INSTRUCTIONS_NAME, LEGACY_FOLDER, PROJECT_FOLDER, project_folder, tools_config_path

TOOLS_MODULE = "ollmcp_tools"
SERVER_FLAG = "--tools-server"
CONVERSATIONS = "conversations"  # The saved conversations, in the project's Local Fox folder

def frozen() -> bool:
    """
    Checks if Local Fox runs as the executable built by PyInstaller.
    """
    return bool(getattr(sys, "frozen", False))

def bundled_version() -> str | None:
    """
    Returns the version of the bundled tools, or None if Local Fox's environment does not have them.
    """
    if importlib.util.find_spec(TOOLS_MODULE) is None:
        return None
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version("ollmcp-tools")
    except PackageNotFoundError:
        return "unknown version"

def runs_the_tools(spec: dict) -> bool:
    """
    Checks if a server of .mcp.json starts the tools: 'python -m ollmcp_tools', with any Python command.
    """
    args = [str(a) for a in spec.get("args") or []]
    return args[-2:] == ["-m", TOOLS_MODULE]

def server_command(spec: dict, use_bundled: bool) -> tuple[str, list[str], str]:
    """
    Returns the command and arguments that start a server of .mcp.json, and where its tools come from, for
    the feed. The tools' server runs with the bundled tools when they are there and use_bundled is set.
    """
    command, args = str(spec["command"]), [str(a) for a in spec.get("args") or []]
    if use_bundled and runs_the_tools(spec):
        version = bundled_version()
        if version:
            source = f"Local Fox's bundled tools (ollmcp-generic-tools {version})"
            if frozen():
                return sys.executable, [SERVER_FLAG], source
            return sys.executable, ["-m", TOOLS_MODULE], source
    return command, args, f"{command} {' '.join(args)}".strip()

def run_server() -> int:
    """
    Runs the bundled tools' MCP server over stdio, in the process LocalFox.exe --tools-server started.
    """
    from ollmcp_tools.__main__ import main
    return main([])

class SetupRefused(Exception):
    """A folder that must not be set up: a system folder, or one whose .mcp.json cannot be read."""

# Folders that are never a project, however they are given: a project is its own folder inside them
_SYSTEM_ENV = ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")
_POSIX_SYSTEM = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys", "/usr", "/System", "/Library")
_POSIX_TOP = ("/home", "/Users", "/root", "/opt", "/var", "/tmp", "/mnt", "/media", "/srv", "/Applications", "/Volumes")

def refusal(project: Path) -> str | None:
    """
    Returns why a folder must never be set up as a project, or None if it may be: a drive or filesystem root
    (C:\\, D:\\, /), the home folder or the folder of all users' homes, an application data folder, or a system folder
    or anything in one (Windows, Program Files, /usr). Setting up writes .mcp.json and .local-fox into the folder and
    lets the AI's tools work in all of it, so it must be a project's own folder.
    """
    project = Path(project).resolve()
    shown = project.as_posix()
    if project.parent == project:
        return f"{shown} is the root of a drive. Set up a project's own folder instead."
    home = Path.home().resolve()
    exact = {home: "your home folder", home.parent: "the folder of the users' home folders"}
    for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE"):
        if os.environ.get(name):
            exact[Path(os.environ[name]).resolve()] = f"a folder of the system ({name})"
    if project in exact:
        return f"{shown} is {exact[project]}. Set up a project's own folder instead."
    systems = [Path(os.environ[name]).resolve() for name in _SYSTEM_ENV if os.environ.get(name)]
    if os.name != "nt":
        systems += [Path(p) for p in _POSIX_SYSTEM]
        if str(project) in _POSIX_TOP:
            return f"{shown} is a system folder. Set up a project's own folder instead."
    for system in systems:
        if project == system or project.is_relative_to(system):
            return f"{shown} is in the system folder {system.as_posix()}. Local Fox does not set up system folders."
    return None

def is_set_up(project: Path) -> bool:
    """
    Checks if a folder is set up as a project: it has a tools config, in .local-fox, or in the folder its .mcp.json
    names (.ollmcp-tools in a project set up with the tools' own init). A .mcp.json alone does not count: other
    applications have them too. Local Fox prompts the model only in a project that is set up. A folder that must never
    be a project (refusal) is not one even if it has a tools config: the AI's tools would reach all of it.
    """
    return tools_config_path(Path(project)).is_file() and refusal(project) is None

def instructions_template() -> str:
    """
    Returns Local Fox's template of the project instructions: the workflow of the tools' own template, with the
    tools named as Local Fox shows them to the model and without what only ollmcp needs.
    """
    return (files(__package__) / "templates" / "instructions.md").read_text(encoding="utf-8")

def _in_folder(value, folder: str):
    """
    Returns a template value with the tools' folder name (.ollmcp-tools) replaced by the project's folder.
    """
    if isinstance(value, str):
        return folder + value[len(LEGACY_FOLDER):] if value == LEGACY_FOLDER or value.startswith(LEGACY_FOLDER + "/") else value
    if isinstance(value, list):
        return [_in_folder(v, folder) for v in value]
    if isinstance(value, dict):
        return {k: _in_folder(v, folder) for k, v in value.items()}
    return value

def _tools_server(folder: str) -> dict:
    """
    Returns the tools' server of the template's .mcp.json, with its config in the project's folder.
    """
    servers = json.loads((files(TOOLS_MODULE) / "templates" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    return _in_folder(next(iter(servers.values())), folder)

def _with_instructions(config: dict, folder: str) -> dict:
    """
    Adds instructions_file to the tools config, after read_only_files: Local Fox gives that file to the model at the
    start of every conversation, so the config says which file it is. Relative to project_dir, as the other paths.
    """
    result = {}
    for key, value in config.items():
        result[key] = value
        if key == "read_only_files":
            result["instructions_file"] = f"{folder}/{INSTRUCTIONS_NAME}"
    result.setdefault("instructions_file", f"{folder}/{INSTRUCTIONS_NAME}")
    return result

def _template_files(folder: str) -> dict[str, bytes]:
    """
    Returns the files of a set-up project, by their path in it: the tools' templates moved into the project's
    folder, with the paths in them changed to match, and Local Fox's instructions.
    """
    templates = files(TOOLS_MODULE) / "templates"
    load = lambda name: json.loads((templates / name).read_text(encoding="utf-8"))
    dump = lambda value: (json.dumps(value, indent=4) + "\n").encode("utf-8")
    gitignore = (f"# Written by Local Fox's setup. Each person's own files are not committed: the AI's plans for their\n"
                 f"# current task, the commands it suggested to them, and their saved conversations.\n"
                 f"context\nsuggested-commands.json\n{CONVERSATIONS}\n")
    instructions = instructions_template().replace(f"{PROJECT_FOLDER}/", f"{folder}/")
    return {
        ".mcp.json": dump({"mcpServers": {"dev": _tools_server(folder)}}),
        f"{folder}/{CONFIG_NAME}": dump(_with_instructions(_in_folder(load("tools-config.json"), folder), folder)),
        f"{folder}/commands.json": (templates / "commands.json").read_bytes(),
        # Commands for common projects to copy into commands.json, which starts empty; nothing reads this file
        f"{folder}/commands-examples.json": (templates / "commands-examples.json").read_bytes(),
        f"{folder}/{INSTRUCTIONS_NAME}": instructions.encode("utf-8"),
        f"{folder}/.gitignore": gitignore.encode("utf-8"),
        f"{folder}/suggested-commands.json": (templates / "suggested-commands.json").read_bytes(),
    }

def _apply(project: Path, write: bool) -> tuple[str, list[str], list[str], list[str]]:
    """
    Creates the project's missing files, or with write False only lists them. Returns the project's folder name, the
    changes to existing files, the files created, and what is already there. The changes are whole sentences, as the
    setup question shows them apart from the created files: they are the surprising part. The only existing file the
    setup changes is .mcp.json: the tools' server is added to it, or, if it names a tools config that does not exist
    (left from an earlier setup), pointed to the new one. Raises SetupRefused.
    """
    reason = refusal(project)
    if reason:
        raise SetupRefused(reason)
    folder_path = project_folder(project)
    folder = folder_path.relative_to(project).as_posix() if folder_path.is_relative_to(project) else PROJECT_FOLDER
    changed, created, skipped = [], [], []
    mcp = project / ".mcp.json"
    mcp_existed = mcp.exists()
    if mcp_existed:
        try:
            data = json.loads(mcp.read_text(encoding="utf-8"))
            servers = data.setdefault("mcpServers", {})
            if not isinstance(servers, dict):
                raise ValueError("'mcpServers' is not an object")
        except (OSError, ValueError, AttributeError) as error:
            raise SetupRefused(f"{mcp.as_posix()} cannot be read ({error}). Fix it, or move it away, and set up again.") from None
        tools_servers = [(key, spec) for key, spec in servers.items() if isinstance(spec, dict) and runs_the_tools(spec)]
        config = (project / folder / CONFIG_NAME).resolve()
        new_config = f"{folder}/{CONFIG_NAME}"
        moved = False
        for key, spec in tools_servers:
            env = spec.get("env") if isinstance(spec.get("env"), dict) else {}
            named = env.get("MCP_TOOLS_CONFIG")
            named_path = (Path(named) if Path(named).is_absolute() else project / named).resolve() if named else None
            if named_path != config:
                # Left from an earlier setup, naming a config that is gone: the server is pointed to the new one
                was = f"names {named}, which does not exist" if named else "names no tools config"
                changed.append(f"The existing .mcp.json has a server for the tools, '{key}', that {was}. It is changed "
                               f"to use {new_config}; the rest of the file stays as it is.")
                if write:
                    spec["env"] = {**env, "MCP_TOOLS_CONFIG": new_config}
                    moved = True
        if not tools_servers:
            key = next(k for k in ("dev", "local-fox-tools", "local-fox-tools-2") if k not in servers)
            changed.append(f"The existing .mcp.json has no server for the tools. The server '{key}' is added to it; "
                           "its other servers stay as they are.")
            if write:
                servers[key] = _tools_server(folder)
                moved = True
        if moved:
            mcp.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
        if not changed:
            skipped.append(".mcp.json")
    for rel, content in _template_files(folder).items():
        if rel == ".mcp.json" and mcp_existed:
            continue  # Handled above
        target = project / rel
        if target.exists():
            skipped.append(rel)
            continue
        created.append(rel + (" (Local Fox's template)" if rel.endswith(INSTRUCTIONS_NAME) else ""))
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    for sub in ("context", CONVERSATIONS):
        path = project / folder / sub
        (skipped if path.is_dir() else created).append(f"{folder}/{sub}/")
        if write:
            path.mkdir(parents=True, exist_ok=True)
    return folder, changed, created, skipped

def plan(project: Path) -> tuple[list[str], list[str]]:
    """
    Returns what setting up the folder would change in existing files, and the files it would create, for the user
    to confirm first. Raises SetupRefused.
    """
    _, changed, created, _ = _apply(Path(project).resolve(), write=False)
    return changed, created

def planned(project: Path) -> list[str]:
    """
    Returns all that setting up the folder would change or create, the changes first; empty if nothing is missing.
    """
    changed, created = plan(project)
    return changed + created

def set_up(project: Path) -> str:
    """
    Sets up a folder as a project, and returns a report of what it created and kept, and what to do next. It is
    only ever done on the user's explicit request, after they confirmed what planned() lists. Raises SetupRefused
    for a system folder, and RuntimeError if Local Fox has no bundled tools.

    The files are those of the tools' own init ('python -m ollmcp_tools init'), in .local-fox instead of
    .ollmcp-tools, with Local Fox's instructions instead of the tools' ones (which are written for ollmcp and name
    the tools with its dev_ prefix), and Local Fox's next steps instead of ollmcp's. The tools take their folder
    from .mcp.json, so its name is free. A project set up earlier keeps its folder: its missing files are created
    there. Existing files are not changed, except that a .mcp.json without the tools' server gets it added.
    """
    if not bundled_version():
        raise RuntimeError("This Local Fox was built without its tools (ollmcp-generic-tools). Build it again with "
                           "build.ps1, or install the tools into the environment it runs in.")
    from ollmcp_tools.locations import USER_CONFIG_PATH, default_backup_folder
    project = Path(project).resolve()
    folder, changed, created, skipped = _apply(project, write=True)
    if USER_CONFIG_PATH.exists():
        skipped.append(str(USER_CONFIG_PATH))
    else:
        USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        USER_CONFIG_PATH.write_text(json.dumps({"ollama_api_key": None}, indent=4) + "\n", encoding="utf-8")
        created.append(str(USER_CONFIG_PATH))

    config_path = tools_config_path(project)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        project_dir = (config_path.parent / config["project_dir"]).resolve()
        backups = (project_dir / config["backup_folder"]).resolve() if config.get("backup_folder") else default_backup_folder(project_dir)
        (skipped if backups.is_dir() else created).append(f"{backups} (backup folder)")
        backups.mkdir(parents=True, exist_ok=True)
        dir_line = (f"{folder}/{CONFIG_NAME}: 'project_dir' is \"{config['project_dir']}\", relative to the config's own folder: "
                    f"{project_dir.as_posix()}. The tools can access this folder, and every other path in the config is relative "
                    f"to it. Add the folders and files the AI must never access to 'forbidden_paths' (or use \"Forbid\" in the filemap's menu).")
    except (OSError, ValueError, KeyError, TypeError) as error:
        dir_line = f"{folder}/{CONFIG_NAME} cannot be read ({error}). Fix it before the tools can start."

    lines = [f"Set up {project.as_posix()}:", ""]
    lines += [f"Changed: {change}" for change in changed] + ([""] if changed else [])
    lines += [f"- Created: {path}" for path in created]
    lines += [f"- Already there, not changed: {path}" for path in skipped]
    lines += ["", "Next:", "",
              f"1. {dir_line}",
              f"2. {folder}/commands.json is empty, so the AI can run no commands yet: copy the ones that fit this project "
              f"from {folder}/commands-examples.json (which nothing reads) and change them as needed.",
              f"3. {folder}/{INSTRUCTIONS_NAME}: adapt the instructions to the project, e.g. its documents at the end, and remove "
              "the tools you disable. Local Fox gives them to the model at the start of every session.",
              f"4. Web search: add your Ollama API key to {USER_CONFIG_PATH}. Without one the web tools are not offered.",
              f"5. Commit .mcp.json and {folder}; its .gitignore leaves out each person's own files (plans, suggested "
              "commands, saved conversations). Others who clone the project set it up again to get those folders."]
    return "\n".join(lines)
