"""Reading and saving the project's text files for the editor.

Every save names the version it was based on (a SHA-256 of the file). If the file changed on disk since,
for example because another program edited it, the save is refused instead of overwriting that change.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from .projects import ProjectError, inside

MAX_TEXT_BYTES = 5_000_000
TEXT_SUFFIXES = {
    ".tex", ".bib", ".sty", ".cls", ".bst", ".txt", ".md", ".py", ".sh", ".toml", ".json", ".yaml", ".yml",
    ".csv", ".tsv", ".log", ".cfg", ".ltx", ".dtx", ".ins", ".r", ".m", ".jl", ".ncl", ".f90",
}


class Conflict(Exception):
    def __init__(self, current_hash: str) -> None:
        super().__init__("The file changed on disk since it was opened.")
        self.current_hash = current_hash


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def read_text(root: Path, rel: str) -> dict:
    path = inside(root, rel)
    if not path.is_file():
        raise ProjectError(f"{rel} is not a file.")
    if path.stat().st_size > MAX_TEXT_BYTES:
        raise ProjectError(f"{rel} is larger than {MAX_TEXT_BYTES // 1_000_000} MB, too large to edit here.")
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        raise ProjectError(f"{rel} is not a text file.")
    return {"path": rel, "text": data.decode("utf-8", errors="replace"), "hash": digest(data)}


def write_text(root: Path, rel: str, text: str, base_hash: str | None) -> dict:
    """Saves atomically. base_hash None means a new file, which must not exist yet."""
    path = inside(root, rel)
    if path.is_dir():
        raise ProjectError(f"{rel} is a folder.")
    if path.exists():
        current = digest(path.read_bytes())
        if base_hash != current:
            raise Conflict(current)
        mode = path.stat().st_mode
    else:
        if base_hash is not None:
            raise Conflict("")
        if not path.parent.is_dir():
            raise ProjectError(f"The folder for {rel} does not exist.")
        mode = 0o644
    data = text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".thor")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return {"path": rel, "hash": digest(data)}


def file_hash(root: Path, rel: str) -> str | None:
    path = inside(root, rel)
    return digest(path.read_bytes()) if path.is_file() else None
