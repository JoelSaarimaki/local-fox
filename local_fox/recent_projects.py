"""
The recent projects, latest first, for the start dialog: recent-projects.json in the folder of config.json, next
to the prompt history. It is state that Local Fox keeps, not a setting. It starts from the project_dir setting of
earlier versions, the project opened last.
"""
import json
from pathlib import Path

from .config import config_path, data_dir

SIZE = 15

def _path() -> Path:
    return data_dir() / "recent-projects.json"

def load() -> list[Path]:
    """
    Returns the recent projects, latest first, also those that no longer exist (the dialog shows them as missing).
    """
    try:
        values = json.loads(_path().read_text(encoding="utf-8"))
        return [Path(v) for v in values if isinstance(v, str)][:SIZE]
    except (OSError, ValueError):
        pass
    try:  # The project opened last in an earlier version
        value = json.loads(config_path().read_text(encoding="utf-8")).get("project_dir")
        return [Path(value)] if isinstance(value, str) and value else []
    except (OSError, ValueError, AttributeError):
        return []

def _save(projects: list[Path]) -> None:
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps([str(p) for p in projects[:SIZE]], indent=4), encoding="utf-8")
    except OSError:
        pass  # The list is a convenience; a project opens without it

def add(project: Path) -> None:
    """
    Puts a project first in the list.
    """
    project = Path(project).resolve()
    _save([project] + [p for p in load() if p.resolve() != project])

def remove(project: Path) -> None:
    project = Path(project).resolve()
    _save([p for p in load() if p.resolve() != project])
