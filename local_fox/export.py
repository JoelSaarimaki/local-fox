"""
The conversation as a Markdown file: the messages, the instructions and filemap, the tool calls with their
arguments and responses, and the model's thinking.
"""
import json
from datetime import datetime

from . import APP_NAME, __version__
from . import conversation as c
from .conversation import Entry, main_argument

def _fence(text: str, language: str = "") -> str:
    """
    Returns the text in a code fence longer than any backtick run in it.
    """
    longest, run = 0, 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"

def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())

def entry_markdown(entry: Entry) -> str:
    if entry.kind == c.USER:
        return f"## User\n\n{entry.text}"
    if entry.kind == c.ASSISTANT:
        if not (entry.text or entry.thinking):
            return ""
        parts = ["## Assistant"]
        if entry.thinking:
            parts.append("**Thinking**\n\n" + _quote(entry.thinking))
        if entry.text:
            parts.append(entry.text)
        if entry.stop_reason == "stopped":
            parts.append("*(stopped by the user)*")
        return "\n\n".join(parts)
    if entry.kind == c.TOOL:
        target = main_argument(entry.args)
        args = json.dumps(entry.args, indent=2, ensure_ascii=False) if isinstance(entry.args, dict) else str(entry.args)
        return (f"### Tool call: {entry.tool}{' ' + target if target else ''} ({entry.status})\n\n"
                f"Arguments:\n\n{_fence(args, 'json')}\n\nResponse:\n\n{_fence(entry.response)}")
    if entry.kind in (c.INSTRUCTIONS, c.FILEMAP, c.TOOLS):
        return f"## {entry.title}\n\n{_fence(entry.text, 'markdown' if entry.kind != c.FILEMAP else 'text')}"
    if entry.kind == c.NOTICE:
        return f"*{APP_NAME}: {entry.text}*"
    if entry.kind == c.ERROR:
        return f"**Error:** {entry.text}"
    return ""

def to_markdown(entries: list[Entry], model: str, project: str) -> str:
    header = (f"# Conversation\n\n{APP_NAME} {__version__}, model {model}, project {project}, "
              f"exported {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return "\n\n".join([header, *filter(None, (entry_markdown(e) for e in entries))]) + "\n"
