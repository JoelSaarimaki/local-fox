"""
A small command line for testing the core without the window:

    python -m local_fox.cli [--project FOLDER] [--model M] [--context N] [--yes] [--setup] ["first message"]

It runs a session in the project folder and prints the feed as it arrives. Tool calls ask for approval
on the console unless --yes is given. --setup sets the folder up for the bundled tools first. Type /clear, /instructions, /context N, /export FILE or /quit.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import config as config_module
from . import conversation as c
from .conversation import Entry, format_number, main_argument
from .export import to_markdown
from .session import APPROVAL_OPTIONS, Frontend, Session
from .session_log import reset
from .tools_setup import SetupRefused, is_set_up, set_up

class ConsoleFrontend(Frontend):
    def __init__(self, approve_all: bool, verbose: bool):
        self.approve_all = approve_all
        self.verbose = verbose
        self.printed: dict[int, tuple[int, int]] = {}  # Entry id -> characters of thinking and text printed

    def changed(self, entry: Entry, new: bool) -> None:
        if entry.kind == c.ASSISTANT:
            thinking, text = self.printed.get(entry.id, (0, 0))
            if self.verbose and len(entry.thinking) > thinking:
                print(f"\033[2m{entry.thinking[thinking:]}\033[0m", end="", flush=True)
            if len(entry.text) > text:
                if text == 0:
                    print("\n", end="")
                print(entry.text[text:], end="", flush=True)
            self.printed[entry.id] = (len(entry.thinking), len(entry.text))
            if not entry.streaming and entry.text:
                print()
        elif entry.kind == c.TOOL and entry.status not in (c.WAITING, c.RUNNING):
            mark = {c.OK: "ok", c.FAILED: "error", c.SKIPPED: "skipped", c.STOPPED: "stopped"}[entry.status]
            print(f"  [{entry.tool}  {main_argument(entry.args)}  {mark}]")
            if self.verbose:
                print("    " + entry.response[:600].replace("\n", "\n    "))
        elif new and entry.kind in (c.INSTRUCTIONS, c.FILEMAP, c.TOOLS):
            print(f"[{entry.title}]")
        elif new and entry.kind in (c.NOTICE, c.ERROR):
            print(f"[{'Error: ' if entry.kind == c.ERROR else ''}{entry.text}]")

    async def decide(self, question: str, options: list[str], entry: Entry | None) -> str:
        if self.approve_all and options == APPROVAL_OPTIONS:
            return "Allow"
        if entry and entry.kind == c.TOOL:
            print(json.dumps(entry.args, indent=2, ensure_ascii=False)[:1500])
        keys = "/".join(f"{i + 1}={o}" for i, o in enumerate(options))
        while True:
            answer = (await asyncio.to_thread(input, f"{question} [{keys}] ")).strip()
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]

async def main() -> None:
    parser = argparse.ArgumentParser(description="Runs a Local Fox session on the command line.")
    parser.add_argument("message", nargs="?", default=None)
    parser.add_argument("--project", default=".")
    parser.add_argument("--model", default=None)
    parser.add_argument("--context", type=int, default=None)
    parser.add_argument("--yes", action="store_true", help="Allow every tool call without asking.")
    parser.add_argument("--verbose", action="store_true", help="Print the thinking and the tool responses.")
    parser.add_argument("--setup", action="store_true", help="Set the folder up for the tools first; existing files are kept.")
    args = parser.parse_args()
    # A Windows console or redirected output may not take every character a model writes (e.g. emoji); a print
    # that fails would end the turn, so such characters are replaced instead
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    settings = config_module.load()
    if args.model:
        settings["model"] = args.model
    if args.context:
        settings["num_ctx"] = args.context
    reset(config_module.data_dir() / "logs")  # Every start begins with an empty log
    project = Path(args.project).resolve()
    if args.setup:
        try:
            print(set_up(project))
        except SetupRefused as error:
            sys.exit(f"Not set up: {error}")
    elif not is_set_up(project):
        sys.exit(f"{project.as_posix()} is not set up as a project, so the model is not prompted there. "
                 "Give a project's folder with --project, or set this one up with --setup.")
    frontend = ConsoleFrontend(args.yes, args.verbose)
    session = Session(settings, project, frontend)
    await session.start()
    print(f"{session.model}, context {format_number(session.num_ctx)}, {format_number(session.used_tokens)} tokens used")
    pending = args.message
    try:
        while True:
            text = pending if pending is not None else (await asyncio.to_thread(input, "\n> ")).strip()
            pending = None
            if not text:
                continue
            if text in ("/quit", "/exit"):
                break
            if text == "/clear":
                await session.new_conversation()
            elif text == "/instructions":
                session.reload_instructions()
            elif text.startswith("/context"):
                parts = text.split()
                if len(parts) > 1:
                    print(session.set_context(int(parts[1])) or f"Context size {format_number(session.num_ctx)}")
                else:
                    print(f"{format_number(session.used_tokens)} / {format_number(session.num_ctx)} tokens")
            elif text.startswith("/export "):
                Path(text.split(" ", 1)[1]).write_text(to_markdown(session.entries, session.model, str(session.project)), encoding="utf-8")
            else:
                task = asyncio.create_task(session.send(text))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    task.cancel()
                    await task
                print(f"\n({format_number(session.used_tokens)} / {format_number(session.num_ctx)} tokens)")
                if args.message is not None and not sys.stdin.isatty():
                    break
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        await session.close()

if __name__ == "__main__":
    asyncio.run(main())
