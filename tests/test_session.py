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
    assert tool_texts[0].startswith("[Removed by Local Fox to make room in the context: the response of read_file_with_metadata a.py")
    assert tool_texts[-1].startswith("c.py: xxx")  # The latest step's response stays
    calls = [e for e in session.entries if e.kind == c.TOOL]
    assert calls[0].removed and not calls[-1].removed
    assert any(e.kind == c.NOTICE and "To fit the context" in e.text for e in session.entries)
    assert session.entries[-1].kind == c.ASSISTANT and session.entries[-1].text == "Done."
    assert not any(e.kind == c.ERROR for e in session.entries)

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
