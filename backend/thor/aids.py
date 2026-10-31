"""What the editor's completion, warnings and project search need from the project's files."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from . import bib, files
from .agent.staging import is_source
from .history import History
from .paths import APP_NAME
from .projects import ProjectConfig, ProjectError, inside

LABEL = re.compile(r"\\label\{([^}]+)\}")
SECTION = re.compile(r"\\(?:section|subsection|subsubsection|chapter)\*?\{([^}]*)\}")
MACRO = re.compile(r"\\(?:re)?newcommand\*?\{?\\(\w+)\}?(?:\[\d\])?\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}")
IMAGE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".eps"}
TEXT_SEARCHED = {".tex", ".bib", ".sty", ".cls", ".bst", ".txt", ".md", ".ltx"}
MAX_MATCHES = 2000
MAX_FILE_BYTES = 5_000_000


def source_files(latex_dir: Path, suffixes: set[str] | None = None) -> list[Path]:
    """The project's files, without hidden folders, revision folders or what LaTeX writes."""
    found = []
    for path in latex_dir.rglob("*"):
        rel = path.relative_to(latex_dir)
        if path.is_file() and is_source(rel) and (suffixes is None or path.suffix.lower() in suffixes):
            found.append(path)
    return sorted(found)


def completions(root: Path, cfg: ProjectConfig) -> dict:
    latex_dir = inside(root, cfg.latex_dir)
    tex_files = source_files(latex_dir, {".tex", ".ltx"})
    labels, macros = [], {}
    for path in tex_files:
        rel = path.relative_to(latex_dir).as_posix()
        heading = ""
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if line.lstrip().startswith("%"):
                continue
            found = SECTION.search(line)
            if found:
                heading = found.group(1)
            for key in LABEL.findall(line):
                labels.append({"key": key, "file": rel, "line": number, "near": heading})
        for name, body in MACRO.findall(path.read_text(errors="replace")):
            macros.setdefault(name, body)
    entries = bib.read_project(latex_dir, tex_files)
    everything = source_files(latex_dir)
    relative = lambda p: p.relative_to(latex_dir).as_posix()
    return {
        "labels": labels,
        "cites": [{"key": e.key, "title": e.title, "authors": e.authors, "year": e.year} for e in entries],
        "images": [relative(p) for p in everything if p.suffix.lower() in IMAGE_SUFFIXES],
        "tex": [relative(p) for p in tex_files],
        "bibs": [relative(p) for p in everything if p.suffix.lower() == ".bib"],
        "macros": macros,
    }


def _matcher(query: str, regex: bool, case: bool) -> re.Pattern:
    try:
        return re.compile(query if regex else re.escape(query), 0 if case else re.IGNORECASE)
    except re.error as e:
        raise ProjectError(f"That is not a valid regular expression: {e}") from e


def _searched(latex_dir: Path, glob: str) -> list[Path]:
    found = []
    for path in source_files(latex_dir, TEXT_SEARCHED):
        rel = path.relative_to(latex_dir).as_posix()
        if path.stat().st_size <= MAX_FILE_BYTES and (not glob or fnmatch.fnmatch(path.name, glob) or fnmatch.fnmatch(rel, glob)):
            found.append(path)
    return found


def search(root: Path, cfg: ProjectConfig, query: str, regex: bool = False, case: bool = False, glob: str = "") -> dict:
    """Every match, by file and line. Paths are relative to the project's root, like everywhere in the interface."""
    if not query:
        return {"files": [], "matches": 0, "truncated": False}
    rx = _matcher(query, regex, case)
    latex_dir = inside(root, cfg.latex_dir)
    out, total = [], 0
    for path in _searched(latex_dir, glob):
        hits = []
        for number, line in enumerate(path.read_text(errors="replace").split("\n"), 1):
            for found in rx.finditer(line):
                if found.start() == found.end():
                    continue
                hits.append({"line": number, "text": line[:400], "from": found.start(), "to": found.end()})
                total += 1
                if total >= MAX_MATCHES:
                    out.append({"path": path.relative_to(root).as_posix(), "hits": hits})
                    return {"files": out, "matches": total, "truncated": True}
        if hits:
            out.append({"path": path.relative_to(root).as_posix(), "hits": hits})
    return {"files": out, "matches": total, "truncated": False}


def replace(root: Path, cfg: ProjectConfig, project_id: str, history: History, query: str, replacement: str, regex: bool,
            case: bool, paths: list[str]) -> dict:
    """Replaces in the named files. Each save goes through the same check as the editor's: a file that changed on
    disk just now is refused, not overwritten. Every file's old text goes into the history."""
    if not query:
        raise ProjectError("There is nothing to search for.")
    rx = _matcher(query, regex, case)
    latex_dir = inside(root, cfg.latex_dir)
    changed, count = [], 0
    allowed = {p.relative_to(root).as_posix() for p in _searched(latex_dir, "")}
    for rel in paths:
        if rel not in allowed:
            raise ProjectError(f"{rel} is not one of the files {APP_NAME} searches.")
        opened = files.read_text(root, rel)
        try:
            new_text, made = rx.subn(replacement if regex else replacement.replace("\\", "\\\\"), opened["text"])
        except (re.error, IndexError) as e:
            raise ProjectError(f"The replacement cannot be used: {e}") from e
        if made and new_text != opened["text"]:
            history.record(project_id, rel, opened["text"].encode())
            files.write_text(root, rel, new_text, opened["hash"])
            history.record(project_id, rel, new_text.encode(), label="Replace across files")
            changed.append(rel)
            count += made
    return {"changed": changed, "replacements": count}
