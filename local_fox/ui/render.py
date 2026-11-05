"""
The feed's entries as HTML: Markdown rendered with the feed's style, and long entries collapsed to one line
that expands when clicked (a link 'toggle:<entry id>').
"""
import difflib
import html
import json
import re

import markdown

from .. import conversation as c
from ..conversation import Entry, main_argument
from .theme import DIM, TEXT

_md = markdown.Markdown(extensions=["fenced_code", "tables", "sane_lists"])
# Raw HTML in the model's text is shown as text, not rendered
_md.preprocessors.deregister("html_block")
_md.inlinePatterns.deregister("html")

STATUS_MARKS = {
    c.WAITING: ("…", "dim"), c.RUNNING: ("⋯", "accent"), c.OK: ("✓", "ok"), c.FAILED: ("✗", "error"),
    c.SKIPPED: ("skipped", "dim"), c.STOPPED: ("stopped", "dim"),
}

def md(text: str) -> str:
    """
    Renders Markdown as HTML for the feed.
    """
    _md.reset()
    return _md.convert(text)

def esc(text: str) -> str:
    return html.escape(text, quote=True)

def pre(text: str, cls: str = "") -> str:
    return f"<pre{f' class={cls}' if cls else ''}>{esc(text)}</pre>"

def toggle(entry: Entry, label: str, extra: str = "", color: str = DIM) -> str:
    arrow = "▸" if entry.collapsed else "▾"
    return f'<a href="toggle:{entry.id}" style="color: {color}">{arrow} {label}</a>{extra}'

def _diff(old: str, new: str) -> str:
    lines = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=2):
        if line.startswith(("---", "+++")):
            continue
        cls = "added" if line.startswith("+") else "removed" if line.startswith("-") else "dim" if line.startswith("@@") else ""
        lines.append(f"<span class={cls}>{esc(line)}</span>" if cls else esc(line))
    return "<pre>" + "\n".join(lines) + "</pre>"

def _arguments(entry: Entry) -> str:
    args = entry.args
    if not isinstance(args, dict):
        return "<p class=dim>Arguments (not a JSON object):</p>" + pre(str(args))
    tool = entry.resolved or entry.tool
    if tool == "edit_file" and isinstance(args.get("old_text"), str) and isinstance(args.get("new_text"), str):
        rest = {k: v for k, v in args.items() if k not in ("old_text", "new_text")}
        return (f"<p class=dim>Arguments: {esc(json.dumps(rest, ensure_ascii=False))}</p>"
                f"<p class=dim>Changed lines:</p>{_diff(args['old_text'], args['new_text'])}")
    long = {k: v for k, v in args.items() if isinstance(v, str) and ("\n" in v or len(v) > 200)}
    short = {k: v for k, v in args.items() if k not in long}
    parts = [f"<p class=dim>Arguments: {esc(json.dumps(short, ensure_ascii=False))}</p>"] if short or not long else []
    for key, value in long.items():
        parts.append(f"<p class=dim>{esc(key)}:</p>{pre(value)}")
    return "".join(parts)

def _response(entry: Entry, heading: str = "Response:") -> str:
    text = entry.response
    if not text:
        return ""
    stripped = text.lstrip()
    body = pre(text) if stripped.startswith(("{", "[")) or entry.status in (c.SKIPPED, c.STOPPED) else md(text)
    images = f"<p class=dim>{len(entry.images)} image{'s' if len(entry.images) > 1 else ''} in the response</p>" if entry.images else ""
    return f"<p class=dim>{esc(heading)}</p>{body}{images}"

PREVIEW_LINES = 2       # The lines a collapsed answer shows
PREVIEW_LINE_CHARS = 160

def _answer(entry: Entry) -> str:
    """
    Returns an answer: whole when it is open or short, otherwise its first lines as plain text with a link
    that opens it. A long open answer that has ended gets a link that collapses it again.
    """
    lines = [line for line in entry.text.strip().splitlines() if line.strip()]
    short = len(lines) <= PREVIEW_LINES and all(len(line) <= PREVIEW_LINE_CHARS for line in lines)
    if short:
        return md(entry.text)
    if entry.answer_collapsed:
        # Plain text: the emphasis, code and heading marks of Markdown left out
        plain = [re.sub(r"^#+\s*|\*\*|__|`", "", line.strip()) for line in lines[:PREVIEW_LINES]]
        preview = "<br>".join(esc(line[:PREVIEW_LINE_CHARS] + ("…" if len(line) > PREVIEW_LINE_CHARS else "")) for line in plain)
        more = len(lines) - PREVIEW_LINES
        link = f'<a href="answer:{entry.id}" style="color: {DIM}">▸ {more} more line{"s" if more != 1 else ""}</a>' if more > 0 \
            else f'<a href="answer:{entry.id}" style="color: {DIM}">▸ all</a>'
        return f"<p class=dim>{preview}<br>{link}</p>"
    html = md(entry.text)
    if not entry.streaming:
        html += f'<p><a href="answer:{entry.id}" style="color: {DIM}">▾ collapse</a></p>'
    return html

def entry_html(entry: Entry) -> str:
    """
    Returns the HTML of one entry, or '' for an entry that shows nothing.
    """
    if entry.kind == c.USER:
        text = esc(entry.text).replace("\n", "<br>")
        return f"<p class=user><span class=mark>›</span> {text}</p>"
    if entry.kind == c.ASSISTANT:
        parts = []
        if entry.thinking:
            words = len(entry.thinking.split())
            parts.append(f"<p class=dim>{toggle(entry, f'Thinking ({words} words)')}</p>")
            if not entry.collapsed:
                parts.append(f"<p class=thinking>{esc(entry.thinking).replace(chr(10), '<br>')}</p>")
        if entry.text:
            parts.append(_answer(entry))
        elif entry.streaming and not entry.thinking:
            parts.append("<p class=dim>…</p>")
        if entry.stop_reason == "stopped" and not entry.text:
            # A reply with text carries its mark in the text, as the model gets it
            parts.append("<p class=dim>(stopped before the reply began; nothing of it goes to the model)</p>")
        if entry.stop_reason == c.REPEATING and not entry.text:
            parts.append("<p class=dim>(stopped while thinking, as it kept repeating the same lines; nothing of it goes to the model)</p>")
        return "".join(parts)
    if entry.kind == c.TOOL:
        mark, cls = STATUS_MARKS.get(entry.status, ("", "dim"))
        target = main_argument(entry.args)
        line = toggle(entry, esc(entry.tool), (f"&nbsp;&nbsp;<span class=dim>{esc(target)}</span>" if target else "")
                      + f"&nbsp;&nbsp;<span class={cls}>{mark}</span>", TEXT)
        if entry.resolved and entry.resolved != entry.tool:
            line += f"&nbsp;&nbsp;<span class=dim>(ran {esc(entry.server)}:{esc(entry.resolved)})</span>"
        if entry.removed:
            line += "&nbsp;&nbsp;<span class=dim>(response removed from the model's context)</span>"
        elif entry.shortened:
            line += "&nbsp;&nbsp;<span class=dim>(response shortened in the model's context)</span>"
        if entry.collapsed:
            return f"<p>{line}</p>"
        if entry.shortened and not entry.removed:
            # What the model has now, and below it the response as the tool gave it
            return (f"<p>{line}</p>{_arguments(entry)}<p class=dim>Response as the model has it now, shortened to make room:</p>"
                    f"{md(entry.shortened)}{_response(entry, 'The whole response, as the tool gave it:')}")
        return f"<p>{line}</p>{_arguments(entry)}{_response(entry)}"
    if entry.kind in (c.INSTRUCTIONS, c.FILEMAP, c.TOOLS):
        line = f"<p class=dim>{toggle(entry, esc(entry.title))}</p>"
        if entry.collapsed:
            return line
        return line + (pre(entry.text) if entry.kind == c.FILEMAP else md(entry.text))
    if entry.kind == c.NOTICE:
        # Line breaks and spaces kept, for the help and the setup report
        return f'<p class=notice style="white-space: pre-wrap">· {esc(entry.text)}</p>'
    if entry.kind == c.ERROR:
        return f"<p class=error>✗ {esc(entry.text).replace(chr(10), '<br>')}</p>"
    return ""
