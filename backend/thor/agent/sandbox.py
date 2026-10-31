"""A sandbox for command-line programs the user adds as models (macOS Seatbelt, through sandbox-exec).

The program may write only in the agent's copy of the paper, in a temporary folder made for it, and in the places
programs keep their own settings and caches. It may not read the files that open other doors, such as ssh keys and
other tools' sign-ins. A paper holds other people's text, and a program told what to do by that text must not be able
to reach the rest of the Mac. Apple marks sandbox-exec as deprecated, but it works and nothing replaces it for this.
The user can turn this off for one command in Settings.
"""

from __future__ import annotations

from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
# Where programs keep their settings and caches, relative to the home folder.
WRITABLE_HOME = ["Library/Caches", ".cache", ".config", ".local/share", ".local/state"]
PRIVATE_FOLDERS = [".ssh", ".aws", ".gnupg", ".kube", "Library/Keychains", "Library/Cookies", ".config/gh",
                   ".config/gcloud"]
PRIVATE_FILES = [".netrc", ".npmrc", ".pypirc", ".codex/auth.json", ".claude.json", ".claude/.credentials.json",
                 ".git-credentials"]


def _quoted(path: Path | str) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def profile(write_root: Path, program: str, home: Path | None = None, network: bool = True) -> str:
    home = (home or Path.home()).resolve()
    allowed = [write_root.resolve(), *(home / rel for rel in WRITABLE_HOME), home / f".{Path(program).name}"]
    lines = ["(version 1)", "(allow default)", "(deny file-write*)"]
    lines += [f"(allow file-write* (subpath {_quoted(p)}))" for p in allowed]
    lines += [f"(allow file-write* (literal {_quoted(p)}))" for p in ("/dev/null", "/dev/tty", "/dev/stdout", "/dev/stderr")]
    lines += [f"(deny file-read* (subpath {_quoted(home / rel)}))" for rel in PRIVATE_FOLDERS]
    lines += [f"(deny file-read* (literal {_quoted(home / rel)}))" for rel in PRIVATE_FILES]
    if not network:
        lines.append("(deny network*)")
    return "\n".join(lines)


def wrap(args: list[str], write_root: Path, network: bool = True) -> list[str]:
    return [SANDBOX_EXEC, "-p", profile(write_root, args[0], network=network), *args]


def available() -> bool:
    return Path(SANDBOX_EXEC).is_file()
