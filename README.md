# Thor

A LaTeX app for macOS, made for revising papers. It writes and compiles offline, as Overleaf does, and its AI agent runs
on the model you choose: Claude Code or Codex on your own plan, an API key, or a model on your Mac. The agent works in a
private copy of your paper, and nothing it writes reaches your files until you accept it.

Thor replaces OpenAI's Prism, which is being retired, on your own Mac. The design and the decisions behind it are in
[docs/architecture.md](docs/architecture.md).

## Install

Thor runs on macOS 13 or later, on Apple silicon.

```bash
make dmg
```

This builds `build/Thor.dmg`. Open it, drag Thor to Applications, and open Thor. On first launch it installs what is
missing, with nothing to click:

- **MacTeX**, after macOS asks for your password once, in its own dialog;
- **Claude Code**, with Anthropic's installer, then the sign-in page in your browser if you are not signed in.

The app carries its own Python and packages, so the Mac needs nothing else. The `.dmg` is ad-hoc signed, which is fine
for your own Macs. Anyone else's Mac would need Developer ID signing and notarization, which is not done yet.

To build and open it straight from the source, run `make run`. Building needs the Xcode command-line tools,
[uv](https://docs.astral.sh/uv/) and Node (`brew install node`).

### Coming from Prism

Prism deletes its data when it shuts down, so export first: Settings, Data Controls, Export all projects (zip). Unzip
it and open a project's folder in Thor. Prism's export leaves out history, inline comments and the Zotero connection.
Keep a copy of the PDF from Prism to compare Thor's first build against.

## Writing and compiling

- **The editor** autosaves, and never overwrites a file that changed on disk. It completes `\cite`, `\ref`, file names
  and commands, checks spelling with your Mac's dictionary, previews equations on hover, and warns about unmatched
  braces and environments as you type. Search and replace across files is in the top bar (⌘⇧F). Vim and Emacs keys,
  and a symbol palette, are there too.
- **The visual editor.** The Visual switch shows headings, math, citations, figures, tables and lists as they read.
  The file stays LaTeX: whatever holds the cursor shows as source, and a selection stays as it reads. Table and Figure
  write the LaTeX for you, and text pasted from Word arrives as LaTeX.
- **Fast compiles.** After a small edit LaTeX usually runs once, in a process that has already read the preamble. The
  files of earlier passes are kept, as Overleaf does. Auto compiles each time you stop typing, and "Compile from
  scratch" starts clean.
- **The PDF** sits beside the editor, with SyncTeX both ways: ⌘J shows the cursor's place in the PDF, and a
  double-click in the PDF goes to the source. Pinch or ⌘-scroll to zoom. Its text can be selected and copied.
- **Problems.** Errors, undefined references and citations, overfull boxes and warnings, each with its file and line.
  Press a count to list just those.
- **History** keeps a version of each file as you open and save it. Compare any version with the text now, and
  restore one.
- **Git and Zotero.** The Git button commits the files you choose, as you, and pulls and pushes. The Zotero button
  searches your library while Zotero is running, adds the work to the bibliography and cites it at the cursor.
- **Your layout.** The files, editor, PDF and agent each collapse (⌘\ for the files, ⌘⇧\ for the agent). The agent
  docks on the right or along the bottom, and the dividers drag. Dark mode uses VS Code's greys.

## The agent

Ask in the agent panel, or press ⌘K. Select text and press ⌘L to attach it; one message can carry several passages.

The agent works in a private copy of your paper and compiles that copy. Its edits wait in the Changes tab, where you
accept or reject each one. An edit that changes a number, a citation or a word that can alter a finding is flagged, and
left out of "Accept all".

It runs on:

- **your plans:** Claude Code and Codex, through their installed command-line tools (the ChatGPT app includes Codex);
- **API keys:** Anthropic and OpenAI, kept in your Keychain;
- **models on your Mac:** a running Ollama or LM Studio is found by itself;
- **anything else:** a server speaking the OpenAI chat API, or any command-line program, added in Settings.

Choose one in Settings or in the agent panel. If it is not set up, Thor uses the first one that is.

## Revising a paper

A revision answers one round of comments. Each is a folder in the paper's folder, `revision1/`, `revision2/` and so on.

1. **Start a revision** from the revision menu in the top bar. It starts from the previous revision's final source,
   which stays frozen; the first starts from the source as it is now. Thor never writes into an earlier revision.
2. **Import the comments** in the Comments tab: a co-author's annotated PDF, and the email that came with it. Thor
   reads highlights, underlines, strike-outs, replacements, insertions, notes, text boxes and replies, and pen strokes
   as underlines, strikes, circles and dots, each joined to the note about it. Every comment gets its page and line,
   and is checked against the previous revision's PDF. A comment that cannot be acted on as it stands, such as a lone
   word, waits for your answer. The annotated PDF and the paper as it is now show side by side.
3. **Plan.** In the Plan tab the agent reads the comments and the paper, changing nothing, and proposes what to do about
   each. You approve each item, change it with a note, or choose to explain instead of changing the paper. Only your
   approval starts the edits.
4. **Edit**, yourself or with the agent. While a revision is active, the editor marks what changed since it started:
   added text tinted, deleted text struck through.
5. **The tracked PDF** follows each compile within seconds, because latexdiff runs only on the paragraphs that changed.
   It marks what the editor marks, and the PDF pane switches between it and the clean PDF.
6. **Reply.** In the Reply tab the agent answers each comment from the plan and from what really changed, in a formal
   and a plain version. Thor puts in each comment's own words itself, so the letter cannot misquote anyone. Edit both,
   then save Markdown, Word or PDF.
7. **Check** before it goes out, in the Checks tab:
   - that every comment has an answer, and how the last compile went;
   - words, figures, tables, references and pages, before and after, against a journal's limits if you set them;
   - each number in the text, with the result file and line that holds it. Finding a value shows it exists, not that it
     is the right one, so the line is there to read;
   - each reference against Crossref, which is sent only the cited works' titles and DOIs;
   - which script makes which figure, with a button to run it and a warning when a figure is older than its script;
   - a submission zip: one `.tex` with its inputs and bibliography in place and the figures it used, saved only once it
     compiles on its own in an empty folder.

After each successful compile the revision folder is brought up to date in the background: a snapshot of the source,
`revision_clean.pdf` and `revision_tracked.pdf`, the main text and the Supplementary Information as `main_draft.pdf` and
`supplementary_draft.pdf`, and a change-log entry naming the sections and figures that changed. Draft PDFs are copied
beside your source only where the paper already keeps them.

The revision menu also goes back to the original or to any finished revision, read only: its main file with what changed
in it marked, and its clean and tracked PDFs. The first or the latest revision can be deleted there. Its folder goes to
the Trash, and the later revisions move down one number.

## Signing in, and what leaves your Mac

- **Claude Code** uses the Claude plan your installed `claude` is signed in with. To sign in, run `claude` in Terminal.
  Thor removes `ANTHROPIC_API_KEY` from its environment, so a key in your shell cannot take over.
- **Codex** uses the ChatGPT plan your installed `codex` is signed in with. Thor finds the copy inside the ChatGPT app.
- **API keys** go into your login Keychain (service "Thor"), never into a file.
- **Ollama and LM Studio** need nothing: if they are running, their models are listed. A model whose name ends in
  `:cloud` sends your text to a cloud service, so Thor never picks one by itself.

Your paper's folder changes only when you accept an edit. Claude Code and Codex run inside their own sandboxes; a command
you add yourself does not. A co-author's comment could try to steer the agent, so with Claude Code, reading outside the
paper and opening a web address each ask you first.

## Project settings: `revision_app.toml`

Opening a folder detects its settings from the source, and writes nothing:

- the main file;
- the figure macro and its folders;
- where the Supplementary Information starts;
- the engine and the bibliography setup;
- the results, scripts and revision folders.

If the main file is the wrong one, open the right one and press **Set as main** in the file list, or right-click it
there. Only files LaTeX can start from are offered: a `\documentclass` and a `\begin{document}`, in the file or through
an `\input`. **Project folder** in the list reaches files outside the LaTeX folder.

Press **Save** in Project settings to write `revision_app.toml` at the top of the project, with a comment for every key.
You can edit it by hand too. It also holds what cannot be detected: a journal's limits, and how to run each kind of
figure script (`.py=/opt/miniforge/bin/python`).

## Where things live

- **App state,** in `~/Library/Application Support/Thor/`:
  - `settings.json`: which model, its effort, the spending cap;
  - `layout.json`: which panes are open, and their sizes;
  - `builds/`: every compile, kept out of your paper's folder;
  - `history/`: the versions of your files;
  - `agent/`: chats, and the agent's copies of your paper;
  - `thor.db`: recent projects, and which revision is active.
- **In a revision folder,** `supporting_files/` holds the source snapshot, the change log, and the `comments/`, `plan/`,
  `reply/` and `submission/` of that revision.
- **The backend's log:** `~/Library/Logs/Thor/backend.log`.

## Security

The backend listens on 127.0.0.1 only. Each launch makes a random token, which the window signs in with once, through
`/auth`, as an HttpOnly cookie. Every `/api` request needs it, and requests for any other host name are refused. The
backend exits when the app quits or crashes.

## Develop

```bash
./run.sh
```

This builds what is out of date and opens a development `build/Thor.app`, which runs this folder's Python.
`./run.sh --browser` opens the same interface in your web browser.

| Command | What it does |
|---|---|
| `make setup` | Creates the Python environment (`backend/.venv`, with uv) and builds the interface. |
| `make test` | Runs the backend tests (pytest), the interface's typecheck and tests (Vitest), and builds the shell. |
| `make check` | Prints the dependency report in the terminal. |
| `make dev` | Runs the backend on port 8790 with the fixed token `dev`, and Vite with live reload on 5173. |
| `make app` | Builds the development `build/Thor.app`. |
| `make run` | Builds the development app and opens it. |
| `make dmg` | Builds the self-contained `build/Thor.dmg`. |

```
backend/   the backend in Python 3.12 (FastAPI): the thor/ package and its tests/
ui/        the interface in React and TypeScript (Vite)
shell/     the Mac app around it, in Swift (AppKit and WKWebView), with make_icon.py for the bolt
scripts/   dev.sh and build_dmg.sh
docs/      architecture.md: the design, the Overleaf feature map, the milestones and the decisions
```

## Not built yet

These are open for anyone to take up:

- **Real-time co-editing.** Co-authors send annotated PDFs for now. Live editing needs a server every co-author can
  reach, because Thor answers only on 127.0.0.1, with a token that changes at each launch, and that has to stay for
  the local app. CodeMirror, Thor's editor, can share a document through Yjs.
- **Running on a cluster:** builds and figure scripts over ssh, with the results copied back.
