"""
Shortening old tool responses to make room in the context, before removing them whole (Session._fit_context).

A read keeps the parts of the file that the conversation has mentioned since the read: the functions and classes
named in the model's thinking and replies, in the arguments of its later calls, or in the user's messages. Every other
part is left out, its first line kept (the signature, a heading) with a marker of the lines left out, so the model
still sees what the file has and where, and reads a part again by its section or lines if it needs the exact text. A
Markdown document is kept whole if its path was mentioned since, and otherwise as its headings. A text search keeps
the matches in files mentioned since; the other files keep a line with their match lines.

If that is not enough, the parts that were mentioned are left out too, those whose last mention is the oldest first
(`last_mention`), across all the responses; parts the user named in a message stay until whole responses are removed.

The choice comes from what was written and called, never from asking the model (see AGENTS.md). Replayed on a
logged session (2026-11-05), keeping the parts named since lost about 28 % less of what the model mentioned later,
per token freed, than removing whole responses. Broader rules (any word the model repeated, the words of the user's
prompt) kept much more without losing less. On another logged session, leaving out mentioned parts by their last
mention, instead of removing whole responses, lost 25 to 45 % less when shortening alone was not enough; weighting
the mentions by their number, age and writer gave the same result as this plain order. Only the current
conversation counts: the session's entries start again at "Clear".

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

@dataclass
class Unit:
    """
    A piece of a response that can be left out: a part of a read (its first line kept) or a file's matches in a text
    search (a line with their line numbers kept). It is mentioned by its names (a code file's functions and classes)
    or by its file's path (a Markdown document, a search's file).
    """
    names: set[str]
    path: str
    start: int  # Its first line in the code block of the response
    end: int
    head: int = 0  # The lines kept before its marker
    part: Part | None = None
    marker: str = ""  # The line that replaces it

    def mentioned(self, text: str, said: set[str]) -> bool:
        return bool(self.names & said) or bool(self.path and _path_mentioned(self.path, text))

@dataclass
class Shortening:
    """
    What of a response can be left out (its units), and how the response looks with some of them left out.
    """
    entry: c.Entry
    units: list[Unit]
    header: str
    fence: str
    code: list[str]
    rest: str
    note: str  # How the model gets a part back

    def text(self, cut: set[int]) -> str:
        """
        Returns the response with the units of these indexes left out.
        """
        left_out = {self.units[n].start: self.units[n] for n in cut}
        out, i = [], 0
        while i < len(self.code):
            unit = left_out.get(i)
            if unit is None:
                out.append(self.code[i])
                i += 1
                continue
            out.extend(self.code[unit.start:unit.start + unit.head])
            out.append(unit.marker)
            i = unit.end + 1
        return "\n".join([self.header.rstrip("\n"), self.note, "", self.fence, *out, self.rest])

def _read_plan(entry: c.Entry) -> Shortening | None:
    """
    Returns what of a read can be left out: its parts that are long enough to be worth a marker.
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
    parts = _outline_parts(code, first, suffix) if suffix in parsing.OUTLINE_SUFFIXES else None
    if parts is None:
        if suffix not in parsing.PYTHON_SUFFIXES:
            return None
        parts = _python_parts(code, first)
    units, example = [], ""
    for part in parts:
        start, end = part.start - first, part.end - first
        if start < 0 or end >= len(code) or end < start or part.always:
            continue
        head = _head_length(code, start, end) if part.keep_head else 0
        if end - start + 1 - head < 3:
            continue  # Too short to be worth a marker
        what = f" ({part.label})" if part.label and not head else ""
        marker = f"{_indent_of(code, start, end, head)}{MARK} lines {part.start + head}-{part.end} left out{what} {MARK}"
        # A document is mentioned by its path, code by the names of its functions and classes
        units.append(Unit(set() if markdown else part.names, path if markdown else "", start, end, head, part, marker))
        example = example or part.section
    if not units:
        return None
    how = (f"read_file_with_metadata(path={path!r}, section={example!r})" if example and not markdown
           else f"read_file_with_metadata(path={path!r}, start_line=..., end_line=...)")
    what = ("the text under its headings is left out" if markdown else
            "the parts the conversation has not mentioned lately are left out, their first lines kept")
    note = (f"- Shortened by Local Fox to make room in the context: {what}, each marked with {MARK} and its lines. "
            f"Read a part again only if you need its exact text, e.g. {how}.")
    return Shortening(entry, units, header, fence, code, rest, note)

def _search_plan(entry: c.Entry) -> Shortening | None:
    """
    Returns what of a text search can be left out: the matches of each file with two or more of them.
    """
    split = _split_response(entry.response)
    if not split:
        return None
    header, fence, lines, rest = split
    groups: dict[str, list[tuple[int, str]]] = {}
    for i, line in enumerate(lines):
        m = _MATCH.match(line)
        if m:
            groups.setdefault(m.group(1), []).append((i, m.group(2)))
    units = []
    for file, matches in groups.items():
        indexes = [i for i, _ in matches]
        # Left out as one line in the place of the first; only a file whose matches are together is a unit
        if len(matches) < 2 or indexes != list(range(indexes[0], indexes[-1] + 1)):
            continue
        marker = f"{file}: {MARK} {len(matches)} matches left out, at lines {', '.join(n for _, n in matches)} {MARK}"
        units.append(Unit(set(), file, indexes[0], indexes[-1], marker=marker))
    if not units:
        return None
    note = (f"- Shortened by Local Fox to make room in the context: the matches in files the conversation has not "
            f"mentioned lately are left out, their line numbers kept ({MARK}).")
    return Shortening(entry, units, header, fence, lines, rest, note)

def plan(entry: c.Entry) -> Shortening | None:
    """
    Returns what of the entry's response can be left out, or None if it is not a response that is shortened (a
    successful read or text search) or has nothing to leave out.
    """
    if entry.kind != c.TOOL or entry.status != c.OK or entry.images:
        return None
    name = entry.resolved or entry.tool
    if name == "read_file_with_metadata":
        return _read_plan(entry)
    if name == "search_text_in_files":
        return _search_plan(entry)
    return None

def worth_it(before: str, after: str) -> bool:
    """
    Checks if a shortening saves enough to change the response for: every change makes Ollama process the prompt again.
    """
    saved = len(before) - len(after)
    return saved >= MIN_SAVED_CHARS and saved >= MIN_SAVED_SHARE * len(before)

def shorten(entry: c.Entry, known: str) -> str | None:
    """
    Returns the entry's response with the units known does not mention left out, or None if it is not shortened
    (see plan) or would not get shorter enough to be worth it. known is what the conversation has written since the
    call (see written_since).
    """
    shortening = plan(entry)
    if not shortening:
        return None
    said = words(known)
    cut = {n for n, unit in enumerate(shortening.units) if not unit.mentioned(known, said)}
    if not cut:
        return None
    text = shortening.text(cut)
    return text if worth_it(entry.response, text) else None

# The last mention of a unit that the user named: the user's messages set the task, so such units are kept longest
BY_USER = 10 ** 9

@dataclass
class Written:
    """
    A text the conversation wrote that can mention a unit, at its position in the entries.
    """
    position: int
    text: str
    said: set[str]
    user: bool

def writings(entries: list[c.Entry]) -> list[Written]:
    """
    Returns the texts that tell which units are still in use: the user's messages, the model's thinking and replies,
    and the arguments of its calls. Tool responses do not count: a search lists names that nobody needs.
    """
    out = []
    for position, entry in enumerate(entries):
        if entry.kind == c.USER:
            texts, user = [entry.text], True
        elif entry.kind == c.ASSISTANT:
            texts, user = [entry.thinking, entry.text], False
        elif entry.kind == c.TOOL:
            texts, user = [str(entry.args)], False
        else:
            continue
        text = "\n".join(t for t in texts if t)
        if text:
            out.append(Written(position, text, words(text), user))
    return out

def last_mention(unit: Unit, written: list[Written], position: int) -> int | None:
    """
    Returns when the conversation last mentioned a unit of the response at this position: BY_USER if a message of
    the user mentions it (any of them), the position of the latest mention after the response, or None if nothing
    mentioned it since.
    """
    if any(w.user and unit.mentioned(w.text, w.said) for w in written):
        return BY_USER
    return next((w.position for w in reversed(written) if w.position > position and unit.mentioned(w.text, w.said)), None)

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
