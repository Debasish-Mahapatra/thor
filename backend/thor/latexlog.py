"""Reads a LaTeX log: errors, undefined references and citations, overfull boxes and other warnings.

Builds run with -file-line-error and a log line width of 10000, so messages are not wrapped and errors
name their file and line. Warnings that give only an input line are attributed to the file open at that
point, tracked from the parentheses TeX writes around each file it reads.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass

# Files LaTeX writes in one pass and reads in the next. An error in one of them comes from an earlier pass.
GENERATED = ("aux", "bbl", "toc", "lof", "lot", "out", "nav", "snm", "vrb", "ind", "gls", "loa", "brf")
FILE_LINE_ERROR = re.compile(r"^(?P<file>\S[^:]*\.(?:tex|sty|cls|bib|ltx|def|cfg|clo|fd|"
                             + "|".join(GENERATED) + r")):(?P<line>\d+): (?P<msg>.*)$")
TEX_ERROR = re.compile(r"^! (?P<msg>.*)$")
CONTEXT_LINE = re.compile(r"^l\.(?P<line>\d+) ?(?P<text>.*)$")
WARNING = re.compile(r"^(?:LaTeX|Package (?P<pkg>[\w-]+)|Class (?P<cls>[\w-]+)) Warning: (?P<msg>.*)$")
UNDEFINED_REF = re.compile(r"Reference `(?P<key>[^']+)' on page \S+ undefined on input line (?P<line>\d+)")
# natbib quotes the key `like this'; biblatex 'like this'.
UNDEFINED_CITE = re.compile(r"Citation [`'](?P<key>[^']+)' on page \S+ undefined on input line (?P<line>\d+)")
INPUT_LINE = re.compile(r"on input line (?P<line>\d+)")
BOX = re.compile(r"^Overfull \\[hv]box \((?P<amount>[^)]*)\) (?P<where>.*?) at lines? (?P<start>\d+)(?:--(?P<end>\d+))?")
OPEN_FILE = re.compile(r"\((?P<path>(?:\.{0,2}/|[A-Za-z]:/)?[^\s()]+\.(?:tex|sty|cls|cfg|def|clo|fd|aux|bbl|toc|out|ltx|bib))")

# Warnings that say nothing the user can act on.
IGNORED_WARNINGS = (
    "Writing or overwriting file",
    "Label(s) may have changed",
    "There were undefined references",
    "There were undefined citations",
    "Citation(s) may have changed",
    "Rerun to get",
    "Unused global option",
)
# latexmk reruns LaTeX until these settle, so they never need the user.
RERUN_HINTS = ("Rerun", "Column widths have changed", "Table widths have changed")


@dataclass
class Problem:
    # error, undefined_reference, undefined_citation, overfull, warning
    kind: str
    message: str
    file: str | None = None
    line: int | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class FileTracker:
    """Which source file TeX was reading, from the "(./file.tex" ... ")" pairs in the log."""

    def __init__(self) -> None:
        self.stack: list[str | None] = []

    def feed(self, line: str) -> None:
        i = 0
        while i < len(line):
            c = line[i]
            if c == "(":
                m = OPEN_FILE.match(line, i)
                if m:
                    self.stack.append(m.group("path"))
                    i = m.end()
                    continue
                self.stack.append(None)
            elif c == ")" and self.stack:
                self.stack.pop()
            i += 1

    def current(self) -> str | None:
        return next((f for f in reversed(self.stack) if f), None)


def relative(path: str | None, latex_dir: str | None) -> str | None:
    """A path as the project knows it: relative to the LaTeX folder, or None for files outside it."""
    if not path:
        return None
    if latex_dir and os.path.isabs(path):
        try:
            rel = os.path.relpath(path, latex_dir)
        except ValueError:
            return None
        return None if rel.startswith("..") else os.path.normpath(rel)
    if os.path.isabs(path):
        return None
    return os.path.normpath(path)


def generated_errors(text: str) -> list[str]:
    """Generated files (main.aux, ...) that LaTeX stopped on, by name."""
    found = (FILE_LINE_ERROR.match(line) for line in text.splitlines())
    return sorted({os.path.basename(m.group("file")) for m in found
                   if m and m.group("file").rsplit(".", 1)[-1] in GENERATED})


def parse(text: str, latex_dir: str | None = None) -> list[Problem]:
    lines = text.splitlines()
    tracker = FileTracker()
    problems: list[Problem] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        m = FILE_LINE_ERROR.match(line) or TEX_ERROR.match(line)
        if m:
            file = m.groupdict().get("file") or tracker.current()
            number = int(m.group("line")) if "line" in m.groupdict() and m.group("line") else None
            context = []
            j = i + 1
            while j < len(lines) and j < i + 8 and lines[j].strip():
                c = CONTEXT_LINE.match(lines[j])
                if c:
                    number = number or int(c.group("line"))
                    context.append(lines[j])
                    if j + 1 < len(lines) and lines[j + 1].strip():
                        context.append(lines[j + 1])
                    break
                context.append(lines[j])
                j += 1
            message = m.group("msg").strip()
            if file and file.rsplit(".", 1)[-1] in GENERATED:
                # Written into the build folder by an earlier pass, so there is no source line to jump to.
                message, file, number = f"{message} (in {os.path.basename(file)})", None, None
            problems.append(Problem("error", message, relative(file, latex_dir), number,
                                    "\n".join(context).strip()))
            tracker.feed(line)
            i += 1
            continue

        w = WARNING.match(line)
        if w:
            msg = w.group("msg")
            source = w.group("pkg") or w.group("cls")
            # Package warnings continue on lines that start with "(package)".
            j = i + 1
            while source and j < len(lines) and lines[j].startswith(f"({source})"):
                msg += " " + lines[j][len(source) + 2:].strip()
                j += 1
            file = relative(tracker.current(), latex_dir)
            if r := UNDEFINED_REF.search(msg):
                problems.append(Problem("undefined_reference", f"Undefined reference {r.group('key')}", file,
                                        int(r.group("line"))))
            elif c := UNDEFINED_CITE.search(msg):
                problems.append(Problem("undefined_citation", f"Undefined citation {c.group('key')}", file,
                                        int(c.group("line"))))
            elif not msg.startswith(IGNORED_WARNINGS) and not any(h in msg for h in RERUN_HINTS):
                at = INPUT_LINE.search(msg)
                label = f"{source}: " if source else ""
                problems.append(Problem("warning", label + msg.strip(), file, int(at.group("line")) if at else None))
            tracker.feed(line)
            i = j
            continue

        b = BOX.match(line)
        if b:
            end = f"–{b.group('end')}" if b.group("end") and b.group("end") != b.group("start") else ""
            problems.append(Problem("overfull", f"Overfull box, {b.group('amount')}, {b.group('where')}, lines "
                                    f"{b.group('start')}{end}", relative(tracker.current(), latex_dir),
                                    int(b.group("start"))))
        tracker.feed(line)
        i += 1
    return dedupe(problems)


def dedupe(problems: list[Problem]) -> list[Problem]:
    """TeX repeats undefined-reference warnings on every pass; keep each once."""
    seen: set[tuple] = set()
    kept = []
    for p in problems:
        key = (p.kind, p.message, p.file, p.line)
        if key not in seen:
            seen.add(key)
            kept.append(p)
    return kept


def counts(problems: list[Problem]) -> dict[str, int]:
    kinds = ["error", "undefined_reference", "undefined_citation", "overfull", "warning"]
    return {k: sum(p.kind == k for p in problems) for k in kinds}
