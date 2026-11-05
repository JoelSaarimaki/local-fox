# AGENTS.md

What an agent working on Local Fox needs to know. How to use and build it is in [README.md](README.md). The
original design plan is `local-only/plan.md`, kept locally and not in the repository (the `local-only` folder is
git-ignored for such notes); read it before changing behaviour if it is there.

## What the project is

Local Fox is a desktop harness for local models run with Ollama and the MCP tools of
[ollmcp-generic-tools](../ollmcp-generic-tools) (a sibling repository, `C:\Users\JoelSaarimäki\Documents\GIT\ollmcp-generic-tools`).
It replaces ollmcp. It has a core without UI dependencies (`local_fox/`) and a PySide6 window (`local_fox/ui/`),
packaged as a Windows executable with PyInstaller (and, untested, a macOS application bundle).

The tools are **bundled, not copied**: the `ollmcp_tools` package is installed into `.venv` from the sibling
repository and packed into the executable. The tools stay developed in their own repository, which other MCP
clients and its `local_ai_tests` use too; don't copy their code into this one. Changes to the tools belong there.

## The default model: flint:40b

`flint:40b` is the default model (`DEFAULTS["model"]` in `local_fox/config.py`) and the preferred one.

- ollmcp-generic-tools recommends `gemma4:26b` only because of ollmcp: flint calls tools by their bare names
  (`read_file_with_metadata` instead of `dev_read_file_with_metadata`), and ollmcp skips such calls without a
  response, so flint repeats them (one test run made 70 such calls and never finished). gemma4 does not do this.
- Local Fox is built to tolerate it: with one server the model sees the tools by their bare names, and
  `Servers.resolve()` in `local_fox/mcp_servers.py` also runs prefixed names (`dev_x`, `dev.x`, `dev__x`) and a bare
  name that only one server has. Any name it cannot resolve gets "Unknown tool x. The tools are: …" as a tool response.
- Checked on 2026-11-03: flint:40b fixed a bug through the core with 18 tool calls, all by bare names, all resolved.
- Keep this tolerance when changing tool naming; it is what makes flint usable. Don't change the default back to
  gemma4 because of the ollmcp-generic-tools README: that recommendation is about ollmcp, not Local Fox.
- Other installed models used in tests: `gemma4:26b`, `qwen3-coder:30b`, `gpt-oss:120b`, `llama3.1` (small and quick for smoke tests).

## Principles (from the design plan, keep them)

- **The model is prompted only in a set-up project** (`tools_setup.is_set_up`: a tools config in the project's
  folder; a `.mcp.json` alone is not enough). `Session.start` raises `NotSetUp` otherwise, the window opens only
  a project (`ui/project_dialog.py` before it, `MainWindow.open_project` checks again), and the CLI exits.
  **Setting up is deliberate**: only on the user's explicit action, after seeing `tools_setup.planned()`; never
  automatic, and `tools_setup.refusal()` refuses drive and disk roots, home folders, `~/Library` and system folders in every front end
  (`is_set_up` is False for them even with a tools config). Don't weaken either.
- **The start dialog** (`ui/project_dialog.py`, run by `ui/app.py` before the window): recent projects
  (`recent_projects.py`, in the data folder, not a setting) and Choose folder (with Open on the right; Quit and Open config folder on the left); no preselection, no default button,
  Enter ignored; the setup question has Set up the project and Back. Closing a dialog quits at the start and keeps the
  open project later. `MainWindow` takes no project in its constructor: `app.py` calls `open_project` after the dialogs.
  Its fox is the logo, braille dots in `ui/logo.py` `FOX` (its only copy; edit it there), painted dot by dot
  (`fox_pixmap`): as text, braille comes from a fallback font with gaps between the lines that break the picture.
  The window and application icon is the same fox, one pixel per dot (`fox_image`); the build scripts write
  `build/local_fox.ico` (`write_ico`) or, on macOS, `build/local_fox.icns` (`write_icns`).

- **Everything that goes to the model is visible** in the feed: the instructions, the filemap, the tool definitions,
  every tool call and response, error responses, thinking. Every part of `Session.messages` must have an `Entry`.
- **No hidden automation**: Local Fox never sends a message on its own (no "Continue." after a stall, no silent
  retries). Decisions go through `Frontend.decide()` and the user chooses. The one exception is the user's own
  choice: auto mode (the checkbox beside "Send", `Session.auto`, off at every start) answers a decision that has
  an option of `AUTO_ANSWERS` ("Allow", "Continue") with it, logged with `auto` and, for "Continue", shown as a
  notice. It answers only questions inside a running turn; it never sends a message, so a stop, an error or a
  full context still ends the turn. Don't widen it to other options (e.g. "Larger context") without asking.
  The same goes for Ollama: `ollama serve` starts only from the panel's "Start Ollama server" button (`MainWindow._start_ollama`),
  never on its own or after a failed request, and the server it started is stopped when the window closes.
  "Stop Ollama server" stops any Ollama server of this computer at the address (the user may want a visible one
  in a console instead of a hidden one): found by its port (`ollama_server.other_server`), only if the program
  is Ollama, and after the user confirms it when Local Fox did not start it.
- **Every tool call gets a response**, including unknown names, broken JSON, missing arguments, skipped and stopped calls.
- **Settings in one place**: `config.json` (`local_fox/config.py`). A new setting needs a default in `DEFAULTS`,
  a help text in `DESCRIPTIONS` (the settings view shows it; a test checks every setting has one), a check in
  `validate()` if it is not a plain bool/int/str, and a row in the README's settings table. A setting whose
  default is `null` also needs its kind in `KINDS` of `ui/settings_view.py`. Unknown keys are an error on load.
- **Fitting the context** (`Session._fit_context`, `_counted`): before each request, earlier tool responses are
  shortened or replaced with a note if the request would not fit (the user's and model's messages and the latest step's
  responses never). In the order below, out-of-date reads are removed whole and the others first shortened
  (`condense.py`): a read keeps the functions and classes the conversation named since the read (the model's thinking,
  replies and call arguments, and the user's messages; never tool responses, and only the current conversation, as
  the entries start again at "Clear"), the rest keeps its first line and a `⋯ lines a-b left out ⋯` marker; Markdown
  is kept whole if its path was named since, otherwise as headings; a text search keeps the files named since. Only
  if that is not enough are whole responses replaced, in the same order. Replayed on a logged session (2026-11-05),
  keeping the parts named since lost about 28 % less of what the model mentioned later per token freed than whole
  removal; broader rules (repeated words, the prompt's words) kept far more without losing less. The order:
  out-of-date reads first (`conversation.superseded`: the same part read again, the whole file read later, an
  edit of text the read showed, or any other change of the file), then the
  oldest, and last the latest current read of each file the user named in their messages (`conversation.named_reads`,
  `mentioned`): kept longest, never protected for good, as named files could fill the context and stop the turn; the note gives the call to repeat (`call_text`), but only if the model needs the exact text, and for a
  whole file only the section it needs, as an invitation to re-read made flint read back each removed file. Room is
  made down to `ROOM_TARGET` (three quarters) of the budget: every removal makes Ollama process the whole prompt again
  (2 to 3 minutes for 55 000 tokens on the CPU), so it must be seldom. After `REREADS_TO_ASK` calls in one turn
  that repeat a removed or shortened call (`conversation.same_call`), the user is asked (`_ask_about_rereads`). The model is told
  that its context fills (`_note_to_model`: `CONTEXT_WARNING` once per fill at `WARN_AT`, `ROOM_MADE_NOTE` whenever
  room is made, as one step can fill the rest at once), at the end of the newest tool response and of its entry, never
  in a message of the user; appending at the end keeps Ollama's cached prompt. They are marked in
  the feed (`Entry.removed`, `Entry.shortened`, which holds the shortened response the model has).
  The order comes from the calls, never from the model: don't add a tool for the model to flag what it needs (it
  costs definitions on every request, and the small models judge it poorly). If it still does not fit, it is not sent; and a drop in
  Ollama's prompt count without such a removal means Ollama cut the conversation, which stops the turn. This is the
  only place Local Fox changes what the model already had, and it is always shown in the feed. The k-th tool message
  answers the k-th tool entry, which is how a message finds its entry.
- **Interrupted replies are marked** (`STOPPED_MARK`, `CUT_OFF_MARK` in `session.py`) in the entry's text and the
  message alike, so the model never gets an unfinished reply as a finished one, and the user sees the same text.
  Continuing is only ever the user's next message: no Continue button, no automatic resume.
  A reply that keeps repeating itself (`conversation.repeating`: a line 12 times outside code blocks, or 3 lines in
  10 code blocks, in the last 20 000 characters) is stopped as a user's Stop would, marked `REPEATING_MARK`, and
  ends the turn with a notice (`Session._stop_repeating`). gemma4:26b looped for 50 000 characters; the limits are high
  because a reply that drafted one class ten times still ended with a good call. Tune them against logged replies.
- **No queue**: while a turn runs (or was sent and has not started, `MainWindow._busy`), a message is not taken
  and stays in the prompt; the user stops the model first. Don't add queued or interrupting sends without asking.
- **Clipboard**: only Qt's own Ctrl + C / X / V, plus the filemap's explicit Copy path; nothing copies or pastes on
  its own. Pasting must never send (`PromptInput.insertFromMimeData` inserts plain text; only a typed Enter in
  `keyPressEvent` emits `submitted`). The feed keeps the user's selection across re-renders (`Feed._restore_selection`).
- **The project's Local Fox folder** is the folder of its tools config (`filemap.project_folder()`): `.local-fox`
  for projects set up by Local Fox, `.ollmcp-tools` for ones set up by the tools' init. Never hard-code either
  name; use `project_folder()` / `instructions_path()`. The tools take their folder from `.mcp.json`'s
  `MCP_TOOLS_CONFIG`; only their init and templates name `.ollmcp-tools`, so `tools_setup.set_up` writes the
  templates itself with the paths moved (`_in_folder`), and uses only the tools' public `locations` module.
- **Before the first prompt** of a conversation, `Session._refresh_start` reads the instructions and the filemap
  again (the window may have been open while files changed) and updates their entries and the system message;
  after it, the system message stays as the model got it.
- **The tools config** is read by the servers only at their start (`Session.tools_config_text` is what they got).
  The window watches the file (`MainWindow._tools_config_touched`) and offers "Restart tools and clear" when it
  changes; it never restarts them on its own, as that starts a new conversation.
- **Saved conversations** (`saved_conversations.py`) go to `<project folder>/conversations/*.json`, after every turn
  (`send`'s finally) and on close (after the turn has stopped, so its calls have responses). They hold the entries
  from the first prompt on and the messages without the system message; a reopened conversation gets the system
  message, instructions, filemap and tools as they are now (`Session.reopen`). The folder is forbidden to the
  tools and has its own `.gitignore` (`*`). Adding a field to `Entry` is compatible: unknown fields are dropped on load.
- The tools' own config (`.local-fox/tools-config.json`) is changed only by the user, never by the model:
  through the settings view's Project tools tab and the filemap's "Add path to …" actions (`tools_config.py`).
  The tools read it when they start, so a change applies when they restart, which starts a new conversation.
- **One log per run** (`session_log.py`): `logs/session.jsonl` next to `config.json`, appended through the run,
  plus the servers' stderr in `logs/server-<key>.log`. Every start of the application (the window, the CLI,
  `tools/compare.py`) empties them with `session_log.reset()`, so the log is ready right after a failure; there
  are deliberately no settings for it. New things sent to the model or decided by the user should be logged too.
- **Secrets** (`config.SECRETS`: the `ollama_api_key`): logged only as whether they are set (`config.without_secrets`),
  hidden in the settings view. The key goes to the tools' server alone, in `MCP_TOOLS_OLLAMA_API_KEY`
  (`tools_setup.API_KEY_ENV_VAR`, `Servers._enter`); the tools keep it from the commands they run. The variable is
  always set for the tools' server, empty for no key, so the tools never take a key from their user config file
  (`~/.ollmcp-tools/user-config.json`, which Local Fox neither creates nor reads): the setting is the only place.

## Layout

| Path | What |
|---|---|
| `local_fox/session.py` | The session loop: system message, streaming from Ollama, tool calls, approvals, stop, step limit, context full |
| `local_fox/mcp_servers.py` | Starts the servers of `.mcp.json`, tool payload and name resolution |
| `local_fox/tools_setup.py` | The bundled tools: which server runs them and how, `--tools-server`, setting up a project |
| `local_fox/templates/instructions.md` | Local Fox's project instructions template, written into a project by the setup |
| `local_fox/tools_config.py` | The user's changes to the project's tools config: checks, path lists, the filemap's actions |
| `local_fox/filemap.py` | The tools' path rules (forbidden, allowed, read-only, ignored) and the filemap for the model |
| `local_fox/session_log.py` | The JSON-lines log of the run, emptied at every start |
| `local_fox/saved_conversations.py` | Saving, listing and loading the project's conversations |
| `local_fox/recent_projects.py` | The recent projects of the start dialog |
| `local_fox/conversation.py` | `Entry`, the feed's data, and what is read from the entries: out-of-date and named reads, repeated calls, loops (`repeating`) |
| `local_fox/condense.py` | Shortening old tool responses to the parts the conversation mentioned since, to make room in the context |
| `local_fox/ollama_errors.py` | Ollama's errors explained for the feed, with what to try first |
| `local_fox/ollama_server.py` | The Ollama panel's data: polling `ollama ps`, the activity text, starting and stopping `ollama serve` |
| `local_fox/shell_path.py` | macOS: the login shell's PATH, added at the start |
| `local_fox/config.py`, `export.py`, `cli.py` | Settings, Markdown export, the command line for testing the core |
| `local_fox/__main__.py` | The entry point of the window and the executable; `--tools-server` runs the bundled tools instead |
| `local_fox/ui/` | The window: `app.py` (the start), `project_dialog.py`, `main_window.py`, `settings_view.py`, `feed.py` + `render.py` (HTML), `filemap_view.py`, `ollama_panel.py`, `prompt.py`, `bridge.py`, `theme.py` (the palette and stylesheets), `logo.py` (the fox and the icons) |
| `tools/compare.py` | Runs ollmcp-generic-tools' comparison scenarios through the core |
| `packaging/local_fox.spec`, `build.ps1`, `build.sh` | The executable (Windows), the application (macOS, Linux) |
| `tests/` | Unit tests with a fake Ollama client and fake MCP servers |

## Environment and commands

- **macOS has basic support, never run on a Mac**: `shell_path.py` (the login shell's PATH, as an application
  started from the Finder gets only the system's), the config in `~/Library/Application Support`, Fusion style and
  Mac fonts (`ui/app.py`, `theme.FONT_FAMILIES`), `/Volumes/<disk>` and `~/Library` refused, `build.sh` and the spec's
  `BUNDLE` with an `.icns` from the logo (`logo.write_icns`). Keep new code portable: no Windows-only calls without a
  `sys.platform` check, paths through `pathlib`; `tests/test_macos.py` simulates macOS where it can.
- Windows 11, Python 3.14. The project's own virtual environment is `.venv` (PySide6, ollama, mcp, markdown, pytest,
  pyinstaller, and ollmcp-tools with tree-sitter, installed non-editable from `..\ollmcp-generic-tools`). After
  changing the tools, reinstall them into `.venv` (the build scripts do it on every build) or the old copy runs.
- Tests: `.venv\Scripts\python -m pytest`. Set `LOCAL_FOX_CONFIG` to a scratch file when running anything, so the
  user's `%APPDATA%\LocalFox\config.json` is not created or changed. Don't build while the user has Local Fox open
  (check for the `LocalFox` process): the build replaces the running executable's files.
- Command line: `.venv\Scripts\python -m local_fox.cli --project <folder> --yes "message"`.
- Window: `.venv\Scripts\python -m local_fox [project]`. Build: `.\build.ps1` → `dist\LocalFox\LocalFox.exe`.
- `tools/compare.py` runs with the **system** Python (`python`), which has ollmcp-generic-tools (editable, with its
  `local_ai_tests`) and ollmcp installed; the `.venv` does not have ollmcp. The core imports nothing from PySide6,
  so this works, and the bundled-tools rule then runs the system Python's (editable) tools.
- The tools' server of `.mcp.json` (`... -m ollmcp_tools`) runs with the bundled tools (`tools_setup.server_command`):
  `sys.executable -m ollmcp_tools` from the source, `LocalFox.exe --tools-server` from the executable. Other
  servers, and all servers with `bundled_tools: false`, run with the command of `.mcp.json`.
- A throwaway test project: an empty folder with a file or two, then `--setup` on the command line (or "Set up
  the project" in the start dialog). Don't run write tasks against real repositories.
- Ollama runs at `http://127.0.0.1:11434`. Model runs take from tens of seconds to several minutes; run long ones in the background.

## Pitfalls already met

- **mcp 2.x uses snake_case fields**: `Tool.input_schema`, `CallToolResult.is_error`, `ImageContent.mime_type`
  (not `inputSchema`, `isError`, `mimeType`). The code reads both.
- **MCP client contexts must be exited in the task that entered them** (anyio cancel scopes). `Servers` runs them in
  one task of its own (`_run`) and closes them through an event; tools may be called from any task.
- **The session runs in an asyncio loop in a background thread** (`ui/bridge.py`). The window talks to it with
  `runner.submit()`/`runner.call()`, and the session talks back only through Qt signals. Don't touch Qt widgets from the loop thread.
- **A windowed executable has no stderr**: the servers' stderr goes to `logs/server-<key>.log` in the data folder,
  and an exception in a Qt slot disappears without a trace (test the window's code paths, as below).
  It has no console either, so a console program started under it opens a console window of its own: the tools
  start every program with `CREATE_NO_WINDOW` (`NO_WINDOW` in their `common.py`). Run from source, under python.exe's
  hidden console, no windows appear, so check this with the built executable. The one deliberate exception is
  the `ollama serve` of "Start Ollama server", which the user should see and be able to stop: a console window of
  its own on Windows (`CREATE_NEW_CONSOLE`), Terminal on macOS and a terminal on Linux (`ollama_server.terminal_command`,
  followed by its port as `TerminalServer`, as the terminal runs it, not Local Fox); hidden with a log only without a terminal.
  The macOS and Linux paths are tested only by simulation (`tests/test_macos.py`).
- **PyInstaller**: `collect_submodules("mcp")` must leave out `mcp.cli` (it calls `sys.exit` on import); `mcp.server`
  is needed by the bundled tools. The tools import their toolsets by name, so the spec collects `ollmcp_tools`'
  submodules, its data files (the setup templates), its metadata (the version in the feed) and the tree-sitter
  modules. An editable install is not followed by PyInstaller, so the build scripts install the tools non-editable.
- **Qt's `clicked` passes `checked`**: PySide gives it to a slot that takes optional arguments, so "Clear" called
  `_clear(False)` and failed silently. `main_window._button` calls its slot without arguments; connect other
  signals the same way, and test a button with `click()`, not by calling its slot.
- **Selected text keeps its own color** (`theme.keep_selected_text_colors`: a highlighted-text brush without a
  color). Setting `selection-color` in the stylesheet would give all selected text one color again.
- **The tools read their config when imported** (`common.py`), so each server is a process of its own, never
  imported into Local Fox. Only `ollmcp_tools.locations` (no config), `ollmcp_tools.parsing` (outlines of text,
  for `condense.py`; it reads no config and no files) and the package's `templates` are used in-process. If the tools' init or templates change (new files, renamed settings), carry it over to
  `tools_setup._template_files`.
- **The tools' rules stay in the tools**: `tools_config.check` checks only the kinds of the values. Don't copy the
  tools' validation; a server that does not start with a config shows its error (and its log's end) in the feed.
- **8.3 short paths** (`C:\Users\JOELSA~1\...`, e.g. from `%TEMP%`) differ from the long ones under
  `os.path.abspath`; compare paths with `Path.resolve()`, which expands them.
- **Windows PowerShell 5.1** treats a native command's stderr as an error under `$ErrorActionPreference = "Stop"`;
  `build.ps1` runs PyInstaller through `cmd /c` for that reason.
- **Screenshots of the window without a display**: `QT_QPA_PLATFORM=offscreen` and `QT_QPA_FONTDIR=C:/Windows/Fonts`
  (without the font dir, all text renders as boxes); `window.grab().save(path)`.
- Qt's rich text supports only part of CSS; style the feed in `ui/theme.py` (`feed_css`) and check it in a screenshot.
- **Instructions template**: the setup writes Local Fox's own `local_fox/templates/instructions.md` into a project,
  not the tools' template, which names the tools with ollmcp's `dev_` prefix and is written for ollmcp's start.
  It follows the workflow of the tools' template; when that template's workflow changes, carry the change over.
  It must name only existing tools (a test checks this against the tools' `TOOLSETS`). Measure any shortening with
  `tools/compare.py`: a version of about 930 tokens (2026-11-03, kept in `local-only/`) kept the modes but read the
  needed files first in 6 of 14 runs instead of 11 of 14 (results in ollmcp-generic-tools' local_ai_test_results.md). Projects set up earlier
  still have the `dev_` names; the name resolution covers them. `tools/compare.py` uses Local Fox's template by
  default (`--instructions tools` for the tools' one). The template is package data (pyproject, and the spec's `datas`).
- Notices in the feed are plain text with their line breaks kept (`render.py`), not Markdown.
- **Errors in the feed are orange** (`.error` uses `ACCENT`), and say what to try: Ollama's errors go through
  `ollama_errors.explain` / `explain_connection`, which add an explanation before Ollama's own message. Add a case there
  for a new failure that users meet, with a test in `tests/test_ollama_errors.py`.
- **`commands.json` starts empty** in a new project (in Local Fox and in the tools' init); `commands-examples.json`
  beside it has the former default commands to copy from, and nothing reads it. The tools' comparison harness copies
  the examples in, as its scenarios run tests.
- **What is open in the feed** is decided by the session (`Session._open_latest`): the latest tool call, thinking
  and answer are open, and opening a newer one collapses only the one before it, so what the user opened stays
  open. `Entry.collapsed` is a tool call's body or an assistant entry's thinking; `Entry.answer_collapsed` is
  its answer. Prompts are never collapsible.
- **The feed writes each entry in a frame of its own** (`ui/feed.py`): an update rewrites only the frames whose HTML
  changed (no `setHtml` of the whole document, which flashed and grew slow), and sets a frame's format only when its
  box state changes, as that lays the whole document out again. An expanded tool call, instructions, filemap or tools
  entry is boxed (`theme.EXPANDED`); every frame has the same margins, border and padding (`FRAME_MARGIN`, `FRAME_PADDING`, the
  border and background drawn only for a box), so a heading never moves when its entry opens, and the empty lines Qt keeps
  between frames are hidden; an entry that shows nothing has an empty, hidden frame. The view follows the end only for new content (`render(follow=True)`), never when
  the user opens or closes an entry. Long lines in `<pre>` wrap (`white-space: pre-wrap`).
- Bash heredocs that contain Python with quotes have failed to parse in this environment, and a `\\` in them has
  arrived as `\`; write longer patch scripts to the scratchpad with the Write tool and run them, or use the Edit tool.

## Conventions

- **In texts shown to the user, put button, menu and command names in quotation marks**: 'choose "Set up the
  project"', 'the "Stop" button', '"Open project" in the ≡ menu'. Without them the sentences are hard to read.
  Don't mention `.ollmcp-tools` or ollmcp-tools in introductions: users are not assumed to have them.
- Follow the style of ollmcp-generic-tools: a docstring on every module and function, written as plain sentences
  saying what it does and why; comments for the reasons that are not obvious.
- Keep the palette in `ui/theme.py`; every color in the window and the feed is one of its constants.
- Add a unit test in `tests/` for session behaviour changes (`tests/test_session.py` has the fakes).
- The project is a git repository (`main`). Commit when asked; build output (`build/`, `dist/`), `.venv` and `runs/` are
  ignored, and `packaging/local_fox.spec` is kept although the `.gitignore` template ignores `*.spec`.

## Not built yet

An automatic context size.
