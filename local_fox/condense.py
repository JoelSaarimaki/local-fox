"""
Shortening old tool responses to make room in the context, before removing them whole (Session._fit_context).

A read keeps the parts of the file that the conversation has mentioned since the read: the functions and classes
named in the model's thinking and replies, in the arguments of its later calls, or in the user's messages. Every other
part is left out, its first line kept (the signature, a heading) with a marker of the lines left out, so the model
still sees what the file has and where, and reads a part again by its section or lines if it needs the exact text. A
Markdown document is kept whole if its path was mentioned since, and otherwise as its headings. A text search keeps
the matches in files mentioned since; the other files keep a line with their match lines.

The choice comes from what was written and called, never from asking the model (see AGENTS.md). Replayed on a
logged session (2026-11-05), keeping the parts named since lost about 28 % less of what the model mentioned later,
per token freed, than removing whole responses. Broader rules (any word the model repeated, the words of the user's
prompt) kept much more without losing less. Only the current conversation counts: the session's entries start
again at "Clear".

The files are split with the tools' own outlines (ollmcp_tools.parsing, which reads no configuration and no files).
A partial read of a Python file often does not parse (it starts inside a class, or ends inside a function), and is
then split at its def and class lines.
"""
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ollmcp_tools import parsing

from . import conversation as c

# A response is shortened only if this saves at least this many characters and this share of it: shortening changes
# the start of the conversation, which makes Ollama process the whole prompt again
MIN_SAVED_CHARS = 400
MIN_SAVED_SHARE = 0.2
SHORTEN_TOOLS = {"read_file_with_metadata", "search_text_in_files"}
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LINES = re.compile(r"^- Lines: (\d+)-(\d+) of \d+", re.MULTILINE)
_PY_HEADER = re.compile(r"^(\s*)(?:async\s+def|def|class)\s+(\w+)")
_MATCH = re.compile(r"^(.+?):(\d+):")
MARK = "⋯"
_BLOCK_PARTS = {"docstring", "imports", "assignments", "statements", "main block"}

@dataclass
class Part:
    """
    A part of a read: a function, a class's own lines, a block of statements or a heading's text, with its lines in
    the file (first and last, inclusive), the names that keep it, and its section name for reading it again.
    """
    start: int
    end: int
    names: set[str] = field(default_factory=set)
    section: str = ""
    label: str = ""  # What a part without a first line to keep is, e.g. "imports, assignments: A, B"
    keep_head: bool = True  # Whether its first line stays (a signature, a heading) when the rest is left out
    always: bool = False  # Kept whatever is mentioned, e.g. a class's lines after its last method

def words(text: str) -> set[str]:
    """
    Returns the identifiers of a text: "ValueCard.set_global_to_local()" has ValueCard and set_global_to_local.
    """
    return set(_WORD.findall(text))

def _path_mentioned(path: str, text: str) -> bool:
    """
    Checks if a text names a file: by its path, or by its name with an extension (app.py; not "app").
    """
    path = path.replace("\\", "/").removeprefix("./")
    name = PurePosixPath(path).name
    return bool(path) and (path in text or ("." in name and re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", text) is not None))

def _split_response(text: str) -> tuple[str, str, list[str], str] | None:
    """
    Splits a tool response into the lines before its code block, the fence line, the block's lines and what follows
    the block; None if it has no closed code block.
    """
    lines = text.split("\n")
    opening = next((i for i, line in enumerate(lines) if line.startswith("```")), None)
    if opening is None:
        return None
    closing = next((i for i in range(len(lines) - 1, opening, -1) if lines[i].startswith("```")), None)
    if closing is None:
        return None
    return "\n".join(lines[:opening]), lines[opening], lines[opening + 1:closing], "\n".join(lines[closing:])

def _outline_parts(code: list[str], first: int, suffix: str) -> list[Part] | None:
    """
    Returns the parts of a read from the tools' outline of its text; None if it does not outline (e.g. a partial
    read of a Python file).
    """
    outline = parsing.outline_text("\n".join(code), suffix)
    if outline is None or outline.error or not outline.sections:
        return None
    shift = first - 1
    parts = []

    def leaves(sections, parents):
        for s in sections:
            path = parents + (s,)
            qualified = parsing.qualified_name(path)
            if s.kind == "block":
                names = {n for n in s.parts if n not in _BLOCK_PARTS}
                parts.append(Part(s.start + shift, s.end + shift, names, "", s.name, keep_head=False))
            elif s.children:
                first_child = min(child.start for child in s.children)
                names = {s.name} if s.kind != "heading" else set()
                parts.append(Part(s.start + shift, first_child - 1 + shift, names, qualified))
                leaves(s.children, path)
                last_end = max(child.end for child in s.children)
                if last_end < s.end:
                    parts.append(Part(last_end + 1 + shift, s.end + shift, always=True))
            else:
                names = {s.name} if s.kind != "heading" else set()
                if s.name.startswith("__") and parents:
                    names = {parents[-1].name}  # __init__ and the like go with their class
                parts.append(Part(s.start + shift, s.end + shift, names, qualified))

    leaves(outline.sections, ())
    return parts

def _python_parts(code: list[str], first: int) -> list[Part]:
    """
    Splits a Python read that does not parse at its def and class lines (with their decorators): each part runs to
    the next such line. The lines before the first are a part of their own, as are lines that only continue a part
    the read began inside of. Methods get their class's name when the class line is in the read.
    """
    starts = []
    classes: list[tuple[int, str]] = []
    for i, line in enumerate(code):
        m = _PY_HEADER.match(line)
        if not m:
            continue
        indent = len(m.group(1).expandtabs())
        while classes and classes[-1][0] >= indent:
            classes.pop()
        qualified = ".".join([name for _, name in classes] + [m.group(2)])
        if line.lstrip().startswith("class "):
            classes.append((indent, m.group(2)))
        start = i
        while start > 0 and code[start - 1].lstrip().startswith("@"):
            start -= 1
        starts.append((start, m.group(2), qualified))
    parts = []
    if not starts or starts[0][0] > 0:
        end = starts[0][0] - 1 if starts else len(code) - 1
        parts.append(Part(first, first + end, label="the lines before", keep_head=False))
    for n, (start, name, qualified) in enumerate(starts):
        end = starts[n + 1][0] - 1 if n + 1 < len(starts) else len(code) - 1
        names = {qualified.split(".")[-2]} if name.startswith("__") and "." in qualified else {name}
        parts.append(Part(first + start, first + end, names, qualified))
    return parts

def _head_length(code: list[str], start: int, end: int) -> int:
    """
    Returns how many of a part's first lines to keep: its decorators and its def, class or heading line.
    """
    n = 0
    while start + n <= end and code[start + n].lstrip().startswith("@"):
        n += 1
    return min(n + 1, end - start + 1)

def _indent_of(code: list[str], start: int, end: int, head: int) -> str:
    """
    Returns the indentation of a part's body, for the marker that replaces it.
    """
    for i in range(start + head, end + 1):
        if code[i].strip():
            return code[i][:len(code[i]) - len(code[i].lstrip())]
    return code[start][:len(code[start]) - len(code[start].lstrip())] + "    "

def shorten_read(entry: c.Entry, known: str) -> str | None:
    """
    Returns a read's response shortened to the parts mentioned in known (what was written since the read), or None
    if it cannot be shortened or would not get shorter enough to be worth it.
    """
    split = _split_response(entry.response)
    path = str((entry.args or {}).get("path") or "") if isinstance(entry.args, dict) else ""
    if not split or not path:
        return None
    header, fence, code, rest = split
    m = _LINES.search(header)
    if not m or not code:
        return None
    first = int(m.group(1))
    suffix = PurePosixPath(path.replace("\\", "/")).suffix.lower()
    markdown = suffix in parsing.MARKDOWN_SUFFIXES
    if markdown and _path_mentioned(path, known):
        return None  # A document named since is kept whole
    parts = _outline_parts(code, first, suffix) if suffix in parsing.OUTLINE_SUFFIXES else None
    if parts is None:
        if suffix not in parsing.PYTHON_SUFFIXES:
            return None
        parts = _python_parts(code, first)
    said = words(known)
    cut = {}  # The first line of each left-out part (index in code) -> (lines kept before the marker, part)
    for part in parts:
        start, end = part.start - first, part.end - first
        if start < 0 or end >= len(code) or end < start or part.always:
            continue
        if not markdown and part.names & said:
            continue
        head = _head_length(code, start, end) if part.keep_head else 0
        if end - start + 1 - head < 3:
            continue  # Too short to be worth a marker
        cut[start] = (head, part)
    if not cut:
        return None
    out, i, example = [], 0, ""
    while i < len(code):
        if i not in cut:
            out.append(code[i])
            i += 1
            continue
        head, part = cut[i]
        start, end = part.start - first, part.end - first
        out.extend(code[start:start + head])
        what = f" ({part.label})" if part.label and not head else ""
        out.append(f"{_indent_of(code, start, end, head)}{MARK} lines {part.start + head}-{part.end} left out{what} {MARK}")
        example = example or part.section
        i = end + 1
    how = (f"read_file_with_metadata(path={path!r}, section={example!r})" if example and not markdown
           else f"read_file_with_metadata(path={path!r}, start_line=..., end_line=...)")
    what = ("the text under its headings is left out" if markdown else
            "the parts not mentioned in the conversation since this read are left out, their first lines kept")
    note = (f"- Shortened by Local Fox to make room in the context: {what}, each marked with {MARK} and its lines. "
            f"Read a part again only if you need its exact text, e.g. {how}.")
    shortened = "\n".join([header.rstrip("\n"), note, "", fence, *out, rest])
    return shortened if _worth_it(entry.response, shortened) else None

def shorten_search(entry: c.Entry, known: str) -> str | None:
    """
    Returns a text search's response with the matches in files not mentioned since replaced with one line per file
    that gives their line numbers, or None if nothing would be left out.
    """
    split = _split_response(entry.response)
    if not split:
        return None
    header, fence, lines, rest = split
    groups: dict[str, list[str]] = {}
    for line in lines:
        m = _MATCH.match(line)
        if m:
            groups.setdefault(m.group(1), []).append(m.group(2))
    left_out = {f for f in groups if not _path_mentioned(f, known) and len(groups[f]) >= 2}
    if not left_out:
        return None
    out, done = [], set()
    for line in lines:
        m = _MATCH.match(line)
        if m and m.group(1) in left_out:
            if m.group(1) not in done:
                numbers = groups[m.group(1)]
                out.append(f"{m.group(1)}: {MARK} {len(numbers)} matches left out, at lines {', '.join(numbers)} {MARK}")
                done.add(m.group(1))
            continue
        out.append(line)
    note = (f"- Shortened by Local Fox to make room in the context: the matches in files not mentioned in the "
            f"conversation since this search are left out, their line numbers kept ({MARK}).")
    shortened = "\n".join([header.rstrip("\n"), note, "", fence, *out, rest])
    return shortened if _worth_it(entry.response, shortened) else None

def _worth_it(before: str, after: str) -> bool:
    saved = len(before) - len(after)
    return saved >= MIN_SAVED_CHARS and saved >= MIN_SAVED_SHARE * len(before)

def shorten(entry: c.Entry, known: str) -> str | None:
    """
    Returns the entry's response shortened for the context, or None if it is not a response that is shortened (a
    successful read or text search) or nothing of it would be left out. known is what the conversation has written
    since the call (see written_since).
    """
    if entry.kind != c.TOOL or entry.status != c.OK or entry.images:
        return None
    name = entry.resolved or entry.tool
    if name == "read_file_with_metadata":
        return shorten_read(entry, known)
    if name == "search_text_in_files":
        return shorten_search(entry, known)
    return None

def written_since(entries: list[c.Entry], entry: c.Entry) -> str:
    """
    Returns what the conversation has written that tells which parts of a response are still in use: the user's
    messages (all of them, as they set the task), and after the call the model's thinking and replies and the arguments
    of its calls. Tool responses do not count: a search lists names that nobody needs.
    """
    texts, after = [], False
    for other in entries:
        if other is entry:
            after = True
        elif other.kind == c.USER:
            texts.append(other.text)
        elif after and other.kind == c.ASSISTANT:
            texts.extend((other.thinking, other.text))
        elif after and other.kind == c.TOOL:
            texts.append(str(other.args))
    return "\n".join(texts)
