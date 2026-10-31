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
# What hide_api_keys took away, for the user's own commands, which may rely on them.
hidden_keys: dict[str, str] = {}


def hide_api_keys() -> None:
    """The Agent SDK passes on this process's whole environment and can only add to it, so the keys have to
    go from here. Thor's own API calls take theirs from the Keychain."""
    for name in API_KEY_VARS:
        if name in os.environ:
            hidden_keys[name] = os.environ.pop(name)


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
    # $THOR_TOOL_PATH says which tools exist (the tests), so the usual places are not looked at.
    for candidate in () if "THOR_TOOL_PATH" in os.environ else CANDIDATES:
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
    # It exits with 1 when signed out, and still prints the JSON.
    _, out = run_tool([path, "auth", "status"], env=env)
    try:
        info = json.loads(out)
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
