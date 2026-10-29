# CLAUDE.md: Thor

Thor is a macOS LaTeX app with Claude as its agent. It is built for one user, an atmospheric scientist
revising papers. The design, the Overleaf feature map, the milestones (M0–M10) and the recorded decisions
are in `docs/architecture.md`. Read it before starting a milestone. Build in the order recorded there.

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
  - PDF pages render off screen and are swapped in whole, so they never blank out.
  - Keep both when changing the editor or the viewer.
- **Paper folders are the user's.**
  - Detection is read-only.
  - Thor writes only:
    - the user's own edits in the editor (autosaved, refused if the file changed on disk);
    - `revision_app.toml` (on Save, or when the engine is changed);
    - the revision folders the workflow defines;
    - once approved, the agent edits the user accepted.
  - Builds go to `~/Library/Application Support/Thor/builds/<project>/`, never next to the source.
  - Test against fixtures or copies, never by opening a real paper folder in a test server:
    the editor autosaves.
- **Security.**
  - Keep the 127.0.0.1-only Host check and the token on every `/api` route, including new ones.
  - Never put the API key in a file, a log or a URL; it lives in the Keychain via `keyring`.
- **Agent sign-in.**
  - Default: the installed `claude` (Agent SDK `cli_path`) with the user's Claude plan, with
    `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` removed from its environment.
  - Do not set `CLAUDE_CONFIG_DIR`; that would lose the sign-in.
  - Use `setting_sources=[]` and pass instructions explicitly.
  - Model `claude-opus-5-5`, effort `high` by default.
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
  - latexmk runs in `builds/<project>/work/`, which keeps `.aux`, `.bbl` and the rest between compiles as
    Overleaf does, so a compile after an edit is usually one LaTeX pass. Each compile's results are
    copied into a new build folder, which is all the rest of Thor reads.
    - The work folder is cleared when the main file, LaTeX folder or engine changes, on "Compile from
      scratch", and by itself when LaTeX stops on an error in one of the kept files.
    - A PDF counts only if this compile wrote it (or latexmk found everything up to date).
  - Revision deliverables never reuse kept files: the revision update makes its own clean build.
  - With pdfLaTeX and LuaLaTeX, latexmk's passes run in draft mode (no images read, no PDF written),
    then one normal pass writes the PDF and SyncTeX (`run_latexmk`). The editor's final pdfLaTeX pass
    uses `\pdfcompresslevel=1`; deliverables keep full compression.
- **The window** has no title bar (`.fullSizeContentView`, an empty compact `NSToolbar` for the button
  height). The shell tells the page the bar's height and the buttons' width (`--titlebar-height`,
  `--titlebar-left`); empty parts of the toolbars move the window through the bridge (`dragWindow`).
- **Revisions** (`thor/revisions.py`):
  - Only the active revision is ever written. It becomes active through the menu (start the next one,
    or continue the latest after a warning). The active revision is kept in Thor's SQLite, never in the
    paper folder.
  - A new revision's baseline is the previous revision's `supporting_files/main.tex`, never the working
    copy. It is copied into `supporting_files/tracked_build/old/` with its figures.
  - Updates run in the background after each successful build. Only the latest queued build is used.
- **Tracked PDF** (`thor/tracked.py`): each fix there exists because latexdiff broke a real revision of
  the user's paper. Keep the tests in `tests/test_tracked.py` passing; they hold the hard cases.
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
