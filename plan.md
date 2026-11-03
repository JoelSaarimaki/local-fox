# Local Fox

Local Fox is a harness for local AI models such as flint:40b and gemma4:26b, run with Ollama, and the tools of ollmcp-generic-tools. It replaces ollmcp. The UI should be simplistic with a limited color palette, and all Markdown shown in the conversation feed should be styled accordingly.

The application is not started from a console: it is installed or run as an executable application. But it has a minimalist UI like a console application.

## Why a harness of its own

What running flint and gemma4 in ollmcp has shown, and what Local Fox does about it:

| In ollmcp | In Local Fox |
|---|---|
| No context size is sent unless `num_ctx` is set, so Ollama uses 4 096 tokens, the tool definitions fill most of it, and replies are cut off with "The model produced no response or tool calls." An ollmcp update lost the setting. | The context size is always sent, from `config.json` (default 32 000), and shown in the top bar. |
| A call by a tool's bare name (`read_file_with_metadata` instead of `dev_read_file_with_metadata`) is skipped without a response, so the model repeats it: one test run made 70 such calls and never finished. flint does this; gemma4 does not. | The model sees the tools without a prefix, and every call gets a response: an unknown name or broken arguments get an error message the model can act on. |
| The AI reads the project instructions only if the system prompt and the first message ask it to, and when the first message is already a task, it often skips them. | Local Fox reads `.ollmcp-tools/instructions.md` itself at the start of every session, also after a clear, and gives it to the model. |
| Long Windows paths with spaces are typed by hand. | Paths are added to the prompt from the filemap. |
| Tool calls and their responses are printed in full and cannot be collapsed, so they flood the output. | Each tool call is one collapsed line in the feed, expandable to its arguments and response. |
| The loop limit (7 by default) stops long tasks again and again with a prompt in the console. | No step limit by default; the user stops the model with Stop when needed. |
| The text input is clumsy: one line by default, and multiline input needs a mode switch. | The input is always multiline, with Enter to send and Shift + Enter for a new line. |

## Principles

- **Everything that goes to the model is visible.** The instructions, tool calls and their responses, error messages to the model and the model's thinking are all shown in the feed, collapsed where they are long. Local Fox does not send anything the user cannot see.
- **No hidden automation.** Local Fox does not inspect the model's replies to send it messages of its own, such as "continue" after a stall. When something needs a decision, it shows a notice with buttons, and the user decides.
- **Settings in one place.** `config.json` holds every setting; the top bar shows the ones that change the model's behaviour.

## Layout

- **Top bar**: application name and version, the model, the context size, how much of the context the conversation uses (e.g. `12 400 / 32 000 tokens`), and where the model runs (e.g. `77 % CPU / 23 % GPU`, as `ollama ps` shows it).
- **Filemap** in a column on the left (see below).
- **Conversation feed** in the middle.
- **Prompt input** at the bottom. Text input is always multiline: Enter sends and Shift + Enter adds a line break, as in chat applications. Previously sent messages can be recalled with Up and Down while the input is empty.

## Filemap

- Shows the project folder as a tree. Left-clicking a folder collapses or expands it.
- Right-clicking a file or folder opens a menu: **Add path to prompt** (inserted relative to the project folder, as the tools take it, e.g. `.ollmcp-tools/context/task plan.md`; full paths as an option in the settings), **Copy path**, and, if the toolset collaboration below is built, **Allow** and **Restrict**.
- Forbidden files are shown in a darker color, read-only files with a mark, ignored folders (`node_modules`, `.venv`) collapsed and dimmed.
- Files that the AI changed in the session are highlighted, so that the user sees what to review.
- The tree refreshes when files change.

### The filemap for the model

The filemap is **given to the model at the start of every session**, after the instructions, as a compact tree: the project folder's path once, then each folder and file by its name only, indented under its folder, without line counts or sections. For example:

```text
Files in the project folder C:/Users/me/GIT/end-quality-simulator:
README.md
src/
  settings.py
  settings_ui.py
  units/
    eu.py
    na.py
tests/
  test_settings.py
```

The model then knows the file names and paths without a first `get_outline` call, and a path in the user's prompt matches a line in the tree. The names alone cost a few tokens per file: about 1 000 to 2 000 tokens for a few hundred files.

- **A size limit** (a setting, default 2 000 tokens): a larger project is shown with its folders only, each with its file count (`src/ (240 files)`), and the model outlines a folder with `get_outline` when it needs its files.
- Forbidden and ignored paths are left out, as the tools hide them too.
- It is a snapshot of the session start: later changes the model sees through the tools.
- The feed shows it as a collapsed entry, e.g. "Filemap (310 files, 1 400 tokens)", and a setting turns it off.

## Sessions

- **Session start**: Local Fox reads the project's `.ollmcp-tools/instructions.md` and gives it to the model as the system message, with the tool definitions. The feed shows it as a collapsed entry, e.g. "Project instructions (2 000 tokens)". Without the file, the feed says so, and the session starts without instructions.
- **Clear** (`/clear` or a button) starts a new session, with the instructions again.
- **Reload instructions** (`/instructions`) after editing them.
- **Export** (`/export` or a button) saves the whole conversation as a Markdown file, chosen with a file browser: the messages, the tool calls and their responses, and the thinking.

## Tool calls

- Local Fox is an MCP client: it starts the servers of the project's `.mcp.json`, as ollmcp does.
- **Tool names**: with one server, the model sees the tools by their own names (`read_file_with_metadata`), without a prefix, so there is no prefix to forget. With several servers, the names get the server key as a prefix, and a call by a bare name that only one server has is run on that server.
- **Faulty calls get a response**: an unknown tool name gets "Unknown tool x. The tools are: …", arguments that are not valid JSON or miss a required argument get the error, as a tool response that the model sees and the feed shows. A call is never skipped silently.
- **Tool calls in the feed** are collapsed by default: one line with the tool and its main argument, e.g. `read_file_with_metadata  src/settings.py  ✓`, expandable to the full arguments and response. An edit shows its changed lines when expanded. A setting chooses whether they start collapsed.
- **Approvals**: by default, every tool call asks for approval before it runs, with the buttons **Allow**, **Allow all for this session**, **Skip** and **Stop**. Allow all for this session runs every further tool call of the session without asking, as ollmcp's "session" option does; a clear asks again. A skipped call gets the response "The user skipped this call", which the model and the feed both see. A setting can make chosen tools, e.g. the reading tools, run without asking.
- **No step limit by default**: the model runs until it answers or the user presses Stop. A limit can be set in `config.json`; it then stops with a notice and **Continue** and **Stop** buttons, instead of a prompt in the console.

## The model

- `config.json`: the Ollama host, the model, the context size (default 32 000), thinking on or off, and the temperature (default: the model's own).
- **Context size**: `/context 65536` (or clicking the context size in the top bar) sets it for the session, and `/context` alone shows it with how much the conversation uses. Ollama reloads the model with the new size on the next request, which takes a few seconds to a minute. The size cannot be larger than the model's maximum, which Ollama reports and the command checks; a larger size needs more memory, which can push more of the model from the GPU to the CPU and make it slower, and the top bar shows that change.
- **An automatic context size** would need the memory a token of context takes for the model (from the model's details in Ollama) and the free memory of the GPU and RAM: possible, but an estimate. Start with the fixed default and `/context`, and add **Auto** later if choosing by hand turns out to be a nuisance.
- A model menu in the top bar switches between the installed models.
- **Stop** (a button, or Esc) stops the generation at any time; what has arrived stays in the feed.
- **A reply cut off by the context limit** is marked as such in the feed ("The reply stopped because the context is full: 32 000 tokens."), with **Clear**, **Export** and **Larger context** buttons; the last sets a larger context size for the session, and the user then asks again. Ollama reports why a reply stopped, so this is not guessed from the text.
- **Thinking** is shown as it arrives, collapsed when the reply starts.
- **Images** from `read_image` are passed to the model if it has vision, and otherwise the feed says that the model cannot see them.

## Commands

`/clear`, `/export`, `/instructions`, `/model`, `/context`, `/config` (opens the folder of `config.json` in the file browser), `/help`. Each also has a button or a menu entry.

## Collaboration with the toolset (later)

Right-clicking a path in the filemap could add it to `allowed_paths`, `read_only_files` or `forbidden_paths` in the project's tools config. This is done only by the user, never by the model: the tools protect their own config so that the AI cannot change its own limits, and Local Fox must keep it that way. A change is shown in the feed, written to `.ollmcp-tools/tools-config.json`, and takes effect when Local Fox restarts the tools' server.

## Implementation

**In two stages**, so that the part that makes the model work is built and measured before the interface:

1. **The core, without a UI**: a Python package that runs a session: Ollama with the settings, the MCP servers, the instructions at the start, the tool calls with error responses, the step limit, and the conversation as data. It is started from a small command line for testing. The comparison scenarios of ollmcp-generic-tools (`local_ai_tests/compare_scenarios.py`) then run through this same core, so that the measurements show how the model behaves in Local Fox, not in a separate test harness. Its harness already does most of this and is the starting point.
2. **The interface** on top of the core.

**Technology**: Python, as the tools, the MCP and Ollama libraries and the test harness are Python. **PySide6** (Qt) for the window: a tree view for the filemap, a styled text view for the feed, a monospace font and a limited palette for the console look, and Markdown rendering. **PyInstaller** packages it as a Windows executable. A web interface in a window (pywebview) would make the Markdown styling easier, but adds HTML and JavaScript to the project.

**Size**: the core is about a week of work, building on the harness. The interface, with the filemap, the feed and the packaging, is the larger part, a few weeks.

## Left out on purpose

- Automatic messages to the model based on its replies (such as "continue" after a stall), and retries the user does not see.
- Git write tools: a person reviews and commits.
- Several models working together, and cloud providers.
