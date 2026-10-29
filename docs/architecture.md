# Thor: a LaTeX app for macOS with Claude as its agent

Approved 29 October 2026, with the decisions in section 6. M0 (foundations) is built; this document is the
plan for the rest. "Thor" is a working name; the final name is not chosen yet.

Scope: a revision workflow for LaTeX papers (co-authors' annotated PDFs, a plan, edits, a tracked PDF
and a reply), plus what Overleaf offers for writing
and compiling LaTeX, as a native Mac app that works offline. Claude is the agent for revisions, and it
also helps in the editor.

## 1. What I checked on this Mac

| Item | State | Consequence |
|---|---|---|
| TeX | No MacTeX/TeX Live (no `latexmk`, `latexdiff`, `pdflatex`, `synctex`); only `tectonic` 0.17 and `biber` | MacTeX must be installed. Earlier revisions were built on a remote machine with TeX Live 2023. |
| Python | System 3.9.6 (too old); uv-managed 3.12.13 in `~/.local/bin`; PyMuPDF not installed | The app runs in its own uv venv on Python 3.12. |
| Node | Not installed | Needed only to build the UI (dev time), not to run the app. |
| Swift / Xcode | Swift 6.4, Xcode 27.1 | Your other apps here are SwiftPM/Xcode projects; the app shell can be Swift too. |
| Claude Code | CLI 2.1.299 | The Agent SDK bundles its own copy; no dependency on this one. |

Scripts from earlier revisions made by hand hold logic worth keeping: the PDF split, the tracked build
(table placeholders, caption diffs, colours, figure frames), the reply generator and the style checks.
I will port these into tested modules rather than rewrite them from scratch.

## 2. What the Agent SDK docs settle

From the current docs (code.claude.com/docs/en/agent-sdk, read today):

- **Python SDK** `claude-agent-sdk` (Python 3.10+) bundles the Claude Code binary. `ClaudeSDKClient`
  holds a multi-turn session; `query()` is for one-shot calls.
- **Permissions run in a fixed order:** hooks → deny rules → ask rules → permission mode → allow rules →
  `can_use_tool` callback. A `PreToolUse` hook sees every tool call and can deny it or rewrite its input.
  `can_use_tool` is an async callback, so it can wait for a click in the UI.
- **Custom tools** run in-process: `@tool` + `create_sdk_mcp_server`, named `mcp__<server>__<tool>`.
- **Sessions** persist as JSONL and can be resumed or forked by id. `list_sessions()` and
  `get_session_messages()` let the app show past sessions. `enable_file_checkpointing` +
  `rewind_files()` undo an agent's file changes.
- **Streaming and cost:** `include_partial_messages` streams tokens. Each `ResultMessage` carries
  `total_cost_usd` and per-model usage. `max_budget_usd` caps a run.
- **System prompt:** the `claude_code` preset plus `append`. `setting_sources=[]` keeps your global
  `~/.claude` instructions out of the app's agent.
- **Bash sandbox** (macOS Seatbelt): writes only inside the working directory and the added
  directories; network through an allow-list.
- **Authentication:** decided: the agent runs the installed `claude` (SDK option `cli_path`) with
  the Claude plan it is signed in to. `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`
  are removed from the agent's environment so a key in the shell cannot take over the sign-in. An
  Anthropic Console API key (stored in the Keychain) is the alternative. Opus 5.5 is billed at $4 per
  million input tokens and $20 per million output tokens. The SDK docs do not let third-party products
  offer claude.ai sign-in, so this sign-in is for the user's own copy only. A copy given to others would
  need API keys.
- **Headless mode** (`claude --bare -p --output-format stream-json`) does the same from a shell. I use it
  only as a fallback. The SDK is better here because hooks and callbacks run inside the app.

## 3. Architecture

```
┌───────────────── Thor.app  (Swift, thin native shell) ──────────────────┐
│ windows, menus, folder picker, notifications, Open With, Dock           │
│ ┌──────────── WKWebView: React + TypeScript UI ───────────────────────┐ │
│ │ CodeMirror 6 editor · PDF.js viewer · comments · plan · diffs ·     │ │
│ │ reply editor · verification report · agent chat                     │ │
│ └──────────────────────▲──── HTTP + WebSocket ────────────────────────┘ │
└────────────────────────│────────────────────────────────────────────────┘
                         │ 127.0.0.1, random port, per-launch secret token
┌────────────────────────▼──── Python 3.12 backend (FastAPI, asyncio) ────┐
│ projects & settings (revision_app.toml) · SQLite app state             │
│ build: latexmk in fresh dir · log parser · SyncTeX · PDF split          │
│ revisions: freeze, snapshot, sync rule, change_log, byte checks         │
│ tracked: latexdiff pipeline with every fix in section 6 of your prompt  │
│ comments: PyMuPDF import, ink classification, location verification    │
│ checks & metrics · reply generator (LaTeX/PDF, Markdown, .docx)         │
│ history: private git store · job queue (local or ssh to HPC)           │
│ agent: ClaudeSDKClient + in-process tools + hooks                       │
└──────┬───────────────────┬──────────────────────┬───────────────────────┘
   MacTeX tools       figure scripts         Anthropic API, Crossref,
   latexmk, latexdiff, (local Python or      Unpaywall
   bibtex, synctex     ssh + batch on HPC)
```

Why this split:

- **The UI is web code** because the best LaTeX editing components are web components: CodeMirror 6 (the
  editor Overleaf uses, with a merge view that accepts or rejects single hunks) and PDF.js. The same UI
  also runs in a normal browser, so Playwright can test it end to end.
- **The backend is Python** because the hard parts are Python: PyMuPDF, pypdf, your existing scripts and
  the Python Agent SDK.
- **The shell is Swift**, about 500 lines, because it matches your other apps. It also gives native
  notifications, menus and the folder picker with no Rust or Electron toolchain. The backend stores the
  API key in the Keychain itself, through `keyring`. I considered Electron (200+ MB, Node runtime) and
  Tauri (needs Rust); neither adds anything we need.
- **Where state lives:** the project folder gets only `revision_app.toml` and the revision folders you
  asked for. App state lives in `~/Library/Application Support/Thor/`: SQLite, settings, history and
  staging copies. This keeps your Overleaf-ready `manuscript/` clean.
- **Agent transcripts** stay where Claude Code keeps them (`~/.claude/projects/`).
  `CLAUDE_CONFIG_DIR` is left alone so the agent keeps your Claude Code sign-in.
  `setting_sources=[]` still keeps your global instructions out.

### How agent edits stay under your control

```
your working files ──APFS clone (instant)──▶ staging copy
                                             agent reads, edits, compiles here
                                             PreToolUse hook: no Edit/Write outside staging
                                             Bash sandbox: writes only in staging + build dirs
          ◀── accepted hunks ── Diffs view (per file, per hunk; rejected hunks reported back to the agent)
then the sync rule rebuilds revisionN/ in the same action
```

- **Before plan approval** the agent has no Edit or Write tools at all (`disallowed_tools`). It can only
  read, and it delivers the plan through a `submit_plan` tool that has a schema.
- **After approval** a new turn gives it Edit and Write, confined to the staging copy.
- **Bash is pre-approved for:** `latexmk`, `latexdiff`, `python` and read-only `git`. Any other command
  becomes an approval card in the chat. `git commit` is denied to the agent: commits happen only when you
  press the button, authored as you, with no co-author line.
- **The agent works through in-process tools:** `build`, `verify`, `comments`, `results_search` (returns
  file:line, so every number has a quoted source), `crossref_lookup`, `fetch_reference_text` (legal open
  copies through Unpaywall, with the source recorded), `check_generated_tables`, `run_script`
  (background job, local or HPC) and `submit_replies`.
- **System prompt:** the `claude_code` preset, plus your style rules, writing rules and standing
  instructions (by default the paper's CLAUDE.md), plus the project memory file the app maintains. A
  settings page shows the full assembled prompt.
- **Settings:** model `claude-opus-5-5` by default, selectable. Effort defaults to `high`, because Opus
  5.5's own default is `medium`. With an API key, each run has a spending cap (`max_budget_usd`). With
  the Claude plan there is no cap, because the plan has its own limits.

### Builds

- **Deliverable builds** follow your section 2 to the letter. Each build runs
  `latexmk -pdf -interaction=nonstopmode -file-line-error -synctex=1` into a fresh output folder. A
  non-zero exit counts as failure. The report lists errors, undefined references and citations, overfull
  boxes (with source lines) and the page count. Then come the split at the marker and a sha256 check that
  the working copies and the revision copies are identical.
- **Editor previews** can reuse aux files in a private cache for speed. They fall back to a clean build on
  any bibliography or reference change. Deliverables never reuse aux files.
- **Engines:** pdfLaTeX, XeLaTeX or LuaLaTeX, chosen per project. Two toolchains:
  - **Local** (MacTeX 2026).
  - **Remote** (a TeX Live on another machine, over ssh), for builds that must match PDFs made there,
    so the page and line numbers in old comments still line up.

## 4. Overleaf features, mapped

| Area | Overleaf | In Thor | Milestone |
|---|---|---|---|
| Editor | Syntax highlighting, folding, bracket matching, auto-close | CodeMirror 6 with a LaTeX grammar | M1 |
| | Autocomplete: commands, environments, `\ref`, `\cite`, file paths | Labels and cite keys from the `filecontents` bibliography and `.bib` files; figure names through `\figfile` | M6 |
| | Search and replace across the project, regex | Same | M6 |
| | Spell check with a custom dictionary | macOS system spell checker through the shell (your learned words apply); skips commands and math | M6 |
| | Code check (unbalanced braces and environments) | Live diagnostics, plus your style rules as warnings in the editor (em dashes, colons, "Supplementary Fig." in the SI) | M6 |
| | Vim/Emacs keys, themes, dark mode | Same | M6 |
| | Outline, word count, symbol palette, math preview on hover | Outline and texcount in M1; the rest in M6 | M1/M6 |
| | Visual editor, table generator, figure dialog, paste from Word | WYSIWYM mode built on CodeMirror decorations | M8 |
| Compile | Engines, main file, auto-compile, draft mode, clear cache | Same; adds the remote TeX Live engine | M1 |
| | Log with hints and jump to error | Problems panel, click to jump | M1 |
| | PDF viewer, SyncTeX both ways, detached PDF window | PDF.js + `synctex` CLI; PDF in a second window or monitor | M1 |
| Files | File tree, upload, image/PDF preview, zip download | Same; Finder drag and drop; submission zip (flattened source + figures) | M1/M7 |
| History | Automatic versions, labels, compare, restore | Private git store: every save and every agent change is a version; revisions are labels; compare any two (text diff, or a tracked PDF) | M2 |
| Review | Comments and track changes in the source | Agent edits arrive as tracked suggestions with per-hunk accept/reject; your own edits can show as live changes against the revision baseline | M4 |
| Collaboration | Real-time co-editing, chat, sharing, roles | Needs a server. See question 4. | M9 |
| Integrations | Git, GitHub, Dropbox, Zotero, Mendeley | Git/GitHub sync; Overleaf git sync (if you use it); Zotero through its local API; Dropbox and iCloud work because the folder is local | M7/M9 |
| AI | Error Assist, rewriting, equation and table generation | Claude agent: fix the build, rewrite a selection to your style rules, chat scoped to project and revision | M4 |

Beyond Overleaf:

- The whole revision workflow in your prompt (sections 1–8).
- Annotated-PDF import, including ink strokes.
- Tracked PDFs built with your latexdiff fixes.
- Number provenance: every number linked to a result file and line.
- Reference checks against Crossref, with a reading of the source text.
- Journal metrics before and after each revision.
- A reply-letter generator in two tones.
- A figure-script runner (local or HPC) that knows which script makes which figure.
- Fully offline editing and compiling.

## 5. Build plan

Each milestone ends with tests passing and something you can use.

- **M0 Foundations.**
  - Repository skeleton (Swift shell, Python backend, React UI), `make dev` and `./run.sh`.
  - Start-up dependency check with install instructions; settings; API key in Keychain.
  - CLAUDE.md and README.
  - *Done when* the app opens a LaTeX folder and the dependency report is correct.
- **M1 Editor and compile.**
  - File tree, CodeMirror editor, latexmk builds with the full report, problems panel.
  - PDF.js viewer, SyncTeX both ways, outline, word count, PDF split at the marker.
  - *Done when* a real paper compiles, an error click jumps to the line, and a
    double-click in the PDF jumps to the source.
- **M2 Revisions, history and the tracked PDF** (your sections 1, 2 and 6).
  - Revision folders exactly as you laid them out; the frozen previous revision; the sync rule;
    `change_log.txt`; byte checks.
  - The latexdiff pipeline with every fix.
  - The sample test project with all the hard cases from your section 11.
  - *Done when* a tracked PDF made by hand for an earlier revision can be rebuilt from its baseline
    and matches.
- **M3 Comments** (section 3).
  - PyMuPDF import of every annotation type; ink stroke classification; caret/strike-out grouping.
  - Locations from `lineno`, captions and notes; verification against the baseline build, with any
    page or line shift reported.
  - Author merge; a queue of unclear comments for you to answer.
  - Comments view: the annotated PDF and the new PDF side by side.
  - Tested on generated PDFs and on a real co-author's annotated revision.
- **M4 Agent and diffs** (sections 5 and 9).
  - Chat with streamed messages, tool calls and cost; sessions per project and revision, resumable.
  - Staging copy; Diffs view with per-hunk approval; background jobs with notifications.
  - Commit button; the in-process tools.
  - Agent tests replay recorded sessions, so they cost nothing. A few live tests run only when you set
    a flag.
- **M5 Plan gate** (section 4).
  - Plan as structured data plus a PDF; decisions D1, D2, … with approve, change and explain.
  - The plan is regenerated with your decisions recorded; nothing is edited until you approve.
- **M6 Editor depth.** The editor rows of the table above.
- **M7 Verification, metrics and reply** (sections 7 and 8).
  - Comment, build and style checks; metrics before and after against journal ranges.
  - Reply document in both tones, editable before the PDF is built, with copyable text; Markdown and
    .docx export.
- **M8 Visual editor**, table and figure dialogs.
- **M9 Collaboration and sync**, as you decide in question 4.
- **M10 HPC runner and packaging.**
  - ssh builds and figure scripts using a batch command you configure,
    with results copied back.
  - A `.dmg` with a self-contained `Thor.app`: its own Python with every package, and the built
    interface. No Python, Node or Homebrew is needed on the Mac it runs on.
  - **A first-run Setup screen** lists what is missing and installs it with one button each, showing
    text progress:
    - **TeX:** if MacTeX or TeX Live is already installed, Thor uses it. Otherwise it installs MacTeX
      (already built; see decision 9).
    - **Claude Code:** if missing, Thor runs Anthropic's official installer (no password), then opens
      the sign-in.
    - **git:** if missing, Thor asks macOS to install its command-line tools, and macOS shows its own
      dialog.
  - **Signing:** a `.dmg` for your own Macs only needs ad-hoc signing, and you confirm the first
    launch. For other people's Macs, the app needs Developer ID signing and Apple notarization.

Recommended order: M0 → M1 → M2 → M3 → M4 → M5 → M7, which completes the revision workflow. M6, M8, M9
and M10 come after. The M1 editor is good enough to write in meanwhile.

**Tests:**
- pytest for the backend (sample project, PDFs annotated inside the test, golden outputs).
- Vitest for UI logic.
- Playwright end to end against the running app in Chromium and WebKit.
- XCTest for the shell.

**UI rule:** no pulsing, flashing or sweeping effects anywhere. Progress shows as text and still bars
("Compiling, pass 2 of 4").

## 6. Decisions (29 October 2026)

1. **Name and place:** Thor, in its own repository.
2. **Order:** M0 → M1 → M2 → M3 → M4 → M5 → M7 (the revision workflow), then M6, M8, M9, M10.
3. **TeX:** MacTeX 2026 is installed locally.
   - **Checked in M1:** a revision's frozen source, built here, matches the build made remotely with
     TeX Live 2023 page for page, including where the Supplementary Information starts; the text
     differs only in `\today`.
   - So the remote TeX engine is not needed for page and line numbers to match.
   When a build stops on a missing package (`File 'x.sty' not found`), Thor offers to install it with
   `tlmgr`. This arrives in M1 for MacTeX, and the private TeX Live of the `.dmg` setup uses it too.
   The remote TeX Live engine moves forward only if pages or line numbers differ.
4. **Collaboration:** co-authors send annotated PDFs. No sync is needed for now, so M9 shrinks to
   Git/GitHub.
5. **Installs:**
   - Homebrew Node, used only to build the UI.
   - A private Python 3.12 venv in `backend/.venv`, managed by uv.
6. **Sign-in:** your Claude plan through the installed Claude Code (see section 2). An API key stays
   available in Settings. With an API key, the spending cap is $5 per run by default.
7. **Revision folders:** the location is a setting, `revisions_dir`. It defaults to wherever revision
   folders already exist, and to the LaTeX folder for a new project.

8. **Name:** "Thor" (with a lightning-bolt icon) is a working name. To rename the app:
   - Change the display name in `backend/thor/paths.py` (`APP_NAME`), `ui/src/brand.tsx` and
     `shell/Info.plist`.
   - The package, the env vars (`THOR_*`) and the bundle id need a search-and-replace.
   - App state moves to a new folder, and a saved API key must be entered again.
9. **A normal Mac `.dmg`** (29 October 2026, built): you drag Thor to Applications and open it. With
   nothing to click, it installs what is missing:
   - **Claude Code**, with Anthropic's installer, then the browser sign-in if needed.
   - **MacTeX:**
     - Thor first reuses a package in Homebrew's cache. Otherwise it downloads the official one from
       CTAN and checks CTAN's SHA-512.
     - It runs the package only when it is signed by "Developer ID Installer: Richard Koch
       (RBGCY5RJWM)" and notarized.
     - macOS asks for the password once, in its own dialog.
   - **Python and every package are inside the app.**
   - **Code:** `scripts/build_dmg.sh`, `backend/thor/first_run.py`, `backend/thor/setup_tex.py`,
     `ui/src/components/SetupScreen.tsx`.
   - **Still in M10:** Developer ID signing and notarization for other people's Macs, and an Intel
     build.

## 7. Status

- **M0 (done):** the shell, backend, dependency check, settings, project detection, the `.dmg` and
  first-run setup.
- **M1 (done, 29 October 2026):** the window from the mockups, with true-black dark mode.
  - CodeMirror editor: autosave, and conflicts with changes on disk.
  - `latexmk` builds into fresh folders, with the log parsed into the Problems drawer.
  - PDF.js viewer with SyncTeX both ways.
  - Outline, file tree, texcount word count, and the main/SI split in the status.
  - Checked on a copy of a real paper with its Supplementary Information: a full build takes about
    2.5 s; errors jump to their line.
- **M2 (done, 29 October 2026):**
  - The revision menu (start from the frozen previous revision; continue the latest with a warning).
  - The sync rule as background updates after each successful compile: snapshot, clean and split
    PDFs, working copies with a byte-identity check, the tracked PDF, and change-log entries.
  - The latexdiff pipeline with every fix from section 6. It also handles:
    - commands and multibib lists only the baseline has;
    - references removed from the bibliography, which keep their baseline numbers.
  - History with diff and restore.
  - **Acceptance:** a real revision's tracked PDF, rebuilt from its baseline, matches the one made by
    hand page for page, apart from the date and one latexdiff word split.
- **Tests:** 86 backend tests (the hard-case sample paper from section 11, real builds, revisions,
  history) and 10 UI tests.
