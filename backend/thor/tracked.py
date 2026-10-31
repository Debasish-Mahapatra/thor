"""The tracked PDF: the changes from the baseline revision marked up by latexdiff, then compiled.

latexdiff alone gets several things wrong in a real paper; each step here fixes one:
1. \\input and \\include are inlined first, so latexdiff sees one file (and no \\input sits inside a longtable).
2. Figures: the include macro (and \\includegraphics) is expanded into explicit paths in a staged fig/ folder,
   named by content. Unchanged and merely renumbered figures get the same path in both sources and are not
   marked; replaced or removed ones are crossed out in red at half size, new ones framed in green.
3. Tables: latexdiff aligns a moved table against the wrong table and breaks the build, so each table and
   longtable (by label) becomes a placeholder in both sources and is put back in its new form afterwards.
   A changed caption gets its own small latexdiff; a new table is shown in green; a deleted one is dropped.
4. Additions dark green and underlined, deletions red and struck out.
5. Citations inside latexdiff's \\mbox wrappers get \\protect, which captions need.
6. A filecontents bibliography is put back clean from the new source.
7. Labels that only the baseline defines, still cited in deleted text, get their baseline numbers from the
   baseline's .aux, so deleted text does not show "??". References removed from the bibliography but cited
   in deleted text get their baseline numbers the same way, instead of "[?]".
8. Commands that only the baseline's preamble defines, which deleted text may still use, are carried over;
   citations of a multibib list the new source dropped (\\citeS) show their keys, struck out.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from . import fastdiff, latexlog
from .build import run_latexmk
from .paths import CANCELLED, clone_file, run_tool, tool_env

FIGURE_SUFFIXES = [".pdf", ".png", ".jpg", ".jpeg", ".eps"]
INPUT = re.compile(r"^(?P<pre>[^%\n]*?)\\(?P<command>input|include)\s*\{(?P<name>[^}]+)\}", re.M)
INCLUDEGRAPHICS = re.compile(r"\\includegraphics\s*(?:\[(?P<opts>[^\]]*)\])?\s*\{(?P<path>[^}]+)\}")
TABLE = re.compile(r"\\begin\{(?P<env>table\*?|longtable)\}.*?\\end\{(?P=env)\}", re.S)
LABEL = re.compile(r"\\label\s*\{([^}]+)\}")
FILECONTENTS = re.compile(r"\\begin\{filecontents\*?\}.*?\\end\{filecontents\*?\}", re.S)
REFERENCE = re.compile(r"\\(?:ref|eqref|pageref|autoref|cref|Cref)\s*\{([^}]+)\}")
NEWLABEL = re.compile(r"^\\newlabel\{(?P<name>[^}]+)\}.*$", re.M)
BIBCITE = re.compile(r"^\\bibcite\{(?P<name>[^}]+)\}.*$", re.M)
CITATION = re.compile(r"\\(?:cite[a-zA-Z]*|nocite)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]+)\}")
BIB_ENTRY = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")
BIBLIOGRAPHY = re.compile(r"\\bibliography\w*\s*\{([^}]+)\}")
MBOX_CITE = re.compile(r"\\mbox\{%DIFAUXCMD\n\\(cite[a-zA-Z]*)")
DEFINITION = re.compile(r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)\*?\s*\{?\\([A-Za-z@]+)\}?")
NEWCITES = re.compile(r"\\newcites\s*\{([^}]+)\}")
STAGED = re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{(fig/[^}]+)\}")

COLOURS = r"""%DIF Thor: additions dark green and underlined, deletions red and struck out, new figures framed in green
\definecolor{DIFGREEN}{rgb}{0,0.55,0}
"""
# latexdiff 1.4 marks text with \DIFadd/\DIFdel; older versions with \DIFaddtex/\DIFdeltex.
OVERRIDES = {
    r"\DIFadd": r"\renewcommand{\DIFadd}[1]{{\protect\color{DIFGREEN}\uline{#1}}}",
    r"\DIFdel": r"\renewcommand{\DIFdel}[1]{{\protect\color{red}\sout{#1}}}",
    r"\DIFaddtex": r"\renewcommand{\DIFaddtex}[1]{{\protect\color{DIFGREEN}\uline{#1}}}",
    r"\DIFdeltex": r"\renewcommand{\DIFdeltex}[1]{{\protect\color{red}\sout{#1}}}",
    r"\DIFaddincludegraphics": r"\renewcommand{\DIFaddincludegraphics}[2][]{{\color{DIFGREEN}"
                               r"\setlength{\fboxrule}{1.5pt}\fbox{\DIFOincludegraphics[#1]{#2}}}}",
}


class TrackedError(RuntimeError):
    pass


@dataclass
class Table:
    key: str
    label: str
    block: str


@dataclass
class TrackedResult:
    ok: bool
    seconds: float
    pages: int = 0
    exit_code: int = 0
    problems: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    tables: list[str] = field(default_factory=list)
    figures: dict[str, int] = field(default_factory=dict)
    additions: int = 0
    deletions: int = 0
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def strip_comment_lines(src: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", src)


def inline_inputs(src: str, base: Path, depth: int = 0) -> str:
    """Replaces \\input{x} and \\include{x} by the file's text when x is a file of the project."""
    if depth > 5:
        return src

    def replace(m: re.Match) -> str:
        name = m.group("name").strip()
        for candidate in (base / name, base / f"{name}.tex"):
            if candidate.is_file():
                text = inline_inputs(candidate.read_text(errors="replace"), base, depth + 1)
                # \\include starts and ends on a new page; keep its page breaks so the pages match the clean PDF.
                if m.group("command") == "include":
                    text = f"\\clearpage\n{text}\n\\clearpage\n"
                return m.group("pre") + text
        return m.group(0)

    return INPUT.sub(replace, src)


class FigureStage:
    """Copies every figure into one folder named by its content, so the same picture gets the same path."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.names: dict[str, str] = {}
        # The folder stays between updates, so a figure is read only when its size or time changes.
        self.known_file = folder / ".digests.json"
        try:
            self.known = json.loads(self.known_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.known = {}

    def digest(self, path: Path) -> str:
        info = path.stat()
        stamp = [info.st_size, info.st_mtime_ns]
        found = self.known.get(str(path))
        if found and found[0] == stamp:
            return found[1]
        digest = hashlib.md5(path.read_bytes()).hexdigest()
        self.known[str(path)] = [stamp, digest]
        return digest

    def stage(self, path: Path) -> str:
        digest = self.digest(path)
        name = self.names.get(digest)
        if not name:
            name = f"{digest[:12]}{path.suffix.lower()}"
            self.names[digest] = name
            target = self.folder / name
            if not target.exists():
                clone_file(path, target)
        return f"{self.folder.name}/{name}"

    def save(self) -> None:
        self.known_file.write_text(json.dumps(self.known))


def find_figure(base: Path, name: str, folders: list[str]) -> Path | None:
    """Where an include macro or \\includegraphics finds a figure: the name as given, then with each
    extension, in each figure folder and the LaTeX folder."""
    places = [*folders, "."]
    if Path(name).suffix.lower() in FIGURE_SUFFIXES:
        for folder in places:
            if (base / folder / name).is_file():
                return base / folder / name
    for suffix in FIGURE_SUFFIXES:
        for folder in places:
            candidate = base / folder / f"{name}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def expand_figures(src: str, base: Path, macro: str, folders: list[str], stage: FigureStage) -> tuple[str, int]:
    """Rewrites figure includes to \\includegraphics of staged files. Returns the source and the count."""
    count = 0

    def include(opts: str | None, staged: str) -> str:
        return f"\\includegraphics[{opts}]{{{staged}}}" if opts else f"\\includegraphics{{{staged}}}"

    def from_macro(m: re.Match) -> str:
        nonlocal count
        found = find_figure(base, m.group("name").strip(), folders)
        if not found:
            return m.group(0)
        count += 1
        return include(m.group("opts"), stage.stage(found))

    def from_includegraphics(m: re.Match) -> str:
        nonlocal count
        path = m.group("path").strip()
        if "#" in path or path.startswith(stage.folder.name + "/"):
            return m.group(0)  # inside a macro definition, or already staged
        found = find_figure(base, path, folders)
        if not found:
            return m.group(0)
        count += 1
        return include(m.group("opts"), stage.stage(found))

    if macro:
        call = re.compile(r"\\" + re.escape(macro) + r"\s*\{(?P<opts>[^{}]*)\}\s*\{(?P<name>[^{}]+)\}")
        src = call.sub(from_macro, src)
    return INCLUDEGRAPHICS.sub(from_includegraphics, src), count


def table_key(label: str) -> str:
    return "XXTABLEXX" + hashlib.sha1(label.encode()).hexdigest()[:8] + "XX"


def caption_span(block: str) -> tuple[int, int] | None:
    """The braces of a table's \\caption{...}: block[start:end] is "{...}"."""
    m = re.search(r"\\caption\s*(?:\[[^\]]*\])?\s*\{", block)
    if not m:
        return None
    start = m.end() - 1
    depth = 0
    for i in range(start, len(block)):
        if block[i] == "\\":
            continue
        if block[i] == "{" and block[i - 1] != "\\":
            depth += 1
        elif block[i] == "}" and block[i - 1] != "\\":
            depth -= 1
            if depth == 0:
                return start, i + 1
    return None


def strip_tables(src: str) -> tuple[str, dict[str, Table]]:
    tables: dict[str, Table] = {}
    seen: dict[str, int] = {}

    def replace(m: re.Match) -> str:
        block = m.group(0)
        found = LABEL.search(strip_comment_lines(block))
        if found:
            label = found.group(1)
        else:
            span = caption_span(block)
            label = "nolabel:" + hashlib.sha1((block[span[0]:span[1]] if span else block).encode()).hexdigest()[:8]
        # Tables with no label and the same caption are still different tables.
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            label = f"{label}:{seen[label]}"
        key = table_key(label)
        tables[key] = Table(key, label, block)
        return f"\n\n{key}\n\n"

    return TABLE.sub(replace, src), tables


def latexdiff(old: Path, new: Path, cwd: Path, extra: list[str]) -> str:
    code, out = run_tool(["latexdiff", "--type=UNDERLINE", *extra, str(old), str(new)], timeout=600, cwd=cwd,
                         env=tool_env())
    if code != 0 or "\\begin{document}" not in out:
        raise TrackedError(f"latexdiff failed (exit {code}): {out.strip().splitlines()[-1] if out.strip() else ''}")
    return out


def diff_snippet(old: str, new: str, workdir: Path) -> str:
    """latexdiff of two caption texts, as marked-up text without a preamble."""
    workdir.mkdir(parents=True, exist_ok=True)
    wrap = "\\documentclass{article}\n\\begin{document}\n%s\n\\end{document}\n"
    (workdir / "old.tex").write_text(wrap % old)
    (workdir / "new.tex").write_text(wrap % new)
    out = latexdiff(workdir / "old.tex", workdir / "new.tex", workdir, [])
    body = out[out.index("\\begin{document}") + len("\\begin{document}"):out.rindex("\\end{document}")]
    return body.strip()


def protect_citations(s: str) -> str:
    return MBOX_CITE.sub(r"\\mbox{%DIFAUXCMD\n\\protect\\\1", s)


def in_float(s: str) -> str:
    return s.replace("\\DIFadd{", "\\DIFaddFL{").replace("\\DIFdel{", "\\DIFdelFL{")


def put_tables_back(s: str, old: dict[str, Table], new: dict[str, Table], workdir: Path) -> tuple[str, list[str]]:
    report = []
    for key in old:
        if key not in new:
            s = re.sub(r"\\DIFdel(?:FL)?\{\s*" + key + r"\s*\}", "", s)
            s = s.replace(key, "")
            report.append(f"{old[key].label}: removed")
    for key, table in new.items():
        block = table.block
        span = caption_span(block)
        if key in old:
            old_span = caption_span(old[key].block)
            old_caption = old[key].block[old_span[0] + 1:old_span[1] - 1] if old_span else ""
            new_caption = block[span[0] + 1:span[1] - 1] if span else ""
            if span and old_caption != new_caption:
                marked = in_float(protect_citations(diff_snippet(old_caption, new_caption, workdir / "captions" / key)))
                block = block[:span[0]] + "{" + marked + "}" + block[span[1]:]
                report.append(f"{table.label}: caption changed")
            else:
                report.append(f"{table.label}: unchanged")
        else:
            if span:
                block = block[:span[0]] + "{\\DIFaddFL{" + block[span[0] + 1:span[1] - 1] + "}}" + block[span[1]:]
            if "\\centering" in block:
                block = block.replace("\\centering", "\\centering\\color{DIFGREEN}", 1)
            else:
                begin = re.match(r"\\begin\{[^}]*\}(?:\[[^\]]*\])?(?:\{[^}]*\})?", block)
                block = block[:begin.end()] + "\\color{DIFGREEN}" + block[begin.end():] if begin else block
            report.append(f"{table.label}: new")
        # A moved table leaves a deleted placeholder where it was and an added one where it is now.
        s = re.sub(r"\\DIFdel(?:FL)?\{\s*" + key + r"\s*\}", "", s)
        s = re.sub(r"\\DIFadd(?:FL)?\{\s*" + key + r"\s*\}", key, s)
        if s.count(key) != 1:
            raise TrackedError(f"Table {table.label} could not be put back ({s.count(key)} places found).")
        s = s.replace(key, block)
    return s, report


def restore_filecontents(s: str, new_src: str) -> str:
    marked, clean = FILECONTENTS.findall(s), FILECONTENTS.findall(new_src)
    if len(marked) != len(clean):
        return s
    for m, c in zip(marked, clean):
        s = s.replace(m, c, 1)
    return s


def baseline_labels(aux: Path | None, new_src: str, marked: str) -> list[str]:
    """\\newlabel lines from the baseline's .aux for labels the new source no longer defines but the marked
    text still references."""
    if not aux or not aux.is_file():
        return []
    defined = set(LABEL.findall(strip_comment_lines(new_src)))
    wanted = set(REFERENCE.findall(marked)) - defined
    wanted = {name.strip() for group in wanted for name in group.split(",")}
    return [m.group(0) for m in NEWLABEL.finditer(aux.read_text(errors="replace")) if m.group("name") in wanted]


def group_end(text: str, start: int, open_: str = "{", close: str = "}") -> int:
    """The index after the group that opens at text[start]."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "\\":
            continue
        if text[i] == open_ and (i == 0 or text[i - 1] != "\\"):
            depth += 1
        elif text[i] == close and text[i - 1] != "\\":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def definitions(preamble: str) -> dict[str, str]:
    """\\newcommand-style definitions in a preamble, by command name, with their full text."""
    found = {}
    text = strip_comment_lines(preamble)
    for m in DEFINITION.finditer(text):
        pos = m.end()
        while pos < len(text) and text[pos] in " \t\n[":
            pos = group_end(text, pos, "[", "]") if text[pos] == "[" else pos + 1
        if pos < len(text) and text[pos] == "{":
            found.setdefault(m.group(1), text[m.start():group_end(text, pos)])
    return found


def baseline_commands(old_src: str, new_src: str) -> list[str]:
    """Definitions deleted text may still need: commands only the baseline defines, and stand-ins for the
    citation commands of multibib lists the new source no longer has."""
    old_pre = old_src[:old_src.find("\\begin{document}")]
    new_pre = new_src[:new_src.find("\\begin{document}")]
    new_defs = definitions(new_pre)
    lines = [re.sub(r"^\\(?:newcommand|renewcommand|DeclareRobustCommand)", r"\\providecommand", text)
             for name, text in definitions(old_pre).items() if name not in new_defs]
    dropped = set(NEWCITES.findall(strip_comment_lines(old_pre))) - set(NEWCITES.findall(strip_comment_lines(new_pre)))
    for name in sorted(dropped):
        lines += [f"\\providecommand{{\\cite{name}}}[2][]{{[#2]}}", f"\\providecommand{{\\nocite{name}}}[1]{{}}",
                  f"\\providecommand{{\\bibliography{name}}}[1]{{}}",
                  f"\\providecommand{{\\bibliographystyle{name}}}[1]{{}}"]
    return lines


def bibliography_keys(src: str, base: Path) -> set[str]:
    """Keys of every entry the source's bibliography can draw on: filecontents blocks and its .bib files."""
    keys = set(BIB_ENTRY.findall("".join(FILECONTENTS.findall(src))))
    for group in BIBLIOGRAPHY.findall(strip_comment_lines(src)):
        for name in group.split(","):
            path = base / (name.strip() if name.strip().endswith(".bib") else f"{name.strip()}.bib")
            if path.is_file():
                keys.update(BIB_ENTRY.findall(path.read_text(errors="replace")))
    return keys


def baseline_citations(aux: Path | None, new_src: str, new_dir: Path, marked: str) -> list[str]:
    """\\bibcite lines from the baseline's .aux for references the new bibliography no longer has but the
    marked text still cites (in deleted text)."""
    if not aux or not aux.is_file():
        return []
    cited = {key.strip() for group in CITATION.findall(marked) for key in group.split(",")}
    wanted = cited - bibliography_keys(new_src, new_dir)
    return [m.group(0) for m in BIBCITE.finditer(aux.read_text(errors="replace")) if m.group("name") in wanted]


def preamble_additions(s: str, labels: list[str], commands: list[str] = (), citations: list[str] = ()) -> str:
    lines = [COLOURS] + [line for macro, line in OVERRIDES.items() if f"\\providecommand{{{macro}}}" in s]
    lines += list(commands)
    if labels or citations:
        lines.append("\\makeatletter")
        lines += labels
        # After the .aux is read, so natbib's own \\bibcite handles them.
        if citations:
            lines.append("\\AtBeginDocument{" + "\n".join(citations) + "}")
        lines.append("\\makeatother")
    return "\n".join(lines) + "\n"


_baseline_lock = threading.Lock()


def baseline_aux(old_dir: Path, old_main: str, engine: str, extra_lists: list[str], cache: Path) -> Path | None:
    """Compiles the baseline once (it is frozen) to learn its label numbers. A revision starts this as it opens,
    so the first update does not have to wait for it."""
    with _baseline_lock:
        digest = hashlib.sha256((old_dir / old_main).read_bytes()).hexdigest()
        stamp = cache / "source.sha256"
        aux = cache / f"{Path(old_main).stem}.aux"
        if stamp.is_file() and stamp.read_text() == digest and aux.is_file():
            return aux
        shutil.rmtree(cache, ignore_errors=True)
        cache.mkdir(parents=True)
        run_latexmk(old_dir, old_main, cache, engine, extra_lists)
        stamp.write_text(digest)
        return aux if aux.is_file() else None


DIFF_FLAGS = ["--graphics-markup=both", "--math-markup=coarse"]


def mark_up(old_p: str, new_p: str, out: Path, diff_cache: Path) -> str:
    """latexdiff's mark-up of the document: changed paragraphs only, or the whole if that does not work."""
    (out / "old_p.tex").write_text(old_p)
    (out / "new_p.tex").write_text(new_p)
    try:
        marked, _ = fastdiff.diff_sources(old_p, new_p, latexdiff, out / "chunks", DIFF_FLAGS, diff_cache)
        return marked
    except (TrackedError, ValueError, OSError):
        return latexdiff(out / "old_p.tex", out / "new_p.tex", out, DIFF_FLAGS)


def prepare_tracked(old_dir: Path, old_main: str, new_dir: Path, new_main: str, out: Path, *, macro: str,
                    folders: list[str], engine: str = "pdflatex", extra_lists: list[str] | tuple = (),
                    baseline_cache: Path | None = None, diff_cache: Path | None = None) -> TrackedResult:
    """Writes out/tracked.tex, with its figures in out/fig. Compiling it is a separate step, because the app shows
    an incremental compile of this file at once, and makes the revision's clean one later."""
    started = time.time()
    out.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(out / "captions", ignore_errors=True)

    old_src = inline_inputs((old_dir / old_main).read_text(errors="replace"), old_dir)
    new_src = inline_inputs((new_dir / new_main).read_text(errors="replace"), new_dir)
    stage = FigureStage(out / "fig")
    old_src, _ = expand_figures(old_src, old_dir, macro, folders, stage)
    new_src, _ = expand_figures(new_src, new_dir, macro, folders, stage)
    stage.save()
    old_figures = set(STAGED.findall(old_src))
    new_figures = set(STAGED.findall(new_src))
    figures = {"unchanged": len(old_figures & new_figures), "new or replaced": len(new_figures - old_figures),
               "removed or replaced": len(old_figures - new_figures)}

    old_p, old_tables = strip_tables(old_src)
    new_p, new_tables = strip_tables(new_src)
    (out / "old.tex").write_text(old_src)
    (out / "new.tex").write_text(new_src)
    s = mark_up(old_p, new_p, out, diff_cache or out / "diff_cache")
    s = restore_filecontents(s, new_src)
    s = protect_citations(s)
    s, tables = put_tables_back(s, old_tables, new_tables, out)

    aux = baseline_aux(old_dir, old_main, engine, list(extra_lists), baseline_cache or out / "baseline_build")
    labels = baseline_labels(aux, new_src, s)
    citations = baseline_citations(aux, new_src, new_dir, s)
    begin = s.index("\\begin{document}")
    s = s[:begin] + preamble_additions(s[:begin], labels, baseline_commands(old_src, new_src), citations) + s[begin:]
    target = out / "tracked.tex"
    # Unchanged text keeps its time, so a compile right after finds nothing to do.
    if not target.is_file() or target.read_text() != s:
        target.write_text(s)

    body = s[s.index("\\begin{document}"):]  # the preamble defines \\DIFaddFL with \\DIFadd{...}
    return TrackedResult(False, round(time.time() - started, 1), tables=tables, figures=figures,
                         additions=len(re.findall(r"\\DIFadd(?:FL)?\{", body)),
                         deletions=len(re.findall(r"\\DIFdel(?:FL)?\{", body)))


def summary_of(ok: bool, pages: int, result: TrackedResult, errors: int, refs: int) -> str:
    return (f"{'Tracked' if ok else 'Tracked build failed'} · {pages} pages · "
            f"{result.additions} additions, {result.deletions} deletions"
            + (f" · {errors} errors" if errors else "")
            + (f" · {refs} undefined references or citations" if refs else ""))


def compile_clean(result: TrackedResult, new_dir: Path, out: Path, engine: str = "pdflatex",
                  extra_lists: list[str] | tuple = (), cancel: threading.Event | None = None) -> TrackedResult:
    """Compiles out/tracked.tex from nothing to out/build/tracked.pdf, as a deliverable must be."""
    started = time.time()
    shutil.rmtree(out / "build", ignore_errors=True)
    # Figures are staged next to tracked.tex; anything else the source reads comes from the new source folder,
    # .bib files included (latexmk does not run biber or bibtex when it cannot find them).
    inputs = f"{new_dir}:"
    code, output = run_latexmk(out, "tracked.tex", out / "build", engine, extra_lists,
                               env={"TEXINPUTS": inputs, "BSTINPUTS": inputs, "BIBINPUTS": inputs}, cancel=cancel)
    result.exit_code = code
    if code == CANCELLED:
        return result
    log = out / "build" / "tracked.log"
    problems = latexlog.parse(log.read_text(errors="replace"), str(out)) if log.is_file() else []
    if any(p.kind == "error" for p in problems):
        problems = [p for p in problems if p.kind in ("error", "overfull")]
    pdf = out / "build" / "tracked.pdf"
    if pdf.is_file():
        with pymupdf.open(pdf) as doc:
            result.pages = doc.page_count
    result.exit_code = code
    result.problems = [p.to_dict() for p in problems]
    result.counts = latexlog.counts(problems)
    result.ok = code == 0 and pdf.is_file() and result.counts["error"] == 0
    result.seconds = round(result.seconds + time.time() - started, 1)
    refs = result.counts["undefined_reference"] + result.counts["undefined_citation"]
    result.summary = summary_of(result.ok, result.pages, result, result.counts["error"], refs)
    return result


def build_tracked(old_dir: Path, old_main: str, new_dir: Path, new_main: str, out: Path, *, macro: str,
                  folders: list[str], engine: str = "pdflatex", extra_lists: list[str] | tuple = (),
                  baseline_cache: Path | None = None, diff_cache: Path | None = None) -> TrackedResult:
    """Writes out/tracked.tex and compiles it from nothing to out/build/tracked.pdf."""
    prepared = prepare_tracked(old_dir, old_main, new_dir, new_main, out, macro=macro, folders=folders, engine=engine,
                               extra_lists=extra_lists, baseline_cache=baseline_cache, diff_cache=diff_cache)
    return compile_clean(prepared, new_dir, out, engine, extra_lists)
