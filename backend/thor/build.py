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

    def to_dict(self) -> dict:
        return asdict(self)


def builds_dir(project_id: str) -> Path:
    return app_home() / "builds" / project_id


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


def build(root: Path, cfg: ProjectConfig, project_id: str) -> BuildResult:
    with lock_for(project_id):
        latex_dir = inside(root, cfg.latex_dir)
        base = builds_dir(project_id)
        build_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        outdir = base / build_id
        outdir.mkdir(parents=True)
        args = ["latexmk", ENGINE_FLAGS[cfg.engine], "-interaction=nonstopmode", "-file-line-error",
                "-synctex=1", f"-outdir={outdir}", cfg.main]
        started = time.time()
        code, output = run_tool(args, timeout=TIMEOUT, env=tool_env(**LOG_ENV), cwd=latex_dir)
        seconds = round(time.time() - started, 1)

        stem = Path(cfg.main).stem
        log = outdir / f"{stem}.log"
        problems = latexlog.parse(log.read_text(errors="replace"), str(latex_dir)) if log.is_file() else []
        if code == -1:
            problems.insert(0, latexlog.Problem("error", f"latexmk could not run: {output}"))
        pdf = outdir / f"{stem}.pdf"
        result = BuildResult(build_id, False, code, seconds, cfg.engine, cfg.main)
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
    builds = sorted((d for d in base.iterdir() if d.is_dir()), key=lambda d: d.name)
    for old in builds[:-KEEP_BUILDS]:
        shutil.rmtree(old, ignore_errors=True)


def latest(project_id: str) -> BuildResult | None:
    base = builds_dir(project_id)
    if not base.is_dir():
        return None
    for folder in sorted((d for d in base.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True):
        record = folder / "thor-build.json"
        if record.is_file():
            return BuildResult(**json.loads(record.read_text()))
    return None


def output_file(project_id: str, build_id: str, name: str) -> Path | None:
    """A file from a build's output folder, refusing names that climb out of it."""
    if "/" in build_id or build_id.startswith(".") or "/" in name or name.startswith("."):
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
