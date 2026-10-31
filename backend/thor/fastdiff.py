"""latexdiff, run only where the text changed.

latexdiff compares the whole document at once, and its time grows far faster than the amount changed: on a paper of
123 pages it took 0.4 s with one edit and 68 s with 63 edited paragraphs. A revision is mostly the second case, and the
tracked PDF is redone after every compile. So the body is split into blocks that end at a blank line outside any
environment or group, the blocks that match are kept as they are, and latexdiff sees only pairs of changed blocks, side
by side on all cores. Each pair's answer is kept, so the next compile redoes only the paragraph just edited.

The mark-up still comes from latexdiff, block by block, so its handling of commands, math and figures is unchanged. If
anything goes wrong, the caller falls back to latexdiff on the whole document.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BEGIN_DOCUMENT = "\\begin{document}"
END_DOCUMENT = "\\end{document}"
WRAP = "\\documentclass{article}\n\\begin{document}\n%s\n\\end{document}\n"
# In the small document that yields latexdiff's preamble: what makes it add its figure commands.
PREAMBLE_PROBE = "\\includegraphics{probe}\nprobe %s\n"
COMMENT = re.compile(r"(?<!\\)%.*")
ENV = re.compile(r"\\(begin|end)\s*\{[^}]*\}")
OPEN_DISPLAY = re.compile(r"(?<!\\)\\\[")
CLOSE_DISPLAY = re.compile(r"(?<!\\)\\\]")
OPEN_BRACE = re.compile(r"(?<!\\)\{")
CLOSE_BRACE = re.compile(r"(?<!\\)\}")
WORKERS = min(8, os.cpu_count() or 4)


def split_document(src: str) -> tuple[str, str, str]:
    """(everything through \\begin{document}, the body, \\end{document} onward)."""
    start = src.index(BEGIN_DOCUMENT) + len(BEGIN_DOCUMENT)
    end = src.rindex(END_DOCUMENT)
    return src[:start], src[start:end], src[end:]


def blocks(body: str) -> list[str]:
    """The body in pieces that can each be compared alone. Joined, they are the body again."""
    out: list[str] = []
    current: list[str] = []
    envs = braces = display = 0
    for line in body.splitlines(keepends=True):
        current.append(line)
        clean = COMMENT.sub("", line)
        for found in ENV.finditer(clean):
            envs += 1 if found.group(1) == "begin" else -1
        braces += len(OPEN_BRACE.findall(clean)) - len(CLOSE_BRACE.findall(clean))
        display += len(OPEN_DISPLAY.findall(clean)) - len(CLOSE_DISPLAY.findall(clean))
        if not line.strip() and envs <= 0 and braces <= 0 and display <= 0:
            out.append("".join(current))
            current = []
            envs = braces = display = 0
    if current:
        out.append("".join(current))
    return out


def changed_pairs(old: list[str], new: list[str]) -> list[tuple[str, list[str], list[str]]]:
    """How to get from old to new: ("same", [], blocks) or ("change", old blocks, new blocks), in order."""
    head = 0
    while head < len(old) and head < len(new) and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < len(old) - head and tail < len(new) - head and old[-1 - tail] == new[-1 - tail]:
        tail += 1
    out: list[tuple[str, list[str], list[str]]] = []
    if head:
        out.append(("same", [], new[:head]))
    old_mid, new_mid = old[head:len(old) - tail], new[head:len(new) - tail]
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_mid, new_mid, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append(("same", [], new_mid[j1:j2]))
        elif i2 - i1 == j2 - j1:
            # The same number on each side: paragraph against paragraph, so that each stays small and cached.
            out.extend(("change", [a], [b]) for a, b in zip(old_mid[i1:i2], new_mid[j1:j2]))
        else:
            out.append(("change", old_mid[i1:i2], new_mid[j1:j2]))
    if tail:
        out.append(("same", [], new[len(new) - tail:]))
    return out


class ChunkDiff:
    def __init__(self, latexdiff, workdir: Path, flags: list[str], cache: Path) -> None:
        """latexdiff(old_path, new_path, cwd, flags) returns the marked-up text, or raises."""
        self.latexdiff = latexdiff
        self.workdir = workdir
        self.flags = flags
        self.cache = cache
        self.ran = 0

    def _key(self, *parts: str) -> Path:
        digest = hashlib.sha256("\0".join([*self.flags, *parts]).encode()).hexdigest()
        return self.cache / f"{digest}.txt"

    def _run(self, key: Path, old_doc: str, new_doc: str) -> str:
        if key.is_file():
            return key.read_text()
        folder = self.workdir / key.stem[:16]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "old.tex").write_text(old_doc)
        (folder / "new.tex").write_text(new_doc)
        self.ran += 1
        out = self.latexdiff(folder / "old.tex", folder / "new.tex", folder, self.flags)
        (folder / "old.tex").unlink(missing_ok=True)
        (folder / "new.tex").unlink(missing_ok=True)
        folder.rmdir()
        partial = key.with_suffix(f".{os.getpid()}.{id(out)}")
        partial.write_text(out)
        partial.replace(key)
        return out

    def preamble(self, old_pre: str, new_pre: str) -> str:
        """latexdiff's preamble for these sources, through \\begin{document}: the new one with its own commands added."""
        old_doc = f"{old_pre}\n{PREAMBLE_PROBE % 'old'}{END_DOCUMENT}\n"
        new_doc = f"{new_pre}\n{PREAMBLE_PROBE % 'new'}{END_DOCUMENT}\n"
        out = self._run(self._key("preamble", old_pre, new_pre), old_doc, new_doc)
        return out[:out.index(BEGIN_DOCUMENT) + len(BEGIN_DOCUMENT)]

    def chunk(self, old: str, new: str) -> str:
        out = self._run(self._key("chunk", old, new), WRAP % old, WRAP % new)
        start = out.index(BEGIN_DOCUMENT) + len(BEGIN_DOCUMENT)
        marked = out[start:out.rindex(END_DOCUMENT)]
        # The wrapper put a line break on each side.
        marked = marked[1:] if marked.startswith("\n") else marked
        return marked[:-1] if marked.endswith("\n") else marked


def diff_sources(old_src: str, new_src: str, latexdiff, workdir: Path, flags: list[str], cache: Path) -> tuple[str, int]:
    """The marked-up document, and how many times latexdiff had to run."""
    cache.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    old_pre, old_body, _ = split_document(old_src)
    new_pre, new_body, new_tail = split_document(new_src)
    diff = ChunkDiff(latexdiff, workdir, flags, cache)
    plan = changed_pairs(blocks(old_body), blocks(new_body))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        preamble = pool.submit(diff.preamble, old_pre, new_pre)
        pieces = [pool.submit(lambda step=step: "".join(step[2]) if step[0] == "same" else diff.chunk("".join(step[1]), "".join(step[2])))
                  for step in plan]
        body = "".join(p.result() for p in pieces)
        head = preamble.result()
    return head + body + new_tail, diff.ran
