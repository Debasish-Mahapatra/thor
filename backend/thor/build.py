"""Compiles a project and reports what happened.

An editor compile runs in the project's kept work folder: one LaTeX pass, woken from a process that has
already read the preamble when it can (spare.py), with figures pdfTeX can embed quickly (figures.py), and more
passes only if references moved. latexmk runs when there is nothing kept yet or when a bibliography or index
tool has work to do. Each compile's results are copied into a new build folder, which is all the rest of Thor
reads. Nothing is written into the project; even the filecontents bibliography lands in the output folder.
A non-zero exit is a failure even when a PDF appears.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import pymupdf

from . import figures, latexlog
from .paths import CANCELLED, app_home, clone_file, run_tool, tool_env
from .projects import ProjectConfig, inside
from .spare import KEPT_SUFFIX, PREAMBLE_NAME, Spare, as_it_was, begin_line, recorded_inputs, wake_command

ENGINE_FLAGS = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}
# Unwrapped logs. bibtex refuses widths much above 10000 ("3 is a bad bad").
LOG_ENV = {"max_print_line": "10000", "error_line": "254", "half_error_line": "238"}
KEEP_BUILDS = 3
TIMEOUT = 600
# The flag that makes an engine's passes skip the images and the PDF; see run_latexmk.
DRAFT_FLAG = {"pdflatex": "-draftmode", "lualatex": "--draftmode"}
# A preview's PDF compresses at zlib level 1 instead of 9: the same pixels and text, in a third of the time and
# a PDF about twice the size. Deliverables keep full compression.
PREVIEW_SETUP = {"pdflatex": r"\pdfcompresslevel=1 ", "lualatex": r"\pdfvariable compresslevel=1 ", "xelatex": ""}
PREVIEW_ARGS = {"xelatex": ["-output-driver=xdvipdfmx -z 1"]}
FINAL_PASS = "--- Final pass, writing the PDF"
LATEX_PASS = "--- LaTeX pass"
FONT_NOT_LOADABLE = re.compile(r'="([^"]+)" at [\d.]+pt not loadable: Metric \(TFM\) file or installed font not found')
# Main files whose name can go into a TeX command line as it is.
SIMPLE_NAME = re.compile(r"^[\w./-]+\.tex$")
PASS = re.compile(rf"^(?:Run number \d+ of rule '(?:pdf|xe|lua)latex'|{FINAL_PASS}|{LATEX_PASS})", re.M)
HEX_LABEL = re.compile(r"<FEFF((?:[0-9A-Fa-f]{4})+)>")
BCF_SOURCE = re.compile(r"<bcf:datasource[^>]*>([^<]+)</bcf:datasource>")
BIBDATA = re.compile(r"\\bibdata\{([^}]+)\}")
BIBSTYLE = re.compile(r"\\bibstyle\{([^}]+)\}")
# latexmk's project files can run anything and may add rules this module does not know about.
LATEXMK_RC = ("latexmkrc", ".latexmkrc")
# LaTeX passes one compile may need before references settle; latexmk's limit is 5.
MAX_PASSES = 4
# Spares kept at once, across projects.
MAX_SPARES = 3
SPARE_LIFETIME = 900
# What bibtex, biber and the index programs read: when these change, latexmk has to run them.
TOOL_INPUTS = {".bcf", ".idx", ".glo", ".nlo", ".acn"}
# Files whose changes do not call for another LaTeX pass: they are not read back, or latexmk deals with them.
NOT_REREAD = {".log", ".fls", ".pdf", ".gz", ".synctex", ".fdb_latexmk", ".blg", ".bbl", ".xml", *TOOL_INPUTS}
# Thor's own records in the work folder.
SETUP_RECORD = "thor-work.json"
TOOLS_RECORD = "thor-tools.json"
INPUTS_RECORD = "thor-inputs.json"
SI_RECORD = "thor-si.json"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_spares: dict[str, Spare] = {}
_spare_timers: dict[str, threading.Timer] = {}
# Preambles a spare failed on, by project, so it is not tried again until they change.
_bad_preambles: dict[str, bytes] = {}
_progress: dict[str, str] = {}


@dataclass
class BuildResult:
    id: str
    ok: bool
    exit_code: int
    seconds: float
    engine: str
    main: str
    pdf: bool = False
    pages: int = 0
    # The first page of the Supplementary Information, 1-based; None when the PDF has none.
    si_start: int | None = None
    problems: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    summary: str = ""
    output_tail: str = ""
    # LaTeX passes this compile needed; 0 when nothing had changed.
    passes: int = 0
    # True when the first pass came from a LaTeX that had already read the preamble.
    warm: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def builds_dir(project_id: str) -> Path:
    return app_home() / "builds" / project_id


def progress(project_id: str) -> str:
    """What the compile running now is doing, in words; empty when none is running."""
    return _progress.get(project_id, "")


# Build folders are named by time; "work" beside them is where LaTeX runs.
BUILD_ID = re.compile(r"^\d{8}-\d{6}-\d{3}$")


def build_folders(base: Path) -> list[Path]:
    return sorted((d for d in base.iterdir() if d.is_dir() and BUILD_ID.match(d.name)), key=lambda d: d.name)


def lock_for(project_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(project_id, threading.Lock())


def decode_label(label: str) -> str:
    """PyMuPDF returns some page labels as UTF-16 hex, e.g. "<FEFF0053>1" for "S1"."""
    return HEX_LABEL.sub(lambda m: bytes.fromhex(m.group(1)).decode("utf-16-be"), label)


def si_start(doc: pymupdf.Document, marker: str) -> int | None:
    """The 0-based index of the first page (after the first) whose text starts with the marker; failing that,
    the first page labelled S1, S2, ..."""
    if not marker:
        return None
    for i in range(1, doc.page_count):
        lines = [ln.strip() for ln in doc[i].get_text("text", sort=True).splitlines()]
        lines = [ln for ln in lines if ln and not ln.isdigit()]
        if lines and lines[0].startswith(marker):
            return i
    for i in range(1, doc.page_count):
        if decode_label(doc[i].get_label() or "").startswith("S"):
            return i
    return None


def label_start(doc: pymupdf.Document) -> int | None:
    """The first page after the first whose label starts with S, from the PDF's label rules: no page is read."""
    for rule in doc.get_page_labels():
        if rule.get("startpage", 0) > 0 and decode_label(rule.get("prefix") or "").startswith("S"):
            return rule["startpage"]
    return None


def first_line_starts(page: pymupdf.Page, marker: str) -> bool:
    lines = [ln.strip() for ln in page.get_text("text", sort=True).splitlines()]
    lines = [ln for ln in lines if ln and not ln.isdigit()]
    return bool(lines) and lines[0].startswith(marker)


def quick_si_start(doc: pymupdf.Document, marker: str, work: Path, sources: list[Path]) -> int | None:
    """si_start for a preview, which reads every page only when it must: reading them takes over a second on a
    long paper. While the marker's uses in the source stay the same, the Supplementary Information starts on the
    same page or near it, or there is none."""
    if not marker:
        return None
    uses = 0
    for path in sources:
        if path.suffix == ".tex" and path.is_file():
            uses += path.read_text(errors="replace").count(marker)
    record = work / SI_RECORD
    try:
        known = json.loads(record.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        known = None
    found: int | None = None
    settled = False
    if known and known.get("uses") == uses and known.get("marker") == marker:
        previous = known.get("start")
        if previous is None:
            found, settled = label_start(doc), True
        else:
            for i in sorted(range(1, doc.page_count), key=lambda i: abs(i - previous))[:9]:
                if first_line_starts(doc[i], marker):
                    found, settled = i, True
                    break
    if not settled:
        found = si_start(doc, marker)
    record.write_text(json.dumps({"uses": uses, "marker": marker, "start": found}))
    return found


def summarize(result: BuildResult) -> str:
    if not result.pdf:
        return "Failed: no PDF"
    c = result.counts
    parts = []
    if result.si_start:
        main = result.si_start - 1
        parts.append(f"{result.pages} pages ({main} + {result.pages - main} SI)")
    else:
        parts.append(f"{result.pages} page{'' if result.pages == 1 else 's'}")
    problems = []
    for key, one, many in (("error", "error", "errors"), ("undefined_reference", "undefined reference", "undefined references"),
                           ("undefined_citation", "undefined citation", "undefined citations"),
                           ("overfull", "overfull box", "overfull boxes")):
        n = c.get(key, 0)
        if n:
            problems.append(f"{n} {one if n == 1 else many}")
    parts.append(", ".join(problems) if problems else "no errors")
    prefix = "Compiled" if result.ok else "Failed"
    return f"{prefix} · " + " · ".join(parts)


TOOL_FAILURE = re.compile(r"^(biber|bibtex8?|makeindex)\b.*\b(?:failed|error|cannot|could not)\b", re.I)


def tool_failure(output: str) -> latexlog.Problem | None:
    """Why biber, bibtex or makeindex failed, from latexmk's output. latexmk's own summary says only that
    the tool failed ("Could not open biber log file"), so the tool's own words come first."""
    found = [line.strip() for line in output.splitlines() if TOOL_FAILURE.search(line.strip())]
    errors = [line.strip() for line in output.splitlines() if " ERROR - " in f" {line.strip()}"]
    if not found and not errors:
        return None
    tool = TOOL_FAILURE.search(found[0]).group(1) if found else "biber"
    own = errors or [line for line in found if re.match(rf"{tool}:", line)] or found
    cause = re.sub(rf"^{tool}:\s*|^.*?ERROR - ", "", own[0])
    return latexlog.Problem("error", f"{tool} failed: {cause}", detail="\n".join(dict.fromkeys(errors + found)))


def latexmk_errors(output: str) -> list[str]:
    """latexmk's collected error summary, without the one that draft passes always give."""
    errors, inside = [], False
    for line in output.splitlines():
        if line.startswith("Collected error summary"):
            inside = True
        elif inside and line.startswith("  "):
            if "failed to create output file" not in line and not line.startswith("      "):
                errors.append(line.strip())
        else:
            inside = False
    return errors


def pass_flags(engine: str, main: str, outdir: Path, preview: bool) -> list[str]:
    """The flags of one LaTeX pass that writes the PDF."""
    return [*(PREVIEW_ARGS.get(engine, []) if preview else []), "-interaction=nonstopmode", "-file-line-error",
            "-synctex=1", "-recorder", f"-output-directory={outdir}", f"-jobname={Path(main).stem}"]


def run_latexmk(cwd: Path, main: str, outdir: Path, engine: str, extra_lists: list[str] | tuple = (),
                env: dict[str, str] | None = None, preview: bool = False,
                shadow: Path | None = None, search: tuple[Path, ...] = (),
                cancel: threading.Event | None = None) -> tuple[int, str]:
    """latexmk into outdir.

    With pdfLaTeX and LuaLaTeX, latexmk's passes run in draft mode: they typeset and write the .aux files but
    neither read the images nor write a PDF, which on a paper with large figures takes a tenth of the time.
    One normal pass then writes the PDF and the SyncTeX file. (latexmk's XeLaTeX passes already skip the
    PDF.) latexmk does not run bibtex for multibib's extra lists (S.aux, ...), so that is done here,
    followed by more passes to bring their citations in. A preview's final pass compresses lightly and finds
    the fast figures in shadow."""
    run_env = tool_env(**LOG_ENV, **(env or {}))
    flags = ["-interaction=nonstopmode", "-file-line-error", "-synctex=1"]
    draft = engine in DRAFT_FLAG
    args = ["latexmk", ENGINE_FLAGS[engine], *flags, f"-outdir={outdir}", main]
    if draft:
        args.insert(2, f"-{engine}={engine} {DRAFT_FLAG[engine]} %O %S")
    code, output = run_tool(args, timeout=TIMEOUT, env=run_env, cwd=cwd, cancel=cancel)
    if code == CANCELLED:
        return code, output
    ok = code == 0 or (draft and code == 12 and not latexmk_errors(output))
    lists = [name for name in extra_lists if (outdir / f"{name}.aux").is_file()]
    if ok and lists:
        bib_env = dict(run_env, BIBINPUTS=f"{cwd}:{outdir}:{run_env.get('BIBINPUTS', '')}")
        for name in lists:
            bib_code, bib_out = run_tool(["bibtex", name], timeout=120, env=bib_env, cwd=outdir, cancel=cancel)
            output += f"\n--- bibtex {name} (exit {bib_code})\n{bib_out}"
        # latexmk sees the new .bbl files among the inputs and reruns LaTeX until references settle.
        code, more = run_tool(args, timeout=TIMEOUT, env=run_env, cwd=cwd, cancel=cancel)
        if code == CANCELLED:
            return code, more
        output += "\n" + more
        ok = code == 0 or (draft and code == 12 and not latexmk_errors(more))
    if not draft or code == -1:
        return code, output
    stem = Path(main).stem
    pdf, aux = outdir / f"{stem}.pdf", outdir / f"{stem}.aux"
    stale = not pdf.is_file() or (aux.is_file() and pdf.stat().st_mtime_ns < aux.stat().st_mtime_ns)
    if not (PASS.search(output) or stale):
        return (0 if ok else code), output
    source = main
    if preview and SIMPLE_NAME.match(main):
        source = f"{PREVIEW_SETUP[engine]}\\input{{{main}}}"
    final_env = dict(run_env, TEXINPUTS=figures.search_path(shadow, *search)) if preview and shadow else run_env
    final_code, final = run_tool([engine, *pass_flags(engine, main, outdir, preview), source], timeout=TIMEOUT,
                                 env=final_env, cwd=cwd, cancel=cancel)
    if final_code == CANCELLED:
        return final_code, final
    output += f"\n{FINAL_PASS} (exit {final_code})\n{final}"
    # The final pass speaks for LaTeX (an error in an early pass, from a stale .aux say, may be gone by then);
    # latexmk still speaks for biber and the other tools.
    others = [e for e in latexmk_errors(output) if not e.startswith(f"{engine}:")]
    return final_code or (12 if others else 0), output


def bibliography_files(built: Path) -> set[str]:
    """The .bib and .bst files a build's bibliography drew on: biblatex names them in its .bcf, bibtex in
    the .aux files' \\bibdata and \\bibstyle."""
    names: set[str] = set()
    for bcf in built.glob("*.bcf"):
        names.update(BCF_SOURCE.findall(as_it_was(bcf).read_text(errors="replace")))
    for aux in built.glob("*.aux"):
        text = as_it_was(aux).read_text(errors="replace")
        for group in BIBDATA.findall(text):
            names.update(name.strip() if name.strip().endswith(".bib") else f"{name.strip()}.bib"
                         for name in group.split(",") if name.strip())
        names.update(f"{style.strip()}.bst" for style in BIBSTYLE.findall(text) if style.strip())
    return {name for name in names if name}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tool_state(work: Path, latex_dir: Path, search: tuple[Path, ...] = ()) -> dict[str, str]:
    """What bibtex, biber and the index programs work from. When this changes, latexmk has to run them."""
    citations = hashlib.sha256()
    state: dict[str, str] = {}
    for path in sorted(work.rglob("*")):
        if path.suffix == ".aux" and path.is_file():
            for line in as_it_was(path).read_text(errors="replace").splitlines():
                if line.startswith(("\\citation{", "\\bibdata{", "\\bibstyle{")):
                    citations.update(line.encode())
        elif path.suffix in TOOL_INPUTS and path.is_file():
            state[path.name] = sha(as_it_was(path))
    state["citations"] = citations.hexdigest()
    for name in sorted(bibliography_files(work)):
        for folder in (latex_dir, work, *search):
            if (folder / name).is_file():
                state[name] = sha(folder / name)
                break
    return state


def generated_files(work: Path) -> dict[str, str]:
    """Hashes of what a LaTeX pass writes and a later pass may read back (.aux, .toc, .out, ...)."""
    state = {}
    for path in work.rglob("*"):
        if (path.suffix not in NOT_REREAD and not path.name.startswith("thor-") and path.is_file()
                and not path.name.endswith(KEPT_SUFFIX)):
            state[str(path)] = sha(as_it_was(path))
    return state


def needs_another_pass(before: dict[str, str], after: dict[str, str], fls: Path, latex_dir: Path) -> bool:
    """True when this pass read a file that it then changed, or wrote a new one (a first .toc, say)."""
    changed = {name for name, digest in after.items() if before.get(name) != digest}
    read = {str(path) for path in recorded_inputs(fls, latex_dir)}
    return any(name in read or name not in before for name in changed)


def input_stamps(fls: Path, latex_dir: Path, work: Path) -> dict[str, list[int]]:
    """Size and time of every file a pass read, apart from what LaTeX wrote itself."""
    stamps = {}
    for path in recorded_inputs(fls, latex_dir):
        if work not in path.parents:
            info = path.stat() if path.is_file() else None
            stamps[str(path)] = [info.st_size, info.st_mtime_ns] if info else []
    return stamps


def stamps_unchanged(record: Path) -> bool:
    try:
        known = json.loads(record.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    for name, stamp in known.items():
        path = Path(name)
        info = path.stat() if path.is_file() else None
        if ([info.st_size, info.st_mtime_ns] if info else []) != stamp:
            return False
    return bool(known)


def can_skip_latexmk(latex_dir: Path, main: str) -> bool:
    return bool(SIMPLE_NAME.match(main)) and not any((latex_dir / name).exists() for name in LATEXMK_RC)


def discard_spare(project_id: str) -> None:
    spare = _spares.pop(project_id, None)
    timer = _spare_timers.pop(project_id, None)
    if timer:
        timer.cancel()
    if spare:
        spare.discard()


def _retire_spare(project_id: str) -> None:
    """A spare has waited long enough. A compile running now has taken it already, or will soon start another."""
    lock = lock_for(project_id)
    if lock.acquire(blocking=False):
        try:
            discard_spare(project_id)
        finally:
            lock.release()


def start_spare(project_id: str, latex_dir: Path, cfg: ProjectConfig, work: Path, shadow: Path,
                search: tuple[Path, ...] = ()) -> None:
    """Starts the LaTeX the next compile will wake, unless this preamble cannot work that way."""
    discard_spare(project_id)
    try:
        data = (latex_dir / cfg.main).read_bytes()
    except OSError:
        return
    line = begin_line(data)
    if not line:
        return
    preamble = b"\n".join(data.split(b"\n")[:line - 1]) + b"\n"
    if _bad_preambles.get(project_id) == preamble:
        return
    for other in [p for p in _spares if p != project_id][:max(0, len(_spares) - MAX_SPARES + 1)]:
        _retire_spare(other)
    (work / PREAMBLE_NAME).write_bytes(preamble)
    wide = []
    if cfg.engine != "pdflatex":
        wide = sorted({ord(c) for c in preamble.decode("utf-8", errors="replace") if ord(c) > 255})
    env = tool_env(**LOG_ENV, TEXINPUTS=figures.search_path(shadow, *search, work), **search_env(search))
    args = [cfg.engine, *pass_flags(cfg.engine, cfg.main, work, preview=True),
            wake_command(cfg.main, line, PREVIEW_SETUP[cfg.engine], wide)]
    try:
        _spares[project_id] = Spare(latex_dir, cfg.main, work, args, env, preamble, line)
    except OSError:
        return
    timer = threading.Timer(SPARE_LIFETIME, _retire_spare, (project_id,))
    timer.daemon = True
    timer.start()
    _spare_timers[project_id] = timer


def search_env(search: tuple[Path, ...]) -> dict[str, str]:
    """Where bibtex and biber look in the extra folders."""
    if not search:
        return {}
    folders = ":".join(map(str, search)) + ":"
    return {"BSTINPUTS": folders, "BIBINPUTS": folders}


def one_pass(project_id: str, latex_dir: Path, cfg: ProjectConfig, work: Path, shadow: Path,
             search: tuple[Path, ...] = ()) -> tuple[int, str, bool]:
    """One LaTeX pass that writes the PDF, woken from the spare if it is good; (code, output, woken)."""
    spare = _spares.pop(project_id, None)
    timer = _spare_timers.pop(project_id, None)
    if timer:
        timer.cancel()
    data = (latex_dir / cfg.main).read_bytes()
    if spare:
        if spare.usable(data):
            code, output = spare.wake(TIMEOUT)
            if code == 0:
                return code, output, True
            # An error in the text, most likely. A fresh pass says for certain, and its report stands.
            cold_code, cold_output = _cold_pass(latex_dir, cfg, work, shadow, search)
            if cold_code == 0:
                _bad_preambles[project_id] = spare.preamble
            return cold_code, cold_output, False
        if spare.failed_in_preamble():
            _bad_preambles[project_id] = spare.preamble
        spare.discard()
    return (*_cold_pass(latex_dir, cfg, work, shadow, search), False)


def _cold_pass(latex_dir: Path, cfg: ProjectConfig, work: Path, shadow: Path,
               search: tuple[Path, ...] = ()) -> tuple[int, str]:
    env = tool_env(**LOG_ENV, TEXINPUTS=figures.search_path(shadow, *search), **search_env(search))
    source = f"{PREVIEW_SETUP[cfg.engine]}\\input{{{cfg.main}}}"
    return run_tool([cfg.engine, *pass_flags(cfg.engine, cfg.main, work, preview=True), source], timeout=TIMEOUT,
                    env=env, cwd=latex_dir)


def latex_passes(project_id: str, latex_dir: Path, cfg: ProjectConfig, work: Path, shadow: Path,
                 search: tuple[Path, ...] = ()) -> tuple[int, str, bool] | None:
    """LaTeX alone: one pass, and more only if it moved something a pass reads back. None when bibtex, biber
    or an index program has work to do, or nothing is kept yet, so latexmk has to run."""
    record = work / TOOLS_RECORD
    if not record.is_file() or not can_skip_latexmk(latex_dir, cfg.main):
        return None
    tools = json.loads(record.read_text())
    if tool_state(work, latex_dir, search) != tools:
        return None
    fls = work / f"{Path(cfg.main).stem}.fls"
    output, woken = "", False
    for number in range(1, MAX_PASSES + 1):
        _progress[project_id] = f"Compiling, pass {number}"
        before = generated_files(work)
        code, out, warm = one_pass(project_id, latex_dir, cfg, work, shadow, search)
        woken = woken or warm
        output += f"\n{LATEX_PASS} (exit {code})\n{out}"
        if code != 0:
            return code, output, woken
        figures.real_paths_in_fls(fls, shadow, latex_dir)
        if tool_state(work, latex_dir, search) != tools:
            return None
        if not needs_another_pass(before, generated_files(work), fls, latex_dir):
            return 0, output, woken
        if number < MAX_PASSES:
            start_spare(project_id, latex_dir, cfg, work, shadow, search)
    return 0, output, woken


def build(root: Path, cfg: ProjectConfig, project_id: str, clean: bool = False,
          search: tuple[Path, ...] = ()) -> BuildResult:
    """Compiles in the project's work folder, which keeps the files of earlier passes (.aux, .bbl, ...) the way
    Overleaf does. The results are copied into a new build folder, which is what the rest of Thor reads. search
    names more folders to find inputs in: the tracked source is compiled apart from the paper's own files."""
    with lock_for(project_id):
        latex_dir = inside(root, cfg.latex_dir)
        base = builds_dir(project_id)
        work = base / "work"
        shadow = base / "figures"
        setup = {"latex_dir": str(latex_dir), "main": cfg.main, "engine": cfg.engine}
        record = work / SETUP_RECORD
        if clean or not record.is_file() or json.loads(record.read_text()) != setup:
            discard_spare(project_id)
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        reused = record.is_file()
        record.write_text(json.dumps(setup))
        stem = Path(cfg.main).stem
        log = work / f"{stem}.log"
        work_pdf = work / f"{stem}.pdf"
        figures.drop_stale(latex_dir, shadow)

        # Nothing LaTeX read has changed since a good compile, so this one would give the same PDF.
        last = latest(project_id) if reused else None
        tools_record = work / TOOLS_RECORD
        if (last and last.ok and stamps_unchanged(work / INPUTS_RECORD) and tools_record.is_file()
                and tool_state(work, latex_dir, search) == json.loads(tools_record.read_text())):
            return replace(last, passes=0, warm=False, seconds=0.0, output_tail="Nothing has changed.")

        build_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        outdir = base / build_id
        outdir.mkdir(parents=True)
        before = work_pdf.stat().st_mtime_ns if work_pdf.is_file() else None

        def full_compile() -> tuple[int, str]:
            _progress[project_id] = "Compiling with latexmk (references, bibliography)"
            discard_spare(project_id)
            inputs = {"TEXINPUTS": ":".join(map(str, search)) + ":", **search_env(search)} if search else None
            done = run_latexmk(latex_dir, cfg.main, work, cfg.engine, cfg.bib_extra_lists, env=inputs, preview=True,
                               shadow=shadow, search=search)
            if done[0] == 0:
                tools_record.write_text(json.dumps(tool_state(work, latex_dir, search)))
            return done

        started = time.time()
        try:
            passes = latex_passes(project_id, latex_dir, cfg, work, shadow, search)
            code, output = full_compile() if passes is None else passes[:2]
            warm = bool(passes and passes[2])
            stale = (reused and code != 0 and log.is_file()
                     and latexlog.generated_errors(log.read_text(errors="replace")))
            if stale:
                # A file kept from an earlier compile is broken; start again without it.
                discard_spare(project_id)
                shutil.rmtree(work, ignore_errors=True)
                work.mkdir()
                record.write_text(json.dumps(setup))
                before = None
                started = time.time()
                code, more = full_compile()
                warm = False
                output = f"{more}\nCompiled again from scratch: {', '.join(stale)} from an earlier compile had errors."
        finally:
            _progress.pop(project_id, None)
        seconds = round(time.time() - started, 1)
        fls = work / f"{stem}.fls"
        figures.real_paths_in_fls(fls, shadow, latex_dir)
        # A run that wrote no PDF leaves the previous one in the work folder; it is not this build's.
        current = work_pdf.is_file() and ("are up-to-date" in output or work_pdf.stat().st_mtime_ns != before)
        for f in work.iterdir():
            if (f.is_file() and not f.name.startswith("thor-") and not f.name.endswith(KEPT_SUFFIX)
                    and (current or f.suffix != ".pdf")):
                clone_file(f, outdir / f.name)
        sources = recorded_inputs(fls, latex_dir)

        log = outdir / f"{stem}.log"
        problems = latexlog.parse(log.read_text(errors="replace"), str(latex_dir)) if log.is_file() else []
        if code == -1:
            problems.insert(0, latexlog.Problem("error", f"latexmk could not run: {output}"))
        pdf = outdir / f"{stem}.pdf"
        result = BuildResult(build_id, False, code, seconds, cfg.engine, cfg.main, warm=warm)
        result.passes = len(PASS.findall(output))
        if pdf.is_file():
            with pymupdf.open(pdf) as doc:
                result.pdf = True
                result.pages = doc.page_count
                latex_sources = [p for p in sources if latex_dir in p.parents]
                start = quick_si_start(doc, cfg.si_marker, work, latex_sources)
                result.si_start = start + 1 if start else None
        fonts = [m for p in problems if (m := FONT_NOT_LOADABLE.search(p.message))]
        missing_font = fonts[0] if cfg.engine == "xelatex" and fonts else None
        if missing_font:
            problems.insert(0, latexlog.Problem(
                "error", f"XeLaTeX cannot find the font “{missing_font.group(1)}”. On a Mac, XeLaTeX finds only fonts "
                "installed in macOS, while Overleaf's XeLaTeX also finds the fonts that come with TeX Live (TeX Gyre, "
                "Latin Modern, Libertinus and others). Choose LuaLaTeX in the engine menu, which finds them, or "
                "install the font in macOS."))
        if any(p.kind == "error" for p in problems):
            # latexmk stops at an error before bibtex and the reruns, so every reference and citation looks
            # undefined; only the errors (and overfull boxes) mean anything until they are fixed.
            problems = [p for p in problems if p.kind in ("error", "overfull")]
        elif code not in (0, -1) and (failure := tool_failure(output)):
            # Without the bibliography every citation is undefined; the tool's failure is the one problem.
            problems = [failure, *(p for p in problems if p.kind == "overfull")]
        elif code not in (0, -1):
            problems.insert(0, latexlog.Problem("error", f"latexmk stopped with exit code {code}.",
                                                detail="\n".join(output.splitlines()[-12:])))
        result.problems = [p.to_dict() for p in problems]
        result.counts = latexlog.counts(problems)
        result.ok = code == 0 and result.pdf and result.counts["error"] == 0
        result.summary = summarize(result)
        result.output_tail = "\n".join(output.splitlines()[-40:])
        (outdir / "thor-build.json").write_text(json.dumps(result.to_dict()))
        prune(base)
        if fls.is_file():
            stamps = input_stamps(fls, latex_dir, work)
            (work / INPUTS_RECORD).write_text(json.dumps(stamps))
            figures.refresh(latex_dir, shadow, fls)
        # After the results are copied: the spare starts by emptying the .log and .fls in the work folder.
        if can_skip_latexmk(latex_dir, cfg.main) and tools_record.is_file():
            start_spare(project_id, latex_dir, cfg, work, shadow, search)
        return result


def prune(base: Path) -> None:
    for old in build_folders(base)[:-KEEP_BUILDS]:
        shutil.rmtree(old, ignore_errors=True)


def latest(project_id: str) -> BuildResult | None:
    base = builds_dir(project_id)
    if not base.is_dir():
        return None
    for folder in reversed(build_folders(base)):
        record = folder / "thor-build.json"
        if record.is_file():
            return BuildResult(**json.loads(record.read_text()))
    return None


def output_file(project_id: str, build_id: str, name: str) -> Path | None:
    """A file from a build's output folder, refusing names that climb out of it."""
    if not BUILD_ID.match(build_id) or "/" in name or name.startswith("."):
        return None
    path = builds_dir(project_id) / build_id / name
    return path if path.is_file() else None


def word_count(root: Path, cfg: ProjectConfig) -> dict:
    """texcount over the whole document, with included files: words in text, headings and captions."""
    code, out = run_tool(["texcount", "-inc", "-utf8", "-brief", "-total", cfg.main], timeout=60,
                         cwd=inside(root, cfg.latex_dir))
    m = re.search(r"(\d+)\+(\d+)\+(\d+)", out)
    if code != 0 or not m:
        return {"ok": False, "detail": out.strip()[:300] or f"texcount exit code {code}"}
    text, headers, captions = (int(x) for x in m.groups())
    return {"ok": True, "text": text, "headers": headers, "captions": captions}
