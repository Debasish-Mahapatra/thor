"""Compiles a project with latexmk into a fresh output folder and reports what happened.

A fresh folder every time: stale aux files once made bibtex run on an empty main.aux and fail. Nothing is
written into the project; with -outdir, even the filecontents bibliography lands in the output folder.
A non-zero latexmk exit is a failure even when a PDF appears.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from . import latexlog
from .paths import app_home, run_tool, tool_env
from .projects import ProjectConfig, inside

ENGINE_FLAGS = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}
# Unwrapped logs. bibtex refuses widths much above 10000 ("3 is a bad bad").
LOG_ENV = {"max_print_line": "10000", "error_line": "254", "half_error_line": "238"}
KEEP_BUILDS = 3
TIMEOUT = 600
# The flag that makes an engine's passes skip the images and the PDF; see run_latexmk.
DRAFT_FLAG = {"pdflatex": "-draftmode", "lualatex": "--draftmode"}
FINAL_PASS = "--- Final pass, writing the PDF"
# Main files whose name can go into a TeX command line as it is.
SIMPLE_NAME = re.compile(r"^[\w./-]+\.tex$")
PASS = re.compile(rf"^(?:Run number \d+ of rule '(?:pdf|xe|lua)latex'|{FINAL_PASS})", re.M)
HEX_LABEL = re.compile(r"<FEFF((?:[0-9A-Fa-f]{4})+)>")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


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

    def to_dict(self) -> dict:
        return asdict(self)


def builds_dir(project_id: str) -> Path:
    return app_home() / "builds" / project_id


# Build folders are named by time; "work" beside them is where latexmk runs.
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


def summarize(result: BuildResult) -> str:
    if not result.pdf:
        return "Failed: no PDF"
    c = result.counts
    parts = []
    if result.si_start:
        main = result.si_start - 1
        parts.append(f"{result.pages} pages ({main} + {result.pages - main} SI)")
    else:
        parts.append(f"{result.pages} pages")
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


def run_latexmk(cwd: Path, main: str, outdir: Path, engine: str, extra_lists: list[str] | tuple = (),
                env: dict[str, str] | None = None, preview: bool = False) -> tuple[int, str]:
    """latexmk into outdir.

    With pdfLaTeX and LuaLaTeX, latexmk's passes run in draft mode: they typeset and write the .aux files but
    neither read the images nor write a PDF, which on a paper with large figures takes a tenth of the time.
    One normal pass then writes the PDF and the SyncTeX file. (latexmk's XeLaTeX passes already skip the
    PDF.) latexmk does not run bibtex for multibib's extra lists (S.aux, ...), so that is done here,
    followed by more passes to bring their citations in.

    A preview's final pdfLaTeX pass compresses at zlib level 1 instead of 9: the same pixels and text, in
    a third of the time and a PDF about twice the size. Deliverables keep full compression."""
    run_env = tool_env(**LOG_ENV, **(env or {}))
    flags = ["-interaction=nonstopmode", "-file-line-error", "-synctex=1"]
    draft = engine in DRAFT_FLAG
    args = ["latexmk", ENGINE_FLAGS[engine], *flags, f"-outdir={outdir}", main]
    if draft:
        args.insert(2, f"-{engine}={engine} {DRAFT_FLAG[engine]} %O %S")
    code, output = run_tool(args, timeout=TIMEOUT, env=run_env, cwd=cwd)
    ok = code == 0 or (draft and code == 12 and not latexmk_errors(output))
    lists = [name for name in extra_lists if (outdir / f"{name}.aux").is_file()]
    if ok and lists:
        bib_env = dict(run_env, BIBINPUTS=f"{cwd}:{outdir}:{run_env.get('BIBINPUTS', '')}")
        for name in lists:
            bib_code, bib_out = run_tool(["bibtex", name], timeout=120, env=bib_env, cwd=outdir)
            output += f"\n--- bibtex {name} (exit {bib_code})\n{bib_out}"
        # latexmk sees the new .bbl files among the inputs and reruns LaTeX until references settle.
        code, more = run_tool(args, timeout=TIMEOUT, env=run_env, cwd=cwd)
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
    if preview and engine == "pdflatex" and SIMPLE_NAME.match(main):
        source = f"\\pdfcompresslevel=1\\input{{{main}}}"
    final_code, final = run_tool([engine, *flags, "-recorder", f"-output-directory={outdir}", f"-jobname={stem}",
                                  source], timeout=TIMEOUT, env=run_env, cwd=cwd)
    output += f"\n{FINAL_PASS} (exit {final_code})\n{final}"
    # The final pass speaks for LaTeX (an error in an early pass, from a stale .aux say, may be gone by then);
    # latexmk still speaks for biber and the other tools.
    others = [e for e in latexmk_errors(output) if not e.startswith(f"{engine}:")]
    return final_code or (12 if others else 0), output


def build(root: Path, cfg: ProjectConfig, project_id: str, clean: bool = False) -> BuildResult:
    """Compiles in the project's work folder, which keeps the files of earlier passes (.aux, .bbl, ...) the way
    Overleaf does, so a compile usually needs one LaTeX pass instead of three. The results are copied into a
    new build folder, which is what the rest of Thor reads."""
    with lock_for(project_id):
        latex_dir = inside(root, cfg.latex_dir)
        base = builds_dir(project_id)
        build_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        outdir = base / build_id
        outdir.mkdir(parents=True)
        work = base / "work"
        setup = {"latex_dir": str(latex_dir), "main": cfg.main, "engine": cfg.engine}
        record = work / "thor-work.json"
        if clean or not record.is_file() or json.loads(record.read_text()) != setup:
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(exist_ok=True)
        reused = record.is_file()
        record.write_text(json.dumps(setup))
        stem = Path(cfg.main).stem
        log = work / f"{stem}.log"
        work_pdf = work / f"{stem}.pdf"
        before = work_pdf.stat().st_mtime_ns if work_pdf.is_file() else None

        started = time.time()
        code, output = run_latexmk(latex_dir, cfg.main, work, cfg.engine, cfg.bib_extra_lists, preview=True)
        stale = reused and code != 0 and log.is_file() and latexlog.generated_errors(log.read_text(errors="replace"))
        if stale:
            # A file kept from an earlier compile is broken; start again without it.
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir()
            record.write_text(json.dumps(setup))
            before = None
            started = time.time()
            code, more = run_latexmk(latex_dir, cfg.main, work, cfg.engine, cfg.bib_extra_lists, preview=True)
            output = f"{more}\nCompiled again from scratch: {', '.join(stale)} from an earlier compile had errors."
        seconds = round(time.time() - started, 1)
        # A run that wrote no PDF leaves the previous one in the work folder; it is not this build's.
        current = work_pdf.is_file() and ("are up-to-date" in output or work_pdf.stat().st_mtime_ns != before)
        for f in work.iterdir():
            if f.is_file() and f != record and (current or f.suffix != ".pdf"):
                shutil.copy2(f, outdir / f.name)

        log = outdir / f"{stem}.log"
        problems = latexlog.parse(log.read_text(errors="replace"), str(latex_dir)) if log.is_file() else []
        if code == -1:
            problems.insert(0, latexlog.Problem("error", f"latexmk could not run: {output}"))
        pdf = outdir / f"{stem}.pdf"
        result = BuildResult(build_id, False, code, seconds, cfg.engine, cfg.main)
        result.passes = len(PASS.findall(output))
        if pdf.is_file():
            with pymupdf.open(pdf) as doc:
                result.pdf = True
                result.pages = doc.page_count
                start = si_start(doc, cfg.si_marker)
                result.si_start = start + 1 if start else None
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
