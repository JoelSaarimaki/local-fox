"""
The MCP servers of a project's .mcp.json, started over stdio as ollmcp starts them, and the names the
model sees their tools by.

With one server, the model sees the tools by their own names, without a prefix. With several, the names
get the server key as a prefix (dev_read_file_with_metadata). Either way a call is resolved forgivingly:
a bare name that only one server has runs on that server, and a prefixed name (dev_x, dev.x) runs the
tool x of the server dev, so that instructions written for ollmcp's prefixed names keep working.

The server that runs the tools of ollmcp-generic-tools ('python -m ollmcp_tools') is started with Local Fox's
bundled copy of them, unless use_bundled is off (see tools_setup.py).
"""
import asyncio
import json
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .tools_setup import server_command

@dataclass
class ServerTool:
    server: str           # The server key in .mcp.json
    name: str             # The tool's own name
    exposed: str          # The name the model sees
    description: str
    schema: dict

class ServerError(Exception):
    pass

def read_mcp_json(project: Path) -> dict:
    """
    Returns the servers of the project's .mcp.json, {} if it has none. Raises ServerError if it cannot be read.
    """
    path = project / ".mcp.json"
    if not path.exists():
        return {}
    try:
        servers = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
    except (ValueError, AttributeError) as error:
        raise ServerError(f"{path} cannot be read: {error}") from error
    if not isinstance(servers, dict):
        raise ServerError(f"{path}: 'mcpServers' must be an object.")
    return servers

class Servers:
    """
    The running servers of a project. They are entered and closed in one task of their own, as the MCP
    client's task groups must be exited in the task that entered them; tools are called from any task.
    """

    def __init__(self, project: Path, log_dir: Path, extra_env: dict | None = None, use_bundled: bool = True):
        self.project = project
        self.log_dir = log_dir
        self.extra_env = extra_env or {}
        self.use_bundled = use_bundled
        self.sources: dict[str, str] = {}  # Server key -> what it runs, for the feed
        self.sessions: dict[str, ClientSession] = {}
        self.tools: list[ServerTool] = []
        self.errors: list[str] = []  # Servers that did not start, with the reason
        self._task: asyncio.Task | None = None
        self._close = asyncio.Event()

    async def start(self) -> None:
        """
        Starts every server of .mcp.json and lists their tools. A server that does not start is reported in errors.
        """
        ready = asyncio.Event()
        self._task = asyncio.create_task(self._run(ready))
        await ready.wait()
        self._name_tools()

    async def _run(self, ready: asyncio.Event) -> None:
        async with AsyncExitStack() as stack:
            try:
                servers = read_mcp_json(self.project)
            except ServerError as error:
                self.errors.append(str(error))
                servers = {}
            for key, spec in servers.items():
                try:
                    await self._start_one(stack, key, spec)
                except Exception as error:  # A broken server must not stop the others
                    self.errors.append(f"The server '{key}' did not start: {error!r}{self._log_tail(key)}")
            ready.set()
            await self._close.wait()

    async def _start_one(self, stack: AsyncExitStack, key: str, spec: dict) -> None:
        if not isinstance(spec, dict) or not spec.get("command"):
            raise ServerError("it has no 'command'")
        # A stack of its own, so that a server that fails to start is closed at once, and the others stay
        own = AsyncExitStack()
        try:
            await self._enter(own, key, spec)
        except BaseException:
            await own.aclose()
            raise
        stack.push_async_callback(own.aclose)

    async def _enter(self, stack: AsyncExitStack, key: str, spec: dict) -> None:
        command, args, self.sources[key] = server_command(spec, self.use_bundled)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # The server's stderr goes to a log file: a windowed application has no console for it. Appended, so that
        # a restart keeps the output of the run before it; the start of the application empties it (session_log.reset).
        log = stack.enter_context(open(self.log_dir / f"server-{key}.log", "a", encoding="utf-8", errors="replace"))
        log.write(f"--- {datetime.now().isoformat(timespec='seconds')} {self.project.as_posix()}: {self.sources[key]}\n")
        log.flush()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", **{k: str(v) for k, v in (spec.get("env") or {}).items()}, **self.extra_env}
        cwd = Path(spec["cwd"]) if spec.get("cwd") else self.project
        params = StdioServerParameters(command=command, args=args, env=env,
                                       cwd=str(cwd if cwd.is_absolute() else self.project / cwd))
        read, write = await stack.enter_async_context(stdio_client(params, errlog=log))
        session = await stack.enter_async_context(ClientSession(read, write))
        await asyncio.wait_for(session.initialize(), timeout=60)
        listed = (await session.list_tools()).tools
        self.sessions[key] = session
        for tool in listed:
            self.tools.append(ServerTool(key, tool.name, tool.name, tool.description or "", dict(getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {"type": "object"})))

    def _log_tail(self, key: str) -> str:
        try:
            text = (self.log_dir / f"server-{key}.log").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        return f"\n\nThe end of its log:\n{text[-2000:]}" if text else ""

    def _name_tools(self) -> None:
        if len(self.sessions) > 1:
            for tool in self.tools:
                tool.exposed = f"{tool.server}_{tool.name}"

    def payload(self) -> list[dict]:
        """
        Returns the tool definitions in Ollama's format.
        """
        return [{"type": "function", "function": {"name": t.exposed, "description": t.description, "parameters": t.schema}}
                for t in self.tools]

    def resolve(self, name: str) -> ServerTool | None:
        """
        Returns the tool a call names: by the name the model sees, by its bare name if only one server has
        it, or by its name prefixed with its server key ('dev_x', 'dev.x', 'dev__x'). None if no tool matches.
        """
        name = (name or "").strip()
        for tool in self.tools:
            if tool.exposed == name:
                return tool
        bare = [tool for tool in self.tools if tool.name == name]
        if len(bare) == 1:
            return bare[0]
        for tool in self.tools:
            for separator in ("_", ".", "__"):
                if name == f"{tool.server}{separator}{tool.name}":
                    return tool
        return None

    async def call(self, tool: ServerTool, args: dict):
        """
        Calls a tool and returns the MCP result.
        """
        return await self.sessions[tool.server].call_tool(tool.name, args)

    async def close(self) -> None:
        self._close.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()
