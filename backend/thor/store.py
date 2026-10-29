"""The app's own state in SQLite: for now, the projects opened recently."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .paths import app_home

SCHEMA = """
create table if not exists projects (
    id text primary key,
    root text not null unique,
    name text not null,
    last_opened real not null
);
create table if not exists revision_state (
    project_id text primary key,
    active integer,
    updated real not null
);
"""


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_home() / "thor.db"
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def remember_project(self, project_id: str, root: str, name: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "insert into projects (id, root, name, last_opened) values (?, ?, ?, ?) "
                "on conflict(id) do update set name = excluded.name, last_opened = excluded.last_opened",
                (project_id, root, name, time.time()),
            )

    def project_root(self, project_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute("select root from projects where id = ?", (project_id,)).fetchone()
        return row["root"] if row else None

    def recent_projects(self, limit: int = 12) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("select * from projects order by last_opened desc limit ?", (limit,)).fetchall()
        return [{**dict(r), "exists": Path(r["root"]).is_dir()} for r in rows]

    def active_revision(self, project_id: str) -> int | None:
        with self._connect() as db:
            row = db.execute("select active from revision_state where project_id = ?", (project_id,)).fetchone()
        return row["active"] if row else None

    def set_active_revision(self, project_id: str, number: int | None) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "insert into revision_state (project_id, active, updated) values (?, ?, ?) "
                "on conflict(project_id) do update set active = excluded.active, updated = excluded.updated",
                (project_id, number, time.time()),
            )

    def forget_project(self, project_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("delete from projects where id = ?", (project_id,))
