"""
The settings of Local Fox, all in one config.json.

The file is in the user's application data folder (%APPDATA%/LocalFox/config.json on Windows), or where
the LOCAL_FOX_CONFIG environment variable points. It is created with the defaults on the first start, and
settings missing from it get their defaults, so that an older file keeps working after an update. The
settings view of the window edits the same file; DESCRIPTIONS are its help texts.
"""
import json
import os
import sys
from pathlib import Path

DEFAULTS = {
    "ollama_host": "http://127.0.0.1:11434",
    "ollama_api_key": "",
    # flint:40b is preferred: its calls by bare tool names, which ollmcp skips, run in Local Fox (see AGENTS.md)
    "model": "flint:40b",
    "num_ctx": 65536,
    "think": None,
    "temperature": None,
    "seed": None,
    "max_steps": None,
    "auto_approve_tools": [],
    "filemap_for_model": True,
    "filemap_max_tokens": 2000,
    "prompt_full_paths": False,
    "font_size": 10,
    "bundled_tools": True,
}
# Settings that are secrets: never written to the log or shown as they are (without_secrets)
SECRETS = {"ollama_api_key"}
# Settings of earlier versions, dropped when a file still has them instead of being unknown
REMOVED = {"session_log", "session_log_days", "tool_calls_collapsed", "project_dir"}

# What each setting does, shown in the settings view. Every setting of DEFAULTS has one.
DESCRIPTIONS = {
    "ollama_host": "Where Ollama runs.",
    "ollama_api_key": "Your Ollama API key, for the web tools (web search and fetch through ollama.com); empty: the web tools are "
                      "not offered. Create one at ollama.com/settings/keys. It is given only to the tools' server, never to the "
                      "model, and applies when the tools restart (\"Restart tools and clear\", or opening the project again).",
    "model": "The model, as Ollama names it. Also set by the model menu.",
    "num_ctx": "The context size in tokens, sent with every request. Ollama uses 4 096 tokens when none is sent. A larger "
               "context needs little memory with flint:40b, but moves more of the model from the GPU to the CPU.",
    "think": "Thinking: empty turns it on for models that can think, true or false forces it, and a level such as "
             "low or high is passed on to models that take one.",
    "temperature": "The model's temperature; empty uses the model's own.",
    "seed": "A fixed seed for repeatable runs; empty for none.",
    "max_steps": "The model requests in one turn before Local Fox asks whether to continue; empty for no limit.",
    "auto_approve_tools": "Tools that run without asking for approval, separated by commas, e.g. read_file_with_metadata, get_outline.",
    "filemap_for_model": "Whether the filemap is given to the model at the start of a session.",
    "filemap_max_tokens": "The largest filemap in tokens; a larger project is shown to the model with its folders only.",
    "prompt_full_paths": "Whether \"Add path to prompt\" inserts full paths instead of paths relative to the project folder.",
    "font_size": "The font size of the window in points, from the next start.",
    "bundled_tools": "Whether a server of .mcp.json that starts the tools with 'python -m ollmcp_tools' runs Local Fox's "
                     "own copy of them. Off, it runs with the Python of .mcp.json, e.g. to try a development version of the tools.",
}

def config_path() -> Path:
    """
    Returns the path of config.json.
    """
    if os.environ.get("LOCAL_FOX_CONFIG"):
        return Path(os.environ["LOCAL_FOX_CONFIG"]).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "LocalFox" / "config.json"

def data_dir() -> Path:
    """
    Returns the folder of config.json, where the logs of the MCP servers and the prompt history are kept too.
    """
    return config_path().parent

class ConfigError(Exception):
    pass

def _whole(value, smallest: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= smallest

def validate(values: dict, where: str = "config.json") -> dict:
    """
    Checks the settings and returns them with the missing ones set to their defaults. Unknown settings are an
    error, so that a mistyped setting is not silently ignored. Raises ConfigError with what is wrong.
    """
    if not isinstance(values, dict):
        raise ConfigError(f"{where} must contain a JSON object.")
    values = {key: value for key, value in values.items() if key not in REMOVED}
    unknown = sorted(set(values) - set(DEFAULTS))
    if unknown:
        raise ConfigError(f"{where}: unknown settings {unknown}. The settings are: {sorted(DEFAULTS)}.")
    config = {**DEFAULTS, **values}
    if not _whole(config["num_ctx"], 512):
        raise ConfigError(f"{where}: 'num_ctx' must be a whole number of at least 512.")
    if config["max_steps"] is not None and not _whole(config["max_steps"], 1):
        raise ConfigError(f"{where}: 'max_steps' must be a whole number of at least 1, or null.")
    if config["seed"] is not None and not _whole(config["seed"], 0):
        raise ConfigError(f"{where}: 'seed' must be a whole number, or null.")
    if config["temperature"] is not None and (isinstance(config["temperature"], bool) or not isinstance(config["temperature"], (int, float))):
        raise ConfigError(f"{where}: 'temperature' must be a number, or null.")
    if config["think"] is not None and not isinstance(config["think"], (bool, str)):
        raise ConfigError(f"{where}: 'think' must be true, false, a level such as \"high\", or null.")
    if not isinstance(config["auto_approve_tools"], list) or not all(isinstance(t, str) for t in config["auto_approve_tools"]):
        raise ConfigError(f"{where}: 'auto_approve_tools' must be a list of tool names.")
    for key in ("filemap_for_model", "prompt_full_paths", "bundled_tools"):
        if not isinstance(config[key], bool):
            raise ConfigError(f"{where}: '{key}' must be true or false.")
    if not _whole(config["filemap_max_tokens"], 1):
        raise ConfigError(f"{where}: 'filemap_max_tokens' must be a whole number of at least 1.")
    if not _whole(config["font_size"], 6):
        raise ConfigError(f"{where}: 'font_size' must be a whole number of at least 6.")
    if not isinstance(config["ollama_api_key"], str):
        raise ConfigError(f"{where}: 'ollama_api_key' must be a string; empty for none.")
    for key in ("ollama_host", "model"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ConfigError(f"{where}: '{key}' must be a non-empty string.")
    return config

def without_secrets(settings: dict) -> dict:
    """
    Returns the settings with the secrets replaced by whether they are set, for the log: a key in the log could be
    read by anyone the log is shown to, e.g. with an issue.
    """
    return {key: ("(set)" if value else "") if key in SECRETS else value for key, value in settings.items()}

def load(path: Path | None = None) -> dict:
    """
    Loads config.json, creating it with the defaults if it does not exist.
    """
    path = path or config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULTS, indent=4), encoding="utf-8")
        return dict(DEFAULTS)
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from error
    return validate(values, str(path))

def save(values: dict, path: Path | None = None) -> dict:
    """
    Writes all the settings to config.json, in the order of DEFAULTS, after checking them. Returns them.
    """
    path = path or config_path()
    config = validate(values, "The settings")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: config[key] for key in DEFAULTS}, indent=4), encoding="utf-8")
    return config

def save_setting(key: str, value, path: Path | None = None) -> None:
    """
    Changes one setting in config.json, keeping the others as they are in the file.
    """
    path = path or config_path()
    try:
        values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else dict(DEFAULTS)
    except json.JSONDecodeError:
        return  # A broken file is left for the user to fix; load() reports it
    values[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=4), encoding="utf-8")
