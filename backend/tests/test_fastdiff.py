"""Chunked latexdiff: the same marks as latexdiff on the whole document, for a fraction of the runs."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from thor import fastdiff, tracked

TEXBIN = "/Library/TeX/texbin"


def test_blocks_put_back_together_are_the_body() -> None:
    body = "\nFirst para.\nStill first.\n\nSecond.\n\n\n\\begin{figure}\nInside.\n\nStill inside.\n\\end{figure}\n\nLast"
    found = fastdiff.blocks(body)
    assert "".join(found) == body
    assert any("Inside.\n\nStill inside." in b for b in found)


@pytest.mark.parametrize("text", [
    "\\footnote{One.\n\nTwo.}\n\n",
    "\\[\na\n\nb\n\\]\n\n",
    "\\caption{Long\n\ncaption}\n\n",
])
def test_a_block_never_ends_inside_a_group(text: str) -> None:
    assert fastdiff.blocks(text + "After.\n") == [text, "After.\n"]


def test_comments_and_escapes_do_not_confuse_the_split() -> None:
    assert fastdiff.blocks("A 100\\% { in a comment % {\nmore }\n\nNext.\n") == ["A 100\\% { in a comment % {\nmore }\n\n", "Next.\n"]
    assert fastdiff.blocks("A brace \\{ only.\n\nNext.\n") == ["A brace \\{ only.\n\n", "Next.\n"]


def test_only_changed_blocks_are_compared() -> None:
    old = ["a\n\n", "b\n\n", "c\n\n", "d\n\n"]
    new = ["a\n\n", "B\n\n", "c\n\n", "d\n\n"]
    assert fastdiff.changed_pairs(old, new) == [("same", [], ["a\n\n"]), ("change", ["b\n\n"], ["B\n\n"]), ("same", [], ["c\n\n", "d\n\n"])]


def test_paragraphs_added_and_removed_line_up() -> None:
    plan = fastdiff.changed_pairs(["a\n\n", "b\n\n", "c\n\n"], ["a\n\n", "new\n\n", "b\n\n"])
    assert plan == [("same", [], ["a\n\n"]), ("change", [], ["new\n\n"]), ("same", [], ["b\n\n"]), ("change", ["c\n\n"], [])]


need_latexdiff = pytest.mark.skipif(not shutil.which("latexdiff", path=TEXBIN), reason="MacTeX is not installed")
PREAMBLE = "\\documentclass{article}\n\\usepackage{graphicx}\n\\begin{document}\n"


def paragraphs(n: int) -> list[str]:
    return [f"Paragraph {i} says something about convection and rain, with $x_{i}$ in it and \\cite{{ref{i}}}.\n\n" for i in range(n)]


@pytest.fixture
def tex_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_TOOL_PATH", f"{TEXBIN}:/usr/bin:/bin")


def marks(text: str) -> list[str]:
    """What latexdiff marked, in order, without its comments or line breaks."""
    body = text[text.index("\\begin{document}"):]
    return [re.sub(r"\s+", " ", m).strip() for m in re.findall(r"\\DIF(?:add|del)\{([^{}]*)\}", body)]


@need_latexdiff
def test_the_same_marks_as_latexdiff_on_the_whole_document(tmp_path: Path, tex_on_path: None) -> None:
    old = paragraphs(30)
    new = list(old)
    new[3] = new[3].replace("rain", "snow")
    new[12] = new[12].replace("says something about", "reports on")
    new.insert(20, "A brand new paragraph.\n\n")
    del new[26]
    old_doc, new_doc = PREAMBLE + "".join(old) + "\\end{document}\n", PREAMBLE + "".join(new) + "\\end{document}\n"
    (tmp_path / "old.tex").write_text(old_doc)
    (tmp_path / "new.tex").write_text(new_doc)
    whole = tracked.latexdiff(tmp_path / "old.tex", tmp_path / "new.tex", tmp_path, tracked.DIFF_FLAGS)
    chunked, ran = fastdiff.diff_sources(old_doc, new_doc, tracked.latexdiff, tmp_path / "chunks", tracked.DIFF_FLAGS, tmp_path / "cache")
    # latexdiff on the whole may pair stray punctuation across paragraphs; the words marked are the same.
    words = lambda text: re.findall(r"\w+", " ".join(marks(text)))
    assert words(chunked) == words(whole) and words(chunked)
    # The two have the same commands defined, so the same document compiles.
    preamble = lambda text: [ln for ln in text[:text.index("\\begin{document}")].splitlines() if not ln.startswith("%DIF ")]
    assert preamble(chunked) == preamble(whole)
    assert ran == 5  # four changed places and the preamble


@need_latexdiff
def test_a_paragraph_already_compared_is_not_compared_again(tmp_path: Path, tex_on_path: None) -> None:
    old = paragraphs(20)
    new = list(old)
    new[5] = new[5].replace("rain", "snow")
    make = lambda blocks: PREAMBLE + "".join(blocks) + "\\end{document}\n"
    run = lambda blocks: fastdiff.diff_sources(make(old), make(blocks), tracked.latexdiff, tmp_path / "chunks", tracked.DIFF_FLAGS, tmp_path / "cache")
    first, ran = run(new)
    assert ran == 2 and "snow" in first
    assert run(new) == (first, 0)
    # A second edit runs latexdiff once, for that paragraph; the first answer is kept.
    new[9] = new[9].replace("rain", "hail")
    third, ran = run(new)
    assert ran == 1 and "snow" in third and "hail" in third


@need_latexdiff
def test_a_failure_falls_back_to_the_whole_document(tmp_path: Path, tex_on_path: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fastdiff, "split_document", lambda _src: (_ for _ in ()).throw(ValueError("no")))
    old, new = PREAMBLE + "Old text.\n\\end{document}\n", PREAMBLE + "New text.\n\\end{document}\n"
    assert "\\DIFadd{New" in tracked.mark_up(old, new, tmp_path, tmp_path / "cache")
