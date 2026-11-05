"""
Tests of shortening old tool responses to the parts the conversation mentioned since (condense.py).
"""
from local_fox import condense
from local_fox import conversation as c

def tool(name, args, response, status=c.OK):
    return c.Entry(c.TOOL, tool=name, resolved=name, args=args, response=response, status=status)

def read(path, body, start=1, total=None):
    lines = body.split("\n")
    end = start + len(lines) - 1
    total = total or end
    partial = " (partial)" if (start, end) != (1, total) else ""
    return tool("read_file_with_metadata", {"path": path},
                f"- File: `{path}`\n- Lines: {start}-{end} of {total}{partial}\n\n```python\n{body}\n```")

def function(name, indent="", lines=20):
    body = "\n".join(f"{indent}    value_{i} = compute('{name}', {i})" for i in range(lines))
    return f"{indent}def {name}(self, argument):\n{body}\n{indent}    return value_0\n"

PY = ("import os\nimport sys\nimport json\n\nLIMIT = 3\n\n"
      + function("first") + "\n" + function("second") + "\n"
      + "class Cart:\n    \"\"\"A cart.\"\"\"\n    size = 1\n\n"
      + function("__init__", "    ") + "\n" + function("total", "    ") + "\n" + function("clear", "    "))

def numbered(body):
    return {i + 1: line for i, line in enumerate(body.split("\n"))}

def test_a_read_keeps_the_parts_mentioned_since_and_the_first_lines_of_the_rest():
    short = condense.shorten(read("cart.py", PY), "I need to change Cart.total and second()")
    lines = numbered(PY)
    code = short.split("```python\n")[1].split("\n```")[0]
    # Mentioned: second, Cart (its own lines and __init__) and total; the others are left out
    assert function("second") in code and function("total", "    ") in code and function("__init__", "    ") in code
    assert "class Cart:\n    \"\"\"A cart.\"\"\"\n    size = 1" in code
    first = next(n for n, line in lines.items() if line.startswith("def first"))
    assert f"def first(self, argument):\n    ⋯ lines {first + 1}-{first + 21} left out ⋯" in code
    assert "    def clear(self, argument):\n        ⋯ lines" in code and "compute('clear'" not in code
    assert "⋯ lines 1-5 left out (imports, assignments: LIMIT) ⋯" in code
    # The model is told what happened and how to get a part back
    assert "- Shortened by Local Fox to make room in the context" in short
    assert "read_file_with_metadata(path='cart.py', section='first')" in short
    assert short.startswith("- File: `cart.py`\n- Lines: 1-")

def test_a_partial_read_that_does_not_parse_is_split_at_its_def_lines():
    # Starts inside a class and ends inside a method, as a long file read in parts does: ast cannot parse it
    body = ("        return self.items\n\n    @property\n" + function("total", "    ")
            + "\n    @classmethod\n" + function("clear", "    ") + "    def unfinished(self):\n        x = 1")
    short = condense.shorten(read("cart.py", body, start=300, total=900), "Look at clear")
    code = short.split("```python\n")[1].split("\n```")[0]
    assert "    @property\n    def total(self, argument):\n        ⋯ lines 304-325 left out ⋯" in code  # Decorator kept
    assert function("clear", "    ") in code
    assert "section='total'" in short

def test_markdown_is_kept_whole_if_named_and_otherwise_as_its_headings():
    text = "# Plan\n\nIntro line.\n\n## Steps\n\n" + "\n".join(f"- step {i} with some words" for i in range(30)) + "\n\n## Done\n\nNothing yet.\nOr maybe.\nNot sure."
    entry = tool("read_file_with_metadata", {"path": ".local-fox/context/plan.md"},
                 f"- File: `plan.md`\n- Lines: 1-{text.count(chr(10)) + 1} of {text.count(chr(10)) + 1}\n\n```markdown\n{text}\n```")
    assert condense.shorten(entry, "As the plan.md says") is None
    short = condense.shorten(entry, "Something else")
    assert "## Steps\n⋯ lines 6-36 left out ⋯" in short and "step 3" not in short
    assert "the text under its headings is left out" in short

def test_a_search_keeps_the_matches_in_files_mentioned_since():
    lines = ([f"src/app.py:{n}: refresh()" for n in (3, 40, 77)] + [""]
             + [f"src/other.py:{n}: refresh()  # a long line of matched text here" for n in range(10, 40)])
    entry = tool("search_text_in_files", {"query": "refresh"},
                 "- Found 33 matches in 2 files in the project:\n\n```text\n" + "\n".join(lines) + "\n```")
    short = condense.shorten(entry, "app.py calls it at startup")
    assert "src/app.py:40: refresh()" in short
    assert "src/other.py: ⋯ 30 matches left out, at lines 10, 11," in short and "src/other.py:12:" not in short

def test_what_is_not_shortened():
    assert condense.shorten(read("cart.py", PY), "first second Cart total clear") is None  # Everything mentioned
    assert condense.shorten(read("cart.py", "def f():\n    return 1"), "") is None  # Too little to gain
    assert condense.shorten(read("cart.py", PY).__class__(c.TOOL, tool="get_outline", resolved="get_outline",
                                                          args={"path": "cart.py"}, response=PY, status=c.OK), "") is None
    failed = read("cart.py", PY)
    failed.status = c.FAILED
    assert condense.shorten(failed, "") is None
    assert condense.shorten(read("notes.txt", PY.replace("def ", "fed ")), "") is None  # No outline, not Python

def test_only_what_was_written_after_the_call_counts_and_all_the_users_messages():
    first = c.Entry(c.ASSISTANT, thinking="before: alpha")
    call = read("cart.py", PY)
    entries = [c.Entry(c.USER, text="Fix beta"), first, call, c.Entry(c.ASSISTANT, thinking="gamma", text="delta"),
               tool("read_file_with_metadata", {"path": "epsilon.py"}, "zeta in a response"), c.Entry(c.USER, text="eta")]
    known = condense.written_since(entries, call)
    assert all(word in known for word in ("beta", "gamma", "delta", "epsilon", "eta"))
    assert "alpha" not in known and "zeta" not in known

def test_the_feed_and_the_export_show_what_the_model_has_and_the_whole_response():
    from local_fox.export import to_markdown
    from local_fox.ui.render import entry_html
    entry = read("cart.py", PY)
    entry.shortened = condense.shorten(entry, "total")
    entry.collapsed = False
    html = entry_html(entry)
    assert "(response shortened in the model's context)" in html
    assert html.index("Response as the model has it now") < html.index("The whole response, as the tool gave it")
    assert "compute('clear', 3)" in html  # The whole response is still there for the user
    exported = to_markdown([entry], "m", "p")
    assert "response later shortened in the model's context" in exported
    assert "Response as the model had it after it was shortened:" in exported and "⋯ lines" in exported
