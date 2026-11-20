# Changelog

Local Fox follows [semantic versioning](https://semver.org/): a minor version for big new features, a patch
version for fixes, small features and minor or invisible changes, and a major version only for changes that break what users or
projects rely on (settings, saved conversations, set-up projects).

## 1.1.4 (2026-11-20)

- **A call that lacks a required argument is answered with the arguments it had**, that it was not run, and to call
  it again with the missing one: "It came with new_text and old_text only". qwen3.6:35b sent an edit without its
  path, read the plain "missing the required argument path" as a fault of the tool's ("which is odd since I
  provided it"), and got it right only on its second try.

## 1.1.3 (2026-11-18)

- **A connection to Ollama that died is noticed.** Local Fox waits for a reply with no time limit, as a model that
  loads or reads a long prompt sends nothing for minutes, so a computer that slept while Ollama on another computer
  replied could wake to "Thinking…" for good. The connections now use TCP keepalive, which the other computer's
  system answers also while Ollama is busy: a dead connection fails within about a minute and a half, and the
  feed shows Ollama's connection error. Nothing is sent again on its own.

## 1.1.2 (2026-11-18)

- **Switching to Ollama on another computer** works through the settings as through `/host`: the settings view's
  model field lists the models installed at the address typed in `ollama_host` (and says if that server does not
  have the model chosen), and on "Save" the model is switched after the address, at the new server. The address
  is saved complete (`192.168.1.20` as `http://192.168.1.20:11434`) and checked before saving.
- After a change of address, what was known of the old server's model is read again from the new one (its
  largest context, whether it thinks), the Ollama panel shows "Checking…" instead of the old server's state, and an
  `ollama serve` started by Local Fox is no longer shown or stopped as the other computer's server. "Save and clear"
  says in the feed which address the new conversation uses.
- A model the server does not have is explained with the models it has. The model menu says when Ollama does not
  answer instead of staying at "Loading the models…".
- **Back to the defaults**: a "Default" button beside each setting, "Restore defaults" for all of them but the API
  key, and `/host default` for this computer's Ollama.

## 1.1.1 (2026-11-14)

- Texts that agree with what Local Fox does: Save's tooltip and notice name the four settings that apply at once,
  the `num_ctx` description says that a larger context needs more memory, and the `/instructions` help no longer
  names a folder that not every project has.
- The bundled tools are version 1.0.0: the same tools as in 1.1.0, now numbered, with their documentation brought
  up to date and the instructions template naming the tools in full.

## 1.1.0 (2026-11-14)

- **Edits no longer need the file's hash.** The bundled tools remember what the model has read of each file in
  the conversation, so the model no longer copies a SHA-256 into every edit, which models left out or miscopied
  in 8 of 20 edits (qwen3.6:35b). An edit whose text matches is made even if the file changed elsewhere since the
  read, with a note naming the changed lines; replacing a whole file still needs a read of it, unchanged since.
  Projects set up earlier keep working: the old argument their instructions name is ignored.
- **No note on every partial read** about edit_file and write_file. Instead, write_file refuses to replace a
  file of which the model has read only part, which the note warned against.
- **qwen3.6:35b is the default model**, instead of flint:40b: as good in the comparison scenarios, with fewer tool
  errors, at about twice the speed. A `model` already in `config.json` stays as it is.

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
