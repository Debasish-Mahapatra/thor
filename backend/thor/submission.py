"""The zip a journal asks for: one .tex with the bibliography in it, figures beside it, and a test that it compiles.

It is made from what the last compile really read (its .fls), so no stray files go in. Inlining is done on a copy. The
test compile runs in an empty folder, with latexmk from scratch, and the zip counts only if it succeeds with the same
number of pages. If flattening the figures breaks it (a macro that computes paths, say), the folders are kept as they
were and the test runs again.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

import pymupdf

from . import build
from .build import run_latexmk
from .projects import ProjectConfig, inside
from .spare import as_it_was, recorded_inputs

INPUT = re.compile(r"^([^%\n]*?)\\(?:input|include)\{([^}]+)\}", re.M)
GRAPHICS_PATH = re.compile(r"^[^%\n]*\\graphicspath\{.*\}\s*$", re.M)
INCLUDE_GRAPHICS = re.compile(r"\\includegraphics(\*?)(\[[^\]]*\])?\{([^{}#]+)\}")
BIBLIOGRAPHY = re.compile(r"^([^%\n]*?)\\bibliography(\w*)\{[^}]*\}", re.M)
BIBSTYLE = re.compile(r"^[^%\n]*\\bibliographystyle\w*\{[^}]*\}[^\n]*\n?", re.M)
FILECONTENTS = re.compile(r"\\begin\{filecontents\*?\}(?:\[[^\]]*\])?\{[^}]+\.bib\}.*?\\end\{filecontents\*?\}\n?", re.S)
BIBLATEX = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{biblatex\}")
BIB_NAMES = re.compile(r"^[^%\n]*\\(?:addbibresource|bibliography\w*)(?:\[[^\]]*\])?\{([^}]+)\}", re.M)
LOCAL_SUFFIXES = {".cls", ".sty", ".bst", ".clo", ".cfg", ".def", ".fd"}
IMAGE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".eps"}
TEST_SECONDS = 600


class SubmissionError(Exception):
    """Told to the user as it is."""


def inline_inputs(path: Path, latex_dir: Path, seen: set[Path]) -> str:
    """The file's text with each \\input replaced by the text of that file."""
    seen.add(path)
    text = path.read_text(errors="replace")

    def replace(match: re.Match) -> str:
        name = match.group(2).strip()
        target = latex_dir / (name if Path(name).suffix else f"{name}.tex")
        if not target.is_file() or target in seen:
            return match.group(0)
        return f"{match.group(1)}{inline_inputs(target, latex_dir, seen)}"

    return INPUT.sub(replace, text)


def inline_bibliography(text: str, work: Path, stem: str) -> str:
    """Puts each list's .bbl where its \\bibliography was: journals want no .bib to process."""
    if BIBLATEX.search(text):
        return text

    def replace(match: re.Match) -> str:
        suffix = match.group(2)
        bbl = as_it_was(work / f"{suffix or stem}.bbl")
        if not bbl.is_file():
            return match.group(0)
        return f"{match.group(1)}{bbl.read_text(errors='replace')}"

    updated = BIBLIOGRAPHY.sub(replace, text)
    if updated != text:
        updated = BIBSTYLE.sub("", updated)
        updated = FILECONTENTS.sub("", updated)
    return updated


def bib_files_needed(text: str, latex_dir: Path) -> list[Path]:
    """The .bib files what is left of the text still asks for: biblatex's, whose .bbl cannot stand on its own, and any
    list that had no .bbl to put in. The .fls does not list them."""
    found = []
    for group in BIB_NAMES.findall(text):
        for name in group.split(","):
            name = name.strip()
            path = latex_dir / (name if name.endswith(".bib") else f"{name}.bib")
            if path.is_file():
                found.append(path)
    return list(dict.fromkeys(found))


def read_by_compile(work: Path, stem: str, latex_dir: Path) -> list[Path]:
    """The files the last compile read that belong to the paper, not to TeX."""
    fls = as_it_was(work / f"{stem}.fls")
    return [p for p in recorded_inputs(fls, latex_dir) if latex_dir in p.parents and p.is_file()]


def unique_names(paths: list[Path]) -> dict[Path, str]:
    """A flat name for each figure; figures of the same name in different folders get the folder in theirs."""
    names: dict[Path, str] = {}
    taken: set[str] = set()
    for path in paths:
        name = path.name
        if name in taken:
            name = f"{path.parent.name}-{path.name}"
        number = 2
        while name in taken:
            name = f"{path.stem}-{number}{path.suffix}"
            number += 1
        taken.add(name)
        names[path] = name
    return names


def flatten_figures(text: str, figures: list[Path], latex_dir: Path, cfg: ProjectConfig) -> tuple[str, dict[Path, str]]:
    names = unique_names(figures)
    by_stem: dict[str, str] = {}
    for path, name in names.items():
        by_stem[path.relative_to(latex_dir).with_suffix("").as_posix()] = name
        by_stem[path.relative_to(latex_dir).as_posix()] = name
        by_stem.setdefault(path.stem, name)
        by_stem.setdefault(path.name, name)

    def lookup(name: str) -> str | None:
        clean = name.strip().removeprefix("./")
        return by_stem.get(clean) or by_stem.get(Path(clean).name) or by_stem.get(Path(clean).stem)

    def replace(match: re.Match) -> str:
        found = lookup(match.group(3))
        return f"\\includegraphics{match.group(1)}{match.group(2) or ''}{{{found}}}" if found else match.group(0)

    updated = INCLUDE_GRAPHICS.sub(replace, text)
    if cfg.figure_macro:
        macro = re.compile(rf"\\{re.escape(cfg.figure_macro)}\{{([^{{}}]*)\}}\{{([^{{}}#]+)\}}")

        def replace_macro(match: re.Match) -> str:
            found = lookup(match.group(2))
            return f"\\includegraphics[{match.group(1)}]{{{found}}}" if found else match.group(0)

        updated = macro.sub(replace_macro, updated)
    return GRAPHICS_PATH.sub("", updated), names


def test_compile(folder: Path, main: str, cfg: ProjectConfig) -> tuple[bool, str, int]:
    """(worked, what went wrong, pages), from a compile of nothing but what is in the folder."""
    out = folder / ".test"
    out.mkdir()
    code, output = run_latexmk(folder, main, out, cfg.engine, cfg.bib_extra_lists)
    pdf = out / f"{Path(main).stem}.pdf"
    if code != 0 or not pdf.is_file():
        errors = [line for line in output.splitlines() if line.startswith("!") or ": error" in line.lower() or re.match(r"^\S+:\d+: ", line)]
        return False, errors[0] if errors else output[-300:], 0
    with pymupdf.open(pdf) as doc:
        return True, "", doc.page_count


def make(root: Path, cfg: ProjectConfig, project_id: str, target_folder: Path) -> dict:
    last = build.latest(project_id)
    if not last or not last.ok or not last.pdf:
        raise SubmissionError("Compile the paper without errors first: the zip is made from what that compile read.")
    latex_dir = inside(root, cfg.latex_dir)
    work = build.builds_dir(project_id) / "work"
    stem = Path(cfg.main).stem
    read = read_by_compile(work, stem, latex_dir)
    if not read:
        raise SubmissionError("The last compile's record of what it read is missing. Compile from scratch, then try again.")
    seen: set[Path] = set()
    text = inline_inputs(latex_dir / cfg.main, latex_dir, seen)
    text = inline_bibliography(text, work, stem)
    figures = [p for p in read if p.suffix.lower() in IMAGE_SUFFIXES]
    local = sorted({p for p in read if p.suffix.lower() in LOCAL_SUFFIXES})
    # What LaTeX reads beyond these has to be kept too, or a table of data read by a package would go missing.
    others = [p for p in read if p not in seen and p not in figures and p not in local and p.suffix.lower() not in {".tex", ".bib"}]
    others += bib_files_needed(text, latex_dir)
    flat_text, names = flatten_figures(text, figures, latex_dir, cfg)
    notes: list[str] = []
    with tempfile.TemporaryDirectory() as temp:
        flat = Path(temp) / "flat"
        flat.mkdir()
        (flat / cfg.main).write_text(flat_text)
        for path, name in names.items():
            shutil.copy2(path, flat / name)
        for path in [*local, *others]:
            shutil.copy2(path, flat / path.name)
        worked, problem, pages = test_compile(flat, cfg.main, cfg)
        folder = flat
        if not worked:
            kept = Path(temp) / "kept"
            kept.mkdir()
            (kept / cfg.main).write_text(text)
            for path in [*figures, *local, *others]:
                copy = kept / path.relative_to(latex_dir)
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, copy)
            worked, second, pages = test_compile(kept, cfg.main, cfg)
            if not worked:
                raise SubmissionError(f"The submission does not compile on its own, so no zip was made. LaTeX said: {second or problem}")
            folder = kept
            notes.append("Putting the figures in one folder broke the compile, so they stay in their folders.")
        if last.pages and pages != last.pages:
            notes.append(f"The test compile has {pages} pages and your last compile {last.pages}. Check it before sending it.")
        shutil.rmtree(folder / ".test", ignore_errors=True)
        target_folder.mkdir(parents=True, exist_ok=True)
        target = target_folder / f"submission_{time.strftime('%Y-%m-%d')}.zip"
        contents = []
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(folder).as_posix()
                    z.write(path, rel)
                    contents.append(rel)
    return {"path": target.relative_to(root).as_posix(), "files": contents, "pages": pages, "notes": notes,
            "size": target.stat().st_size}
