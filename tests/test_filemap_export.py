import json

from local_fox import conversation as c
from local_fox.export import to_markdown
from local_fox.filemap import FORBIDDEN, IGNORED, READ_ONLY, SHOWN, Rules, model_filemap

def make_project(tmp_path):
    for rel in ["README.md", "src/settings.py", "src/units/eu.py", "tests/test_settings.py", "node_modules/x/index.js",
                ".ollmcp-tools/instructions.md", ".ollmcp-tools/context/plan.md", ".ollmcp-tools/secret.md"]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    (tmp_path / ".ollmcp-tools" / "tools-config.json").write_text(json.dumps({
        "project_dir": "..", "forbidden_paths": [".ollmcp-tools"], "allowed_paths": [".ollmcp-tools/context"],
        "read_only_files": [".ollmcp-tools/instructions.md"]}), encoding="utf-8")
    (tmp_path / ".mcp.json").write_text("{}", encoding="utf-8")
    return Rules(tmp_path)

def test_states(tmp_path):
    rules = make_project(tmp_path)
    assert rules.state(tmp_path / "src" / "settings.py") == SHOWN
    assert rules.state(tmp_path / ".ollmcp-tools" / "secret.md") == FORBIDDEN
    assert rules.state(tmp_path / ".ollmcp-tools" / "context" / "plan.md") == SHOWN
    assert rules.state(tmp_path / ".ollmcp-tools" / "instructions.md") == READ_ONLY
    assert rules.state(tmp_path / "node_modules") == IGNORED
    assert rules.state(tmp_path / ".mcp.json") == FORBIDDEN
    assert rules.relative(tmp_path / "src" / "units" / "eu.py") == "src/units/eu.py"

def test_model_filemap(tmp_path):
    rules = make_project(tmp_path)
    text, files, folders_only = model_filemap(rules, 2000)
    lines = text.splitlines()
    assert lines[0] == f"Files in the project folder {tmp_path.resolve().as_posix()}:"
    assert "README.md" in lines and "src/" in lines and "  settings.py" in lines and "    eu.py" in lines
    assert "  context/" in lines and "    plan.md" in lines and "  instructions.md" in lines
    assert not any("secret" in line or "node_modules" in line or ".mcp.json" in line for line in lines)
    assert files == 6 and not folders_only

def test_model_filemap_folders_only_when_large(tmp_path):
    rules = make_project(tmp_path)
    text, _, folders_only = model_filemap(rules, 20)
    assert folders_only
    assert "src/ (2 files)" in text

def test_export(tmp_path):
    entries = [c.Entry(c.USER, text="Hi"), c.Entry(c.ASSISTANT, text="Hello", thinking="hmm"),
               c.Entry(c.TOOL, tool="read_file_with_metadata", args={"path": "a.py"}, response="```\nx\n```", status=c.OK)]
    text = to_markdown(entries, "gemma4:26b", str(tmp_path))
    assert "## User\n\nHi" in text and "> hmm" in text and "Tool call: read_file_with_metadata a.py (ok)" in text
    assert "````\n```\nx\n```\n````" in text

def test_collapsed_answer_shows_its_first_lines():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from local_fox.conversation import ASSISTANT, USER, Entry
    from local_fox.ui.render import entry_html
    long = Entry(ASSISTANT, text="First line.\n\nSecond line.\n\nThird line.\n\nFourth.", answer_collapsed=True)
    html = entry_html(long)
    assert "First line." in html and "Second line." in html and "Third line." not in html and "2 more lines" in html
    long.answer_collapsed = False
    assert "Fourth." in entry_html(long) and "collapse" in entry_html(long)
    short = Entry(ASSISTANT, text="Just this.", answer_collapsed=True)
    assert "Just this." in entry_html(short) and "answer:" not in entry_html(short)
    prompt = Entry(USER, text="Line one\nLine two\nLine three", collapsed=True)
    assert "Line three" in entry_html(prompt) and "toggle:" not in entry_html(prompt)
