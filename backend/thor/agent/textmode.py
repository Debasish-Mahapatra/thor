"""Edits from a model that can only write text: a command-line program, or a local model without tool calling.

It is given the files to work on and answers with edit blocks, which are applied to the agent's copy the way a
tool call would be.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .base import Turn, read_if_exists
from .staging import LATEXMK_RC
from .tools import ToolError

MAX_FILE_CHARS = 200_000
RULES = """To change a file, write one or more edit blocks exactly like this, with the file's path after SEARCH:

<<<<<<< SEARCH main.tex
the exact lines to find
=======
the lines to put there
>>>>>>> REPLACE

The search text must match the file exactly and appear only once, so include enough lines. To create a file, leave the search part empty. Outside the blocks, say briefly what you changed."""
OPEN = re.compile(r"^<{5,}\s*SEARCH[ \t]+(.+?)\s*$")
MIDDLE = re.compile(r"^={5,}\s*$")
CLOSE = re.compile(r"^>{5,}\s*REPLACE\s*$")


@dataclass
class Edit:
    path: str
    search: str
    replace: str


class BlockFilter:
    """Takes a model's text line by line; passes on what is meant for reading and collects the edit blocks."""

    def __init__(self) -> None:
        self.edits: list[Edit] = []
        self._path: str | None = None
        self._search: list[str] = []
        self._replace: list[str] = []
        self._in_replace = False

    def feed(self, line: str) -> str:
        bare = line.rstrip("\n")
        if self._path is None:
            opened = OPEN.match(bare)
            if opened:
                self._path, self._search, self._replace, self._in_replace = opened.group(1), [], [], False
                return ""
            return line
        if not self._in_replace and MIDDLE.match(bare):
            self._in_replace = True
        elif self._in_replace and CLOSE.match(bare):
            self.edits.append(Edit(self._path, "".join(self._search), "".join(self._replace)))
            self._path = None
        else:
            (self._replace if self._in_replace else self._search).append(line if line.endswith("\n") else f"{line}\n")
        return ""


def apply_edits(edits: list[Edit], work: Path) -> tuple[list[str], list[str]]:
    """Applies the edits to the copy; returns what was done and what could not be done, in words."""
    done, failed = [], []
    root = work.resolve()
    for edit in edits:
        target = (work / edit.path).resolve()
        if root not in target.parents or target.name in LATEXMK_RC or any(p.startswith(".") for p in target.relative_to(root).parts):
            failed.append(f"{edit.path}: not a file the agent may change")
            continue
        if not edit.search.strip():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit.replace)
            done.append(edit.path)
            continue
        if not target.is_file():
            failed.append(f"{edit.path}: no such file")
            continue
        text = target.read_text(errors="replace")
        search = edit.search
        if text.count(search) == 0 and search.endswith("\n") and text.endswith(search[:-1]):
            search = search[:-1]
        count = text.count(search)
        if count != 1:
            failed.append(f"{edit.path}: the search text was found {count} times, not once")
            continue
        target.write_text(text.replace(search, edit.replace))
        done.append(edit.path)
    return done, failed


JSON_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


def rules_for(turn: Turn) -> str:
    """How this model hands in its work when it cannot call tools."""
    if not turn.box.submission:
        return RULES
    schema = json.dumps(turn.box.submission.spec["parameters"], indent=1)
    return ("You cannot call tools here, whatever else you were told, so do not try to. Change no files. Hand in your "
            f"work as one JSON object in a ```json block, matching this schema, and say anything else briefly outside the "
            f"block:\n{schema}")


def take_submission(turn: Turn, answer: str) -> None:
    """Finds the JSON a model without tools answered with. Without one, the turn has handed in nothing."""
    submission = turn.box.submission
    blocks = JSON_BLOCK.findall(answer) or [answer[answer.find("{"):answer.rfind("}") + 1]]
    for text in reversed(blocks):
        try:
            found = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(found, dict):
            try:
                submission.check(found)
            except ToolError as e:
                turn.emit({"type": "notice", "text": f"The model's answer had a problem: {e}"})
                return
            submission.received = found
            return


def full_prompt(turn: Turn) -> str:
    """Everything a model with no tools needs, since it can look at nothing itself."""
    shown = []
    for path in dict.fromkeys(turn.open_files):
        content = read_if_exists(path, MAX_FILE_CHARS)
        if content:
            shown.append(f"--- {path.relative_to(turn.cwd).as_posix()}\n{content}\n--- end of file")
    files = "\n\n".join(shown)
    parts = [turn.recap and f"The conversation so far:\n{turn.recap}", files and f"Files:\n\n{files}",
             turn.comments and f"Co-authors' comments:\n{turn.comments}", turn.prompt]
    return "\n\n".join(p for p in parts if p)


def report(turn: Turn, filter_: BlockFilter) -> None:
    done, failed = apply_edits(filter_.edits, turn.cwd)
    if done:
        turn.box.wrote = True
        turn.activity(f"Applied {len(done)} edit{'s' if len(done) != 1 else ''} to its copy of the paper")
    for problem in failed:
        turn.emit({"type": "notice", "text": f"An edit could not be applied. {problem}."})
