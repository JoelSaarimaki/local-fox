# Local Fox

A harness for local AI models run with Ollama (`flint:40b` by default, or e.g. `gemma4:26b`) and the MCP tools of
[ollmcp-generic-tools](../ollmcp-generic-tools). It replaces ollmcp; what it does differently is in "How a session
goes to the model" below.

![The Local Fox window: the project's files on the left, and on the right the conversation with flint:40b fixing a bug, with its tool calls, thinking and answer](docs/window.png)

## Running it

Local Fox needs Ollama running with a model pulled. The tools of ollmcp-generic-tools are bundled: no Python with
the tools installed is needed.

- **The executable**: build it with `.\build.ps1` (or `.\build.ps1 -ToolsRepo <path>`), which creates `.venv`
  with the dependencies if needed, installs the tools from the sibling `ollmcp-generic-tools` repository into it,
  and writes `dist\LocalFox\LocalFox.exe`. Copy the `dist\LocalFox` folder anywhere and start `LocalFox.exe`,
  optionally with a project folder as its argument.
- **From the source**: `python -m venv .venv`, `.venv\Scripts\python -m pip install -r requirements.txt` and
  `.venv\Scripts\python -m pip install ..\ollmcp-generic-tools`, then `.venv\Scripts\python -m local_fox [project folder]`.

### Choosing the project

Local Fox starts by asking for the project, before the window opens:

![The start dialog: the fox logo, the name and version, the recent projects, and the "Quit", "Open config folder", "Choose folder" and "Open" buttons](docs/start-dialog.png)

- **The start dialog** shows the name and version, and lists the recent projects (missing folders dimmed; right-click to remove one from the list)
  and has **Choose folder** (next to **Open**), **Quit**, and **Open config folder** (the folder of `config.json`). Nothing is selected at first and Enter does nothing: a project opens with **Open**
  or a double click.
- **A folder that is not a project yet** gets the setup question: the folder, exactly what setting it up would
  create or change, **Set up the project**, and **Back** to the start dialog. A drive root or system folder gets
  only the reason and **Back**.
- **Closing either dialog (X, Esc, Quit) quits Local Fox** before anything else starts.
- A folder given as an argument (`LocalFox.exe C:\...\project`) opens at once if it is a project, otherwise it
  starts with the setup question for it.
- **Open project** in the ≡ menu (and `/project`) uses the same dialogs later; closing them then keeps the
  open project.

### The bundled tools

- **Only a project is prompted.** A folder is a project when it is set up: it has a `.local-fox` folder with
  the tools config (or the `.ollmcp-tools` folder of the tools' own init). A `.mcp.json` alone does not count.
  The window only ever opens a project, and the command line refuses to start in any other folder.
- **Setting up is always deliberate**: only from the setup question (above), or `--setup` on the command line.
  `/setup` (**Set up project** in the ≡ menu) creates an open project's missing files, after confirming them.
  Nothing is created before you confirm. A drive root (`C:\`, `D:\`), the home
  folder and the folder of all homes, the application data folders, and system folders (Windows, Program
  Files, `/usr`) are never set up. Setting up creates `.mcp.json` and the project's **`.local-fox` folder** and
  shows what to adapt next; it changes no existing file, except that a `.mcp.json` of other applications gets
  the tools' server added (the confirmation says so). The folder holds:

  | In `.local-fox` | What |
  |---|---|
  | `instructions.md` | The project instructions, given to the model at the start of every conversation |
  | `tools-config.json` | The tools' config; `instructions_file` names the instructions |
  | `commands.json` | The commands the AI may run: **empty at first**, so it runs none until you add them |
  | `commands-examples.json` | Commands for common Python and npm projects, to copy into `commands.json`; nothing reads it |
  | `context/` | The AI's plans; the only part of the folder the tools may use |
  | `conversations/` | The saved conversations (below) |
  | `suggested-commands.json`, `.gitignore` | Commands the AI suggested; what is each person's own (plans, suggestions, conversations) |

  These are the files of the tools' own `python -m ollmcp_tools init`, which puts them in `.ollmcp-tools`. The
  tools find their config wherever `.mcp.json` points (`MCP_TOOLS_CONFIG`), so the folder can be named after
  Local Fox; the project still works in ollmcp and other clients. A project set up with the tools' init keeps
  its `.ollmcp-tools` folder, and Local Fox uses that folder for everything instead.
- **Local Fox's own instructions template**: the project instructions come from `local_fox/templates/instructions.md`
  instead of the tools' template, which is written for ollmcp. It has the same workflow (answering, planning
  and implementing modes, verifying with the listed commands), but names the tools as the model sees them
  (`edit_file`, not `dev_edit_file`), points the model to the file list that follows the instructions, and tells
  it that nobody prompts it to continue. A project set up before keeps its file: to switch, delete its
  `instructions.md` (or merge your changes into the new template) and run `/setup`.
- **Running them**: a project's `.mcp.json` starts the tools with `python -m ollmcp_tools`, as ollmcp and other
  clients do. Local Fox runs that server with its own copy instead: from the source with the Python that runs
  Local Fox, from the executable as `LocalFox.exe --tools-server`. The file is not changed, so the project keeps
  working in other clients. The tools entry of the feed says what each server runs. Set `bundled_tools` to
  `false` to run the command of `.mcp.json` as it is, e.g. to try a development version of the tools.
- Other servers of `.mcp.json` run as they are given.

## The window

- **Top bar**: the version, the model (click to switch), the context size (click to choose another for the session,
  from 4 096 tokens doubled up to the model's maximum; `/context <size>` sets any other),
  and where the model runs (`ollama ps`). **Export** and the ≡ menu with the other commands are on the right.
- **Bottom bar**: **Clear** and **Help** on the left, and on the right how much of the context the conversation
  uses and **Send** (**Stop** while the model runs), far apart so that one is not pressed for the other. The
  filemap reaches down to it; the prompt is as wide as the feed above it.
- **Filemap** on the left: the project folder. A click on a folder opens or closes it; right-click a file or
  folder for **Add path to prompt** (relative to the project folder, as the tools take paths) and **Copy path**.
  Forbidden files are darker, read-only files are marked `ro`, ignored folders are dimmed, and the files the AI
  changed in the session are orange.
- **Add path to allowed**, **Add path to read-only** and **Add path to forbidden** in the filemap's menu add the
  path to `allowed_paths` (inside a forbidden folder),
  `read_only_files` or `forbidden_paths` of the project's tools config, or remove it from there. Only you make
  these changes, never the model. The feed says what changed and offers **Restart tools and clear**: the tools
  read their config when they start, and the model gets the tools and the filemap again in a new conversation.
- **Feed**: the instructions, filemap and tool definitions given to the model (collapsed), your prompts, the
  model's thinking and answers, and its tool calls. The **latest** tool call (with its arguments and response; an
  edit shows its changed lines), the latest thinking and the latest answer are open; when a newer one arrives, the
  one before it collapses: a tool call to one line with the tool and its main argument, thinking to its title, and
  an answer to its first two lines. Click a collapsed line to open it again; what you open stays open. Your
  prompts are always shown whole. This also keeps a long conversation quick to show.
- **Notice bar**: approvals of tool calls (**Allow**, **Allow all for this session**, **Skip**, **Stop**) and
  other decisions, such as **Larger context** when a reply stopped because the context was full.
- **Prompt**: Enter sends, Shift + Enter adds a line, Up and Down recall sent messages, Esc stops the model.
  **There is no queue**: while the model runs, Enter does not send (the button is **Stop**); the message stays
  in the input, and a tooltip says to stop the model (Esc) or wait. Stopping and then pressing Enter sends it.
- **Copy and paste** are the usual Ctrl + C, Ctrl + X and Ctrl + V, and nothing is copied or pasted on its
  own. In the feed, select text with the mouse and press Ctrl + C; the selection stays while a reply streams
  in. Pasting into the prompt only inserts the text, whatever it contains (line breaks, a leading `/`): only
  Enter typed by you sends. The filemap's **Copy path** and **Add path to prompt** are the only other ways.

Commands: `/clear`, `/export`, `/instructions`, `/model [name]`, `/context [size]`, `/settings`, `/config`,
`/log`, `/setup`, `/reopen`, `/project`, `/help`.

## Settings

Every setting is in `config.json`, in `%APPDATA%\LocalFox` (or where `LOCAL_FOX_CONFIG` points); `/config`
opens its folder, and a clear reads it again.

**The settings view** (`/settings`, or **Settings** in the ≡ menu) shows the same file as a form, with what each
setting does. **Save** writes it: the model and context size apply at once, the others from the next clear.
**Save and clear** applies them all at once. Its **Project tools** tab shows the open project's tools config
(`.local-fox/tools-config.json`) the same way; settings it does not know are kept, and a saved change asks
to restart the tools (Save and clear restarts them). The tools' own rules are checked by the tools: a server
that does not start with the new config shows its error in the feed.

`config.json` is created with these defaults:

| Setting | Default | |
|---|---|---|
| `ollama_host` | `http://127.0.0.1:11434` | Where Ollama runs |
| `ollama_api_key` | empty | Your Ollama API key for the web tools (search and fetch through ollama.com; create one at ollama.com/settings/keys). Empty: the web tools are not offered (a key in the tools' own `~/.ollmcp-tools/user-config.json` is not used). Given only to the tools' server when it starts, never to the model, and not written to the log; hidden in the settings view |
| `model` | `flint:40b` | Also set by the model menu |
| `num_ctx` | `65536` | The context size sent with every request; with flint:40b a larger one needs little memory |
| `think` | `null` | `null`: on for models that can think; `true`, `false` or a level such as `"high"` |
| `temperature` | `null` | `null`: the model's own |
| `seed` | `null` | A fixed seed, for repeatable runs |
| `max_steps` | `null` | Model requests per turn before asking whether to continue; `null` for no limit |
| `auto_approve_tools` | `[]` | Tools that run without asking, e.g. `["read_file_with_metadata", "get_outline"]` |
| `filemap_for_model` | `true` | Whether the model gets the filemap at the start of a session |
| `filemap_max_tokens` | `2000` | A larger project is shown to the model with its folders only |
| `prompt_full_paths` | `false` | Add full paths to the prompt instead of relative ones |
| `font_size` | `10` | In points |
| `bundled_tools` | `true` | Run `python -m ollmcp_tools` servers with Local Fox's own copy of the tools |

The prompt history is in the same folder, and the logs in its `logs` folder.

## The log

Local Fox keeps one log of the current run: `logs/session.jsonl` next to `config.json`, one JSON object per line,
written as it happens, with every conversation of the run (each clear starts a new one with a `conversation`
line). The output of the MCP servers (their stderr) is in `logs/server-<key>.log`. **Every start of Local Fox
empties them**, so after something goes wrong the log is right there and holds only that run; save a copy before
starting again if you need it. `/log` (or **Open config folder** in the ≡ menu) opens the folder.

Unlike the export, the log is written without asking and holds what the feed does not show:

| Event | What |
|---|---|
| `conversation` | The version, project, model, context size, all settings, and the servers with their tool counts and errors |
| `system`, `tools` | The system message (again when the instructions are reloaded) and the tool definitions as sent |
| `request` | One request to Ollama: its options and thinking setting, and the messages added since the request before |
| `reply` | The reply, thinking and tool calls as the model made them, why it stopped, the token counts and the durations |
| `tool` | A call by the name the model used, the tool that ran, the arguments, status, response and duration |
| `decision` | A question to the user, the options and the choice |
| `notice`, `error`, `setting`, `close` | The notices and errors of the feed, model and context changes, the end |

The system message and the messages of the requests make up exactly what the model was given. Images are
not written, only their number.

## How a session goes to the model

- **System message**: the project instructions and the filemap, read at the start of every
  session, also after a clear, and nothing else: Local Fox has no instructions of its own besides the template
  the setup copies into the project. The instructions are the file the tools config names in `instructions_file`
  (`.local-fox/instructions.md` after setup; `instructions.md` next to the config if a config has no such
  setting); the feed's entry shows which file it is. Both are shown in the feed as collapsed entries. **Both are read again
  before the first prompt** of a conversation, so files added (or instructions edited) while the window was open
  are in it; their entries then say so. The instructions start with a line naming their file and saying the model
  already has them, so a task that asks to read them does not send it looking. After the first prompt they stay as
  the model got them (the model sees
  later changes with its tools); `/instructions` reads the instructions again when you want.
- **The tools config edited by hand**: the tools read `.local-fox/tools-config.json` only when they start. When
  the file changes outside Local Fox, the feed says so and offers **Restart tools and clear**; until then the
  tools keep the config they started with. Changes made in Local Fox (the settings view, the filemap's menu) offer
  the same. Local Fox's own filemap colors follow the file at the next clear or restart.
- **Tools**: with one server, by their own names (`read_file_with_metadata`); with several, prefixed with the
  server key (`dev_read_file_with_metadata`). A call by a name that is prefixed with a server key, or a bare name
  only one server has, runs that tool, so instructions written for ollmcp's `dev_` names still work. The feed
  shows "(ran dev:x)" when the called name was not the exact one.
- **Faulty calls get a response** the model sees: an unknown tool lists the tools, broken JSON or a missing
  required argument gets the error, a skipped call gets "The user skipped this call.", and calls stopped with Stop
  get a response too.
- **The thinking** is shown as it arrives and stays open until newer thinking arrives; as in ollmcp, it is not
  sent back to the model in later requests.
- **Images** from `read_image` are passed to models with the vision capability; for others the feed says they
  were not passed.

## When something goes wrong

Errors are shown in the feed in orange, with what to try first and the original message after it. Local Fox
recognizes these:

| What happened | What the feed suggests |
|---|---|
| The model ran out of memory (GPU or system) | A smaller context, closing other programs that use the GPU, or a smaller model |
| The model's runner process stopped | Often memory as well; where Ollama's log is |
| The model took too long to load | Asking again; a smaller context or model if it repeats |
| Ollama is not running or not reachable | Starting Ollama; checking `ollama_host` |
| Ollama closed the connection in the middle of a reply | Asking again; Ollama's log |
| The model is not installed, cannot call tools, or cannot think | `ollama pull`, another model, or the `think` setting |
| The tools' server stopped | **Restart tools and clear**; its log in `logs/server-<key>.log` (the model is told not to call tools until then) |

## Fitting the context

A model sees only as much of the conversation as fits in its context (`num_ctx`, 65 536 tokens by default).
Ollama does not refuse a longer request: it silently drops the oldest messages, the user's request first, and the
model goes on without knowing its task. Local Fox keeps that from happening:

- **Before every request**, it estimates the request's size (calibrated with Ollama's own counts) and leaves room
  for the reply. If the request would not fit, the responses of earlier tool calls are replaced with a short
  note ("Removed by Local Fox to make room in the context: the response of
  read_file_with_metadata(path='src/app.py'), about 5 900 tokens. To see it again, call
  read_file_with_metadata(path='src/app.py').") until it fits. Out-of-date reads go first: a part of a file the
  model read again later (or the whole file), text it then edited, or a file it rewrote, moved or deleted. An edit
  elsewhere in the file leaves a read in place. Then the oldest responses go. Your messages, the
  model's replies and the latest step's tool responses always stay, so the model keeps its task and can read a
  file again. The feed says so, and marks those tool calls "(response removed from the model's context)".
- **If it still does not fit**, the request is not sent, and the feed offers **Clear**, **Export** and **Larger
  context**.
- **If Ollama cuts the conversation anyway** (its count of a request drops although nothing was removed), the turn
  stops and the feed says so.
- **New projects limit a tool response** to 16 000 characters (`max_output_chars` in the tools config, about 4 000
  tokens; the tools' own default is 40 000), and the outline of a folder to a quarter of that.

## Stopping and continuing

- **Esc or Stop** stops the turn: the reply being written, the approval being asked, or the tool call running.
  Every tool call still gets a response the model sees ("The user stopped the session before this call ran.",
  or "while this call ran; its result is unknown.").
- **An interrupted reply is marked**, in the feed and in what the model gets, so that neither takes it as
  finished: "[The user stopped this reply here.]", or "[This reply was cut off here: the context was full.]".
  A reply stopped before any text arrived is not sent to the model at all.
- **Only your next message continues.** Local Fox never sends anything on its own; type e.g. "continue", or a
  new instruction. The model gets the whole conversation with the marks and the stopped calls' responses.

## Saved conversations

Every conversation is saved after each turn, and when the window closes or another project opens, as a file in
the project's `.local-fox/conversations` folder (forbidden to the tools, and left out of git by a `.gitignore`
of its own). When a project opens, the notice bar offers to reopen its last conversation (**Reopen it**);
**≡ > Conversations** lists all of them, and `/reopen` reopens the last one. A reopened conversation starts as a
new one, with the instructions, filemap and tools as they are now, followed by the saved messages, and goes on
being saved to the same file. A clear starts a new file. Delete the files you do not need from the folder.
## The core and its command line

The core (`local_fox` without `local_fox.ui`) needs only `ollama` and `mcp`. A small command line runs it:

```bash
python -m local_fox.cli --project <folder> [--model flint:40b] [--context 65536] [--yes] [--verbose] [--setup] ["first message"]
# The folder must be a project; --setup sets it up first (never a drive root or system folder)
```

## Comparing models through Local Fox

`tools/compare.py` runs the scenarios of ollmcp-generic-tools' `local_ai_tests/compare_scenarios.py` through the
core, with the checks and summary of its `compare_models.py`. Run it with the Python that has ollmcp-generic-tools
and ollmcp installed:

```bash
python tools/compare.py --models flint:40b,gemma4:26b --seeds 1,2,3
python tools/compare.py --models flint:40b --scenarios plan_new,implement_fix --tools-repo ../ollmcp-generic-tools
```

The results go to `runs/compare/<time>/`. The differences from `compare_models.py` are those of Local Fox: the
instructions and filemap are given at the start, faulty calls get a response, and no "Continue." is sent after a
stall (it is counted instead).

## Tests

```bash
.venv\Scripts\python -m pytest
```

## Not built yet

- **Auto** context size.
