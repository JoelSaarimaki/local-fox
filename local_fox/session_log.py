"""
The log of a run of Local Fox: logs/session.jsonl next to config.json, one JSON object per line, written as it
happens. Every start of the application empties the logs folder (reset), so the log always holds the run
that is on screen, ready to look at when something goes wrong; each conversation of the run (the start, every
clear) begins with a "conversation" line. The servers' own output is in logs/server-<key>.log of the same run.

The export is a readable copy of the feed that the user saves on purpose. The log holds what the feed does not
show: the exact requests to Ollama (each request lists the messages added since the one before, so the whole
history is the system message plus the messages of the requests), the token counts and durations of the
replies, the decisions of the user, and the servers that started or failed. A log that cannot be written never
stops the session.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

from . import __version__

LOG_NAME = "session.jsonl"

def reset(folder: Path) -> None:
    """
    Empties the logs folder at the start of the application: the session log, the servers' logs, and the
    per-conversation logs of earlier versions. A file another running Local Fox still writes is left as it is.
    """
    if not folder.is_dir():
        return
    for path in [folder / LOG_NAME, *folder.glob("server-*.log")]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    shutil.rmtree(folder / "sessions", ignore_errors=True)

class SessionLog:
    def __init__(self, path: Path):
        self.path = path
        self.error: str | None = None  # Why the log is not written, shown once
        self._failed = False

    def begin(self, **fields) -> None:
        """
        Marks the start of a conversation, with what it runs with.
        """
        self.write("conversation", version=__version__, **fields)

    def write(self, event: str, **fields) -> None:
        """
        Appends one event. The file is opened for each line, so that every line is on disk at once, also when
        the application stops unexpectedly.
        """
        if self._failed:
            return
        line = json.dumps({"time": datetime.now().isoformat(timespec="milliseconds"), "event": event, **fields},
                          ensure_ascii=False, default=str)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as file:
                file.write(line + "\n")
        except OSError as error:
            self.error = f"The log cannot be written to {self.path.as_posix()}: {error}"
            self._failed = True
