# Changelog

Local Fox follows [semantic versioning](https://semver.org/): a minor version for big new features, a patch
version for fixes, small features and minor or invisible changes, and a major version only for changes that break what users or
projects rely on (settings, saved conversations, set-up projects).

## 1.0.3 (2026-11-13)

- **Text search results name each file once**, above its lines, instead of on every line: about a quarter less
  output, so more matches fit. Old search results are still shortened when the context fills, and a file's
  context lines now go with its matches.
- **An edit without the file's hash is told so** ("missing_sha256", with the hash to use), instead of being told
  that the file changed, which made qwen3.6:35b read the unchanged file again and repeat the edit.
- The CPU threads menu says "auto", and its tooltip and the `/threads` texts are shorter.

## 1.0.2 (2026-11-13)

- **The model, context size and CPU threads are dropdowns alike** in the top bar; the context size is no longer a
  dialog.
- **The mouse wheel scrolls past dropdowns and number fields** instead of changing them, in the settings and
  everywhere else; a value changes only by clicking, typing or the arrow keys.

## 1.0.1 (2026-11-13)

- **CPU threads**: the threads Ollama runs the model with, chosen in the top bar ("threads auto / 16"), by
  `/threads` or with the `num_thread` setting: Ollama's own choice, or 1 up to the computer's processors. Fewer
  threads use less of the CPU, so the computer stays cooler and quieter, without limiting other programs.

## 1.0.0 (2026-11-13)

The first release, for Windows; macOS has basic support that has not been run on a Mac.

- **Projects**: a start dialog with the recent projects; a project is set up only on your choice, after seeing
  what will be written, and the model is prompted only in a set-up project.
- **The window**: the filemap (forbidden, read-only, ignored and changed files marked; its menu adds paths to the
  prompt and changes the tools' path rules), the feed, the prompt, the notice bar for approvals and decisions,
  and the Ollama panel that shows where the model runs and what the server does, and starts and stops it.
- **Viewing files**: a file clicked in the filemap opens in a tab beside the conversation, Markdown rendered and
  other text raw, read again when it changes.
- **Everything sent to the model is in the feed**: the instructions, the filemap, the tool definitions, every tool
  call and response, and thinking.
- **The bundled tools** of ollmcp-generic-tools, with tool name resolution that makes flint:40b, the default
  model, usable.
- **Fitting the context**: old tool responses shortened to what the conversation mentioned, and removed only
  after that, always shown in the feed; the model is told when its context fills.
- **Safeguards**: no automatic messages; a reply that repeats itself is stopped; a tool call that keeps failing the
  same way ends the turn; auto mode only on your choice.
- **Conversations** saved after every turn and reopened from the ≡ menu; Markdown export; one log per run.
- Settings in `config.json` with a settings view, model and context size switching, `/host` for Ollama on another
  computer, and an Ollama API key setting.
