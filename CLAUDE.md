# CLAUDE.md: Thor

Thor is a macOS LaTeX app with an AI agent, and the local replacement for OpenAI's Prism, which is being retired.
It is built for one user, an atmospheric scientist revising papers. The design, the Overleaf feature map, the
milestones (M0–M10) and the recorded decisions are in `docs/architecture.md`. Read it before starting a milestone.
Build in the order recorded there.

"Thor" is a working name, and the logo is a lightning bolt. The final name is not chosen yet.
- **The display name lives only in:** `backend/thor/paths.py` (`APP_NAME`), `ui/src/brand.tsx` (`APP_NAME`)
  and `shell/Info.plist` (`CFBundleName`, which the shell reads as `appName`).
- **User-facing text must use these**, never a hard-coded name.
- **The bolt** is drawn by `shell/make_icon.py` (app icon), `ui/src/brand.tsx` (`BoltLogo`) and
  `ui/public/favicon.svg`. Keep the three shapes the same.

## Layout and commands

- `backend/`: Python 3.12, FastAPI, managed by uv (`backend/.venv`).
  - Package `thor/`, tests `tests/`.
  - Entry points: `python -m thor serve` and `python -m thor check`.
- `ui/`: React 18 + TypeScript + Vite. The built UI in `ui/dist` is served by the backend.
- `shell/`: Swift (SwiftPM, AppKit + WKWebView), about 500 lines.
  - It starts the backend with `THOR_TOKEN`, reads `THOR_LISTENING port=N` from its stdout, and
    loads `/auth?token=...`.
  - The bridge (`window.webkit.messageHandlers.thor`) answers only pages from 127.0.0.1.
- `make test` runs everything. `make dev` uses ports 8790 (backend) and 5173 (Vite). Port 8765 is taken
  by another program on this Mac.

## Rules

- **No motion in the UI.** No pulsing, flashing or sweeping effects, no animated spinners, no CSS
  transitions or animations. The user finds them medically harmful. Show progress as text and still bars
  ("Compiling, pass 2 of 4").
  - The editor cursor does not blink (`cursorBlinkRate: 0`).
  - `styles.css` turns off every animation and transition, for what libraries bring along too (Vim's block cursor).
  - PDF pages render off screen and are swapped in whole, so they never blank out.
  - Keep both when changing the editor or the viewer.
- **Paper folders are the user's.**
  - Detection is read-only.
  - Thor writes only:
    - the user's own edits in the editor (autosaved, refused if the file changed on disk);
    - `revision_app.toml` (on Save, or when the engine is changed);
    - the revision folders the workflow defines;
    - once approved, the agent edits the user accepted;
    - what the user presses a button for: running a figure script in place, adding a work from Zotero to the
      bibliography, and git commit, pull and push.
  - Builds go to `~/Library/Application Support/Thor/builds/<project>/`, never next to the source.
  - Test against fixtures or copies, never by opening a real paper folder in a test server:
    the editor autosaves.
- **Security.**
  - Keep the 127.0.0.1-only Host check and the token on every `/api` route, including new ones.
  - Never put the API key in a file, a log or a URL; it lives in the Keychain via `keyring`.
- **The agent** (`thor/agent/`). Every model works the same way: in a staging copy of the paper, whose edits the
  user accepts or rejects hunk by hunk (`staging.py`, the Changes tab). Nothing else writes the paper folder.
  - **Providers** (`providers.py`): Claude Code and Codex (the user's plans, through the installed CLI), the Anthropic
    and OpenAI APIs (keys in the Keychain), Ollama and LM Studio (found by asking them, nothing to set up), any server
    speaking the OpenAI chat API, and any command-line program. The chosen one is used if it works, else the first
    that does (`effective`).
  - **Backends load only when used:** `anthropic` and `claude_agent_sdk` each take a second to import. Keep them out
    of the import path of `thor.server`.
  - **Claude Code:** the installed `claude` (Agent SDK `cli_path`), with `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`
    removed from the environment (`hide_api_keys`; the SDK can only add to it). Do not set `CLAUDE_CONFIG_DIR`; that
    would lose the sign-in. Use `setting_sources=[]` and pass instructions explicitly. Model `claude-opus-5-5`, effort
    `high` by default. Keep `permission_mode="default"`: left unset, a classifier answers instead of the user.
  - **Codex:** `codex exec --json --sandbox workspace-write`, with flags before `resume`. The ChatGPT app bundles a CLI;
    `codex_cli.CANDIDATES` finds it. The stream has no schema version, so unknown events are ignored.
  - **The Anthropic API** goes through the `anthropic` SDK; the rest through `httpx`. The history is append-only, with
    responses kept as they came (thinking blocks), and tool input is validated before running (`check_args`).
  - **What the agent may do without asking:** read and edit inside its copy. Reading elsewhere and `WebFetch` show an
    approval card, because a co-author's comment could tell it to read a private file and send it to a web address.
    Thor's tools confine paths themselves (`ToolBox._path`).
  - **`latexmkrc` runs as code,** so the agent cannot change it: edits are refused, and `restore_rc` puts it back before
    Thor compiles the agent's copy, outside the agent's sandbox.
  - **A cloud model is never picked by itself:** Ollama's `*:cloud` models send text away (`is_cloud_model`).
  - **The transcript takes text in whole lines,** at most once a second (`runs.py`), so it does not flicker.
  - **A task is a turn that can only read** and hands in structured work (`Task`, `Submission`, `ToolBox.read_only`): the
    plan and the reply. Claude Code gets no Edit, Write or Bash and a tool over MCP; Codex gets `--sandbox read-only` and
    `--output-schema`; the rest get the look-only tools and the tool, or a JSON block in text mode (`take_submission`).
    Tell a provider about a hand-in tool only if it has one (`how_to_compile`): Codex, told to use one it lacked, wrote
    "I could not submit" into the letter.
- **The plan** (`thor/plan.py`, `PlanView`): nothing is edited until it is approved. Each item is approved, changed or
  explained. A new draft keeps an approval only if the proposal is word for word what the user read. It lives in the active
  revision's `supporting_files/plan/`.
- **The reply** (`thor/reply.py`, `ReplyView`): two tones, edited before any file is made of it. Thor adds each comment's
  own words itself, so the letter cannot misquote anyone. `thor/documents.py` writes the PDF (XeLaTeX or LuaLaTeX), Word
  (a small zip of XML, no library) and Markdown.
- **Checks** (`thor/verify.py`, `ChecksView`) say what they looked at and what they did not.
  - **Numbers** (`thor/numbers.py`): a number is found when a result file holds a value that rounds to it. That shows the
    value exists, not that it is right, and the report says so.
  - **References** (`thor/references.py`): one Crossref request at a time, waiting when told to, because it answers 429 to
    anyone asking in parallel. Unpaywall needs an email address: only one the user typed in for this, and it goes only there.
  - **Submission** (`thor/submission.py`): made from what the last compile read, and compiled from scratch in an empty
    folder before it counts.
- **Figure scripts** (`thor/scripts.py`): the user's click runs a script where it is. The agent's `run_script` asks first and
  runs it on its copy under the sandbox with no network, with the results linked in read-only.
- **Git** (`thor/gitops.py`): only on a button, with the user's own identity and nothing added to the message. The agent's
  copy has no `.git`, and no tool of its reaches this.
- **The visual editor** (`editor/visual.ts`): decorations over the LaTeX, not a second document. Whatever holds the cursor
  shows as source, so undo, autosave and track changes work as ever. They come from a `StateField`, because a plugin may not
  replace line breaks. A paste from Word turns into LaTeX only in this view (`editor/insert.ts`).
- **Tests stay isolated.**
  - `tests/conftest.py` sets `THOR_HOME` (app state) and `THOR_TOOL_PATH` (PATH for tools) to
    temporary folders.
  - Use `MemorySecret`, never the real Keychain.
  - Fake tools are small shell scripts written into the test's bin folder.
- **Tools on PATH.** An app opened from Finder has a short PATH. Run tools through
  `thor.paths.which`/`run_tool`/`tool_env`, which add the login shell's PATH and `/Library/TeX/texbin`.
  - MacTeX's biber is a universal binary that unpacks itself with `lipo -extract_family`, which the lipo
    in Xcode 27 no longer has, so every biblatex build failed. `tool_path` puts this Mac's slice of it
    first (`thin_binary_dir`, kept in Thor's `tools/` folder until biber changes).
- **The `.dmg` app** (`scripts/build_dmg.sh`, `make dmg`):
  - It is self-contained: a python-build-standalone CPython in `Contents/Resources/python` with the
    locked packages, plus the built UI.
  - The Agent SDK's bundled Claude Code (270 MB) is removed, because Thor runs the installed `claude`.
  - Python is precompiled and `PYTHONDONTWRITEBYTECODE` is set, so nothing writes into the signed
    bundle.
- **First-run setup** (`thor/first_run.py`, started by the shell's `--setup`): installs missing Claude
  Code and MacTeX with nothing to click. The only prompts allowed are macOS's password dialog and the
  Claude sign-in page, so never read TCC-protected folders such as Downloads, Documents or Desktop
  there.
- **MacTeX setup** (`thor/setup_tex.py`):
  - It runs a package only when `pkgutil` reports MacTeX's signer (team RBGCY5RJWM) and Apple
    notarization. Never loosen that check.
  - The password goes only into macOS's own dialog (`osascript ... with administrator privileges`).
- **Builds** (`thor/build.py`, `thor/latexlog.py`, `thor/synctex.py`):
  - The log width is set to 10000 so log lines are not wrapped. bibtex refuses much more ("3 is a bad
    bad").
  - When a build has errors, only errors and overfull boxes are reported, because latexmk stopped before
    bibtex and the reruns.
  - LaTeX runs in `builds/<project>/work/`, which keeps `.aux`, `.bbl` and the rest between compiles as
    Overleaf does. Each compile's results are copied into a new build folder, which is all the rest of Thor reads.
  - An editor compile is one LaTeX pass, and more only if it changed a file a pass reads back (`latex_passes`).
    latexmk runs only when nothing is kept yet, when bibtex, biber or an index program has work to do
    (`tool_state`), or when the project has a `latexmkrc`. If nothing LaTeX read has changed, nothing runs at all.
  - **The spare** (`thor/spare.py`): after each compile a LaTeX starts on a frozen copy of the preamble and waits on
    `\read`; the next compile wakes it. It reads `main.tex` with every character of the preamble ignored, so line
    numbers and SyncTeX are right, and works on all three engines. It is used only if the preamble and every file it
    read are unchanged. If a woken pass fails, a fresh pass decides, and its report stands.
  - **Fast figures** (`thor/figures.py`): PNGs with an opaque alpha channel get a copy without it, which pdfTeX embeds
    without decoding. Found first through `TEXINPUTS`; the `.fls` is rewritten to name the originals. Editor compiles
    only, never deliverables.
    - The work folder is cleared when the main file, LaTeX folder or engine changes, on "Compile from
      scratch", and by itself when LaTeX stops on an error in one of the kept files.
    - A PDF counts only if this compile wrote it (or latexmk found everything up to date).
  - Revision deliverables never reuse kept files: the revision update makes its own clean build.
  - With pdfLaTeX and LuaLaTeX, latexmk's passes run in draft mode (no images read, no PDF written),
    then one normal pass writes the PDF and SyncTeX (`run_latexmk`). The editor's passes compress at level 1;
    deliverables keep full compression.
- **Track changes in the editor** (`editor/tracked.ts`) updates with each keystroke, in the same update as the text. It
  stays fast because `trackchanges.ts` caches each paragraph's comparison. Within one word it marks only the letters
  that differ, so the text barely moves while typing.
- **Selections** are drawn above the text in a see-through colour (`.cm-selectionLayer`, `--editor-selection`). CodeMirror
  puts them underneath, where the current line's background and the tracked-change marks hide them.
- **The window** has no title bar (`.fullSizeContentView`, an empty compact `NSToolbar` for the button
  height). The shell tells the page the bar's height and the buttons' width (`--titlebar-height`,
  `--titlebar-left`); empty parts of the toolbars move the window through the bridge (`dragWindow`).
  - No banner saying which app this is: the toolbar starts with the back button and the project's name.
  - The files, editor, PDF and agent each collapse, and the agent docks on the right or as a bar along the bottom.
  - Splitters show a still line while dragging, and panes take the new size on release, so the PDF is not redrawn
    over and over.
  - Layout choices are kept by the backend (`layout.ts`, `layout.json`), not in browser storage: the port changes with
    each launch, and browser storage goes with the port.
- **Revisions** (`thor/revisions.py`):
  - Only the active revision is ever written. It becomes active through the menu (start the next one,
    or continue the latest after a warning). The active revision is kept in Thor's SQLite, never in the
    paper folder.
  - A new revision's baseline is the previous revision's `supporting_files/main.tex`, never the working
    copy. It is copied into `supporting_files/tracked_build/old/` with the rest of its source
    (`copy_source`: `\input` files, bibliography, class and style files, figures).
  - Draft PDFs are copied beside the source (`working_copies`) only where the paper already keeps them.
  - Updates run in the background after each successful build. Only the latest queued build is used.
    The tracked PDF comes first, from an incremental compile (`_tracked_preview`), so it shows within seconds of the
    compile. The revision's own clean files wait for a pause (`DELIVERABLES_AFTER_SECONDS`), and a newer compile stops
    them at once, killing their TeX (`run_tool(cancel=)`), so that they never hold up the next tracked PDF.
  - The snapshot takes what the compile read (its `.fls`) plus the `.bib` and `.bst` files named in its
    `.bcf`, `\bibdata` or `\bibstyle`, which the `.fls` does not list.
- **Tracked PDF** (`thor/tracked.py`): each fix there exists because latexdiff broke a real revision of
  the user's paper. Keep the tests in `tests/test_tracked.py` passing; they hold the hard cases.
  - latexdiff's time grows far faster than the amount changed (68 s for 63 edited paragraphs of a long paper), so
    `thor/fastdiff.py` runs it only on the changed blocks, in parallel, and keeps each answer. If that fails,
    `mark_up` falls back to latexdiff on the whole document.
  - **The PDF marks what the editor marks.** latexdiff's smallest unit is a whole hyphenated word, so it struck
    `single-moment` when only `moment` changed. `thor/wordmarks.py` compares each plain pair it writes again, by the rules
    of `ui/src/trackchanges.ts`. Change one, change both: `tests/data/word_changes.json` runs in both test suites. Anything
    with a command, braces or math stays as latexdiff marked it.
  - A spare's preamble can empty files just by being read (multibib's `\newcites` opens `S.aux`). `Spare` keeps copies,
    and whatever judges the last compile's state reads through `as_it_was`.
- **Comments** (`thor/comments.py`):
  - Each import lives in the active revision's `supporting_files/comments/<name>/`: a copy of the PDF
    (`annotated.pdf`), `comments.json`, `comments.txt` and any pasted `email.txt`. The PDF the user
    picked is only read.
  - The baseline is the previous revision's `revision_clean.pdf` (`revisions.baseline_pdf`).
  - `tests/test_comments.py` builds an annotated PDF with every annotation type, pen strokes and a
    shifted baseline. Each case there came from a real co-author's PDF; keep them passing.
- **History** (`thor/history.py`) lives in `~/Library/Application Support/Thor/history/`. Versions are
  recorded on file open and save; unnamed saves within 2 minutes coalesce.
- **PyMuPDF 1.x:** pinned below 2.0, which pulls in onnxruntime and numpy (100 MB). Use `import pymupdf`.
- **Commits:** only when the user asks, authored as the user, with no Claude co-author line and no
  mention of Claude in commit messages.
- **Code style:** match the surrounding code. Comments are sparse and say why. User-facing text is plain
  and specific: say what happened and what to do.
