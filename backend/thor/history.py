"""Version history of the project's text files, kept in the app's folder, never in the project.

A version is recorded when a file is opened or saved, so edits made by other programs are kept too.
Saves less than two minutes apart replace each other, unless a version is named (revision events), so
typing does not fill the history. Contents are stored once each, compressed, by hash.
"""

from __future__ import annotations

import difflib
import gzip
import hashlib
import sqlite3
import threading
import time
from pathlib import Path

from .paths import app_home

COALESCE_SECONDS = 120

SCHEMA = """
create table if not exists versions (
    id integer primary key autoincrement,
    project_id text not null,
    path text not null,
    hash text not null,
    time real not null,
    label text
);
create index if not exists versions_by_file on versions (project_id, path, time);
"""


class History:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or app_home() / "history"
        self.home.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.home / "history.db")
        db.row_factory = sqlite3.Row
        return db

    def _blob(self, digest: str) -> Path:
        return self.home / "blobs" / digest[:2] / f"{digest}.gz"

    def record(self, project_id: str, path: str, data: bytes, label: str | None = None,
               now: float | None = None) -> int:
        """Records this content of the file; returns the version id."""
        now = time.time() if now is None else now
        digest = hashlib.sha256(data).hexdigest()
        blob = self._blob(digest)
        with self._lock, self._connect() as db:
            if not blob.exists():
                blob.parent.mkdir(parents=True, exist_ok=True)
                blob.write_bytes(gzip.compress(data))
            last = db.execute("select * from versions where project_id = ? and path = ? order by time desc, id desc "
                              "limit 1", (project_id, path)).fetchone()
            if last and last["hash"] == digest:
                if label and not last["label"]:
                    db.execute("update versions set label = ? where id = ?", (label, last["id"]))
                return last["id"]
            if last and not last["label"] and not label and now - last["time"] < COALESCE_SECONDS:
                db.execute("update versions set hash = ?, time = ? where id = ?", (digest, now, last["id"]))
                return last["id"]
            cur = db.execute("insert into versions (project_id, path, hash, time, label) values (?, ?, ?, ?, ?)",
                             (project_id, path, digest, now, label))
            return cur.lastrowid

    def versions(self, project_id: str, path: str, limit: int = 200) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("select id, hash, time, label from versions where project_id = ? and path = ? "
                              "order by time desc, id desc limit ?", (project_id, path, limit)).fetchall()
        return [dict(r) for r in rows]

    def text(self, project_id: str, version_id: int) -> tuple[str, str]:
        """The path and text of a version."""
        with self._connect() as db:
            row = db.execute("select path, hash from versions where id = ? and project_id = ?",
                             (version_id, project_id)).fetchone()
        if not row:
            raise KeyError(version_id)
        return row["path"], gzip.decompress(self._blob(row["hash"]).read_bytes()).decode("utf-8", errors="replace")


def unified_diff(old: str, new: str, context: int = 3) -> list[dict]:
    """The diff from old to new as lines tagged same, removed, added or hunk."""
    lines = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=context):
        if line.startswith(("---", "+++")):
            continue
        kind = {"@": "hunk", "-": "removed", "+": "added"}.get(line[:1], "same")
        lines.append({"kind": kind, "text": line[1:] if kind != "hunk" else line})
    return lines
