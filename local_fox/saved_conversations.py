"""
Saved conversations: each conversation of a project is saved as it goes, after every turn and when the session
closes, in the conversations folder of the project's Local Fox folder (.local-fox/conversations), so that it can
be reopened later, also after Local Fox was closed.

A saved conversation holds the feed's entries and the messages the model got, except the system message and the
instructions, filemap and tools entries: a reopened conversation gets them as they are when it is reopened, as a
new conversation does. The folder is forbidden to the tools, as the rest of the Local Fox folder, and has a
.gitignore of its own, as conversations are each person's own and hold the project's code.
"""
import dataclasses
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import conversation as c
from .conversation import Entry
from .filemap import project_folder

FOLDER = "conversations"
FORMAT = 1
# Entries that a new or reopened conversation makes again from the project as it is then
NOT_SAVED = (c.INSTRUCTIONS, c.FILEMAP, c.TOOLS)
_ENTRY_FIELDS = {f.name for f in dataclasses.fields(Entry)} - {"id"}

@dataclass
class Saved:
    path: Path
    title: str
    created: str
    updated: str
    model: str
    messages: int

def folder(project: Path) -> Path:
    return project_folder(project) / FOLDER

def new_path(project: Path) -> Path:
    """
    Returns the path for a new saved conversation, named by its start time.
    """
    base = folder(project)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path, number = base / f"{stamp}.json", 2
    while path.exists():
        path, number = base / f"{stamp}-{number}.json", number + 1
    return path

def title_of(entries: list[Entry]) -> str:
    """
    Returns a conversation's title: the first line of its first prompt.
    """
    first = next((e.text for e in entries if e.kind == c.USER and e.text.strip()), "")
    line = first.strip().splitlines()[0] if first.strip() else "(no prompt)"
    return line if len(line) <= 80 else line[:79] + "…"

def save(path: Path, entries: list[Entry], messages: list[dict], model: str, num_ctx: int, created: str) -> None:
    """
    Writes a conversation. It is written to a temporary file first, so that a failure never leaves half a file.
    Raises OSError if it cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ignore = path.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# Saved conversations are each person's own and hold the project's code\n*\n", encoding="utf-8")
    # From the first prompt on: what comes before it (server errors, the setup report) belongs to the session's start
    first = next((i for i, e in enumerate(entries) if e.kind == c.USER), len(entries))
    data = {
        "format": FORMAT, "title": title_of(entries), "created": created,
        "updated": datetime.now().isoformat(timespec="seconds"), "model": model, "num_ctx": num_ctx,
        "entries": [_entry_dict(e) for e in entries[first:] if e.kind not in NOT_SAVED],
        "messages": [m for m in messages if m.get("role") != "system"],
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temporary, path)

def _entry_dict(entry: Entry) -> dict:
    data = dataclasses.asdict(entry)
    del data["id"]  # Ids are given again when it is reopened
    data["streaming"] = False
    return data

def load(path: Path) -> tuple[dict, list[Entry], list[dict]]:
    """
    Reads a saved conversation: its details, entries and messages. Raises ValueError if it cannot be read.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [Entry(**{k: v for k, v in item.items() if k in _ENTRY_FIELDS}) for item in data["entries"]]
        messages = list(data["messages"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"{path.as_posix()} cannot be read: {error}") from None
    for entry in entries:
        entry.images = [tuple(image) for image in entry.images]
    return data, entries, messages

def list_saved(project: Path) -> list[Saved]:
    """
    Returns the project's saved conversations, the latest first. Files that cannot be read are left out.
    """
    saved = []
    base = folder(project)
    for path in base.glob("*.json") if base.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            saved.append(Saved(path, data.get("title", ""), data.get("created", ""), data.get("updated", ""),
                               data.get("model", ""), len(data.get("messages", []))))
        except (OSError, ValueError, AttributeError):
            continue
    return sorted(saved, key=lambda s: s.updated, reverse=True)
