from __future__ import annotations

from pathlib import Path

import pytest

from thor import files
from thor.projects import ProjectError


def test_read_and_save(tmp_path: Path) -> None:
    tmp_path = tmp_path / "project"
    tmp_path.mkdir()
    (tmp_path / "main.tex").write_text("Hello\n")
    (tmp_path / "main.tex").chmod(0o640)
    opened = files.read_text(tmp_path, "main.tex")
    assert opened["text"] == "Hello\n" and opened["hash"] == files.digest(b"Hello\n")
    saved = files.write_text(tmp_path, "main.tex", "Hello, world\n", opened["hash"])
    assert (tmp_path / "main.tex").read_text() == "Hello, world\n"
    assert saved["hash"] == files.digest(b"Hello, world\n")
    assert (tmp_path / "main.tex").stat().st_mode & 0o777 == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["main.tex"]  # no temporary file left behind


def test_changed_on_disk_is_not_overwritten(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text("v1")
    base = files.read_text(tmp_path, "main.tex")["hash"]
    (tmp_path / "main.tex").write_text("v2 from another program")
    with pytest.raises(files.Conflict) as e:
        files.write_text(tmp_path, "main.tex", "my edit", base)
    assert e.value.current_hash == files.digest(b"v2 from another program")
    assert (tmp_path / "main.tex").read_text() == "v2 from another program"


def test_new_files(tmp_path: Path) -> None:
    files.write_text(tmp_path, "notes.tex", "new", None)
    assert (tmp_path / "notes.tex").read_text() == "new"
    with pytest.raises(files.Conflict):
        files.write_text(tmp_path, "notes.tex", "again", None)
    with pytest.raises(ProjectError, match="does not exist"):
        files.write_text(tmp_path, "missing/x.tex", "x", None)


def test_refusals(tmp_path: Path) -> None:
    (tmp_path / "fig.pdf").write_bytes(b"%PDF\0\0binary")
    with pytest.raises(ProjectError, match="not a text file"):
        files.read_text(tmp_path, "fig.pdf")
    with pytest.raises(ProjectError, match="outside"):
        files.read_text(tmp_path, "../etc/passwd")
    with pytest.raises(ProjectError, match="not a file"):
        files.read_text(tmp_path, "nothing.tex")
    (tmp_path / "big.tex").write_bytes(b"x" * (files.MAX_TEXT_BYTES + 1))
    with pytest.raises(ProjectError, match="too large"):
        files.read_text(tmp_path, "big.tex")
