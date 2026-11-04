"""
Tests of the session loop with a fake Ollama client and fake MCP servers: every call gets a response, the
user's decisions are followed, and nothing is sent to the model that has no entry in the feed.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_fox import config
from local_fox import conversation as c
from local_fox.mcp_servers import Servers, ServerTool
from local_fox.session import CUT_OFF_MARK, SKIPPED_RESPONSE, STOPPED_BEFORE, STOPPED_MARK, Frontend, Session

SCHEMA = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

def call(name, arguments):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))

def chunk(content="", thinking="", tool_calls=None, done=False, reason="stop"):
    return SimpleNamespace(message=SimpleNamespace(content=content, thinking=thinking, tool_calls=tool_calls),
                           done=done, done_reason=reason if done else None, prompt_eval_count=100 if done else None,
                           eval_count=10 if done else None)

class FakeClient:
    """Replies with the given turns in order: each a list of chunks."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def chat(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)

        async def stream():
            for part in reply:
                yield part
        return stream()

class FakeMcp:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"read {args.get('path')}")], is_error=False)

class Decider(Frontend):
    def __init__(self, choices):
        self.choices = list(choices)
        self.questions = []

    async def decide(self, question, options, entry):
        self.questions.append(question)
        return self.choices.pop(0)

def make_session(tmp_path: Path, replies, choices=(), **settings) -> tuple[Session, FakeClient, FakeMcp]:
    (tmp_path / ".local-fox").mkdir()
    (tmp_path / ".local-fox" / "instructions.md").write_text("Be careful.", encoding="utf-8")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    session = Session({**config.DEFAULTS, **settings}, tmp_path, Decider(choices), log_dir=tmp_path / "logs")
    client, mcp = FakeClient(replies), FakeMcp()
    session.client = client
    servers = Servers(tmp_path, tmp_path / "logs")
    servers.sessions = {"dev": mcp}
    servers.tools = [ServerTool("dev", "read_file_with_metadata", "read_file_with_metadata", "Reads a file.", SCHEMA),
                     ServerTool("dev", "edit_file", "edit_file", "Edits a file.", SCHEMA)]
    session.servers = servers
    session.capabilities = ["completion", "tools"]
    asyncio.run(session.new_conversation())
    return session, client, mcp

def tool_messages(session):
    return [m["content"] for m in session.messages if m["role"] == "tool"]

def test_the_api_key_is_not_logged(tmp_path):
    make_session(tmp_path, [], ollama_api_key="secret-key")
    log = (tmp_path / "logs" / "session.jsonl").read_text(encoding="utf-8")
    assert "secret-key" not in log and '"ollama_api_key": "(set)"' in log

def test_start_gives_instructions_and_filemap(tmp_path):
    session, _, _ = make_session(tmp_path, [])
    system = session.messages[0]
    assert system["role"] == "system"
    assert "Be careful." in system["content"] and "a.py" in system["content"]
    kinds = [e.kind for e in session.entries]
    assert kinds[:3] == [c.INSTRUCTIONS, c.FILEMAP, c.TOOLS]

def test_answer_with_context_size(tmp_path):
    session, client, _ = make_session(tmp_path, [[chunk("Hello"), chunk(" there", done=True)]])
    asyncio.run(session.send("Hi"))
    assert session.messages[-1] == {"role": "assistant", "content": "Hello there"}
    assert client.requests[0]["options"]["num_ctx"] == 65536
    assert session.used_tokens == 110

def test_unknown_tool_gets_a_response(tmp_path):
    session, _, mcp = make_session(tmp_path, [[chunk(tool_calls=[call("read_file", {"path": "a.py"})], done=True)],
                                              [chunk("ok", done=True)]])
    asyncio.run(session.send("Read a.py"))
    assert tool_messages(session) == ["Unknown tool read_file. The tools are: read_file_with_metadata, edit_file."]
    assert mcp.calls == []

def test_prefixed_name_runs_the_tool(tmp_path):
    session, _, mcp = make_session(tmp_path, [[chunk(tool_calls=[call("dev_read_file_with_metadata", {"path": "a.py"})], done=True)],
                                              [chunk("ok", done=True)]], ["Allow"])
    asyncio.run(session.send("Read a.py"))
    assert mcp.calls == [("read_file_with_metadata", {"path": "a.py"})]
    assert tool_messages(session) == ["read a.py"]

def test_missing_and_broken_arguments(tmp_path):
    session, _, mcp = make_session(tmp_path, [[chunk(tool_calls=[call("read_file_with_metadata", {}), call("read_file_with_metadata", "{broken")], done=True)],
                                              [chunk("ok", done=True)]])
    asyncio.run(session.send("Read"))
    first, second = tool_messages(session)
    assert "missing the required argument path" in first
    assert "not valid JSON" in second
    assert mcp.calls == []

def test_skip_and_allow_all(tmp_path):
    calls = [call("read_file_with_metadata", {"path": "a.py"})] * 3
    session, _, mcp = make_session(tmp_path, [[chunk(tool_calls=calls, done=True)], [chunk("done", done=True)]],
                                   ["Skip", "Allow all for this session"])
    asyncio.run(session.send("Read"))
    assert tool_messages(session) == [SKIPPED_RESPONSE, "read a.py", "read a.py"]
    assert len(session.frontend.questions) == 2  # The third call ran without asking

def test_stop_answers_the_remaining_calls(tmp_path):
    calls = [call("read_file_with_metadata", {"path": "a.py"})] * 2
    session, _, mcp = make_session(tmp_path, [[chunk(tool_calls=calls, done=True)]], ["Stop"])
    asyncio.run(session.send("Read"))
    assert tool_messages(session) == [STOPPED_BEFORE, STOPPED_BEFORE]
    assert session.entries[-1].text == "Stopped."
    assert not session.running

def test_auto_approved_tools_do_not_ask(tmp_path):
    session, _, mcp = make_session(tmp_path, [[chunk(tool_calls=[call("read_file_with_metadata", {"path": "a.py"})], done=True)],
                                              [chunk("ok", done=True)]], auto_approve_tools=["read_file_with_metadata"])
    asyncio.run(session.send("Read"))
    assert session.frontend.questions == []
    assert mcp.calls

def test_context_full_notice(tmp_path):
    session, _, _ = make_session(tmp_path, [[chunk("Long", done=True, reason="length")]])
    asyncio.run(session.send("Write a lot"))
    notice = session.entries[-1]
    assert notice.kind == c.NOTICE and "context is full" in notice.text
    assert notice.actions == ["Clear", "Export", "Larger context"]
    assert session.messages[-1]["content"] == "Long" + CUT_OFF_MARK  # The model sees that it was cut off

def test_step_limit_asks_to_continue(tmp_path):
    read = [chunk(tool_calls=[call("read_file_with_metadata", {"path": "a.py"})], done=True)]
    session, client, _ = make_session(tmp_path, [read, read, read], ["Allow all for this session", "Stop"], max_steps=2)
    asyncio.run(session.send("Read"))
    assert len(client.requests) == 2
    assert "limit" in session.frontend.questions[-1]

def test_changed_files_are_tracked(tmp_path):
    session, _, _ = make_session(tmp_path, [[chunk(tool_calls=[call("edit_file", {"path": "a.py"})], done=True)],
                                            [chunk("ok", done=True)]], ["Allow"])
    asyncio.run(session.send("Edit"))
    assert session.changed_files == {(tmp_path / "a.py").resolve()}

def test_thinking_is_shown_but_not_kept(tmp_path):
    session, _, _ = make_session(tmp_path, [[chunk(thinking="hmm"), chunk("Answer", done=True)]])
    asyncio.run(session.send("Q"))
    entry = next(e for e in session.entries if e.kind == c.ASSISTANT)
    assert entry.thinking == "hmm" and not entry.collapsed  # The latest thinking stays open
    assert session.messages[-1] == {"role": "assistant", "content": "Answer"}

def test_latest_of_each_kind_is_open(tmp_path):
    read = lambda path: call("read_file_with_metadata", {"path": path})
    session, _, _ = make_session(tmp_path, [
        [chunk(thinking="first"), chunk("Looking.", tool_calls=[read("a.py")], done=True)],
        [chunk(thinking="second"), chunk("Now b.", tool_calls=[read("b.py"), read("c.py")], done=True)],
        [chunk("Done.", done=True)]], ["Allow all for this session"])
    asyncio.run(session.send("Read"))
    replies = [e for e in session.entries if e.kind == c.ASSISTANT]
    calls = [e for e in session.entries if e.kind == c.TOOL]
    assert [e.collapsed for e in calls] == [True, True, False]           # Only the last call is open
    assert [e.collapsed for e in replies[:2]] == [True, False]           # Only the last thinking is open
    assert [e.answer_collapsed for e in replies] == [True, True, False]  # Only the last answer is open

def test_what_the_user_opened_stays_open(tmp_path):
    read = lambda path: call("read_file_with_metadata", {"path": path})
    session, _, _ = make_session(tmp_path, [[chunk(tool_calls=[read("a.py")], done=True)], [chunk(tool_calls=[read("b.py")], done=True)],
                                            [chunk(tool_calls=[read("c.py")], done=True)], [chunk("ok", done=True)]],
                                 ["Allow", "Allow", "Allow"])
    session.frontend.choices = ["Allow"] * 3
    original = session.frontend.decide

    async def decide(question, options, entry):
        if "b.py" in question:
            first = next(e for e in session.entries if e.kind == c.TOOL)
            first.collapsed = False  # The user opens the first call again while the second is asked for
        return await original(question, options, entry)
    session.frontend.decide = decide
    asyncio.run(session.send("Read"))
    assert [e.collapsed for e in session.entries if e.kind == c.TOOL] == [False, True, False]

def test_clear_asks_again(tmp_path):
    session, _, _ = make_session(tmp_path, [])
    session.allow_all = True
    asyncio.run(session.new_conversation())
    assert not session.allow_all
    assert [m["role"] for m in session.messages] == ["system"]

def test_multiple_servers_prefix_names(tmp_path):
    servers = Servers(tmp_path, tmp_path)
    servers.sessions = {"dev": None, "web": None}
    servers.tools = [ServerTool("dev", "read", "read", "", {}), ServerTool("web", "fetch", "fetch", "", {}),
                     ServerTool("web", "read", "read", "", {})]
    servers._name_tools()
    assert [t["function"]["name"] for t in servers.payload()] == ["dev_read", "web_fetch", "web_read"]
    assert servers.resolve("fetch").server == "web"   # Only one server has it
    assert servers.resolve("read") is None             # Two servers have it
    assert servers.resolve("web.read").server == "web"

def read_log(session):
    import json
    return [json.loads(line) for line in session.log.path.read_text(encoding="utf-8").splitlines()]

def test_log_holds_what_the_model_was_given(tmp_path):
    session, client, _ = make_session(tmp_path, [[chunk(tool_calls=[call("dev_read_file_with_metadata", {"path": "a.py"})], done=True)],
                                                 [chunk("ok", done=True)]], ["Allow"])
    asyncio.run(session.send("Read a.py"))
    events = read_log(session)
    assert session.log.path == tmp_path / "logs" / "session.jsonl"
    assert [e["event"] for e in events] == ["conversation", "tools", "system", "request", "reply", "decision", "tool", "request", "reply"]
    system = next(e for e in events if e["event"] == "system")["content"]
    logged = [{"role": "system", "content": system}] + [m for e in events if e["event"] == "request" for m in e["messages"]]
    assert logged == session.messages[:-1]  # All but the last reply, which is in the last reply event
    tool = next(e for e in events if e["event"] == "tool")
    assert tool["called"] == "dev_read_file_with_metadata" and tool["ran"] == "dev:read_file_with_metadata"
    assert tool["status"] == c.OK and tool["response"] == "read a.py"
    assert events[4]["prompt_tokens"] == 100 and events[4]["tool_calls"][0]["name"] == "dev_read_file_with_metadata"

def test_one_log_for_the_run(tmp_path):
    session, _, _ = make_session(tmp_path, [])
    asyncio.run(session.new_conversation())
    assert [e["event"] for e in read_log(session)].count("conversation") == 2
    assert list((tmp_path / "logs").glob("*.jsonl")) == [session.log.path]

def test_start_empties_the_logs(tmp_path):
    from local_fox.session_log import reset
    folder = tmp_path / "logs"
    (folder / "sessions").mkdir(parents=True)  # From an earlier version
    for name in ("session.jsonl", "server-dev.log", "sessions/old.jsonl"):
        (folder / name).write_text("old\n", encoding="utf-8")
    (folder / "keep.txt").write_text("not ours\n", encoding="utf-8")
    reset(folder)
    assert [p.name for p in folder.iterdir()] == ["keep.txt"]

def test_stopped_reply_is_marked_for_the_model(tmp_path):
    session, client, _ = make_session(tmp_path, [])

    async def chat(**kwargs):
        async def stream():
            yield chunk("Half an ans")
            await asyncio.sleep(30)  # Stopped here
        return stream()
    client.chat = chat

    async def run():
        task = asyncio.create_task(session.send("Q"))
        await asyncio.sleep(0.05)
        session.stop()
        await task
    asyncio.run(run())
    reply = next(e for e in session.entries if e.kind == c.ASSISTANT)
    assert reply.text == "Half an ans" + STOPPED_MARK  # In the feed as the model gets it
    assert session.messages[-1] == {"role": "assistant", "content": "Half an ans" + STOPPED_MARK}

def test_conversation_is_saved_and_reopened(tmp_path):
    from local_fox import saved_conversations
    session, client, _ = make_session(tmp_path, [[chunk(thinking="hmm"), chunk("", tool_calls=[call("read_file_with_metadata", {"path": "a.py"})], done=True)],
                                                 [chunk("It sets x.", done=True)]], ["Allow"])
    asyncio.run(session.send("What is in a.py?"))
    path = session.saved_path
    assert path.parent == tmp_path / ".local-fox" / "conversations" and path.exists()
    assert (path.parent / ".gitignore").exists()
    [item] = saved_conversations.list_saved(tmp_path)
    assert item.title == "What is in a.py?" and item.messages == 4
    history = [m for m in session.messages if m["role"] != "system"]

    asyncio.run(session.new_conversation())  # A clear, then reopening the saved one
    assert session.saved_path is None
    asyncio.run(session.reopen(path))
    assert [m for m in session.messages if m["role"] != "system"] == history
    assert session.messages[0]["role"] == "system" and "Be careful." in session.messages[0]["content"]
    kinds = [e.kind for e in session.entries]
    assert kinds[:3] == [c.INSTRUCTIONS, c.FILEMAP, c.TOOLS] and kinds.count(c.INSTRUCTIONS) == 1
    assert kinds[3:7] == [c.USER, c.ASSISTANT, c.TOOL, c.ASSISTANT] and "Reopened" in session.entries[-1].text
    tool = next(e for e in session.entries if e.kind == c.TOOL)
    assert tool.response == "read a.py" and tool.status == c.OK

    # It continues in the same file with the next prompt
    client.replies = [[chunk("Nothing else.", done=True)]]
    asyncio.run(session.send("Anything else?"))
    assert session.saved_path == path and saved_conversations.list_saved(tmp_path)[0].messages == 6
    assert len(list(path.parent.glob("*.json"))) == 1

def test_filemap_and_instructions_are_read_again_before_the_first_prompt(tmp_path):
    session, client, _ = make_session(tmp_path, [[chunk("ok", done=True)], [chunk("ok", done=True)]])
    (tmp_path / "added_later.py").write_text("y = 2\n", encoding="utf-8")  # After the window opened
    (tmp_path / ".local-fox" / "instructions.md").write_text("Be very careful.", encoding="utf-8")
    asyncio.run(session.send("Hi"))
    system = session.messages[0]["content"]
    assert "added_later.py" in system and "Be very careful." in system
    filemap = next(e for e in session.entries if e.kind == c.FILEMAP)
    assert "added_later.py" in filemap.text and "read again before the first prompt" in filemap.title
    (tmp_path / "after_the_first.py").write_text("z = 3\n", encoding="utf-8")
    asyncio.run(session.send("Again"))
    assert "after_the_first.py" not in session.messages[0]["content"]  # The system message stays as the model got it

def test_out_of_memory_is_explained_in_the_feed(tmp_path):
    import ollama
    session, client, _ = make_session(tmp_path, [])

    async def chat(**kwargs):
        raise ollama.ResponseError("model requires more system memory (40 GiB) than is available (31 GiB)", 500)
    client.chat = chat
    asyncio.run(session.send("Hi"))
    error = session.entries[-1]
    assert error.kind == c.ERROR and "ran out of memory with a context of 65 536 tokens" in error.text
    assert "Ollama's message: model requires more system memory" in error.text

def test_a_stopped_tools_server_is_reported(tmp_path):
    from local_fox.session import RESTART_TOOLS
    session, _, mcp = make_session(tmp_path, [[chunk(tool_calls=[call("read_file_with_metadata", {"path": "a.py"})], done=True)],
                                              [chunk("I cannot read it.", done=True)]], ["Allow"])

    async def call_tool(name, args):
        raise RuntimeError("Connection closed")
    mcp.call_tool = call_tool
    asyncio.run(session.send("Read a.py"))
    assert "the tools' server stopped" in tool_messages(session)[0]
    assert any(e.kind == c.ERROR and "stopped, so the tools cannot run" in e.text for e in session.entries)
    assert any(e.kind == c.NOTICE and e.actions == [RESTART_TOOLS] for e in session.entries)

def test_the_model_is_told_where_its_instructions_are_from(tmp_path):
    session, _, _ = make_session(tmp_path, [[chunk("ok", done=True)]])
    note = "These are the project instructions, from `.local-fox/instructions.md`. You already have them"
    system = session.messages[0]["content"]
    assert system.startswith(note) and "Be careful." in system
    entry = next(e for e in session.entries if e.kind == c.INSTRUCTIONS)
    assert entry.text.startswith(note) and entry.text in system  # The feed shows what the model gets
    asyncio.run(session.send("Hi"))  # Unchanged file: not read again before the first prompt
    assert "read again" not in next(e for e in session.entries if e.kind == c.INSTRUCTIONS).title

class CountingClient(FakeClient):
    """Reports the prompt's tokens as Ollama would (about 3.2 characters per token), or the given counts."""

    def __init__(self, replies, counts=None):
        super().__init__(replies)
        self.counts = list(counts or [])

    async def chat(self, **kwargs):
        import json
        size = len(json.dumps(kwargs.get("tools") or [])) + sum(len(json.dumps({k: v for k, v in m.items() if k != "images"}))
                                                                 for m in kwargs["messages"])
        count = self.counts.pop(0) if self.counts else int(size / 3.2)
        stream = await super().chat(**kwargs)

        async def counted():
            async for part in stream:
                if part.done:
                    part.prompt_eval_count = count
                yield part
        return counted()

def big_reads(session, mcp, size=3000):
    async def call_tool(name, args):
        mcp.calls.append((name, args))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"{args.get('path')}: " + "x" * size)], is_error=False)
    mcp.call_tool = call_tool

def test_old_tool_responses_make_room_instead_of_the_request_being_dropped(tmp_path):
    read = lambda path: [chunk(tool_calls=[call("read_file_with_metadata", {"path": path})], done=True)]
    session, _, mcp = make_session(tmp_path, [], num_ctx=4000, auto_approve_tools=["read_file_with_metadata"])
    session.client = CountingClient([read("a.py"), read("b.py"), read("c.py"), [chunk("Done.", done=True)]])
    big_reads(session, mcp)
    asyncio.run(session.send("Read the three files"))
    assert session.messages[1] == {"role": "user", "content": "Read the three files"}  # The request stays
    tool_texts = tool_messages(session)
    assert tool_texts[0].startswith("[Removed by Local Fox to make room in the context: the response of read_file_with_metadata(path='a.py')")
    # The call to repeat, asked for only if needed, and only the section of a whole file
    assert ("Read it again only if you need its exact text, and then only the section you need: "
            "read_file_with_metadata(path='a.py', section=...)." in tool_texts[0])
    assert tool_texts[-1].startswith("c.py: xxx")  # The latest step's response stays
    calls = [e for e in session.entries if e.kind == c.TOOL]
    assert calls[0].removed and not calls[-1].removed
    assert any(e.kind == c.NOTICE and "To fit the context" in e.text for e in session.entries)
    assert session.entries[-1].kind == c.ASSISTANT and session.entries[-1].text == "Done."
    assert not any(e.kind == c.ERROR for e in session.entries)

def test_room_is_made_down_to_a_share_of_the_budget(tmp_path):
    # Every removal makes Ollama process the whole prompt again, so room is made seldom, with a margin
    from local_fox.session import ROOM_TARGET
    read = lambda path: [chunk(tool_calls=[call("read_file_with_metadata", {"path": path})], done=True)]
    session, _, mcp = make_session(tmp_path, [], num_ctx=6000, auto_approve_tools=["read_file_with_metadata"])
    session.client = CountingClient([read(f"{n}.py") for n in "abcde"] + [[chunk("Done.", done=True)]])
    big_reads(session, mcp)
    budget = session.num_ctx - max(2048, session.num_ctx // 8)
    estimates = []
    real_fit = session._fit_context

    def fit():
        made = real_fit()
        if session._made_room:
            estimates.append(session._estimate())
        return made
    session._fit_context = fit
    asyncio.run(session.send("Read the files"))
    assert estimates and all(estimate <= budget * ROOM_TARGET for estimate in estimates)

def test_reading_removed_responses_again_asks_the_user(tmp_path):
    reads = lambda *paths: [chunk(tool_calls=[call("read_file_with_metadata", {"path": p}) for p in paths], done=True)]
    session, _, mcp = make_session(tmp_path, [], choices=["Stop"], auto_approve_tools=["read_file_with_metadata"])
    session.client = CountingClient([reads("a.py", "b.py", "c.py"), [chunk("Read.", done=True)],
                                     reads("a.py", "./b.py"), reads("c.py"), [chunk("Done.", done=True)]])
    asyncio.run(session.send("Read the files"))
    for entry in session.entries:
        if entry.kind == c.TOOL:
            entry.removed = True  # As if room had been made for them
    asyncio.run(session.send("Go on"))
    assert len(session.frontend.questions) == 1 and "called 3 tools again" in session.frontend.questions[0]
    assert session.entries[-1].kind == c.NOTICE and session.entries[-1].text == "Stopped."  # "Stop" ended the turn
    assert not any(e.kind == c.ASSISTANT and e.text == "Done." for e in session.entries)

def test_reading_other_parts_is_not_a_re_read(tmp_path):
    reads = lambda *calls_: [chunk(tool_calls=[call("read_file_with_metadata", a) for a in calls_], done=True)]
    session, _, _ = make_session(tmp_path, [], auto_approve_tools=["read_file_with_metadata"])
    session.client = CountingClient([reads({"path": "a.py"}), [chunk("Read.", done=True)],
                                     reads({"path": "a.py", "section": "f"}, {"path": "a.py", "section": "g"},
                                           {"path": "a.py", "start_line": 1, "end_line": 5}), [chunk("Done.", done=True)]])
    asyncio.run(session.send("Read"))
    next(e for e in session.entries if e.kind == c.TOOL).removed = True
    asyncio.run(session.send("Go on"))
    assert session.frontend.questions == [] and session.entries[-1].text == "Done."  # The sections it was asked for

def test_out_of_date_reads_make_room_first(tmp_path):
    read = lambda path: [chunk(tool_calls=[call("read_file_with_metadata", {"path": path})], done=True)]
    session, _, mcp = make_session(tmp_path, [], num_ctx=5800, auto_approve_tools=["read_file_with_metadata"])
    session.client = CountingClient([read("b.py"), read("a.py"), read("a.py"), read("c.py"), [chunk("Done.", done=True)]])
    big_reads(session, mcp)
    asyncio.run(session.send("Read the files"))
    import json
    calls = [e for e in session.entries if e.kind == c.TOOL]
    assert calls[1].removed  # The first read of a.py, which the second read made out of date, went first
    made_room = [json.loads(line) for line in (tmp_path / "logs" / "session.jsonl").read_text(encoding="utf-8").splitlines()
                 if '"made_room"' in line][0]
    assert made_room["removed"][0] == "read_file_with_metadata a.py"  # Before b.py, although b.py is older
    assert "A later call read or changed the same file" in tool_messages(session)[1]

def tool(name, args, response="", status=c.OK):
    return c.Entry(c.TOOL, tool=name, resolved=name, args=args, response=response, status=status)

def read_response(start, end, total, body, limited=False):
    partial = " (partial)" if (start, end) != (1, total) else ""
    return (f"- File: `x`\n- Lines: {start}-{end} of {total}{partial}\n"
            + ("- Output limited: lines are shown, as one read is limited.\n" if limited else "") + f"\n```python\n{body}\n```")

def test_an_edit_makes_out_of_date_only_the_reads_that_showed_its_text():
    calls = [
        tool("read_file_with_metadata", {"path": "a.py", "section": "f"}, read_response(1, 3, 9, "def f():\n    return 1")),
        tool("read_file_with_metadata", {"path": "a.py", "section": "g"}, read_response(5, 7, 9, "def g():\n    return 2")),
        tool("get_outline", {"path": "b.py"}),
        tool("edit_file", {"path": "./a.py", "old_text": "def f():\n  return 1", "new_text": "def f():\n    return 3"}),
        tool("edit_file", {"path": "b.py", "old_text": "x"}, status=c.FAILED),
    ]
    # f's read showed the edited text (with other indentation); g's read is still right; b.py's edit failed
    assert c.superseded(calls) == {0}

def test_other_changes_and_outlines():
    calls = [
        tool("read_file_with_metadata", {"path": "a.py", "section": "g"}, read_response(5, 7, 9, "def g(): pass")),
        tool("get_outline", {"path": "a.py"}, "5-7 g()"),
        tool("edit_file", {"path": "a.py", "old_text": "def f(): pass", "new_text": ""}),  # Line spans shift
        tool("read_file_with_metadata", {"path": "c.py"}, read_response(1, 4, 4, "c")),
        tool("write_file", {"path": "c.py", "content": "new"}),
    ]
    assert c.superseded(calls) == {1, 3}

def test_reads_made_out_of_date_by_reads():
    calls = [
        tool("read_file_with_metadata", {"path": "a.py", "section": "f"}, read_response(1, 3, 9, "f")),
        tool("read_file_with_metadata", {"path": "a.py", "section": "f"}, read_response(1, 3, 9, "f")),  # The same again
        tool("read_file_with_metadata", {"path": "b.py", "section": "f"}, read_response(1, 3, 2000, "f")),
        tool("read_file_with_metadata", {"path": "b.py"}, read_response(1, 1000, 2000, "...")),  # Long file: only a part
        tool("read_file_with_metadata", {"path": "a.py", "section": "g"}, read_response(5, 7, 9, "g")),
        tool("read_file_with_metadata", {"path": "a.py"}, read_response(1, 9, 9, "all")),  # The whole file
    ]
    assert c.superseded(calls) == {0, 1, 4}  # Not b.py's section: the later read did not show all of b.py

def test_call_text():
    assert c.call_text("read_file_with_metadata", {"path": "a.py", "section": "Cart.total"}) == \
        "read_file_with_metadata(path='a.py', section='Cart.total')"
    assert c.call_text("write_file", {"path": "a.py", "content": "line\nline"}) == ""  # Not worth repeating
    assert c.call_text("x", "{broken") == ""

def test_a_cut_by_ollama_stops_the_turn(tmp_path):
    read = lambda path: [chunk(tool_calls=[call("read_file_with_metadata", {"path": path})], done=True)]
    session, _, mcp = make_session(tmp_path, [], auto_approve_tools=["read_file_with_metadata"])
    session.client = CountingClient([read("a.py"), read("b.py"), [chunk("Done.", done=True)]], counts=[30000, 29000])
    asyncio.run(session.send("Read"))
    assert any(e.kind == c.ERROR and "Ollama dropped the beginning of the conversation" in e.text for e in session.entries)
    assert tool_messages(session)[-1] == STOPPED_BEFORE  # The call of the cut reply is answered, not run
    assert len(mcp.calls) == 1

def test_a_request_that_cannot_fit_is_not_sent(tmp_path):
    session, _, _ = make_session(tmp_path, [], num_ctx=2100)
    session.client = CountingClient([[chunk("never", done=True)]])
    asyncio.run(session.send("x" * 20000))
    assert session.client.requests == []
    assert any(e.kind == c.ERROR and "no longer fits in the context" in e.text for e in session.entries)

def test_a_named_file_is_recognized(tmp_path):
    texts = ["Fix the bug in src/app.py.", r"See C:\x\notes.md", "and ./lib/util.py"]
    assert c.mentioned("src/app.py", texts, tmp_path)
    assert c.mentioned("lib/util.py", texts, tmp_path)
    assert c.mentioned("src/app.py", [f"Read {tmp_path.resolve().as_posix()}/src/app.py"], tmp_path)  # The full path
    assert c.mentioned(str(tmp_path / "src" / "app.py"), texts, tmp_path)  # A read by the full path
    assert not c.mentioned("app.py", texts, tmp_path)  # Only src/app.py was named
    assert not c.mentioned("src/app.py", ["Fix src/my_app.py and src/app.pyx"], tmp_path)

def test_files_the_user_named_are_kept_longest(tmp_path):
    read = lambda path: [chunk(tool_calls=[call("read_file_with_metadata", {"path": path})], done=True)]
    session, _, mcp = make_session(tmp_path, [], num_ctx=5800, auto_approve_tools=["read_file_with_metadata"])
    session.client = CountingClient([read("a.py"), read("b.py"), read("c.py"), read("d.py"), [chunk("Done.", done=True)]])
    big_reads(session, mcp)
    asyncio.run(session.send("Explain how a.py works"))
    calls = {e.args["path"]: e for e in session.entries if e.kind == c.TOOL}
    assert calls["b.py"].removed and not calls["a.py"].removed  # a.py is the oldest, but named
    assert not any("which you named" in e.text for e in session.entries if e.kind == c.NOTICE)

def test_a_named_file_goes_last_and_the_feed_says_so(tmp_path):
    read = lambda path: [chunk(tool_calls=[call("read_file_with_metadata", {"path": path})], done=True)]
    session, _, mcp = make_session(tmp_path, [], num_ctx=4000, auto_approve_tools=["read_file_with_metadata"])
    session.client = CountingClient([read("a.py"), read("b.py"), read("a.py"), read("c.py"), [chunk("Done.", done=True)]])
    big_reads(session, mcp)
    asyncio.run(session.send("Read a.py and b.py"))
    calls = [e for e in session.entries if e.kind == c.TOOL]
    assert calls[0].removed  # The first read of a.py: out of date, so first, although a.py is named
    notices = [e.text for e in session.entries if e.kind == c.NOTICE]
    assert any("this included the latest read of" in text and "which you named" in text for text in notices)

def run_reads(tmp_path, num_ctx, names="abcdef"):
    read = lambda path: [chunk(tool_calls=[call("read_file_with_metadata", {"path": path})], done=True)]
    session, _, mcp = make_session(tmp_path, [], num_ctx=num_ctx, auto_approve_tools=["read_file_with_metadata"])
    session.client = CountingClient([read(f"{n}.py") for n in names] + [[chunk("Done.", done=True)]])
    big_reads(session, mcp)
    asyncio.run(session.send("Read the files"))
    return session

def test_the_model_is_told_when_the_context_is_filling(tmp_path):
    session = run_reads(tmp_path, 8750)
    noted = [e for e in session.entries if e.kind == c.TOOL and "[Local Fox: " in e.response]
    assert len(noted) == 1 and not any(e.removed for e in session.entries)  # Once per fill, before room is needed
    assert "Read only what you still need: first write down what you have found" in noted[0].response
    assert tool_messages(session)[-1] == noted[0].response  # The model gets what the feed shows
    assert '"context_note"' in (tmp_path / "logs" / "session.jsonl").read_text(encoding="utf-8")

def test_the_model_is_told_when_room_was_made(tmp_path):
    # One step can fill the rest of the context at once: making room tells the model too
    session = run_reads(tmp_path, 8250)
    full = [e for e in session.entries if e.kind == c.TOOL and "the context was full, so the responses of" in e.response]
    assert full and any(e.removed for e in session.entries)
    assert not full[-1].removed  # On the newest response, which the model reads next

def test_a_users_message_never_gets_a_note(tmp_path):
    session, _, _ = make_session(tmp_path, [])
    session.messages.append({"role": "user", "content": "Explain a.py"})
    session._note_to_model("\n\n[Local Fox: a note]")
    assert session.messages[-1] == {"role": "user", "content": "Explain a.py"}
