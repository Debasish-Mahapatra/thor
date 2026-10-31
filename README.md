# Thor

"Thor" is a working name, with a lightning-bolt icon.

A LaTeX app for macOS with an AI agent that runs on your Mac, using whichever model you choose. It compiles fast, reads
co-authors' annotated PDFs, edits only what you approve, and builds clean and tracked PDFs. It is meant to replace
OpenAI's Prism, and to do what Overleaf does for writing and compiling, offline. The design and the build plan are in
[docs/architecture.md](docs/architecture.md).

**Status:** M3 (co-authors' annotated PDFs), and the first part of M4 (the agent).

Working now:
- **The agent.** Ask in the panel, or press ⌘K. Select text and press ⌘L to attach it; you can attach several passages to one
  message. It works in a private copy of your paper and compiles that copy; you accept or reject each edit in the Changes
  tab, and edits that change a number, a citation or a word that can alter a finding are flagged and left out of "Accept
  all". It can use:
  - **Your plans:** Claude Code and Codex, through their installed command-line tools. The ChatGPT app includes Codex.
  - **API keys:** Anthropic and OpenAI, kept in the Keychain.
  - **Models on this Mac:** a running Ollama or LM Studio is found by itself.
  - **Bring your own:** any server speaking the OpenAI chat API, or any command-line program, added in Settings.
- **Fast compiles.** After a small edit, LaTeX usually runs once, and resumes from a process that has already read the
  preamble. The Auto box compiles each time you stop typing.
- **The tracked PDF within seconds of a compile,** because latexdiff runs only on the paragraphs that changed. The revision's
  clean files follow when you pause. The editor marks your changes as you type.
- **Editor aids.** Completion of `\cite`, `\ref`, file names and commands; spell check with your Mac's dictionary; equation
  preview on hover; search and replace across files (⌘⇧F); live warnings for unmatched braces and environments, and for your
  style rules; Vim and Emacs keys; a symbol palette.
- **Your layout.** The files, editor, PDF and agent each collapse (⌘\ for the files, ⌘⇧\ for the agent). The agent sits on
  the right or along the bottom, and the dividers drag.
- **The window from the mockups,** with a true-black dark mode, the CodeMirror editor (autosave, never
  overwrites changes made on disk), `latexmk` builds, the Problems drawer, and the PDF viewer with SyncTeX
  both ways. Compiles keep the files of earlier passes, as Overleaf does; the passes that only settle
  references run in draft mode, and one full pass writes the PDF. "Compile from scratch" in the Problems
  bar starts clean.
- **Revisions.** The revision menu starts the next revision from the previous one's frozen final source,
  or continues the latest. From then on, each successful compile updates the active revision folder in
  the background:
  - the source snapshot;
  - `revision_clean.pdf`, `main_draft.pdf` and `supplementary_draft.pdf`;
  - the working copies, checked byte-identical;
  - `revision_tracked.pdf`;
  - a change-log entry naming the changed sections and figures.

  Earlier revisions are never written. While a revision is active, the editor marks what changed since
  it started (added text tinted, deleted text struck through), and the PDF beside it switches between
  the clean and the tracked PDF.
- **The tracked PDF** (latexdiff, with fixes for moved tables, figures, captions, citations, the
  `filecontents` bibliography, and labels or references that only the baseline has). It shows in the
  Tracked PDF tab, with the update report beside it.
- **History** of every file: versions are kept on open and save; compare any of them with the current
  text, and restore one.
- **Comments.** Import a co-author's annotated PDF (and the email that came with it) into the active
  revision. The Comments tab lists every comment with its page and line:
  - highlights, underlines, strike-outs, replacements (strike-out plus caret), insertions, notes, text
    boxes and replies; pen strokes are read as underlines, strikes, circles and dots, and joined to the
    note that refers to them;
  - each one is checked against the previous revision's PDF, and any that moved or changed are marked;
  - comments that can't be acted on as they stand (a lone word, a mark with no note) wait for your
    answer;
  - one person annotating under two account names can be merged;
  - the annotated PDF and the paper as it is now show side by side, each with the comment marked.

  It is saved as `comments.json` and a readable `comments.txt` in
  `revisionN/supporting_files/comments/<name>/`.

Next is the rest of M4: the agent working from the comments and the plan, and the commit button.

## Coming from Prism

Prism's data is deleted when it shuts down, so export first: Settings, Data Controls, Export all projects (zip). Unzip it
and open a project's folder in Thor. History, inline comments and the Zotero connection are not in Prism's export. Keep a
copy of the PDF from Prism to compare the first build against.

## Not built yet

These are open for anyone to take up:

- **Real-time co-editing.** Co-authors send annotated PDFs for now. Live editing needs a server that every co-author can
  reach, because Thor answers only on 127.0.0.1, with a token that changes at each launch. That protection has to stay
  for the local app. CodeMirror, the editor Thor uses, can share a document through Yjs.
- **The revision plan** (milestone M5): comments become a plan with a decision for each, and the agent edits after you
  approve it.

## Run

```bash
make run
```

This builds the app and opens it. Or install it as a normal app:

```bash
make dmg
```

This builds `build/Thor.dmg`:
1. Open it and drag Thor to Applications.
2. Open Thor. On first launch it installs what is missing by itself, with nothing to click:
   - **Claude Code**, with Anthropic's installer, then the browser sign-in if you aren't signed in;
   - **MacTeX**, for which macOS asks your password once, in its own dialog.

The app carries its own Python and packages, so the Mac needs nothing else. The `.dmg` is ad-hoc
signed, which is fine for your own Macs. Other people's Macs need Developer ID signing and notarization
(M10).

## Develop

```bash
./run.sh
```

This builds what is out of date and opens a development `build/Thor.app`, which uses this folder's
Python. `./run.sh --browser` opens the same interface in your web browser.

| Command | What it does |
|---|---|
| `make setup` | Creates the Python venv (`backend/.venv`, through uv) and builds the interface. |
| `make test` | Runs the backend tests (pytest), the interface typecheck and tests (Vitest), and builds the shell. |
| `make check` | Prints the dependency report in the terminal. |
| `make dev` | Runs the backend on port 8790 with the fixed token `dev`, and Vite with live reload on 5173. |
| `make app` | Builds the development `build/Thor.app`. |
| `make dmg` | Builds the self-contained `build/Thor.dmg`. |

## Requirements

- **To use Thor (from the `.dmg`):** macOS 13 or later on Apple silicon. Thor installs the rest itself.
- **To build it:** the Xcode command-line tools (Swift), uv (which provides Python 3.12) and Node
  (`brew install node`).

## How the agent signs in

Choose the model in Settings or in the agent panel. If the one you chose is not set up, Thor uses the first that is.

- **Claude Code** uses the Claude plan your installed `claude` is signed in with. To sign in, run `claude` in Terminal.
  Thor removes `ANTHROPIC_API_KEY` from its environment so a key in your shell cannot take over. This sign-in is for your
  own copy of Thor; a copy given to others would need API keys.
- **Codex** uses the ChatGPT plan your installed `codex` is signed in with, and finds the copy inside the ChatGPT app.
- **API keys** go into your login Keychain (service "Thor"), never into a file.
- **Ollama and LM Studio** need nothing: if they are running, they are listed. Models whose name ends in `:cloud` send
  your text to a cloud service, so Thor never picks one by itself.

Whichever you use, your paper's folder changes only when you accept an edit. Claude Code and Codex run inside their own
sandboxes. A command you add yourself is not sandboxed. A co-author's comment could try to steer the agent, so with Claude
Code, reading outside the paper and opening a web address each ask you first.

## Project settings: `revision_app.toml`

Opening a folder detects the settings from the source:
- the main file;
- the figure macro and its folders;
- where the Supplementary Information starts;
- the engine and the bibliography setup;
- the results, scripts and revision folders.

Nothing is written until you press **Save**. Saving writes `revision_app.toml` at the top of the
project, with a comment for every key. You can edit the file by hand too.

## Where things live

- **App state** (`~/Library/Application Support/Thor/`):
  - `settings.json`: which model, effort, spending cap;
  - `layout.json`: which panes are open, and their sizes;
  - `agent/`: chats and the agent's copies of your paper;
  - `thor.db`: recent projects.
- **Backend log:** `~/Library/Logs/Thor/backend.log`.

## Security

The backend listens on 127.0.0.1 only. Each launch makes a random token. The window signs in with it
once, through `/auth`, which sets an HttpOnly cookie. Every `/api` request needs the token, and requests
for any other host name are refused. The backend exits when the app quits or crashes.

## Layout

```
backend/   Python 3.12 backend (FastAPI): thor/ package, tests/
ui/        React + TypeScript interface (Vite)
shell/     Swift app shell (AppKit + WKWebView), Info.plist, make_icon.py (the bolt icon)
scripts/   dev.sh
docs/      architecture.md: design, Overleaf feature map, milestones, decisions
```
