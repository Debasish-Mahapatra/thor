"""Where Thor keeps its state, and where it finds command-line tools."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

# The display name. "Thor" is a working name; change it here, in ui/src/brand.tsx and in shell/Info.plist.
APP_NAME = "Thor"

# An app opened from Finder gets a minimal PATH, so these are searched as well as the login shell's PATH.
EXTRA_DIRS = ["/Library/TeX/texbin", "/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "/usr/bin", "/bin"]


def app_home() -> Path:
    """~/Library/Application Support/Thor, or $THOR_HOME (used by the tests)."""
    override = os.environ.get("THOR_HOME")
    home = Path(override) if override else Path.home() / "Library" / "Application Support" / APP_NAME
    home.mkdir(parents=True, exist_ok=True)
    return home


@functools.lru_cache(maxsize=1)
def login_shell_path() -> str:
    shell = os.environ.get("SHELL", "/bin/zsh")
    try:
        done = subprocess.run([shell, "-l", "-c", 'printf %s "$PATH"'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def tool_path() -> str:
    """The PATH used for every subprocess. $THOR_TOOL_PATH replaces it entirely (used by the tests)."""
    override = os.environ.get("THOR_TOOL_PATH")
    if override is not None:
        return override
    dirs: list[str] = []
    for chunk in (os.environ.get("PATH", ""), login_shell_path(), os.pathsep.join(EXTRA_DIRS)):
        for d in chunk.split(os.pathsep):
            d = os.path.expanduser(d)
            if d and d not in dirs:
                dirs.append(d)
    return os.pathsep.join(dirs)


def which(name: str) -> str | None:
    return shutil.which(name, path=tool_path())


def tool_env(drop: tuple[str, ...] = (), **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["PATH"] = tool_path()
    env.update(extra)
    return env


def run_tool(args: list[str], timeout: float = 15, env: dict[str, str] | None = None,
             cwd: str | Path | None = None) -> tuple[int, str]:
    """Runs a command and returns its exit code and combined output; (-1, reason) if it could not run."""
    try:
        done = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, env=env or tool_env(), stdin=subprocess.DEVNULL,
            cwd=cwd, errors="replace",
        )
    except FileNotFoundError:
        return -1, "not found"
    except subprocess.TimeoutExpired:
        return -1, f"no answer within {timeout:g} s"
    except OSError as e:
        return -1, str(e)
    return done.returncode, (done.stdout + done.stderr).strip()
