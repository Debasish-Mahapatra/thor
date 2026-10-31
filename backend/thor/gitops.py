"""Git for the paper's own repository, only when the user presses a button.

Commits are authored by the user's own git settings, with nothing added to the message. The agent cannot reach any of
this: its copy of the paper has no .git folder, and no tool of its calls these.
"""

from __future__ import annotations

import re
from pathlib import Path

from .paths import run_tool, tool_env, which

SECONDS = 30
NETWORK_SECONDS = 120
MAX_FILES = 400


class GitError(Exception):
    """Told to the user as it is."""


def _git(root: Path, *args: str, timeout: float = SECONDS) -> tuple[int, str]:
    # Never wait for a password nobody can type: the user is told to sign in through Terminal instead.
    env = tool_env(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/usr/bin/true", SSH_ASKPASS="/usr/bin/true",
                   GIT_SSH_COMMAND="ssh -o BatchMode=yes", LC_ALL="C")
    return run_tool(["git", "-C", str(root), *args], timeout=timeout, env=env)


def _must(root: Path, *args: str, timeout: float = SECONDS) -> str:
    code, out = _git(root, *args, timeout=timeout)
    if code != 0:
        raise GitError(explain(args[0], out))
    return out


def explain(command: str, output: str) -> str:
    words = output.lower()
    if "could not read username" in words or "terminal prompts disabled" in words or "permission denied (publickey)" in words \
            or "authentication failed" in words:
        return (f"Git could not sign in to the remote. Run `git {command}` once in Terminal to sign in, then try here. "
                f"Git said: {output[:300]}")
    if "not possible to fast-forward" in words or "diverging branches" in words:
        return "The remote has commits this copy lacks, and this copy has commits it lacks. Merge them in Terminal first."
    if "non-fast-forward" in words or "fetch first" in words or "[rejected]" in words:
        return "The remote has commits this copy lacks. Pull first."
    if "would be overwritten" in words:
        return f"Pulling would overwrite your uncommitted changes. Commit them first. Git said: {output[:300]}"
    if "no upstream" in words or "no tracking information" in words or "no configured push destination" in words:
        return f"This branch has no remote branch to {command} with. Set one up in Terminal. Git said: {output[:300]}"
    return f"git {command} failed: {output[:600] or 'it gave no reason'}"


def status(root: Path) -> dict:
    if not which("git"):
        return {"available": False, "repo": False, "detail": "git was not found. Install Apple's command-line tools."}
    code, top = _git(root, "rev-parse", "--show-toplevel")
    if code != 0:
        return {"available": True, "repo": False, "detail": "This paper's folder is not in a git repository."}
    top_path = Path(top.strip())
    _, name = _git(root, "config", "user.name")
    _, email = _git(root, "config", "user.email")
    _, branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _, upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    has_upstream = bool(upstream.strip()) and "fatal" not in upstream and "@{upstream}" not in upstream
    ahead = behind = 0
    if has_upstream:
        code, counts = _git(root, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
        found = re.match(r"(\d+)\s+(\d+)", counts)
        if code == 0 and found:
            ahead, behind = int(found.group(1)), int(found.group(2))
    # -z keeps names with spaces whole, and only this paper's folder is listed, even in a repository that holds more.
    # Version 2 of the format, because the first starts a line with a space, which run_tool would take for padding.
    code, listing = _git(root, "status", "--porcelain=v2", "-z", "--", ".")
    changed = parse_status(listing) if code == 0 else []
    _, last = _git(root, "log", "-1", "--format=%h %s")
    return {"available": True, "repo": True, "top": str(top_path), "branch": branch.strip(), "upstream": upstream.strip() if has_upstream else None,
            "ahead": ahead, "behind": behind, "files": changed, "author": name.strip(), "email": email.strip(),
            "last_commit": last.strip() if not last.startswith("fatal") else ""}


def parse_status(listing: str) -> list[dict]:
    """Paths are relative to the top of the repository, however deep the paper's folder is."""
    changed = []
    parts = iter(listing.split("\0"))
    for entry in parts:
        if len(changed) >= MAX_FILES:
            break
        kind = entry[:1]
        if kind == "?":
            changed.append({"path": entry[2:], "state": "new"})
        elif kind == "1":
            fields = entry.split(" ", 8)
            changed.append({"path": fields[8], "state": _state_word(fields[1])})
        elif kind == "2":
            fields = entry.split(" ", 9)
            next(parts, None)  # The old name of a renamed file.
            changed.append({"path": fields[9], "state": "renamed"})
        elif kind == "u":
            changed.append({"path": entry.split(" ", 10)[10], "state": "conflict"})
    return changed


def _state_word(code: str) -> str:
    if "D" in code:
        return "deleted"
    if "A" in code:
        return "added"
    return "changed"


def commit(root: Path, message: str, paths: list[str]) -> dict:
    """Commits the named files only. Whatever else the user has staged in Terminal stays staged and out of it."""
    message = message.strip()
    if not message:
        raise GitError("Write a commit message first.")
    state = status(root)
    if not state["repo"]:
        raise GitError(state["detail"])
    if not state["author"] or not state["email"]:
        raise GitError("Git does not know who you are, so it cannot sign this commit as you. Run these in Terminal:\n"
                       'git config --global user.name "Your Name"\ngit config --global user.email "you@example.com"')
    if not paths:
        raise GitError("Choose at least one file to commit.")
    known = {f["path"] for f in state["files"]}
    unknown = [p for p in paths if p not in known]
    if unknown:
        raise GitError(f"These have no changes to commit: {', '.join(unknown[:5])}.")
    top = Path(state["top"])
    _must(top, "add", "-A", "--", *paths)
    _must(top, "commit", "-m", message, "--only", "--", *paths)
    return status(root)


def pull(root: Path) -> dict:
    _must(root, "pull", "--ff-only", timeout=NETWORK_SECONDS)
    return status(root)


def push(root: Path) -> dict:
    _must(root, "push", timeout=NETWORK_SECONDS)
    return status(root)
