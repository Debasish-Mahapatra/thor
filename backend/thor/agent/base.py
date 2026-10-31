"""What a model provider is given for one turn of a conversation, and how it reports back."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..paths import APP_NAME
from .tools import ToolBox

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


class AgentError(Exception):
    """A failure to show the user as it is, so it says what happened and what to do."""


@dataclass
class Turn:
    prompt: str
    instructions: str
    cwd: Path
    box: ToolBox
    model: str
    effort: str
    budget_usd: float
    emit: Callable[[dict], None]
    cancelled: threading.Event
    ask: Callable[[str, str], bool]
    readable: list[Path] = field(default_factory=list)
    # Earlier turns in words, for a provider that has not seen them.
    recap: str = ""
    # Files the user is looking at, for models that cannot read files themselves.
    open_files: list[Path] = field(default_factory=list)
    # The provider's own memory of this conversation; it may replace these.
    session: str | None = None
    history: list = field(default_factory=list)
    # The top of the agent's copy, which a sandbox lets it write in; its working folder if unset.
    write_root: Path | None = None
    # Answer quickly, without the model's hidden thinking, where the model allows it.
    fast: bool = False
    # Co-authors' comments, for a model that sees only what it is sent.
    comments: str = ""
    # The model may look but not change anything, and answers through box.submission.
    read_only: bool = False

    def text(self, chunk: str) -> None:
        if chunk:
            self.emit({"type": "text", "text": chunk})

    def activity(self, summary: str, ident: str = "") -> None:
        self.emit({"type": "activity", "id": ident, "summary": summary})

    def activity_done(self, ident: str, ok: bool = True, detail: str = "") -> None:
        self.emit({"type": "activity_done", "id": ident, "ok": ok, "detail": detail})

    def usage(self, input_tokens: int, output_tokens: int, cost_usd: float | None = None) -> None:
        self.emit({"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost_usd})


def read_if_exists(path: Path, limit: int = 40_000) -> str:
    try:
        return path.read_text(errors="replace")[:limit]
    except OSError:
        return ""


def instructions(name: str, main: str, engine: str, how_to_compile: str, standing: str, style: str,
                 readable: list[Path]) -> str:
    extra = "".join([
        f"\n\nThe results and scripts behind the paper are in these folders, which you can read but not change:\n"
        + "\n".join(f"- {p}" for p in readable) if readable else "",
        f"\n\nThe user's standing instructions for this paper:\n{standing}" if standing else "",
        f"\n\nThe user's writing and style rules:\n{style}" if style else "",
    ])
    return f"""You are the agent in {APP_NAME}, a LaTeX editor for scientists, helping with the paper "{name}". The main file is {main}, compiled with {engine}.

Your working folder is a private copy of the paper. What you change there waits for the user, who accepts or rejects each edit. So make the edits you are asked for directly in the files; do not ask permission first, and do not paste replacement text into the chat instead. Nothing you do reaches the user's real files until they accept it. If the user only asks a question, answer it and change nothing.

{how_to_compile}

How to edit:
- Change as little as the task needs. Keep the author's words, voice and line breaks everywhere else, so each edit is easy to review. Do not rewrap paragraphs or tidy what you were not asked about.
- Fix LaTeX errors at their cause. Do not hide them by commenting out text, removing citations or adding \\nonstopmode.
- After editing, compile, and fix anything your edits broke.
- Never change a number, result or scientific claim unless asked. If a number looks wrong, say so.
- Never invent a citation. Only cite works you have confirmed exist, and say plainly when you could not confirm one.

In the chat, be brief: say what you changed and why, and what you could not do. Write plainly, and do not repeat what the edits already show.{extra}"""
