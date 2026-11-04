"""
The conversation as data: the entries of the feed. Each entry is something the user sees; what goes to the
model is kept separately in Session.messages, and every part of it has an entry.
"""
from dataclasses import dataclass, field
from itertools import count

_ids = count(1)

# Kinds of entries
INSTRUCTIONS = "instructions"  # The project instructions given as the system message
FILEMAP = "filemap"            # The filemap given after the instructions
TOOLS = "tools"                # The tool definitions sent with every request
USER = "user"                  # A message of the user
ASSISTANT = "assistant"        # A reply of the model, with its thinking
TOOL = "tool"                  # A tool call and its response
NOTICE = "notice"              # A message of Local Fox to the user, never sent to the model
ERROR = "error"                # An error of Local Fox, Ollama or a server, never sent to the model

# States of a tool call
WAITING, RUNNING, OK, FAILED, SKIPPED, STOPPED = "waiting", "running", "ok", "error", "skipped", "stopped"

@dataclass
class Entry:
    kind: str
    text: str = ""            # The message, the instructions, the filemap or the notice
    title: str = ""           # The one-line title of a collapsible entry
    thinking: str = ""        # The model's thinking, for an assistant entry
    streaming: bool = False   # Whether an assistant entry is still arriving
    stop_reason: str = ""     # Why an assistant reply ended, as Ollama reports it ('stop', 'length'), or 'stopped' by the user
    tool: str = ""            # The tool name as the model called it
    resolved: str = ""        # The tool's own name, if the call named a known tool
    server: str = ""          # The key of the server that has the tool
    args: object = None       # The call's arguments, a dict, or the raw text if they were not valid JSON
    response: str = ""        # The tool response the model got
    images: list = field(default_factory=list)  # Images of a tool response: (base64 data, MIME type)
    status: str = ""          # The state of a tool call
    actions: list = field(default_factory=list)  # Buttons of a notice, e.g. ["Clear", "Export", "Larger context"]
    # Whether a collapsible entry is collapsed: a tool call's arguments and response, an assistant entry's
    # thinking, or the body of the instructions, filemap and tools. The session keeps the latest open.
    collapsed: bool = True
    answer_collapsed: bool = False  # Whether an assistant entry's answer shows only its first lines
    removed: bool = False     # Whether a tool call's response was replaced with a note to make room in the context
    id: int = field(default_factory=lambda: next(_ids))

def main_argument(args) -> str:
    """
    Returns the argument that tells best what a tool call does, for its one-line title: the path, the
    command, the query or the pattern, or else the first short text argument.
    """
    if not isinstance(args, dict):
        return ""
    for key in ("path", "source", "command_name", "query", "pattern", "url", "section"):
        if args.get(key):
            value = str(args[key])
            if key == "source" and args.get("destination"):
                value += f" → {args['destination']}"
            return value
    for value in args.values():
        if isinstance(value, str) and value and len(value) <= 80 and "\n" not in value:
            return value
    return ""

# The tools whose responses show a file as it was when they ran, and the tools that change a file. The tools' own
# names: an entry's `resolved` name, whatever prefix the model used.
READING_TOOLS = {"read_file_with_metadata", "read_image", "get_outline"}
CHANGING_TOOLS = {"write_file", "edit_file", "move_file", "delete_file", "restore_file"}

def _path(args) -> str:
    """
    Returns a tool call's path in one spelling (forward slashes, no leading ./), or "" if it has none.
    """
    if not isinstance(args, dict):
        return ""
    path = str(args.get("path") or args.get("source") or "").replace("\\", "/").rstrip("/")
    while path.startswith("./"):
        path = path[2:]
    return path

def superseded(tool_entries: list[Entry]) -> set[int]:
    """
    Returns the positions of the tool calls whose responses are out of date: a read of a file (or its outline) that
    a later successful call read again with the same arguments, or that a later successful call changed. These are
    the first to go when the context must make room, as the model has newer content or the file is no longer like
    that. Deciding this from the calls needs no judgment from the model.
    """
    result = set()
    for k, entry in enumerate(tool_entries):
        name, path = entry.resolved or entry.tool, _path(entry.args)
        if name not in READING_TOOLS or not path:
            continue
        for later in tool_entries[k + 1:]:
            later_name = later.resolved or later.tool
            if later.status != OK:
                continue  # A failed call neither shows the file again nor changes it
            if (later_name == name and later.args == entry.args) or (later_name in CHANGING_TOOLS and _path(later.args) == path):
                result.add(k)
                break
    return result

def call_text(name: str, args) -> str:
    """
    Returns a tool call as the model can repeat it, read_file_with_metadata(path='cart.py', section='Cart.total'),
    for the note that replaces its response; "" if its arguments are not a dict or too long to repeat usefully.
    """
    if not isinstance(args, dict) or any(isinstance(value, str) and "\n" in value for value in args.values()):
        return ""  # A call with file content in it is not one to repeat
    text = f"{name}({', '.join(f'{key}={value!r}' for key, value in args.items())})"
    return text if len(text) <= 200 else ""

def format_number(n: int) -> str:
    """
    Formats a number with spaces between the thousands: 32 000.
    """
    return f"{n:,}".replace(",", " ")

def estimate_tokens(text: str) -> int:
    """
    Estimates the number of tokens of a text, about four characters per token.
    """
    return (len(text) + 3) // 4
