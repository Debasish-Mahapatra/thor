"""The revision's checks before it goes out: comments answered, the build, the style rules, the numbers and the metrics.

Each check says what it looked at and what it did not. A pass here means these things hold, not that the paper is right.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from . import build, numbers, plan, reply, style
from .aids import source_files
from .paths import run_tool
from .projects import ProjectConfig, inside

INPUT = re.compile(r"\\(?:input|include)\{([^}]+)\}")
CITE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}")
ABSTRACT = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.S)
COMMAND = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?")
MAX_DETAILS = 60
LIMITS = [("Words", "max_words"), ("Abstract words", "max_abstract_words"), ("Figures", "max_figures"),
          ("Tables", "max_tables"), ("References", "max_references"), ("Pages", "max_pages")]


def expand(path: Path, seen: set[Path] | None = None) -> str:
    """A file's text with its \\input files in place, and comments gone."""
    seen = seen if seen is not None else set()
    if path in seen or not path.is_file():
        return ""
    seen.add(path)
    text = re.sub(r"(?<!\\)%.*", "", path.read_text(errors="replace"))

    def inline(match: re.Match) -> str:
        name = match.group(1).strip()
        return expand(path.parent / (name if name.endswith(".tex") else f"{name}.tex"), seen)

    return INPUT.sub(inline, text)


def main_part(text: str, cfg: ProjectConfig) -> str:
    """The text before the Supplementary Information: what a journal counts."""
    if cfg.si_marker:
        at = text.find(cfg.si_marker, max(0, text.find("\\begin{document}")))
        if at >= 0:
            return text[:at]
    return text


def plain_words(text: str) -> int:
    return len(re.findall(r"[^\s{}$&~]+", COMMAND.sub(" ", text)))


def word_total(folder: Path, main: str) -> int | None:
    code, out = run_tool(["texcount", "-inc", "-utf8", "-brief", "-total", main], timeout=60, cwd=folder)
    found = re.search(r"(\d+)\+(\d+)\+(\d+)", out)
    return sum(int(x) for x in found.groups()) if code == 0 and found else None


def count_of(path: Path, cfg: ProjectConfig) -> dict[str, int | None]:
    text = main_part(expand(path), cfg)
    body = text[max(0, text.find("\\begin{document}")):]
    abstract = ABSTRACT.search(body)
    keys = {k.strip() for group in CITE.findall(body) for k in group.split(",") if k.strip()}
    return {"Words": word_total(path.parent, path.name) if path.is_file() else None,
            "Abstract words": plain_words(abstract.group(1)) if abstract else None,
            "Figures": len(re.findall(r"\\begin\{figure\*?\}", body)), "Tables": len(re.findall(r"\\begin\{table\*?\}", body)),
            "References": len(keys)}


def pages_of(pdf: Path | None) -> int | None:
    if not pdf or not pdf.is_file():
        return None
    with pymupdf.open(pdf) as doc:
        return doc.page_count


def metrics(root: Path, cfg: ProjectConfig, project_id: str, revision_folder: Path | None, baseline_pdf: Path | None) -> list[dict]:
    latex_dir = inside(root, cfg.latex_dir)
    now = count_of(latex_dir / cfg.main, cfg)
    last = build.latest(project_id)
    now["Pages"] = (last.si_start - 1 if last.si_start else last.pages) if last and last.pdf else None
    before: dict[str, int | None] = {}
    if revision_folder:
        old = revision_folder / "supporting_files" / "tracked_build" / "old" / cfg.main
        if old.is_file():
            before = count_of(old, cfg)
            before["Pages"] = pages_of(baseline_pdf)
    rows = []
    for name, field in LIMITS:
        limit = getattr(cfg, field)
        after = now.get(name)
        rows.append({"name": name, "before": before.get(name), "after": after, "limit": limit or None,
                     "over": bool(limit and after is not None and after > limit)})
    return rows


def _check(ident: str, title: str, status: str, summary: str, details: list[dict] | None = None) -> dict:
    details = details or []
    if len(details) > MAX_DETAILS:
        details = [*details[:MAX_DETAILS], {"text": f"And {len(details) - MAX_DETAILS} more."}]
    return {"id": ident, "title": title, "status": status, "summary": summary, "details": details}


def comments_check(revision_folder: Path | None) -> dict:
    title = "Every comment has an answer"
    if not revision_folder:
        return _check("comments", title, "info", "No revision is active, so there are no comments to check.")
    known = plan.all_comments(revision_folder)
    if not known:
        return _check("comments", title, "info", "No comments have been imported into this revision.")
    agreed = plan.load(revision_folder)
    answer = reply.load(revision_folder)
    details = []
    if agreed:
        details += [{"text": f"{r} is in no item of the plan."} for r in plan.uncovered(revision_folder, agreed)]
    if answer:
        details += [{"text": f"{r} has no reply."} for r in reply.unanswered(revision_folder, answer)]
    questions = [r for r, c in known.items() if c.get("question") and not c.get("answer")]
    details += [{"text": f"{r} is unclear and has no answer from you: {known[r]['question']}"} for r in questions]
    missing_parts = [word for word, have in (("plan", agreed), ("reply", answer)) if not have]
    if details:
        return _check("comments", title, "warn", f"{len(details)} thing{'s' if len(details) != 1 else ''} to look at, of {len(known)} comments.", details)
    if missing_parts:
        return _check("comments", title, "warn", f"{len(known)} comments, and no {' or '.join(missing_parts)} yet.")
    state = "approved" if agreed["status"] == "approved" else "not approved yet"
    return _check("comments", title, "ok" if agreed["status"] == "approved" else "warn",
                  f"All {len(known)} comments are in the plan ({state}) and have a reply.")


def build_check(project_id: str) -> dict:
    title = "The last compile"
    last = build.latest(project_id)
    if not last:
        return _check("build", title, "warn", "Nothing has been compiled yet. Compile first.")
    details = [{"text": p["message"], "file": p.get("file"), "line": p.get("line")} for p in last.problems
               if p.get("kind") in ("error", "undefined_reference", "undefined_citation", "missing_file")]
    bad = not last.ok or not last.pdf
    undefined = sum(v for k, v in last.counts.items() if "undefined" in k or "error" in k)
    return _check("build", title, "fail" if bad else "warn" if undefined else "ok", last.summary, details)


def style_check(root: Path, cfg: ProjectConfig) -> dict:
    title = "Your style rules"
    latex_dir = inside(root, cfg.latex_dir)
    results = inside(root, cfg.results_dir) if cfg.results_dir else None
    found_rules = style.rules(root, cfg)
    details = []
    for path in source_files(latex_dir, {".tex"}):
        if results and results in path.parents:
            continue
        rel = path.relative_to(root).as_posix()
        for hit in style.check(path.read_text(errors="replace"), found_rules):
            details.append({"text": f"{hit['message']} ({hit['text']})", "file": rel, "line": hit["line"]})
    extra = "" if cfg.style_rules else " No style file is set, so only the common checks ran."
    if details:
        return _check("style", title, "warn", f"{len(details)} place{'s' if len(details) != 1 else ''}.{extra}", details)
    return _check("style", title, "ok", f"Nothing found by {len(found_rules)} rules.{extra}")


def numbers_check(root: Path, cfg: ProjectConfig, revision_folder: Path | None) -> dict:
    title = "Numbers against the result files"
    found = numbers.report(root, cfg)
    missing = [n for n in found["numbers"] if not n["sources"]]
    details = []
    if revision_folder:
        old = revision_folder / "supporting_files" / "tracked_build" / "old" / cfg.main
        new = inside(root, cfg.latex_dir) / cfg.main
        if old.is_file() and new.is_file():
            for row in numbers.changed(old.read_text(errors="replace"), new.read_text(errors="replace")):
                details.append({"text": f"{row['value']} appears {row['before']} times in the baseline and {row['after']} now."})
    if not found["has_results"]:
        note = "No result files to compare with." + (" Set a results folder in the project's settings." if not cfg.results_dir else "")
        return _check("numbers", title, "info", f"{note} {len(found['numbers'])} numbers in the text.", details)
    details += [{"text": f"{n['shown']} is not in the result files: {n['context']}", "file": n["file"], "line": n["line"]}
                for n in missing]
    details += [{"text": f"Not searched: {s}"} for s in found["skipped"]]
    status = "warn" if missing or details else "ok"
    files = f"{found['searched']} result file{'s' if found['searched'] != 1 else ''}"
    return _check("numbers", title, status, f"{found['found']} of {found['found'] + found['missing']} numbers found in "
                                            f"{files}. Finding a value does not prove it is the right one.", details)


def run(root: Path, cfg: ProjectConfig, project_id: str, revision_folder: Path | None, baseline_pdf: Path | None) -> dict:
    return {
        "checks": [comments_check(revision_folder), build_check(project_id), style_check(root, cfg),
                   numbers_check(root, cfg, revision_folder)],
        "metrics": metrics(root, cfg, project_id, revision_folder, baseline_pdf),
    }
