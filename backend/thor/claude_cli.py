"""Finds the Claude Code command-line tool and checks that it is signed in.

With the "Claude plan" sign-in method, the agent runs this installed `claude`, which uses the Claude
plan the user signed in with, so the app needs no API key of its own.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from .paths import run_tool, tool_env, which

# Where the installers put `claude`, checked before PATH because an app opened from Finder has a short PATH.
CANDIDATES = [
    "~/.local/bin/claude",
    "~/.claude/local/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    "~/.npm-global/bin/claude",
    "~/.bun/bin/claude",
    "~/.volta/bin/claude",
]

# The agent's environment drops these in Claude-plan mode, so a key in the shell can't take over the sign-in.
API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


@dataclass
class ClaudeStatus:
    path: str | None = None
    version: str | None = None
    # None when this version of Claude Code cannot report it.
    logged_in: bool | None = None
    auth_method: str | None = None
    email: str | None = None
    plan: str | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def locate(custom: str | None = None) -> str | None:
    if custom:
        path = os.path.expanduser(custom.strip())
        return path if os.access(path, os.X_OK) and os.path.isfile(path) else None
    for candidate in CANDIDATES:
        path = os.path.expanduser(candidate)
        if os.access(path, os.X_OK) and os.path.isfile(path):
            return path
    return which("claude")


def status(custom: str | None = None) -> ClaudeStatus:
    path = locate(custom)
    if not path:
        where = f"at {custom}" if custom else "in the usual places or on PATH"
        return ClaudeStatus(detail=f"Claude Code was not found {where}.")
    env = tool_env(drop=API_KEY_VARS)
    code, out = run_tool([path, "--version"], env=env)
    version = out.split()[0] if code == 0 and out else None
    code, out = run_tool([path, "auth", "status"], env=env)
    try:
        info = json.loads(out) if code == 0 else None
    except json.JSONDecodeError:
        info = None
    if not isinstance(info, dict):
        return ClaudeStatus(path=path, version=version, detail="This Claude Code cannot report its sign-in.")
    logged_in = bool(info.get("loggedIn"))
    return ClaudeStatus(
        path=path,
        version=version,
        logged_in=logged_in,
        auth_method=info.get("authMethod"),
        email=info.get("email"),
        plan=info.get("subscriptionType"),
        detail="" if logged_in else "Claude Code is not signed in. Run `claude` in Terminal and sign in.",
    )
