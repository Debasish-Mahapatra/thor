"""Where Thor keeps its state, and where it finds command-line tools."""

from __future__ import annotations

import ctypes
import functools
import os
import shutil
import signal
import struct
import subprocess
import threading
import time
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
    biber = shutil.which("biber", path=os.pathsep.join(dirs))
    thin = thin_binary_dir(biber) if biber else None
    return os.pathsep.join([thin, *dirs] if thin else dirs)


# CPU types in a universal binary's header, by what os.uname() calls the machine.
CPU_TYPES = {"arm64": 0x0100000C, "x86_64": 0x01000007}
_thin_lock = threading.Lock()


def slice_of(path: Path, machine: str) -> tuple[int, int] | None:
    """(offset, size) of the slice for machine in a universal binary; None if the file is not one."""
    with path.open("rb") as f:
        head = f.read(8)
        if len(head) < 8:
            return None
        magic, count = struct.unpack(">II", head)
        if magic not in (0xCAFEBABE, 0xCAFEBABF) or not 0 < count < 20:
            return None
        wide = magic == 0xCAFEBABF
        entry = ">iiQQII" if wide else ">iiIII"
        for _ in range(count):
            cputype, _subtype, offset, size, *_ = struct.unpack(entry, f.read(struct.calcsize(entry)))
            if cputype == CPU_TYPES.get(machine):
                return offset, size
    return None


def thin_binary_dir(path: str) -> str | None:
    """A folder in Thor's home with this Mac's slice of a universal program, to search before it.

    MacTeX's biber is a universal binary that unpacks its slice on every run with
    `lipo -extract_family`, an option the lipo in Xcode 27 no longer has, so it fails before reading
    anything. Its slice runs on its own. The slice is cut out here (lipo is not needed), checked with
    `--version` once, and kept until the program changes. None when the program is not universal or its
    slice does not run."""
    real = Path(path).resolve()
    try:
        found = slice_of(real, os.uname().machine)
        info = real.stat()
    except (OSError, struct.error):
        return None
    if not found:
        return None
    folder = app_home() / "tools" / f"{real.name}-{info.st_size}-{info.st_mtime_ns}"
    thin = folder / real.name
    if thin.is_file():
        return str(folder)
    # Checks run in parallel, and the first caller may still be cutting the slice out.
    with _thin_lock:
        if thin.is_file():
            return str(folder)
        if (folder / "failed").exists():
            return None
        folder.mkdir(parents=True, exist_ok=True)
        partial = folder / f".{real.name}.{os.getpid()}"
        offset, size = found
        try:
            with real.open("rb") as src, partial.open("wb") as out:
                src.seek(offset)
                while size > 0:
                    chunk = src.read(min(size, 1 << 20))
                    if not chunk:
                        break
                    out.write(chunk)
                    size -= len(chunk)
            partial.chmod(0o755)
            works = size == 0 and subprocess.run([str(partial), "--version"], capture_output=True, timeout=120,
                                                 stdin=subprocess.DEVNULL).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            works = False
        if not works:
            partial.unlink(missing_ok=True)
            (folder / "failed").touch()
            return None
        partial.replace(thin)
    return str(folder)


@functools.lru_cache(maxsize=1)
def _clonefile():
    try:
        function = ctypes.CDLL(None, use_errno=True).clonefile
    except (OSError, AttributeError):
        return None
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    return function


def clone_file(src: Path, dst: Path) -> None:
    """An instant copy that shares its blocks until either file changes (APFS), or a normal copy elsewhere."""
    dst.unlink(missing_ok=True)
    clone = _clonefile()
    if not clone or clone(os.fsencode(src), os.fsencode(dst), 0) != 0:
        shutil.copy2(src, dst)


def _run_cancellable(args: list[str], timeout: float, env: dict[str, str] | None, cwd: str | Path | None,
                     cancel: threading.Event) -> tuple[int, str]:
    try:
        # Its own process group: latexmk starts LaTeX, and stopping latexmk alone would leave LaTeX running.
        process = subprocess.Popen(args, env=env or tool_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, cwd=cwd, text=True, errors="replace", start_new_session=True)
    except FileNotFoundError:
        return -1, "not found"
    except OSError as e:
        return -1, str(e)
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    reader = threading.Thread(target=lambda: chunks.extend(process.stdout), daemon=True)
    reader.start()
    while process.poll() is None:
        if cancel.wait(0.1) or time.monotonic() > deadline:
            stopped = cancel.is_set()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.wait()
            return (CANCELLED, "cancelled") if stopped else (-1, f"no answer within {timeout:g} s")
    reader.join(5)
    return process.returncode, "".join(chunks).strip()


def which(name: str) -> str | None:
    return shutil.which(name, path=tool_path())


def tool_env(drop: tuple[str, ...] = (), **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["PATH"] = tool_path()
    env.update(extra)
    return env


CANCELLED = -2


def run_tool(args: list[str], timeout: float = 15, env: dict[str, str] | None = None,
             cwd: str | Path | None = None, cancel: threading.Event | None = None) -> tuple[int, str]:
    """Runs a command and returns its exit code and combined output; (-1, reason) if it could not run. If cancel is
    set while it runs, it and everything it started are stopped, and the code is CANCELLED."""
    if cancel is not None:
        return _run_cancellable(args, timeout, env, cwd, cancel)
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
