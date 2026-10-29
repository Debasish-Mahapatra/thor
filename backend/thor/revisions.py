"""Revision folders: starting one, keeping the active one in step with the working source, and its change log.

Layout of revision N (revisions_dir/revisionN/):
    main_draft.pdf, supplementary_draft.pdf   the clean build, split at the Supplementary Information
    revision_clean.pdf, revision_tracked.pdf  the clean build, and the changes since revision N-1
    supporting_files/
        main.tex, figures/, ...                a snapshot of the source that compiles on its own
        baseline_revision{N-1}.tex             the frozen source the revision started from
        tracked_build/old/                     that source with its figures; tracked_build/ holds the diff
        logs/compile_clean.log, logs/compile_tracked.log
        change_log.txt

Only the active revision is ever written. Earlier ones are frozen. A revision becomes active when the user
starts it in Thor, or explicitly continues the latest one.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from . import tracked
from .build import BuildResult, builds_dir, run_latexmk, si_start
from .paths import app_home
from .projects import CONFIG_NAME, DELIVERABLES, ProjectConfig, ProjectError, REVISION_DIR, inside, revision_dirs
from .store import Store

SECTION = re.compile(r"^[^%\n]*\\(section|subsection|subsubsection)\*?\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", re.M)
BCF_SOURCE = re.compile(r"<bcf:datasource[^>]*>([^<]+)</bcf:datasource>")
BIBDATA = re.compile(r"\\bibdata\{([^}]+)\}")
BIBSTYLE = re.compile(r"\\bibstyle\{([^}]+)\}")
# What a frozen source leaves out: Thor's own folders and files, and what LaTeX writes.
NOT_SOURCE_DIRS = {"tracked_build", "logs", "comments", "__pycache__", "node_modules"}
NOT_SOURCE_FILES = {"change_log.txt", CONFIG_NAME, *DELIVERABLES}
BASELINE_FILE = re.compile(r"^baseline_(?:start|revision\d+)\.tex$")
BUILD_OUTPUT = (".aux", ".log", ".out", ".fls", ".fdb_latexmk", ".blg", ".toc", ".lof", ".lot", ".bcf", ".run.xml",
                ".synctex.gz", ".nav", ".snm", ".vrb")
MAX_SOURCE_FILE = 200_000_000
SNAPSHOT_SKIP = {".aux", ".log", ".out", ".synctex.gz", ".fls", ".fdb_latexmk", ".bbl", ".blg", ".toc"}


class RevisionError(ProjectError):
    pass


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class SyncStatus:
    revision: int | None = None
    state: str = "idle"  # idle, running, done, failed
    started: float | None = None
    finished: float | None = None
    steps: list[Step] = field(default_factory=list)
    tracked: dict | None = None
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def folder_of(root: Path, cfg: ProjectConfig, number: int) -> Path:
    return inside(root, cfg.revisions_dir) / f"revision{number}"


def numbers(root: Path, cfg: ProjectConfig) -> list[int]:
    found = []
    for folder in revision_dirs(inside(root, cfg.revisions_dir)):
        found.append(int(REVISION_DIR.match(folder.name).group(1)))
    return sorted(found)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_if_changed(src: Path, dst: Path) -> bool:
    if dst.is_file() and dst.stat().st_size == src.stat().st_size and sha(dst) == sha(src):
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def mirror(src: Path, dst: Path) -> list[str]:
    """Makes dst a copy of src (files only), returning what was added, changed or removed."""
    changes = []
    seen = set()
    for path in sorted(p for p in src.rglob("*") if p.is_file() and not p.name.startswith(".")):
        rel = path.relative_to(src)
        seen.add(rel)
        existed = (dst / rel).exists()
        if copy_if_changed(path, dst / rel):
            changes.append(f"{'changed' if existed else 'added'} {rel}")
    if dst.is_dir():
        for path in sorted(p for p in dst.rglob("*") if p.is_file()):
            if path.relative_to(dst) not in seen:
                path.unlink()
                changes.append(f"removed {path.relative_to(dst)}")
    return changes


def sections(text: str) -> list[tuple[int, str]]:
    """(line, title) of each section heading, for naming where a change is."""
    found = []
    for m in SECTION.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        title = re.sub(r"\\[a-zA-Z]+|[{}$]", "", m.group(2)).strip()
        found.append((line, title))
    return found


def bibliography_lines(text: str) -> tuple[int, int] | None:
    m = re.search(r"\\begin\{filecontents\*?\}.*?\\end\{filecontents\*?\}", text, re.S)
    if not m:
        return None
    return text.count("\n", 0, m.start()) + 1, text.count("\n", 0, m.end()) + 1


def describe_changes(old: str, new: str) -> str:
    """Which sections of the new text changed, with their line ranges."""
    old_lines, new_lines = old.splitlines(), new.splitlines()
    heads = sections(new)
    bib = bibliography_lines(new)
    places: dict[str, list[tuple[int, int]]] = {}
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        start, end = j1 + 1, max(j1 + 1, j2)
        if bib and bib[0] <= start <= bib[1]:
            title = "Bibliography"
        else:
            title = next((t for line, t in reversed(heads) if line <= start), "Preamble")
        places.setdefault(title, []).append((start, end))
    if not places:
        return "no text changes"
    parts = []
    for title, spans in places.items():
        lines = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in spans[:4]) + (", ..." if len(spans) > 4 else "")
        parts.append(f"{title} (lines {lines})")
    return "; ".join(parts)


def bibliography_files(built: Path) -> set[str]:
    """The .bib and .bst files a build's bibliography drew on: biblatex names them in its .bcf, bibtex in
    the .aux files' \\bibdata and \\bibstyle."""
    names: set[str] = set()
    for bcf in built.glob("*.bcf"):
        names.update(BCF_SOURCE.findall(bcf.read_text(errors="replace")))
    for aux in built.glob("*.aux"):
        text = aux.read_text(errors="replace")
        for group in BIBDATA.findall(text):
            names.update(name.strip() if name.strip().endswith(".bib") else f"{name.strip()}.bib"
                         for name in group.split(",") if name.strip())
        names.update(f"{style.strip()}.bst" for style in BIBSTYLE.findall(text) if style.strip())
    return {name for name in names if name}


def copy_source(src: Path, dst: Path, skip: tuple[str, ...] = ()) -> None:
    """Copies a LaTeX folder's source, with \\input files, bibliography, class and style files and figures:
    everything but hidden files, revision folders, Thor's own files and what LaTeX writes. skip names
    folders (relative to src) to leave out, such as results."""
    for folder, dirs, files in os.walk(src):
        rel = Path(folder).relative_to(src)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not REVISION_DIR.match(d)
                         and d not in NOT_SOURCE_DIRS and (rel / d).as_posix() not in skip)
        for name in files:
            path = Path(folder) / name
            if (name.startswith(".") or name in NOT_SOURCE_FILES or BASELINE_FILE.match(name)
                    or name.endswith(BUILD_OUTPUT) or path.stat().st_size > MAX_SOURCE_FILE):
                continue
            (dst / rel).mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dst / rel / name)


def file_state(base: Path, folders: list[str]) -> dict[str, str]:
    """Hash of every file in the figure folders, by path."""
    state = {}
    for name in folders:
        if (base / name).is_dir():
            for path in (base / name).rglob("*"):
                if path.is_file() and not path.name.startswith("."):
                    state[str(path.relative_to(base))] = sha(path)
    return state


def file_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Added, changed, removed and renamed (same content, new name) files."""
    removed = sorted(before.keys() - after.keys())
    added = sorted(after.keys() - before.keys())
    changes = []
    for old in list(removed):
        twin = next((new for new in added if after[new] == before[old]), None)
        if twin:
            changes.append(f"renamed {old} to {twin}")
            removed.remove(old)
            added.remove(twin)
    changes += [f"changed {k}" for k in sorted(before.keys() & after.keys()) if before[k] != after[k]]
    changes += [f"added {k}" for k in added] + [f"removed {k}" for k in removed]
    return changes


def split_pdf(pdf: Path, si_start: int | None, main_out: Path, si_out: Path) -> tuple[int, int]:
    """Writes the main text and the Supplementary Information as separate PDFs; returns their page counts."""
    with pymupdf.open(pdf) as doc:
        end = (si_start - 1) if si_start else doc.page_count
        with pymupdf.open() as main:
            main.insert_pdf(doc, from_page=0, to_page=end - 1)
            main.save(main_out, garbage=3, deflate=True)
        si_pages = 0
        if si_start:
            with pymupdf.open() as si:
                si.insert_pdf(doc, from_page=end, to_page=doc.page_count - 1)
                si.save(si_out, garbage=3, deflate=True)
                si_pages = si.page_count
        elif si_out.exists():
            si_out.unlink()
        return end, si_pages


class Revisions:
    """Revision state per project, and the background worker that keeps the active revision in step."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._status: dict[str, SyncStatus] = {}
        self._pending: dict[str, tuple] = {}
        self._workers: dict[str, threading.Thread] = {}

    # State

    def overview(self, root: Path, cfg: ProjectConfig, project_id: str) -> dict:
        found = numbers(root, cfg)
        active = self.store.active_revision(project_id)
        if active is not None and active not in found:
            active = None
        return {
            "revisions": found,
            "latest": found[-1] if found else None,
            "active": active,
            "baseline": self.baseline_name(root, cfg, active) if active else None,
            "sync": self.status(project_id).to_dict(),
        }

    def baseline_pdf(self, root: Path, cfg: ProjectConfig, number: int) -> Path | None:
        """The final build of the revision a revision started from: revision N-1's clean PDF, or the
        baseline build the tracked PDF made."""
        previous = folder_of(root, cfg, number - 1) / "revision_clean.pdf"
        if previous.is_file():
            return previous
        built = folder_of(root, cfg, number) / "supporting_files" / "tracked_build" / "baseline_build"
        found = sorted(built.glob("*.pdf")) if built.is_dir() else []
        return found[0] if found else None

    def baseline_name(self, root: Path, cfg: ProjectConfig, number: int) -> str | None:
        sf = folder_of(root, cfg, number) / "supporting_files"
        old = sf / "tracked_build" / "old" / cfg.main
        return str(old.relative_to(root)) if old.is_file() else None

    def status(self, project_id: str) -> SyncStatus:
        with self._lock:
            return self._status.get(project_id) or self._load_status(project_id)

    def _status_file(self, project_id: str) -> Path:
        return app_home() / "revisions" / f"{project_id}.json"

    def _load_status(self, project_id: str) -> SyncStatus:
        try:
            data = json.loads(self._status_file(project_id).read_text())
            data["steps"] = [Step(**s) for s in data.get("steps", [])]
            return SyncStatus(**data)
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return SyncStatus()

    def _save_status(self, project_id: str, status: SyncStatus) -> None:
        path = self._status_file(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(status.to_dict()))

    # Starting and continuing

    def start(self, root: Path, cfg: ProjectConfig, project_id: str) -> int:
        """Creates revision N+1, frozen from revision N's final source (or from the working source for the
        first revision)."""
        found = numbers(root, cfg)
        number = (found[-1] + 1) if found else 1
        folder = folder_of(root, cfg, number)
        sf = folder / "supporting_files"
        old = sf / "tracked_build" / "old"
        latex_dir = inside(root, cfg.latex_dir)
        if found:
            prev_sf = folder_of(root, cfg, found[-1]) / "supporting_files"
            prev_main = prev_sf / cfg.main
            if not prev_main.is_file():
                raise RevisionError(
                    f"revision{found[-1]}/supporting_files/{cfg.main} does not exist, so there is no final source "
                    f"of revision {found[-1]} to start from.")
            baseline_dir, baseline_name = prev_sf, f"baseline_revision{found[-1]}.tex"
        else:
            baseline_dir, baseline_name = latex_dir, "baseline_start.tex"
        folder.mkdir(parents=True, exist_ok=False)
        old.mkdir(parents=True)
        shutil.copy2(baseline_dir / cfg.main, sf / baseline_name)
        # The whole source, not only the main file: \\input files and the bibliography belong to the baseline.
        copy_source(baseline_dir, old, skip=self._outside_source(root, cfg, baseline_dir))
        self.store.set_active_revision(project_id, number)
        source = f"revision {found[-1]}'s final source" if found else f"{cfg.latex_dir}/{cfg.main}"
        self._log(sf, f"Revision {number} started from {source} ({baseline_name}).", header=True)
        return number

    @staticmethod
    def _outside_source(root: Path, cfg: ProjectConfig, base: Path) -> tuple[str, ...]:
        """The results and scripts folders, when they sit inside the folder being copied."""
        found = []
        for place in (cfg.results_dir, cfg.scripts_dir):
            if place:
                try:
                    found.append(inside(root, place).relative_to(base).as_posix())
                except (ProjectError, ValueError):
                    continue
        return tuple(found)

    def continue_latest(self, root: Path, cfg: ProjectConfig, project_id: str) -> int:
        """Makes the latest existing revision active again, so its folder follows the working source."""
        found = numbers(root, cfg)
        if not found:
            raise RevisionError("There is no revision to continue; start one instead.")
        number = found[-1]
        sf = folder_of(root, cfg, number) / "supporting_files"
        old = sf / "tracked_build" / "old"
        if not (old / cfg.main).is_file():
            baseline = sf / f"baseline_revision{number - 1}.tex"
            prev_sf = folder_of(root, cfg, number - 1) / "supporting_files"
            if not baseline.is_file() and (prev_sf / cfg.main).is_file():
                baseline = prev_sf / cfg.main
            if not baseline.is_file():
                raise RevisionError(f"Revision {number} has no baseline source (supporting_files/"
                                    f"baseline_revision{number - 1}.tex), so its tracked PDF cannot be built.")
            old.mkdir(parents=True, exist_ok=True)
            if prev_sf.is_dir():
                copy_source(prev_sf, old)
            shutil.copy2(baseline, old / cfg.main)
        self.store.set_active_revision(project_id, number)
        self._log(sf, f"Thor continues revision {number}: its PDFs now follow {cfg.latex_dir}/{cfg.main}.")
        return number

    # Keeping the active revision in step

    def enqueue(self, root: Path, cfg: ProjectConfig, project_id: str, result: BuildResult) -> bool:
        """Queues a sync after a successful build; only the latest queued build is used."""
        active = self.store.active_revision(project_id)
        if active is None or not result.ok:
            return False
        with self._lock:
            self._pending[project_id] = (root, cfg, result, active)
            worker = self._workers.get(project_id)
            if worker is None or not worker.is_alive():
                worker = threading.Thread(target=self._work, args=(project_id,), daemon=True)
                self._workers[project_id] = worker
                worker.start()
        return True

    def wait(self, project_id: str, timeout: float | None = None) -> None:
        worker = self._workers.get(project_id)
        if worker:
            worker.join(timeout)

    def _work(self, project_id: str) -> None:
        while True:
            with self._lock:
                job = self._pending.pop(project_id, None)
                if job is None:
                    return
                self._status[project_id] = SyncStatus(revision=job[3], state="running", started=time.time())
            status = self.sync(*job[:3], project_id, job[3])
            with self._lock:
                self._status[project_id] = status
            self._save_status(project_id, status)

    def sync(self, root: Path, cfg: ProjectConfig, result: BuildResult, project_id: str, number: int) -> SyncStatus:
        status = SyncStatus(revision=number, state="running", started=time.time())
        folder = folder_of(root, cfg, number)
        sf = folder / "supporting_files"
        latex_dir = inside(root, cfg.latex_dir)
        built = builds_dir(project_id) / result.id
        # The deliverables come from a build of their own: the editor's compiles reuse files from earlier
        # passes for speed, and deliverables never do.
        outdir = builds_dir(project_id) / "clean"
        stem = Path(cfg.main).stem
        try:
            # What the change log compares with: the last snapshot, or the baseline before the first one.
            before = sf if (sf / cfg.main).is_file() else sf / "tracked_build" / "old"
            previous = (before / cfg.main).read_text(errors="replace") if (before / cfg.main).is_file() else None
            figures = file_changes(file_state(before, cfg.figure_folders), file_state(latex_dir, cfg.figure_folders))

            # 1. The source snapshot: every file the compile read from the LaTeX folder, its bibliography
            # files and all figures.
            changed_files = self._snapshot(latex_dir, sf, cfg, built)
            status.steps.append(Step("Source snapshot", True, ", ".join(changed_files[:6]) + (
                ", ..." if len(changed_files) > 6 else "") if changed_files else "unchanged"))

            # 2. The tracked PDF first: it is what the author is waiting to see.
            status.steps.append(self._tracked(cfg, folder, sf, status))
            if self._superseded(project_id):
                status.steps.append(Step("Stopped early", True, "You compiled again; the next update does the rest."))
                status.state = "done"
                return status

            # 3. The clean PDF and its split.
            shutil.rmtree(outdir, ignore_errors=True)
            outdir.mkdir(parents=True)
            code, _ = run_latexmk(latex_dir, cfg.main, outdir, cfg.engine, cfg.bib_extra_lists)
            (sf / "logs").mkdir(exist_ok=True)
            if (outdir / f"{stem}.log").is_file():
                shutil.copy2(outdir / f"{stem}.log", sf / "logs" / "compile_clean.log")
            if code != 0 or not (outdir / f"{stem}.pdf").is_file():
                raise RuntimeError(f"The clean build failed (latexmk exit code {code}); "
                                   "see supporting_files/logs/compile_clean.log.")
            with pymupdf.open(outdir / f"{stem}.pdf") as doc:
                pages = doc.page_count
                start = si_start(doc, cfg.si_marker)
            shutil.copy2(outdir / f"{stem}.pdf", folder / "revision_clean.pdf")
            main_pages, si_pages = split_pdf(folder / "revision_clean.pdf", start + 1 if start else None,
                                             folder / "main_draft.pdf", folder / "supplementary_draft.pdf")
            status.steps.append(Step("Clean PDF", True, f"{pages} pages: main text {main_pages}"
                                     + (f", Supplementary Information {si_pages}" if si_pages else "")))

            # 4. The working copies, which must be the same files.
            status.steps.append(self._working_copies(root, cfg, folder))

            # 5. The change log.
            text = (sf / cfg.main).read_text(errors="replace")
            what = describe_changes(previous, text) if previous is not None else "first snapshot"
            entry = [f"Updated from {cfg.latex_dir}/{cfg.main}: {what}."]
            if figures:
                entry.append("Files: " + ", ".join(figures[:8]) + (", ..." if len(figures) > 8 else "") + ".")
            entry.append(f"Clean PDF {pages} pages ({main_pages}" + (f" + {si_pages} SI" if si_pages else "")
                         + ")" + (f"; tracked PDF {status.tracked['pages']} pages" if status.tracked else "") + ".")
            self._log(sf, " ".join(entry))
            status.steps.append(Step("Change log", True, what))
            status.state = "done" if all(s.ok for s in status.steps) else "failed"
        except Exception as e:  # reported in the status; the revision keeps its last good files
            status.steps.append(Step("Update stopped", False, str(e)))
            status.state = "failed"
        finally:
            shutil.rmtree(outdir, ignore_errors=True)
            status.finished = time.time()
            failed = [s.name for s in status.steps if not s.ok]
            status.summary = (f"Revision {number} updated" if not failed
                              else f"Revision {number}: {', '.join(failed)} failed")
        return status

    def _superseded(self, project_id: str) -> bool:
        """A newer compile is waiting, so the rest of this update would soon be redone."""
        with self._lock:
            return project_id in self._pending

    def _snapshot(self, latex_dir: Path, sf: Path, cfg: ProjectConfig, built: Path) -> list[str]:
        changes = []
        inputs = {latex_dir / cfg.main}
        fls = built / f"{Path(cfg.main).stem}.fls"
        # biber and bibtex read the .bib files, LaTeX does not, so the .fls does not list them.
        for name in bibliography_files(built):
            path = Path(os.path.normpath(latex_dir / name))
            if path.is_file() and latex_dir in path.parents:
                inputs.add(path)
        if fls.is_file():
            for line in fls.read_text(errors="replace").splitlines():
                if line.startswith("INPUT "):
                    path = Path(os.path.normpath(latex_dir / line[6:].strip()))
                    if path.is_file() and latex_dir in path.parents and path.suffix not in SNAPSHOT_SKIP:
                        inputs.add(path)
        for path in sorted(inputs):
            rel = path.relative_to(latex_dir)
            if rel.parts[0] in cfg.figure_folders:
                continue
            existed = (sf / rel).exists()
            if copy_if_changed(path, sf / rel):
                changes.append(f"{'changed' if existed else 'added'} {rel}")
        for name in cfg.figure_folders:
            if (latex_dir / name).is_dir():
                changes += [f"{c.split(' ', 1)[0]} {name}/{c.split(' ', 1)[1]}" for c in mirror(latex_dir / name, sf / name)]
        return changes

    def _working_copies(self, root: Path, cfg: ProjectConfig, folder: Path) -> Step:
        names = ["main_draft.pdf", "supplementary_draft.pdf"]
        different = []
        for place in cfg.working_copies:
            target = inside(root, place)
            if not target.is_dir():
                different.append(f"{place}/ does not exist")
                continue
            for name in names:
                src = folder / name
                if src.is_file():
                    copy_if_changed(src, target / name)
                    if sha(src) != sha(target / name):
                        different.append(f"{place}/{name}")
        if different:
            return Step("Working copies", False, "Not the same as the revision's: " + ", ".join(different))
        if not cfg.working_copies:
            return Step("Working copies", True, "none: the paper folder keeps no draft PDFs")
        places = ", ".join(f"{p}/" for p in cfg.working_copies)
        return Step("Working copies", True, f"identical to the revision's ({places})")

    def _tracked(self, cfg: ProjectConfig, folder: Path, sf: Path, status: SyncStatus) -> Step:
        old = sf / "tracked_build" / "old"
        if not (old / cfg.main).is_file():
            return Step("Tracked PDF", False, "No baseline source in supporting_files/tracked_build/old.")
        try:
            result = tracked.build_tracked(old, cfg.main, sf, cfg.main, sf / "tracked_build", macro=cfg.figure_macro,
                                           folders=cfg.figure_folders, engine=cfg.engine,
                                           extra_lists=cfg.bib_extra_lists)
        except tracked.TrackedError as e:
            return Step("Tracked PDF", False, str(e))
        status.tracked = result.to_dict()
        build = sf / "tracked_build" / "build"
        if (build / "tracked.log").is_file():
            (sf / "logs").mkdir(exist_ok=True)
            shutil.copy2(build / "tracked.log", sf / "logs" / "compile_tracked.log")
        if (build / "tracked.pdf").is_file():
            shutil.copy2(build / "tracked.pdf", folder / "revision_tracked.pdf")
        return Step("Tracked PDF", result.ok, result.summary)

    def _log(self, sf: Path, text: str, header: bool = False) -> None:
        stamp = time.strftime("%d %B %Y %H:%M").lstrip("0")
        with open(sf / "change_log.txt", "a", encoding="utf-8") as log:
            if header:
                log.write(f"\n{text}\n{'=' * min(len(text), 100)}\n")
            else:
                log.write(f"\n{stamp} (Thor)\n  {text}\n")
