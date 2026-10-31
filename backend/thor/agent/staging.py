"""The agent's copy of the paper, and the edits waiting for the user.

base/ holds the project's files as the agent last saw them, work/ the same files as the agent left them; both
are APFS clones, so even a paper with large figures takes no time or room. What differs between them is
what the user is asked about. An accepted hunk goes into the project and into base/; a rejected one goes out of
work/. Either way the difference shrinks, and nothing else ever writes to the paper folder.

If the user edits the paper during a chat, sync() carries those edits into both copies and keeps the agent's
waiting edits on top of them.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import files
from ..paths import clone_file
from ..projects import ProjectConfig, ProjectError, inside
from ..revisions import BASELINE_FILE, BUILD_OUTPUT, NOT_SOURCE_DIRS, NOT_SOURCE_FILES, REVISION_DIR

# latexmk runs these as code, so an edit to one must not reach a compile through Thor or the paper folder.
LATEXMK_RC = {"latexmkrc", ".latexmkrc"}
# What LaTeX may leave beside the source when a command-line agent compiles in its folder.
GENERATED = (*BUILD_OUTPUT, ".bbl", ".pdf.tmp", ".synctex", ".xdv", ".dvi")
CONTEXT = 3
# What an edit must not change without someone looking. The instructions tell the model the same, but a real one changed
# "38 degrees of latitude" to "38 degrees south and 38 degrees north" anyway.
NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*")
REFERENCE = re.compile(r"\\(?:[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*|ref|eqref|autoref|pageref|[cC]ref|label)\{[^}]*\}")
# Words that can turn a finding around while no number changes.
CLAIM_WORDS = {
    "above", "after", "before", "below", "decrease", "decreased", "decreases", "decreasing", "east", "fewer", "greater",
    "higher", "increase", "increased", "increases", "increasing", "larger", "less", "lower", "more", "negative", "never",
    "no", "none", "north", "not", "northern", "positive", "significant", "significantly", "smaller", "south", "southern",
    "stronger", "tropical", "unlike", "weaker", "west", "without",
}
WORD = re.compile(r"[a-z]+")


class StagingConflict(Exception):
    pass


@dataclass
class Hunk:
    id: str
    # 1-based line in the agent's starting text, and in its edited text.
    old_start: int
    new_start: int
    old: list[str]
    new: list[str]
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    # Why someone should read this one: it is left out of "accept all".
    flags: list[str] = field(default_factory=list)


@dataclass
class FileChange:
    # Relative to the project's root, like every path the interface uses.
    path: str
    kind: str  # edit, new, deleted or binary
    hunks: list[Hunk] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    refused: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def split_lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def hunk_flags(old: str, new: str) -> list[str]:
    """What this edit changes that could alter the science. It cannot see meaning, so no flag is no guarantee."""

    def changed(found_old: list[str], found_new: list[str]) -> list[str]:
        a, b = Counter(found_old), Counter(found_new)
        return sorted(((a - b) + (b - a)).keys())

    flags = []
    numbers = changed(NUMBER.findall(old), NUMBER.findall(new))
    if numbers:
        flags.append(f"Changes a number: {', '.join(numbers[:6])}")
    references = changed(REFERENCE.findall(old), REFERENCE.findall(new))
    if references:
        flags.append(f"Changes a citation or reference: {', '.join(references[:4])}")
    words = changed([w for w in WORD.findall(old.lower()) if w in CLAIM_WORDS],
                    [w for w in WORD.findall(new.lower()) if w in CLAIM_WORDS])
    if words:
        flags.append(f"Changes a word that can alter a finding: {', '.join(words[:6])}")
    return flags


def compute_hunks(path: str, old_text: str, new_text: str) -> list[Hunk]:
    a, b = split_lines(old_text), split_lines(new_text)
    # Most lines of a paper are the same; the matcher is quadratic at worst, so it gets only the middle.
    head = 0
    while head < len(a) and head < len(b) and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < len(a) - head and tail < len(b) - head and a[-1 - tail] == b[-1 - tail]:
        tail += 1
    matcher = difflib.SequenceMatcher(None, a[head:len(a) - tail], b[head:len(b) - tail], autojunk=False)
    hunks: list[Hunk] = []
    seen: dict[str, int] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        i1, i2, j1, j2 = i1 + head, i2 + head, j1 + head, j2 + head
        before, after = a[max(0, i1 - CONTEXT):i1], a[i2:i2 + CONTEXT]
        digest = hashlib.sha1("\0".join([path, *a[i1:i2], "\1", *b[j1:j2], "\1", *before]).encode()).hexdigest()[:12]
        seen[digest] = seen.get(digest, 0) + 1
        hunks.append(Hunk(digest if seen[digest] == 1 else f"{digest}-{seen[digest]}", i1 + 1, j1 + 1, a[i1:i2], b[j1:j2],
                          before, after, hunk_flags("".join(a[i1:i2]), "".join(b[j1:j2]))))
    return hunks


def place_hunk(lines: list[str], hunk: Hunk) -> int | None:
    """Where this hunk's old lines are in text that may have changed around them: the place nearest its old
    position whose neighbours also match, or failing that the nearest; None if they are not there."""
    size = len(hunk.old)
    starts = range(0, len(lines) - size + 1)
    if hunk.old:
        places = [i for i in starts if lines[i:i + size] == hunk.old]
    else:
        places = [i for i in starts if lines[max(0, i - len(hunk.before)):i] == hunk.before
                  or lines[i:i + len(hunk.after)] == hunk.after]
    if not places:
        return None

    def score(i: int) -> tuple[int, int]:
        fits = lines[max(0, i - len(hunk.before)):i] == hunk.before and lines[i + size:i + size + len(hunk.after)] == hunk.after
        return (0 if fits else 1, abs(i - (hunk.old_start - 1)))

    return min(places, key=score)


def apply_hunk(text: str, hunk: Hunk) -> str | None:
    lines = split_lines(text)
    at = place_hunk(lines, hunk)
    if at is None:
        return None
    lines[at:at + len(hunk.old)] = hunk.new
    return "".join(lines)


def is_source(rel: Path) -> bool:
    if any(part.startswith(".") and part != ".latexmkrc" for part in rel.parts):
        return False
    if any(REVISION_DIR.match(part) or part in NOT_SOURCE_DIRS for part in rel.parts[:-1]):
        return False
    name = rel.name
    return not (name in NOT_SOURCE_FILES or BASELINE_FILE.match(name) or name.endswith(GENERATED))


def walk(folder: Path, skip: tuple[str, ...] = ()) -> dict[str, Path]:
    found = {}
    for current, dirs, names in os.walk(folder):
        here = Path(current).relative_to(folder)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and (here / d).as_posix() not in skip)
        for name in names:
            rel = here / name
            if is_source(rel):
                found[rel.as_posix()] = Path(current) / name
    return found


def same_file(a: Path, b: Path) -> bool:
    if not a.is_file() or not b.is_file():
        return False
    ia, ib = a.stat(), b.stat()
    if ia.st_size != ib.st_size:
        return False
    return ia.st_mtime_ns == ib.st_mtime_ns or a.read_bytes() == b.read_bytes()


def is_text_file(path: Path) -> bool:
    if not files.is_text(path):
        return False
    with path.open("rb") as f:
        return b"\0" not in f.read(8192)


class Staging:
    def __init__(self, folder: Path, root: Path, cfg: ProjectConfig) -> None:
        self.root = root
        self.cfg = cfg
        self.latex_dir = inside(root, cfg.latex_dir)
        self.prefix = Path(cfg.latex_dir)
        # Mirrors of the project's root, so a build's latex_dir works in them as it does in the project.
        self.base_root = folder / "base"
        self.work_root = folder / "work"
        self.base = self.base_root / self.prefix
        self.work = self.work_root / self.prefix
        self.skip = tuple(
            d for d in (self._inside_latex(cfg.results_dir), self._inside_latex(cfg.scripts_dir)) if d
        )

    def _inside_latex(self, name: str) -> str:
        if not name:
            return ""
        try:
            rel = inside(self.root, name).relative_to(self.latex_dir).as_posix()
        except (ValueError, ProjectError):
            return ""
        return "" if rel == "." else rel

    def project_path(self, rel: str) -> str:
        return (self.prefix / rel).as_posix()

    def relative(self, project_path: str) -> str:
        return Path(os.path.relpath(self.root / project_path, self.latex_dir)).as_posix()

    def exists(self) -> bool:
        return self.work.is_dir()

    def create(self) -> None:
        for folder in (self.base, self.work):
            folder.mkdir(parents=True, exist_ok=True)
        for rel, path in walk(self.latex_dir, self.skip).items():
            for target in (self.base / rel, self.work / rel):
                target.parent.mkdir(parents=True, exist_ok=True)
                clone_file(path, target)

    def restore_rc(self) -> None:
        """Puts back any latexmkrc the agent changed. Called before Thor compiles its copy, because latexmk
        would run it as code, outside whatever sandbox the agent's own tools have."""
        for name in LATEXMK_RC:
            work, base = self.work / name, self.base / name
            if base.is_file():
                if not same_file(work, base):
                    clone_file(base, work)
            else:
                work.unlink(missing_ok=True)

    def sync(self) -> list[str]:
        """Brings the project's changes into both copies; returns the agent's edits that no longer fit."""
        project = walk(self.latex_dir, self.skip)
        base = walk(self.base)
        dropped: list[str] = []
        for rel, source in project.items():
            base_file, work_file = self.base / rel, self.work / rel
            if base_file.is_file() and same_file(source, base_file):
                continue
            if not base_file.is_file() or same_file(base_file, work_file) or not (is_text_file(source) and work_file.is_file()):
                for target in (base_file, work_file):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    clone_file(source, target)
                continue
            merged = source.read_text(errors="replace")
            for hunk in compute_hunks(rel, base_file.read_text(errors="replace"), work_file.read_text(errors="replace")):
                placed = apply_hunk(merged, hunk)
                if placed is None:
                    dropped.append(f"{self.project_path(rel)}, line {hunk.old_start}")
                else:
                    merged = placed
            clone_file(source, base_file)
            work_file.write_text(merged)
        for rel in base.keys() - project.keys():
            work_file = self.work / rel
            if same_file(self.base / rel, work_file):
                work_file.unlink(missing_ok=True)
            (self.base / rel).unlink(missing_ok=True)
        return dropped

    def changes(self) -> list[FileChange]:
        base, work = walk(self.base), walk(self.work)
        out: list[FileChange] = []
        for rel in sorted(base.keys() | work.keys()):
            refused = "latexmk runs this file as code, so Thor does not take the agent's edits to it." if Path(rel).name in LATEXMK_RC else ""
            if rel not in work:
                out.append(FileChange(self.project_path(rel), "deleted", refused=refused))
            elif rel not in base:
                text = is_text_file(work[rel])
                count = len(split_lines(work[rel].read_text(errors="replace"))) if text else 0
                out.append(FileChange(self.project_path(rel), "new" if text else "binary", additions=count, refused=refused))
            elif not same_file(base[rel], work[rel]):
                if is_text_file(work[rel]) and is_text_file(base[rel]):
                    hunks = compute_hunks(rel, base[rel].read_text(errors="replace"), work[rel].read_text(errors="replace"))
                    out.append(FileChange(self.project_path(rel), "edit", hunks, sum(len(h.new) for h in hunks),
                                          sum(len(h.old) for h in hunks), refused))
                else:
                    out.append(FileChange(self.project_path(rel), "binary", refused=refused))
        return out

    def change_for(self, project_path: str) -> FileChange:
        found = next((c for c in self.changes() if c.path == project_path), None)
        if not found:
            raise StagingConflict(f"The agent has no waiting edit to {project_path}.")
        return found

    def accept(self, project_path: str, hunk_id: str | None = None, skip_flagged: bool = False) -> tuple[str, bytes | None]:
        """Writes the edit into the project; (the path, what the file held before, for the history). With
        skip_flagged, flagged hunks stay waiting, and a deleted file stays as it is."""
        change = self.change_for(project_path)
        if change.refused:
            raise StagingConflict(change.refused)
        if skip_flagged and change.kind == "deleted":
            return project_path, None
        if hunk_id == "delete":
            hunk_id = None
        rel = self.relative(project_path)
        target, base_file, work_file = self.latex_dir / rel, self.base / rel, self.work / rel
        before = target.read_bytes() if target.is_file() else None
        if change.kind != "edit":
            if base_file.is_file() and (before is None or not same_file(target, base_file)):
                raise StagingConflict(f"{project_path} changed since the agent read it. Ask the agent again.")
            if change.kind in ("new", "binary") and before is not None and not base_file.is_file():
                raise StagingConflict(f"{project_path} now exists in the project. Ask the agent again.")
            if change.kind == "deleted":
                target.unlink(missing_ok=True)
                base_file.unlink(missing_ok=True)
                return project_path, before
            target.parent.mkdir(parents=True, exist_ok=True)
            base_file.parent.mkdir(parents=True, exist_ok=True)
            clone_file(work_file, target)
            clone_file(work_file, base_file)
            return project_path, before
        hunks = [h for h in change.hunks if hunk_id in (None, h.id) and not (skip_flagged and h.flags)]
        if not hunks:
            if skip_flagged:
                return project_path, None
            raise StagingConflict("That edit is no longer waiting; the list has been refreshed.")
        current = before.decode("utf-8", errors="replace") if before is not None else None
        if current is None:
            raise StagingConflict(f"{project_path} is no longer in the project.")
        base_text = base_file.read_text(errors="replace")
        # The last hunks first, so the earlier ones keep their positions.
        for hunk in sorted(hunks, key=lambda h: -h.old_start):
            placed = apply_hunk(current, hunk)
            if placed is None:
                raise StagingConflict(f"The text at line {hunk.old_start} of {project_path} changed since the agent "
                                      "read it. Ask the agent again.")
            current = placed
            base_text = "".join(_replaced(split_lines(base_text), hunk))
        try:
            files.write_text(self.root, project_path, current, files.digest(before))
        except files.Conflict as e:
            raise StagingConflict(f"{project_path} changed on disk just now. Try again.") from e
        base_file.write_text(base_text)
        return project_path, before

    def reject(self, project_path: str, hunk_id: str | None = None) -> list[str]:
        """Takes the edit out of the agent's copy; returns a description of it for the agent's next turn."""
        change = self.change_for(project_path)
        rel = self.relative(project_path)
        base_file, work_file = self.base / rel, self.work / rel
        if change.kind != "edit":
            if base_file.is_file():
                work_file.parent.mkdir(parents=True, exist_ok=True)
                clone_file(base_file, work_file)
            else:
                work_file.unlink(missing_ok=True)
            return [f"{project_path}: whole {change.kind} file"]
        hunks = [h for h in change.hunks if hunk_id in (None, h.id)]
        lines = split_lines(work_file.read_text(errors="replace"))
        notes = []
        for hunk in sorted(hunks, key=lambda h: -h.new_start):
            lines[hunk.new_start - 1:hunk.new_start - 1 + len(hunk.new)] = hunk.old
            notes.append(f"{project_path}, line {hunk.old_start}: \"{''.join(hunk.new).strip()[:200]}\"")
        work_file.write_text("".join(lines))
        return notes

    def remove(self) -> None:
        shutil.rmtree(self.base_root, ignore_errors=True)
        shutil.rmtree(self.work_root, ignore_errors=True)


def _replaced(lines: list[str], hunk: Hunk) -> list[str]:
    lines[hunk.old_start - 1:hunk.old_start - 1 + len(hunk.old)] = hunk.new
    return lines
