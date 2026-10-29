# Thor

"Thor" is a working name, with a lightning-bolt icon.

A LaTeX app for macOS with Claude as its agent. It reads co-authors' annotated PDFs and plans the
revision with you. It edits only what you approve, builds clean and tracked PDFs, and drafts the reply.
It also aims to do what Overleaf does for writing and compiling, offline. The design and the build plan
are in [docs/architecture.md](docs/architecture.md).

**Status:** M1 (editor, builds, PDF viewer).

Working now:
- **The window from the mockups,** with a true-black dark mode.
- **Editor:** CodeMirror with LaTeX colouring, line numbers, folding and search. It autosaves 1.5 s
  after you stop typing. A file changed by another program is picked up when Thor comes to the front,
  and is never overwritten.
- **Compile** (⌘S, ⌘B, ⌘↩): `latexmk` into a fresh folder, with nothing written next to your source.
  - The status shows pages, the main/SI split, and errors, undefined references and citations, and
    overfull boxes.
  - The Problems drawer jumps to each problem's line.
- **PDF viewer:** double-click jumps to the source; ⌘J shows the cursor's place in the PDF.
- **Sidebar:** the file tree, opened at the LaTeX folder, and the document outline.
- **Status line:** the texcount word count.

Next is M2: revision folders, history and the tracked PDF.

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
