from __future__ import annotations

from pathlib import Path

import pytest

from thor.history import COALESCE_SECONDS, History, unified_diff


@pytest.fixture
def history(tmp_path: Path) -> History:
    return History(tmp_path / "history")


def test_saves_close_together_replace_each_other(history: History) -> None:
    first = history.record("p", "main.tex", b"v1", now=1000)
    assert history.record("p", "main.tex", b"v2", now=1030) == first
    assert history.record("p", "main.tex", b"v2", now=1040) == first  # same content: nothing new
    later = history.record("p", "main.tex", b"v3", now=1000 + COALESCE_SECONDS + 31)
    assert later != first
    assert [v["id"] for v in history.versions("p", "main.tex")] == [later, first]
    assert history.text("p", first) == ("main.tex", "v2")


def test_named_versions_are_kept(history: History) -> None:
    named = history.record("p", "main.tex", b"sent to a co-author", label="Revision 2", now=1000)
    after = history.record("p", "main.tex", b"next edit", now=1010)
    assert after != named
    assert history.text("p", named)[1] == "sent to a co-author"
    # Naming an existing version adds the name without a new entry.
    again = history.record("p", "main.tex", b"next edit", label="Revision 3 started", now=1020)
    assert again == after and history.versions("p", "main.tex")[0]["label"] == "Revision 3 started"


def test_projects_and_files_are_separate(history: History) -> None:
    a = history.record("p", "a.tex", b"x", now=1)
    history.record("p", "b.tex", b"x", now=2)
    history.record("q", "a.tex", b"y", now=3)
    assert [v["id"] for v in history.versions("p", "a.tex")] == [a]
    with pytest.raises(KeyError):
        history.text("q", a)


def test_unified_diff() -> None:
    lines = unified_diff("a\nb\nc\n", "a\nB\nc\n")
    assert [line["kind"] for line in lines] == ["hunk", "same", "removed", "added", "same"]
    assert lines[2]["text"] == "b" and lines[3]["text"] == "B"
