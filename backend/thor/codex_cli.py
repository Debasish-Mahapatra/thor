"""Finds the Codex command-line tool and checks that it is signed in.

With the "Codex" provider the agent runs this installed `codex`, which uses the ChatGPT plan the user signed in
with, so the app needs no API key of its own.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import run_tool, tool_env, which

CANDIDATES = [
    "~/.local/bin/codex",
    # The ChatGPT app carries its own copy, signed in with the same account.
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
    "~/.npm-global/bin/codex",
    "~/.bun/bin/codex",
    "~/.volta/bin/codex",
]

# As with Claude Code: a key in the shell must not take over the sign-in.
API_KEY_VARS = ("OPENAI_API_KEY", "CODEX_API_KEY")


@dataclass
class CodexStatus:
    path: str | None = None
    version: str | None = None
    logged_in: bool | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def locate(custom: str | None = None) -> str | None:
    if custom:
        path = os.path.expanduser(custom.strip())
        return path if os.access(path, os.X_OK) and os.path.isfile(path) else None
    # $THOR_TOOL_PATH says which tools exist (the tests), so the usual places are not looked at.
    for candidate in () if "THOR_TOOL_PATH" in os.environ else CANDIDATES:
        path = os.path.expanduser(candidate)
        if os.access(path, os.X_OK) and os.path.isfile(path):
            return path
    return which("codex")


def status(custom: str | None = None) -> CodexStatus:
    path = locate(custom)
    if not path:
        where = f"at {custom}" if custom else "in the usual places or on PATH"
        return CodexStatus(detail=f"Codex was not found {where}.")
    env = tool_env(drop=API_KEY_VARS)
    code, out = run_tool([path, "--version"], env=env)
    version = out.split()[-1] if code == 0 and out else None
    code, out = run_tool([path, "login", "status"], env=env)
    if code == -1:
        return CodexStatus(path=path, version=version, detail="This Codex cannot report its sign-in.")
    if code != 0:
        return CodexStatus(path=path, version=version, logged_in=False,
                           detail="Codex is not signed in. Run `codex login` in Terminal and sign in.")
    return CodexStatus(path=path, version=version, logged_in=True, detail=out.splitlines()[0] if out else "Signed in.")


def models() -> list[dict]:
    """What this account can use, from the list Codex keeps up to date itself. Empty if there is none yet."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        found = json.loads((home / "models_cache.json").read_text()).get("models", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []
    listed = [m for m in found if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
    return [{"id": m["slug"], "name": m.get("display_name") or m["slug"]}
            for m in sorted(listed, key=lambda m: m.get("priority", 999))]
