# Thor

"Thor" is a working name, with a lightning-bolt icon.

A LaTeX app for macOS with Claude as its agent. It reads co-authors' annotated PDFs and plans the
revision with you. It edits only what you approve, builds clean and tracked PDFs, and drafts the reply.
It also aims to do what Overleaf does for writing and compiling, offline. The design and the build plan
are in [docs/architecture.md](docs/architecture.md).

**Status:** M3 (co-authors' annotated PDFs).

Working now:
- **The window from the mockups,** with a true-black dark mode, the CodeMirror editor (autosave, never
  overwrites changes made on disk), `latexmk` builds, the Problems drawer, and the PDF viewer with SyncTeX
  both ways. Compiles keep the files of earlier passes, as Overleaf does, so a compile after an edit is
  usually a single LaTeX pass; "Compile from scratch" in the Problems bar starts clean.
- **Revisions.** The revision menu starts the next revision from the previous one's frozen final source,
  or continues the latest. From then on, each successful compile updates the active revision folder in
  the background:
  - the source snapshot;
  - `revision_clean.pdf`, `main_draft.pdf` and `supplementary_draft.pdf`;
  - the working copies, checked byte-identical;
  - `revision_tracked.pdf`;
  - a change-log entry naming the changed sections and figures.

  Earlier revisions are never written.
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

Next is M4: the agent, with its edits shown as diffs for you to approve.

## Install

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

By default the agent runs your installed `claude`, which uses the Claude plan you signed in with. Thor
removes `ANTHROPIC_API_KEY` from the agent's environment so a key in your shell cannot take over. You can
switch to an Anthropic API key in Settings. The key goes into your login Keychain (service "Thor") and
never into a file. The Claude plan sign-in is for your own copy of Thor; a copy given to others would
need API keys.

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
  - `settings.json`: sign-in method, model, effort, spending cap;
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
