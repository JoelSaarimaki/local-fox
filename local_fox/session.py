"""
A session with a model: the project instructions and filemap at the start, the user's messages, the model's
replies streamed from Ollama, and its tool calls run on the project's MCP servers.

Everything that goes to the model has an entry in the feed (Session.entries), and Local Fox sends nothing on
its own: when something needs a decision (an approval, the step limit), the frontend is asked, and the user decides.
"""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import httpx
import ollama

from . import conversation as c
from . import saved_conversations as saved
from . import tools_setup
from .config import data_dir, without_secrets
from .conversation import Entry, call_text, estimate_tokens, format_number, main_argument, named_reads, same_call, superseded
from .filemap import Rules, instructions_path, model_filemap, tools_config_path
from .mcp_servers import Servers
from .ollama_errors import explain, explain_connection
from .session_log import LOG_NAME, SessionLog

WRITE_TOOLS = {"write_file", "edit_file", "move_file", "delete_file", "restore_file"}
RESTART_TOOLS = "Restart tools and clear"  # The action that restarts the MCP servers (the window does it)
APPROVAL_OPTIONS = ["Allow", "Allow all for this session", "Skip", "Stop"]
SKIPPED_RESPONSE = "The user skipped this call."
STOPPED_BEFORE = "The user stopped the session before this call ran."
# Added to a reply that did not end on its own, in the feed and in the history the model gets, so that neither
# the user nor the model takes it as finished
STOPPED_MARK = "\n\n[The user stopped this reply here.]"
# Making room brings the request down to this share of the context's budget, not just under it: every removal changes
# the start of the conversation, and Ollama then processes the whole prompt again, about 55 000 tokens taking two to
# three minutes on a laptop's CPU. Removing a little before every step made every step that slow.
ROOM_TARGET = 0.75
# The re-reads of removed responses in one turn after which the user is asked whether to go on: the model reading
# again what was removed for room, which fills the context and removes the next, is a loop without progress.
REREADS_TO_ASK = 3
CUT_OFF_MARK = "\n\n[This reply was cut off here: the context was full.]"
# The parts of the feed of which the latest is kept open
TOOL_CALL, THINKING, ANSWER = "tool call", "thinking", "answer"
STOPPED_WHILE ="The user stopped the session while this call ran; its result is unknown."

class Frontend:
    """
    What a session tells the user and asks from them. The defaults do nothing and allow every tool call,
    for running without a user, e.g. in the comparison scripts.
    """

    def reset(self) -> None:
        """The conversation was cleared: the feed starts again from Session.entries."""

    def changed(self, entry: Entry, new: bool) -> None:
        """An entry was added (new) or changed."""

    def status(self) -> None:
        """The model, the context size, the tokens used, the running state or the changed files changed."""

    async def decide(self, question: str, options: list[str], entry: Entry | None) -> str:
        """Asks the user to choose one of the options, e.g. whether a tool call may run."""
        return options[0]

class NotSetUp(Exception):
    """The folder is not set up as a project, so no session starts in it."""

class StopTurn(Exception):
    """The user chose Stop in a decision."""

def is_error(text: str) -> bool:
    """
    Checks if a tool response is an error, in the text or the JSON format of the tools.
    """
    return text.startswith(("Error", "Unknown tool")) or '"success": false' in text

class Session:
    def __init__(self, config: dict, project: Path, frontend: Frontend | None = None, extra_env: dict | None = None,
                 log_dir: Path | None = None):
        self.config = dict(config)
        self.project = Path(project).resolve()
        self.frontend = frontend or Frontend()
        self.extra_env = extra_env
        self.log_dir = log_dir or data_dir() / "logs"
        self.model: str = config["model"]
        self.num_ctx: int = config["num_ctx"]
        self.client = ollama.AsyncClient(host=config["ollama_host"])
        self.servers: Servers | None = None
        self.rules: Rules | None = None
        self.entries: list[Entry] = []
        self.messages: list[dict] = []
        self.allow_all = False
        self.changed_files: set[Path] = set()
        self.used_tokens = 0
        self.running = False
        self.capabilities: list[str] | None = None
        self.max_context: int | None = None
        self._instructions = ""
        self._filemap = ""
        self._task: asyncio.Task | None = None
        self.log = SessionLog(self.log_dir / LOG_NAME)
        self._logged = 0                       # The messages other than the system message already in the log
        self._logged_system: str | None = None
        self._call_started: dict[int, float] = {}
        self._latest: dict[str, Entry] = {}  # The open tool call, thinking and answer (_open_latest)
        self.saved_path: Path | None = None  # Where the conversation is saved, from its first turn on
        self._created: str | None = None
        self._save_failed = False
        self.tools_config_text: str | None = None  # The tools config the servers started with
        # Fitting the context (_fit_context): tokens per character of the requests, learned from Ollama's counts, as
        # code has fewer characters per token than prose; and Ollama's count of the last request, to notice a cut
        self._tokens_per_char = 1 / 3.2
        self._last_prompt_tokens = 0
        self._request_chars = 0
        self._made_room = False
        self._rereads = 0  # Calls in this turn of tools whose responses were removed to make room (_ask_about_rereads)
        self._cut_by_ollama = False

    # --- Entries ---

    def add(self, entry: Entry) -> Entry:
        self.entries.append(entry)
        self.frontend.changed(entry, True)
        if entry.kind in (c.NOTICE, c.ERROR):
            self.log.write(entry.kind, text=entry.text)
        return entry

    def notice(self, text: str, actions: list[str] | None = None) -> Entry:
        return self.add(Entry(c.NOTICE, text=text, actions=actions or []))

    def error(self, text: str) -> Entry:
        return self.add(Entry(c.ERROR, text=text))

    def _update(self, entry: Entry) -> None:
        self.frontend.changed(entry, False)

    # --- Start, clear and stop ---

    async def start(self) -> None:
        """
        Starts the project's MCP servers, reads the model's details and starts the first conversation. Raises
        NotSetUp for a folder that is not set up as a project: the model is never prompted outside one.
        """
        if not tools_setup.is_set_up(self.project):
            raise NotSetUp(f"{self.project.as_posix()} is not set up as a project: it has no .local-fox folder with a tools config. "
                           "Set it up first (/setup in the window, --setup on the command line).")
        self.rules = Rules(self.project)
        self.servers = self._new_servers()
        self.tools_config_text = self.read_tools_config()
        await self.servers.start()
        await self.load_model_info()
        await self.new_conversation()

    async def restart_servers(self) -> None:
        """
        Restarts the MCP servers, e.g. after the tools config changed, as the tools read it when they start, and
        starts a new conversation, so that the model gets the tools and the filemap as they are now.
        """
        await self.stop_and_wait()
        if self.servers:
            await self.servers.close()
        self.servers = self._new_servers()
        self.tools_config_text = self.read_tools_config()
        await self.servers.start()
        await self.new_conversation()

    def _new_servers(self) -> Servers:
        """
        Returns the project's MCP servers, to be started, with the settings as they are now: whether the tools run
        bundled, and the Ollama API key for the tools' web toolset.
        """
        return Servers(self.project, self.log_dir, self.extra_env, self.config.get("bundled_tools", True),
                       api_key=self.config.get("ollama_api_key", ""))

    def read_tools_config(self) -> str | None:
        """
        Returns the text of the project's tools config, or None if it cannot be read. The servers read it when they
        start, so the text they started with tells whether a change of the file is applied yet.
        """
        try:
            return tools_config_path(self.project).read_text(encoding="utf-8")
        except OSError:
            return None

    async def close(self) -> None:
        """
        Stops a running turn, so that its calls get their responses, saves the conversation and stops the servers.
        """
        await self.stop_and_wait()
        self.save_conversation()
        self.log.write("close")
        if self.servers:
            await self.servers.close()

    # --- Saved conversations ---

    def save_conversation(self) -> None:
        """
        Saves the conversation in the project's conversations folder, once it has a prompt. A failure is shown once.
        """
        if not any(e.kind == c.USER for e in self.entries):
            return
        if self.saved_path is None:
            self.saved_path = saved.new_path(self.project)
            self._created = datetime.now().isoformat(timespec="seconds")
        try:
            saved.save(self.saved_path, self.entries, self.messages, self.model, self.num_ctx, self._created)
        except OSError as error:
            if not self._save_failed:
                self._save_failed = True
                self.error(f"The conversation could not be saved to {self.saved_path.as_posix()}: {error}")

    async def reopen(self, path: Path) -> None:
        """
        Reopens a saved conversation: the current one is saved, and a new conversation starts with the instructions,
        filemap and tools as they are now, followed by the saved entries and messages, so that the model continues
        from them with the next prompt. Raises ValueError if the file cannot be read.
        """
        await self.stop_and_wait()
        self.save_conversation()
        data, entries, messages = saved.load(path)
        await self.new_conversation()
        self.entries.extend(entries)
        self.messages.extend(messages)
        self.saved_path, self._created = path, data.get("created")
        for entry in entries:  # The latest of each stays open until a newer one arrives
            if entry.kind == c.TOOL:
                self._latest[TOOL_CALL] = entry
            elif entry.kind == c.ASSISTANT:
                if entry.thinking:
                    self._latest[THINKING] = entry
                if entry.text:
                    self._latest[ANSWER] = entry
        self.log.write("reopen", path=str(path), entries=len(entries), messages=len(messages))
        self.used_tokens = self.estimate_context()
        self.frontend.reset()
        model = data.get("model")
        self.notice(f"Reopened the conversation “{data.get('title', '')}” of {data.get('created', '?').replace('T', ' ')}"
                    f"{f', held with {model}' if model and model != self.model else ''}. The model gets it with the instructions, "
                    "filemap and tools as they are now, and continues from it with your next message.")
        self.frontend.status()

    async def load_model_info(self) -> str | None:
        """
        Reads the model's capabilities and largest context size from Ollama. Returns an error message if it cannot.
        """
        self.capabilities, self.max_context = None, None
        try:
            info = await self.client.show(self.model)
        except ollama.ResponseError as error:
            return explain(error.error, self.model, self.num_ctx)
        except (httpx.HTTPError, ConnectionError) as error:
            return explain_connection(error, self.config["ollama_host"])
        self.capabilities = list(info.capabilities or [])
        for key, value in (info.modelinfo or {}).items():
            if key.endswith(".context_length") and isinstance(value, int):
                self.max_context = value
        self.frontend.status()
        return None

    async def new_conversation(self) -> None:
        """
        Starts a new conversation: the instructions, the filemap and the tools again, and approvals asked again.
        """
        self.entries, self.messages = [], []
        self._latest = {}
        self._last_prompt_tokens = 0
        self.saved_path, self._created = None, None
        self.allow_all = False
        self.changed_files = set()
        self.rules = Rules(self.project)
        self.frontend.reset()
        self._start_log()
        for message in self.servers.errors if self.servers else []:
            self.error(message)
        self._read_instructions(reloaded=False)
        if self.config.get("filemap_for_model", True):
            self._filemap, files, folders_only = model_filemap(self.rules, self.config.get("filemap_max_tokens", 2000))
            what = f"{files} files, folders only" if folders_only else f"{files} files"
            self.add(Entry(c.FILEMAP, text=self._filemap, title=f"Filemap ({what}, {format_number(estimate_tokens(self._filemap))} tokens)"))
        else:
            self._filemap = ""
        if self.servers and self.servers.tools:
            payload = json.dumps(self.servers.payload())
            sources = "  \n".join(f"Server {key}: {self.servers.sources[key]}" for key in self.servers.sessions if key in self.servers.sources)
            text = "\n\n".join(([sources] if sources else []) + [f"**{t.exposed}**\n\n{t.description.strip()}" for t in self.servers.tools])
            self.add(Entry(c.TOOLS, text=text, title=f"Tools ({len(self.servers.tools)} tools, {format_number(estimate_tokens(payload))} tokens)"))
            self.log.write("tools", tools=self.servers.payload())
        else:
            self.notice("No tools: the project has no MCP servers in .mcp.json, or none of them started.")
        if error := (None if self.capabilities is not None else await self.load_model_info()):
            self.error(error)
        self._set_system_message()
        self.used_tokens = self.estimate_context()
        self.frontend.status()

    def _refresh_start(self) -> None:
        """
        Reads the filemap and the instructions again before the first prompt of a conversation, as files may have
        been added, removed or edited since it started (the window may have been open for a while). What changed is
        updated in its feed entry and in the system message, so the model starts from the project as it is and the
        user sees what it gets. After the first prompt they stay as they are: the model finds later changes with its
        tools, and a system message that changed under it would confuse it.
        """
        changed = []
        path = instructions_path(self.project)
        try:
            instructions = self._with_source(path, path.read_text(encoding="utf-8").strip())
        except OSError:
            instructions = ""
        if instructions != self._instructions:
            self._instructions = instructions
            entry = next((e for e in self.entries if e.kind == c.INSTRUCTIONS), None)
            title = f"Project instructions, read again before the first prompt ({self._shown(path)}, {format_number(estimate_tokens(instructions))} tokens)"
            if entry:
                entry.text, entry.title = instructions, title
                self._update(entry)
            elif instructions:
                self.entries.insert(0, Entry(c.INSTRUCTIONS, text=instructions, title=title))
                self.frontend.reset()
            changed.append("instructions")
        if self.config.get("filemap_for_model", True):
            self.rules = Rules(self.project)
            filemap, files, folders_only = model_filemap(self.rules, self.config.get("filemap_max_tokens", 2000))
            if filemap != self._filemap:
                self._filemap = filemap
                what = f"{files} files, folders only" if folders_only else f"{files} files"
                entry = next((e for e in self.entries if e.kind == c.FILEMAP), None)
                if entry:
                    entry.text = filemap
                    entry.title = f"Filemap, read again before the first prompt ({what}, {format_number(estimate_tokens(filemap))} tokens)"
                    self._update(entry)
                changed.append("filemap")
        if changed:
            self._set_system_message()
            self.used_tokens = self.estimate_context()

    def _start_log(self) -> None:
        """
        Marks a new conversation in the log, with the settings it runs with, as a clear reads them again.
        """
        self._logged, self._logged_system = 0, None
        servers = {key: {"runs": self.servers.sources.get(key), "tools": sum(1 for t in self.servers.tools if t.server == key)}
                   for key in self.servers.sessions} if self.servers else {}
        self.log.begin(project=str(self.project), model=self.model, num_ctx=self.num_ctx, settings=without_secrets(self.config), servers=servers,
                       server_errors=list(self.servers.errors) if self.servers else [])
        if self.log.error:
            self.notice(self.log.error)
            self.log.error = None

    def _with_source(self, path: Path, text: str) -> str:
        """
        Returns the instructions as the model gets them: after a line that says which file they are and that the model
        already has them. Without it, a model asked to read the instructions (e.g. by a task that names the file) does
        not know it has them, and looks for the file. Empty instructions stay empty.
        """
        if not text:
            return ""
        return (f"These are the project instructions, from `{self._shown(path)}`. You already have them: there is no need "
                f"to read that file.\n\n{text}")

    def _read_instructions(self, reloaded: bool) -> None:
        path = instructions_path(self.project)
        try:
            self._instructions = self._with_source(path, path.read_text(encoding="utf-8").strip())
        except OSError:
            self._instructions = ""
            self.notice(f"No project instructions: {path.as_posix()} was not found. "
                        "The session runs without them.")
            return
        label = "Project instructions reloaded" if reloaded else "Project instructions"
        self.add(Entry(c.INSTRUCTIONS, text=self._instructions, title=f"{label} ({self._shown(path)}, {format_number(estimate_tokens(self._instructions))} tokens)"))

    def _shown(self, path: Path) -> str:
        """
        Returns a path as the feed shows it: relative to the project folder, as the tools take paths.
        """
        try:
            return path.resolve().relative_to(self.project).as_posix()
        except ValueError:
            return path.as_posix()

    def _set_system_message(self) -> None:
        content = "\n\n".join(part for part in (self._instructions, self._filemap) if part)
        self.messages = [m for m in self.messages if m["role"] != "system"]
        if content:
            self.messages.insert(0, {"role": "system", "content": content})
        if content != self._logged_system:
            self.log.write("system", content=content)
            self._logged_system = content

    def reload_instructions(self) -> None:
        """
        Reads the instructions again, e.g. after editing them, and gives them to the model from the next request.
        """
        self._read_instructions(reloaded=True)
        self._set_system_message()
        self._made_room = True  # The system message changed: a smaller count next is not a cut by Ollama
        self.frontend.status()

    def add_history(self, messages: list[dict]) -> None:
        """
        Adds earlier user and assistant messages to the conversation, e.g. for a scenario that continues a session.
        """
        for message in messages:
            entry = Entry(c.USER if message["role"] == "user" else c.ASSISTANT, text=message["content"])
            if entry.kind == c.ASSISTANT:
                self._open_latest(ANSWER, entry)
            self.add(entry)
            self.messages.append({"role": message["role"], "content": message["content"]})

    def stop(self) -> None:
        """
        Stops the running turn: the generation or the tool call in progress. Call it in the session's event loop.
        """
        if self._task and not self._task.done():
            self._task.cancel()

    async def stop_and_wait(self) -> None:
        """
        Stops the running turn and waits until it has ended, e.g. before a clear.
        """
        task = self._task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.wait([task], timeout=10)

    # --- Settings of the session ---

    def estimate_context(self) -> int:
        """
        Estimates the tokens of the conversation and the tool definitions, before Ollama has counted them.
        """
        tools = json.dumps(self.servers.payload()) if self.servers else ""
        return estimate_tokens(tools + "".join(str(m.get("content", "")) for m in self.messages))

    def set_context(self, size: int) -> str | None:
        """
        Sets the context size for the session. Returns an error message if the model cannot take the size.
        """
        if size < 512:
            return "The context size must be at least 512 tokens."
        if self.max_context and size > self.max_context:
            return f"{self.model} takes at most {format_number(self.max_context)} tokens of context."
        self.num_ctx = size
        self.log.write("setting", num_ctx=size)
        self.frontend.status()
        return None

    def larger_context(self) -> str | None:
        """
        Doubles the context size, up to the model's largest.
        """
        size = self.num_ctx * 2
        if self.max_context:
            if self.num_ctx >= self.max_context:
                return f"The context is already the largest {self.model} takes: {format_number(self.max_context)} tokens."
            size = min(size, self.max_context)
        return self.set_context(size)

    async def apply_settings(self, config: dict) -> None:
        """
        Takes the settings read again from config.json, e.g. before a clear: a changed Ollama address, model
        or context size applies at once, the others from the next conversation or request.
        """
        old = self.config
        self.config = dict(config)
        if config["ollama_host"] != old.get("ollama_host"):
            self.client = ollama.AsyncClient(host=config["ollama_host"])
            self.capabilities = None  # Read again from the new address by the next conversation
        if config["model"] != self.model:
            self.model = config["model"]
            self.capabilities = None
        self.num_ctx = config["num_ctx"]

    async def set_model(self, model: str) -> str | None:
        self.model = model
        self._tokens_per_char, self._last_prompt_tokens = 1 / 3.2, 0  # Another tokenizer
        error = await self.load_model_info()
        if not error and self.max_context and self.num_ctx > self.max_context:
            self.num_ctx = self.max_context
        self.log.write("setting", model=model, num_ctx=self.num_ctx, error=error)
        self.frontend.status()
        return error

    async def list_models(self) -> list[str]:
        return sorted(m.model for m in (await self.client.list()).models)

    async def placement(self) -> str:
        """
        Returns where the model runs, as 'ollama ps' shows it: '77 % CPU / 23 % GPU', or 'not loaded'.
        """
        try:
            loaded = (await self.client.ps()).models
        except Exception:
            return "Ollama not reachable"
        for model in loaded:
            if model.model == self.model or model.name == self.model:
                if not model.size:
                    return "loaded"
                gpu = round(100 * (model.size_vram or 0) / model.size)
                if gpu >= 100:
                    return "100 % GPU"
                return "100 % CPU" if gpu <= 0 else f"{100 - gpu} % CPU / {gpu} % GPU"
        return "not loaded"

    def think_setting(self):
        think = self.config.get("think")
        if think is None:
            return True if self.capabilities and "thinking" in self.capabilities else None
        return think

    # --- A turn ---

    async def send(self, text: str) -> None:
        """
        Sends the user's message and runs the model, and its tool calls, until it answers or the user stops it.
        """
        if self.running:
            raise RuntimeError("The model is already running.")
        self._task = asyncio.current_task()
        self.running = True
        self.frontend.status()
        if not any(e.kind == c.USER for e in self.entries):
            self._refresh_start()
        self.add(Entry(c.USER, text=text))
        self.messages.append({"role": "user", "content": text})
        try:
            await self._turn()
        except (asyncio.CancelledError, StopTurn):
            self.notice("Stopped.")
        except Exception as error:
            self.error(f"{type(error).__name__}: {error}")
        finally:
            self.running = False
            self._task = None
            self.save_conversation()  # After every turn, also a stopped or failed one
            self.frontend.status()

    async def _turn(self) -> None:
        steps = 0
        self._rereads = 0
        while True:
            limit = self.config.get("max_steps")
            if limit and steps >= limit:
                choice = await self._decide(
                    f"The model has made {steps} requests in this turn, the limit set in config.json. Continue?", ["Continue", "Stop"], None)
                if choice != "Continue":
                    raise StopTurn()
                steps = 0
            steps += 1
            if not self._fit_context():
                self.error(f"The conversation no longer fits in the context of {format_number(self.num_ctx)} tokens, even "
                           "without the responses of the earlier tool calls, so it was not sent. Start a new conversation, "
                           "export this one, or make the context larger.")
                self.notice("The context is full.", ["Clear", "Export", "Larger context"])
                return
            reply = await self._generate()
            if reply is None:
                return
            entry, calls = reply
            if self._cut_by_ollama:
                await self._refuse_calls(calls)
                self.error(f"Ollama dropped the beginning of the conversation, as it did not fit in the context of "
                           f"{format_number(self.num_ctx)} tokens: the model no longer had all of it, perhaps not even your "
                           "request, so the turn was stopped. Make the context larger, or start a new conversation.")
                self.notice("The context is full.", ["Clear", "Export", "Larger context"])
                return
            if entry.stop_reason == "length":
                self.notice(f"The reply stopped because the context is full: {format_number(self.num_ctx)} tokens.",
                            ["Clear", "Export", "Larger context"])
                return
            if not calls:
                if not entry.text.strip():
                    self.notice("The model ended its turn without a reply or a tool call.")
                return
            await self._run_calls(calls)
            await self._ask_about_rereads()

    async def _ask_about_rereads(self) -> None:
        """
        Asks the user whether to go on when the model has called again, REREADS_TO_ASK times in this turn, tools
        whose responses were removed to make room: each re-read fills the context, which removes the next response,
        and every removal makes the next request slow. Local Fox decides nothing itself; the user can continue, make
        the context larger, or stop.
        """
        if self._rereads < REREADS_TO_ASK:
            return
        count, self._rereads = self._rereads, 0
        choice = await self._decide(
            f"The model has called {count} tools again whose responses were removed to make room in the context of "
            f"{format_number(self.num_ctx)} tokens. The context is too small for how it works on this task, and each "
            "removal makes the next request slower, as Ollama processes the whole conversation again. Continue?",
            ["Continue", "Larger context", "Stop"], None)
        if choice == "Stop":
            raise StopTurn()
        if choice == "Larger context":
            error = self.larger_context()
            self.notice(error or f"The context size is {format_number(self.num_ctx)} tokens for this session.")

    # --- Fitting the context ---

    def _chars(self) -> int:
        """
        Returns the characters of the next request: the tool definitions and the messages, without their images.
        """
        tools = json.dumps(self.servers.payload(), ensure_ascii=False) if self.servers else ""
        messages = sum(len(json.dumps({k: v for k, v in m.items() if k != "images"}, ensure_ascii=False)) for m in self.messages)
        return len(tools) + messages

    def _estimate(self) -> int:
        """
        Estimates the tokens of the next request, with the ratio learned from Ollama's counts, and 800 per image.
        """
        images = sum(len(m.get("images") or []) for m in self.messages)
        return int(self._chars() * self._tokens_per_char) + 800 * images

    def _counted(self, prompt_tokens: int) -> None:
        """
        Takes Ollama's count of the request just sent. Ollama does not fail on a request longer than the context: it
        drops the oldest messages. The count then is smaller than the one before, although messages were only added
        (unless Local Fox made room itself), and the model no longer had all of the conversation.
        """
        if not prompt_tokens:
            return
        if self._last_prompt_tokens and prompt_tokens < self._last_prompt_tokens and not self._made_room:
            self._cut_by_ollama = True
            self.log.write("cut", prompt_tokens=prompt_tokens, before=self._last_prompt_tokens, num_ctx=self.num_ctx)
        elif self._request_chars:
            self._tokens_per_char = prompt_tokens / self._request_chars
        self._last_prompt_tokens = prompt_tokens
        self._made_room = False

    def _fit_context(self) -> bool:
        """
        Makes the next request fit in the context, leaving room for the reply, before it is sent: if it would not
        fit, the responses of earlier tool calls are replaced with a short note until it does. Out-of-date reads go
        first (a file read again or changed later, see `superseded`), then the oldest responses, and last the latest
        reads of the files the user named in their messages (`named_reads`). The note gives the
        call that gets the content back, but asks the model to call it only if it needs the exact text, and for a whole
        file to read only the section it needs. Room is made down to ROOM_TARGET of the budget, so that it is made
        seldom. The responses of the latest step stay, as do all messages of the user and the model, so the model keeps
        its task. The replaced calls are marked in the feed. Returns False if the request does not fit even then. Without this, Ollama would drop the oldest messages, the user's request first.
        The order is decided from the calls alone, not by asking the model what it still needs: the small models
        judge that poorly, and a tool for it would add to the definitions sent with every request.
        """
        budget = self.num_ctx - max(2048, self.num_ctx // 8)
        if self._estimate() <= budget:
            return True
        room_goal = int(budget * ROOM_TARGET)
        tool_indexes = [i for i, m in enumerate(self.messages) if m.get("role") == "tool"]
        last_reply = max((i for i, m in enumerate(self.messages) if m.get("role") == "assistant"), default=-1)
        tool_entries = [e for e in self.entries if e.kind == c.TOOL]  # One response message per tool call, in order
        matched = len(tool_entries) == len(tool_indexes)
        stale = superseded(tool_entries) if matched else set()
        user_texts = [m.get("content") or "" for m in self.messages if m.get("role") == "user"]
        named = named_reads(tool_entries, user_texts, self.project, stale) if matched else set()
        # The responses before the latest step: out-of-date reads first, then the rest, and last the files the user
        # named; each group oldest first
        candidates = [k for k, i in enumerate(tool_indexes) if i <= last_reply]
        candidates.sort(key=lambda k: (0 if k in stale else 2 if k in named else 1, k))
        removed, tokens, named_removed = [], 0, []
        for k in candidates:
            i = tool_indexes[k]
            message = self.messages[i]
            entry = tool_entries[k] if matched else None
            if (entry and entry.removed) or len(message.get("content", "")) < 400:
                continue
            size = int(len(message["content"]) * self._tokens_per_char)
            name = entry.tool if entry else message.get("tool_name", "tool")
            target = main_argument(entry.args) if entry else ""
            again = call_text(name, entry.args) if entry else ""
            why = (" A later call read or changed the same file, so this content was out of date." if k in stale else "")
            whole_file = (entry and (entry.resolved or entry.tool) == "read_file_with_metadata" and isinstance(entry.args, dict)
                          and not any(entry.args.get(key) for key in ("section", "start_line", "end_line")))
            if whole_file and again:
                how = (f" Read it again only if you need its exact text, and then only the section you need: "
                       f"{again[:-1]}, section=...).")
            elif again:
                how = f" Call {again} again only if you need its exact content."
            else:
                how = " Call the tool again only if you need its exact content."
            self.messages[i] = {k2: v for k2, v in message.items() if k2 != "images"} | {"content": (
                f"[Removed by Local Fox to make room in the context: the response of {again or (name + (' ' + target if target else ''))}, "
                f"about {format_number(size)} tokens.{why}{how}]")}
            if entry:
                entry.removed = True
                self._update(entry)
            removed.append(f"{name} {target}".strip())
            if k in named:
                named_removed.append(target)
            tokens += size
            if self._estimate() <= room_goal:
                break
        if removed:
            self._made_room = True
            self.log.write("made_room", removed=removed, tokens=tokens, estimate=self._estimate(), budget=budget,
                           named_removed=named_removed)
            named_note = (f" Nothing else was left to remove, so this included the latest read of {', '.join(named_removed)}, "
                          "which you named: the context is small for this task." if named_removed else "")
            self.notice(f"To fit the context, the responses of {len(removed)} earlier tool call{'s' if len(removed) > 1 else ''} "
                        f"(about {format_number(tokens)} tokens) were replaced with a note for the model, which can call them "
                        f"again. They are marked in the feed.{named_note}")
            self.used_tokens = self._estimate()
            self.frontend.status()
        return self._estimate() <= budget

    async def _refuse_calls(self, calls: list) -> None:
        """
        Answers the tool calls of a reply that is not acted on, so that every call has its response.
        """
        for call in calls:
            entry = self.add(Entry(c.TOOL, tool=call.function.name, args=call.function.arguments, status=c.WAITING))
            self._respond(entry, STOPPED_BEFORE, c.STOPPED)

    async def _decide(self, question: str, options: list[str], entry: Entry | None) -> str:
        """
        Asks the user through the frontend, and logs the answer.
        """
        try:
            choice = await self.frontend.decide(question, options, entry)
        except asyncio.CancelledError:
            self.log.write("decision", question=question, options=options, choice=None)
            raise
        self.log.write("decision", question=question, options=options, choice=choice)
        return choice

    def _log_request(self, tools: list | None, think) -> None:
        """
        Logs a request to Ollama: its settings and the messages added since the request before, so that the
        system message and the messages of all the requests make up what the model was given.
        """
        history = [m for m in self.messages if m["role"] != "system"]
        new = [{**m, "images": f"<{len(m['images'])} images>"} if m.get("images") else m for m in history[self._logged:]]
        self._logged = len(history)
        self.log.write("request", model=self.model, options=self._options(), think=think, tools=len(tools or []), messages=new)

    def _options(self) -> dict:
        options = {"num_ctx": self.num_ctx}
        if self.config.get("temperature") is not None:
            options["temperature"] = self.config["temperature"]
        if self.config.get("seed") is not None:
            options["seed"] = self.config["seed"]
        return options

    async def _generate(self) -> tuple[Entry, list] | None:
        """
        Streams one reply of the model into an assistant entry. Returns the entry and the tool calls, or None
        after an error, which is shown in the feed.
        """
        entry = self.add(Entry(c.ASSISTANT, streaming=True))
        calls = []
        started = time.monotonic()
        counts = {}
        tools = (self.servers.payload() if self.servers else None) or None
        think = self.think_setting()
        self._log_request(tools, think)
        self._request_chars = self._chars()
        self._cut_by_ollama = False

        def log_reply(**fields):
            self.log.write("reply", content=entry.text, thinking=entry.thinking or None, done_reason=entry.stop_reason,
                           seconds=round(time.monotonic() - started, 2), **counts, **fields)
        try:
            stream = await self.client.chat(model=self.model, messages=self.messages, tools=tools,
                                            options=self._options(), think=think, stream=True)
            async for chunk in stream:
                message = chunk.message
                if message.thinking:
                    if not entry.thinking:
                        self._open_latest(THINKING, entry)
                    entry.thinking += message.thinking
                if message.content:
                    if not entry.text:
                        self._open_latest(ANSWER, entry)
                    entry.text += message.content
                if message.tool_calls:
                    calls.extend(message.tool_calls)
                if chunk.done:
                    entry.stop_reason = chunk.done_reason or "stop"
                    self.used_tokens = (chunk.prompt_eval_count or 0) + (chunk.eval_count or 0) or self.used_tokens
                    self._counted(chunk.prompt_eval_count or 0)
                    counts = {"prompt_tokens": chunk.prompt_eval_count, "output_tokens": chunk.eval_count,
                              "load_seconds": _seconds(getattr(chunk, "load_duration", None)),
                              "prompt_seconds": _seconds(getattr(chunk, "prompt_eval_duration", None)),
                              "output_seconds": _seconds(getattr(chunk, "eval_duration", None))}
                self._update(entry)
        except asyncio.CancelledError:
            entry.streaming, entry.stop_reason = False, "stopped"
            if entry.text:  # What has arrived stays, also for the model, marked so that it is not taken as finished
                entry.text += STOPPED_MARK
                self.messages.append({"role": "assistant", "content": entry.text})
            self._update(entry)
            log_reply(tool_calls=_logged_calls(calls))
            raise
        except ollama.ResponseError as error:
            entry.streaming = False
            self._update(entry)
            log_reply(error=error.error)
            self.error(explain(error.error, self.model, self.num_ctx))
            return None
        except (httpx.HTTPError, ConnectionError) as error:
            entry.streaming = False
            self._update(entry)
            log_reply(error=str(error))
            self.error(explain_connection(error, self.config["ollama_host"]))
            return None
        entry.streaming = False
        if entry.stop_reason == "length":
            entry.text += CUT_OFF_MARK  # The model sees in later requests that the reply did not end on its own
        self._update(entry)
        log_reply(tool_calls=_logged_calls(calls))
        message = {"role": "assistant", "content": entry.text}
        if calls:
            message["tool_calls"] = [{"function": {"name": call.function.name, "arguments": _arguments_dict(call.function.arguments)}}
                                     for call in calls]
        self.messages.append(message)
        self.frontend.status()
        return entry, calls

    def _open_latest(self, part: str, entry: Entry) -> None:
        """
        Opens a part of an entry, a tool call, the thinking or the answer, as the latest of its kind, and collapses
        the one that was the latest before it, so that the feed shows the latest of each open and stays short. Entries
        the user opened themselves stay as they are.
        """
        previous = self._latest.get(part)
        if previous is not None and previous is not entry:
            if part == ANSWER:
                previous.answer_collapsed = True
            else:
                previous.collapsed = True
            self._update(previous)
        self._latest[part] = entry
        if part == ANSWER:
            entry.answer_collapsed = False
        else:
            entry.collapsed = False

    async def _run_calls(self, calls: list) -> None:
        removed = [e for e in self.entries if e.kind == c.TOOL and e.removed]
        entries = [self.add(Entry(c.TOOL, tool=call.function.name, args=call.function.arguments, status=c.WAITING))
                   for call in calls]
        for i, entry in enumerate(entries):
            try:
                self._open_latest(TOOL_CALL, entry)  # Open while it is asked for and runs, and after, until the next
                self._update(entry)
                await self._run_call(entry)
                if any(same_call(entry, earlier) for earlier in removed):
                    self._rereads += 1  # Its name is resolved now, so a prefixed name counts too
            except (asyncio.CancelledError, StopTurn):
                # Every call gets a response, also those the user stopped
                for later in entries[i:]:
                    if later.status in (c.WAITING, c.RUNNING):
                        self._respond(later, STOPPED_WHILE if later.status == c.RUNNING else STOPPED_BEFORE, c.STOPPED)
                raise

    def _respond(self, entry: Entry, text: str, status: str, images: list | None = None) -> None:
        entry.response, entry.status = text, status
        message = {"role": "tool", "content": text, "tool_name": entry.tool}
        if images:
            message["images"] = [data for data, _ in images]
        self.messages.append(message)
        started = self._call_started.pop(entry.id, None)
        self.log.write("tool", called=entry.tool, ran=f"{entry.server}:{entry.resolved}" if entry.resolved else None, args=entry.args,
                       status=status, response=text, images=len(entry.images or images or []),
                       seconds=round(time.monotonic() - started, 2) if started else None)
        self._update(entry)

    async def _run_call(self, entry: Entry) -> None:
        tool = self.servers.resolve(entry.tool) if self.servers else None
        if tool is None:
            names = ", ".join(t.exposed for t in self.servers.tools) if self.servers and self.servers.tools else "none"
            self._respond(entry, f"Unknown tool {entry.tool}. The tools are: {names}.", c.FAILED)
            return
        entry.resolved, entry.server = tool.name, tool.server
        args = entry.args
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError as error:
                self._respond(entry, f"Error: the arguments of {tool.exposed} are not valid JSON ({error}). Call it again with its arguments as a JSON object.", c.FAILED)
                return
        if args is None:
            args = {}
        if not isinstance(args, dict):
            self._respond(entry, f"Error: the arguments of {tool.exposed} must be a JSON object, not {type(args).__name__}.", c.FAILED)
            return
        entry.args = args = dict(args)
        missing = [name for name in tool.schema.get("required", []) if name not in args]
        if missing:
            properties = tool.schema.get("properties", {})
            described = ", ".join(f"{name} ({properties.get(name, {}).get('type', 'any')}{', required' if name in tool.schema.get('required', []) else ''})"
                                  for name in properties)
            self._respond(entry, f"Error: the call to {tool.exposed} is missing the required argument{'s' if len(missing) > 1 else ''} "
                                 f"{', '.join(missing)}. Its arguments are: {described}.", c.FAILED)
            return
        auto = self.config.get("auto_approve_tools") or []
        if not (self.allow_all or tool.name in auto or tool.exposed in auto):
            target = main_argument(args)
            choice = await self._decide(f"Run {tool.exposed}{'  ' + target if target else ''}?", APPROVAL_OPTIONS, entry)
            if choice == "Skip":
                self._respond(entry, SKIPPED_RESPONSE, c.SKIPPED)
                return
            if choice == "Stop":
                raise StopTurn()
            if choice == "Allow all for this session":
                self.allow_all = True
        entry.status = c.RUNNING
        self._call_started[entry.id] = time.monotonic()
        self._update(entry)
        try:
            result = await self.servers.call(tool, args)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if _server_stopped(error):
                # Every later call would fail the same way, so the model is told to stop calling tools, and the user
                # is offered the restart; the cause, often a lack of memory, is in the server's log
                self._respond(entry, f"Error: the tools' server stopped, so {tool.exposed} could not run. Do not call tools "
                                     "until the user has restarted them.", c.FAILED)
                self.error(f"The tools' server '{tool.server}' stopped, so the tools cannot run. Restarting them starts a new "
                           f"conversation. The end of its log is in {(self.log_dir / f'server-{tool.server}.log').as_posix()}.")
                self.notice("Restart the tools?", [RESTART_TOOLS])
            else:
                self._respond(entry, f"Error: the call to {tool.exposed} failed: {error}", c.FAILED)
            return
        text = "".join(getattr(part, "text", "") for part in result.content)
        images = [(part.data, getattr(part, "mime_type", None) or getattr(part, "mimeType", "")) for part in result.content if getattr(part, "type", "") == "image"]
        if images and not (self.capabilities and "vision" in self.capabilities):
            self.notice(f"{self.model} cannot see images, so the image{'s' if len(images) > 1 else ''} of this {tool.name} call "
                        f"{'are' if len(images) > 1 else 'is'} not passed to it.")
            entry.images = images
            images = []
        else:
            entry.images = images
        failed = bool(getattr(result, "is_error", None) or getattr(result, "isError", False)) or is_error(text)
        self._respond(entry, text, c.FAILED if failed else c.OK, images)
        if not failed and tool.name in WRITE_TOOLS and self.rules:
            for key in ("path", "source", "destination"):
                if isinstance(args.get(key), str):
                    path = Path(args[key])
                    self.changed_files.add((path if path.is_absolute() else self.rules.base / path).resolve())
            self.frontend.status()

def _server_stopped(error: BaseException) -> bool:
    """
    Checks if a tool call failed because its server process is gone: the MCP client's streams are closed.
    """
    names = {type(e).__name__ for e in (error, error.__cause__, error.__context__) if e is not None}
    return bool(names & {"ClosedResourceError", "BrokenResourceError", "EndOfStream"}) or "connection closed" in str(error).lower()

def _seconds(nanoseconds) -> float | None:
    """
    Returns one of Ollama's durations, given in nanoseconds, in seconds.
    """
    return round(nanoseconds / 1e9, 2) if nanoseconds else None

def _logged_calls(calls: list) -> list[dict] | None:
    """
    Returns the tool calls of a reply as the model made them, for the log.
    """
    return [{"name": call.function.name, "arguments": call.function.arguments} for call in calls] or None

def _arguments_dict(arguments) -> dict:
    """
    Returns a call's arguments as a dict for the conversation history: Ollama takes no other form.
    """
    if isinstance(arguments, dict):
        return dict(arguments)
    if isinstance(arguments, str):
        try:
            value = json.loads(arguments)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
