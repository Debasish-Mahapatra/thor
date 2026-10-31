"""Makes the tracked PDF mark what the editor marks.

latexdiff's smallest unit is a whole hyphenated word, and it knows nothing smaller than a word. Changing "single-moment
scheme" to "single-layer scheme" comes back as all of "single-moment" struck and retyped, which says the author deleted
"single" when they did not. The editor (ui/src/trackchanges.ts) treats the hyphen as a word of its own, and marks only the
letters that differ within one word.

So each plain pair of deleted and inserted text that latexdiff writes is compared again here, by the editor's rules, which
this file follows step by step. tests/data/word_changes.json holds cases both test suites run, so the two cannot drift
apart unnoticed. Anything holding a command, braces or math stays as latexdiff marked it: that is what latexdiff is for,
and splitting it could break the LaTeX.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CELLS = 4_000_000
# An escaped sign is one word here, unlike in the editor: splitting \% in two would leave a stray backslash in the LaTeX.
TOKEN = re.compile(r"\\.|\s+|(?:[^\W_]|@)+|[^\s\w@]|_", re.S)
PLAIN = r"((?:[^{}\\$%]|\\[%&#_$])*)"
PAIR = re.compile(
    r"\\DIFdelbegin(FL)? +\\DIFdel(FL)?\{" + PLAIN + r"\}\\DIFdelend(FL)? +"
    r"\\DIFaddbegin(FL)? +\\DIFadd(FL)?\{" + PLAIN + r"\}\\DIFaddend(FL)? ?"
)
BLANK_LINE = re.compile(r"\n[ \t]*\n")


@dataclass
class Change:
    # The inserted text's place in the new text; start == end when text was only deleted.
    start: int
    end: int
    deleted: str


def _weight(token: str) -> int:
    return 2 if token.strip() else 1


def _diff(a: list[str], b: list[str]) -> list[str] | None:
    n, m = len(a), len(b)
    if n * m > MAX_CELLS:
        return None
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            table[i][j] = (table[i + 1][j + 1] + _weight(a[i]) if a[i] == b[j]
                           else max(table[i + 1][j], table[i][j + 1]))
    ops: list[str] = []
    i = j = 0
    while i < n or j < m:
        skip = max(table[i + 1][j] if i < n else 0, table[i][j + 1] if j < m else 0)
        if i < n and j < m and a[i] == b[j] and table[i][j] >= skip:
            ops.append("same")
            i += 1
            j += 1
        elif j < m and (i == n or table[i][j + 1] >= table[i + 1][j]):
            ops.append("ins")
            j += 1
        else:
            ops.append("del")
            i += 1
    return ops


def _narrowed(start: int, end: int, removed: str, text: str) -> Change:
    """Within one replaced word, only the letters that differ. Unrelated words stay whole."""
    added = text[start:end]
    both = removed + added
    if removed and added and not re.search(r"\s", both) and "\\" not in both:
        head = 0
        while head < len(removed) and head < len(added) and removed[head] == added[head]:
            head += 1
        tail = 0
        while tail < len(removed) - head and tail < len(added) - head and removed[-1 - tail] == added[-1 - tail]:
            tail += 1
        if (head + tail) * 2 >= max(len(removed), len(added)):
            return Change(start + head, end - tail, removed[head:len(removed) - tail])
    return Change(start, end, removed)


def _compared(deleted: str, inserted: str) -> list[Change]:
    a, b = TOKEN.findall(deleted), TOKEN.findall(inserted)
    head = 0
    while head < len(a) and head < len(b) and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < len(a) - head and tail < len(b) - head and a[-1 - tail] == b[-1 - tail]:
        tail += 1
    middle = _diff(a[head:len(a) - tail], b[head:len(b) - tail])
    if middle is None:
        return [Change(0, len(inserted), deleted)]
    out: list[Change] = []
    i = j = pos = start = 0
    removed = ""

    def flush() -> None:
        nonlocal removed
        if removed or pos > start:
            out.append(_narrowed(start, pos, removed, inserted))
        removed = ""

    for op in ["same"] * head + middle + ["same"] * tail:
        if op == "same":
            flush()
            pos += len(b[j])
            i += 1
            j += 1
            start = pos
        elif op == "del":
            removed += a[i]
            i += 1
        else:
            pos += len(b[j])
            j += 1
    flush()
    return out


def word_changes(deleted: str, inserted: str) -> list[Change]:
    """What changed from one text to the other. Changes with at most one space or tab between them read as one."""
    out: list[Change] = []
    for change in _compared(deleted, inserted):
        previous = out[-1] if out else None
        gap = inserted[previous.end:change.start] if previous else ""
        if previous and re.fullmatch(r"[ \t]?", gap):
            previous.deleted += gap + change.deleted
            previous.end = change.end
        else:
            out.append(Change(change.start, change.end, change.deleted))
    return out


def _marked(match: re.Match) -> str:
    begin_del, del_fl, old, end_del, begin_add, add_fl, new, end_add = (group or "" for group in match.groups())
    if not old or not new or BLANK_LINE.search(old) or BLANK_LINE.search(new):
        return match.group(0)
    changes = word_changes(old, new)
    if len(changes) == 1 and changes[0].deleted == old and changes[0].start == 0 and changes[0].end == len(new):
        return match.group(0)
    out: list[str] = []
    at = 0

    def plain(text: str) -> None:
        # TeX drops any space after a command's name, and this space is one the author wrote.
        if text and out and text[0].isspace():
            out[-1] = out[-1].rstrip(" ") + "{}"
        out.append(text)

    for change in changes:
        if change.start > at:
            plain(new[at:change.start])
        added = new[change.start:change.end]
        if change.deleted:
            out.append(f"\\DIFdelbegin{begin_del} \\DIFdel{del_fl}{{{change.deleted}}}\\DIFdelend{end_del} ")
            # latexdiff's struck text ended in a space, which kept it apart from what replaced it. That space is often
            # kept text now, and "Extreme" struck right against "small" is hard to read.
            if added and not added[0].isspace() and not change.deleted[-1].isspace():
                out.append("\\,")
        if added:
            out.append(f"\\DIFaddbegin{begin_add} \\DIFadd{add_fl}{{{added}}}\\DIFaddend{end_add} ")
        at = change.end
    if at < len(new):
        plain(new[at:])
    return "".join(out)


def narrow_marks(marked: str) -> str:
    """latexdiff's output, with each plain pair of deleted and inserted text marked as the editor would mark it."""
    return PAIR.sub(_marked, marked)
