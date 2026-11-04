"""
Tests of the bundled tools: which servers of .mcp.json run them, and the setup of a project folder.
They need the ollmcp-tools package in the environment, as the executable bundles it.
"""
import json
import sys

import pytest

from local_fox import tools_setup

pytestmark = pytest.mark.skipif(tools_setup.bundled_version() is None, reason="ollmcp-tools is not installed")

def test_tools_server_runs_bundled():
    spec = {"command": "python", "args": ["-m", "ollmcp_tools"]}
    command, args, source = tools_setup.server_command(spec, True)
    assert (command, args) == (sys.executable, ["-m", "ollmcp_tools"])
    assert "bundled" in source
    assert tools_setup.server_command(spec, False)[:2] == ("python", ["-m", "ollmcp_tools"])
    assert tools_setup.runs_the_tools({"command": "py", "args": ["-3", "-m", "ollmcp_tools"]})

def test_other_servers_run_as_given():
    spec = {"command": "node", "args": ["server.js"]}
    assert tools_setup.server_command(spec, True)[:2] == ("node", ["server.js"])

def server_env(monkeypatch, tmp_path, spec, api_key):
    """Starts a server of .mcp.json as far as its process, and returns the environment it would get."""
    import asyncio
    from contextlib import AsyncExitStack
    from local_fox import mcp_servers
    seen = {}

    def stdio_client(params, errlog=None):
        seen["env"] = params.env
        raise RuntimeError("not started")
    monkeypatch.setattr(mcp_servers, "stdio_client", stdio_client)
    servers = mcp_servers.Servers(tmp_path, tmp_path / "logs", api_key=api_key)

    async def enter():
        async with AsyncExitStack() as stack:
            with pytest.raises(RuntimeError):
                await servers._enter(stack, "x", spec)
    asyncio.run(enter())
    return seen["env"]

def test_the_api_key_goes_only_to_the_tools_server(monkeypatch, tmp_path):
    tools = {"command": "python", "args": ["-m", "ollmcp_tools"]}
    other = {"command": "node", "args": ["server.js"]}
    assert server_env(monkeypatch, tmp_path, tools, " key ")["MCP_TOOLS_OLLAMA_API_KEY"] == "key"
    assert "MCP_TOOLS_OLLAMA_API_KEY" not in server_env(monkeypatch, tmp_path, other, "key")
    assert "MCP_TOOLS_OLLAMA_API_KEY" not in server_env(monkeypatch, tmp_path, tools, "")  # The user config file, if any

@pytest.fixture
def home(tmp_path, monkeypatch):
    """The tools' user folder in tmp_path, so that the setup does not touch the real one."""
    from ollmcp_tools import locations
    monkeypatch.setattr(locations, "USER_CONFIG_PATH", tmp_path / "home" / "user-config.json")
    monkeypatch.setattr(locations, "DEFAULT_BACKUP_ROOT", tmp_path / "home" / "backups")
    return tmp_path / "home"

def test_set_up_creates_the_local_fox_folder(tmp_path, home):
    from local_fox.filemap import Rules, instructions_path
    project = tmp_path / "project"
    project.mkdir()
    assert not tools_setup.is_set_up(project)
    report = tools_setup.set_up(project)
    assert tools_setup.is_set_up(project)
    server = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["dev"]
    assert server["args"] == ["-m", "ollmcp_tools"] and server["env"]["MCP_TOOLS_CONFIG"] == ".local-fox/tools-config.json"
    config = json.loads((project / ".local-fox" / "tools-config.json").read_text(encoding="utf-8"))
    assert config["forbidden_paths"] == [".local-fox"] and config["allowed_paths"] == [".local-fox/context"]
    assert config["command_registry"] == ".local-fox/commands.json"
    assert not (project / ".ollmcp-tools").exists()
    assert (project / ".local-fox" / "conversations").is_dir() and "conversations" in (project / ".local-fox" / ".gitignore").read_text()
    # The instructions are Local Fox's own, naming the tools as the model sees them
    instructions = instructions_path(project).read_text(encoding="utf-8")
    assert instructions_path(project) == project / ".local-fox" / "instructions.md"
    assert instructions == tools_setup.instructions_template() and "dev_" not in instructions
    assert "Created: .local-fox/instructions.md (Local Fox's template)" in report and "ollmcp's system prompt" not in report
    # The API key is a setting of Local Fox: the tools' user config file is not created
    assert not (home / "user-config.json").exists() and 'API key in "Settings"' in report
    # The saved conversations are forbidden to the tools, as the rest of the folder except the plans
    rules = Rules(project)
    assert rules.is_forbidden((project / ".local-fox" / "conversations" / "a.json").resolve())
    assert not rules.is_forbidden((project / ".local-fox" / "context" / "plan.md").resolve())
    instructions_path(project).write_text("Mine.", encoding="utf-8")
    assert "Already there, not changed: .local-fox/instructions.md" in tools_setup.set_up(project)
    assert instructions_path(project).read_text(encoding="utf-8") == "Mine."

def test_project_set_up_by_the_tools_keeps_its_folder(tmp_path, home):
    from local_fox.filemap import instructions_path
    project = tmp_path / "project"
    (project / ".ollmcp-tools").mkdir(parents=True)
    (project / ".ollmcp-tools" / "tools-config.json").write_text(json.dumps({"project_dir": ".."}), encoding="utf-8")
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"dev": {"command": "python", "args": ["-m", "ollmcp_tools"],
                                       "env": {"MCP_TOOLS_CONFIG": ".ollmcp-tools/tools-config.json"}}}}), encoding="utf-8")
    assert tools_setup.is_set_up(project)
    report = tools_setup.set_up(project)  # Creates its missing files, in its own folder
    assert instructions_path(project) == project / ".ollmcp-tools" / "instructions.md"
    assert ".ollmcp-tools/context" in instructions_path(project).read_text(encoding="utf-8")
    assert "Already there, not changed: .ollmcp-tools/tools-config.json" in report and not (project / ".local-fox").exists()

def test_template_names_only_existing_tools():
    import importlib.util
    import re
    from pathlib import Path
    # common.py loads a tools config when imported, so its TOOLSETS are read from its source
    source = (Path(importlib.util.find_spec("ollmcp_tools").origin).parent / "common.py").read_text(encoding="utf-8")
    known = set(re.findall(r'"([a-z_]+)"', source[source.index("TOOLSETS = {"):source.index("DEFAULT_MAX_OUTPUT_CHARS")]))
    named = set(re.findall(r"\*\*([a-z_]+)\*\*", tools_setup.instructions_template()))
    assert named and named <= known

def test_system_folders_are_refused(tmp_path, home):
    import os
    from pathlib import Path
    for folder in (Path(tmp_path.anchor), Path.home(), Path.home().parent):
        assert tools_setup.refusal(folder), folder
        with pytest.raises(tools_setup.SetupRefused):
            tools_setup.set_up(folder)
        with pytest.raises(tools_setup.SetupRefused):
            tools_setup.planned(folder)
    if os.environ.get("SystemRoot"):
        assert tools_setup.refusal(Path(os.environ["SystemRoot"]) / "System32")
    project = tmp_path / "project"
    project.mkdir()
    assert tools_setup.refusal(project) is None
    assert not (Path(tmp_path.anchor) / ".local-fox").exists()

def test_a_mcp_json_of_other_applications_is_not_a_project(tmp_path, home):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "node", "args": ["x.js"]}}}), encoding="utf-8")
    assert not tools_setup.is_set_up(project)
    changed, created = tools_setup.plan(project)
    assert changed == ["The existing .mcp.json has no server for the tools. The server 'dev' is added to it; "
                       "its other servers stay as they are."]
    assert ".mcp.json" not in created  # A change to the existing file, not a new file
    assert not (project / ".local-fox").exists()  # Planning creates nothing
    tools_setup.set_up(project)
    servers = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["other"] == {"command": "node", "args": ["x.js"]}  # Kept as it was
    assert servers["dev"]["env"]["MCP_TOOLS_CONFIG"] == ".local-fox/tools-config.json"
    assert tools_setup.is_set_up(project) and tools_setup.planned(project) == []

def test_no_session_outside_a_project(tmp_path):
    import asyncio
    from local_fox import config
    from local_fox.session import NotSetUp, Session
    session = Session(dict(config.DEFAULTS), tmp_path, log_dir=tmp_path / "logs")
    with pytest.raises(NotSetUp):
        asyncio.run(session.start())
    assert session.servers is None

def test_a_mcp_json_naming_a_missing_config_gets_local_fox(tmp_path, home):
    from local_fox.filemap import project_folder
    project = tmp_path / "project"
    project.mkdir()
    # As in a project set up with ollmcp earlier, whose .ollmcp-tools folder was later removed
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"dev": {"command": "python", "args": ["-m", "ollmcp_tools"],
                                       "env": {"MCP_TOOLS_CONFIG": ".ollmcp-tools/tools-config.json"}}}}), encoding="utf-8")
    assert project_folder(project) == project / ".local-fox"
    changed, created = tools_setup.plan(project)
    assert len(changed) == 1 and "names .ollmcp-tools/tools-config.json, which does not exist" in changed[0]
    assert "changed to use .local-fox/tools-config.json" in changed[0]
    assert all(item.startswith(".local-fox/") for item in created)  # Nothing in .ollmcp-tools, no new .mcp.json
    tools_setup.set_up(project)
    server = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["dev"]
    assert server["env"]["MCP_TOOLS_CONFIG"] == ".local-fox/tools-config.json"
    assert (project / ".local-fox" / "tools-config.json").exists() and not (project / ".ollmcp-tools").exists()
    assert tools_setup.is_set_up(project) and tools_setup.planned(project) == []

def test_the_config_names_the_instructions(tmp_path, home):
    from local_fox.filemap import instructions_path
    project = tmp_path / "project"
    project.mkdir()
    tools_setup.set_up(project)
    config = json.loads((project / ".local-fox" / "tools-config.json").read_text(encoding="utf-8"))
    assert config["instructions_file"] == ".local-fox/instructions.md"
    assert list(config).index("instructions_file") == list(config).index("read_only_files") + 1
    assert instructions_path(project) == (project / ".local-fox" / "instructions.md").resolve()
    # Another file, named in the config, relative to project_dir as the tools' other paths
    config["instructions_file"] = "docs/ai.md"
    (project / ".local-fox" / "tools-config.json").write_text(json.dumps(config), encoding="utf-8")
    assert instructions_path(project) == (project / "docs" / "ai.md").resolve()

def test_commands_start_empty_with_examples(tmp_path, home):
    project = tmp_path / "project"
    project.mkdir()
    tools_setup.set_up(project)
    assert json.loads((project / ".local-fox" / "commands.json").read_text(encoding="utf-8")) == {}
    examples = json.loads((project / ".local-fox" / "commands-examples.json").read_text(encoding="utf-8"))
    assert "run_tests" in examples
