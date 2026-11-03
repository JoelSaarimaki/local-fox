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
