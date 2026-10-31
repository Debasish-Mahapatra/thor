"""The tracked PDF must mark what the editor marks. latexdiff struck a whole hyphenated word when only part of it changed,
so the PDF said an author had deleted words they had kept."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from thor import tracked
from thor.wordmarks import narrow_marks, word_changes

CASES = json.loads((Path(__file__).parent / "data" / "word_changes.json").read_text())
TEXBIN = "/Library/TeX/texbin"


def pair(old: str, new: str, fl: str = "") -> str:
    return (f"\\DIFdelbegin{fl} \\DIFdel{fl}{{{old}}}\\DIFdelend{fl} "
            f"\\DIFaddbegin{fl} \\DIFadd{fl}{{{new}}}\\DIFaddend{fl} ")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["old"])
def test_the_same_changes_as_the_editor(case: dict) -> None:
    found = [{"from": c.start, "to": c.end, "deleted": c.deleted} for c in word_changes(case["old"], case["new"])]
    assert found == case["changes"]


def test_a_kept_part_of_a_hyphenated_word_is_not_struck() -> None:
    marked = narrow_marks("a " + pair("single-moment scheme ", "single- other words ") + "with")
    assert marked == ("a single-\\DIFdelbegin \\DIFdel{moment scheme}\\DIFdelend "
                      "\\DIFaddbegin \\DIFadd{ other words}\\DIFaddend{} with")


def test_a_kept_space_after_a_mark_survives() -> None:
    # TeX drops a space after \DIFaddend, and this one is the author's.
    assert narrow_marks(pair("two-moment ", "two-layer ") + "scheme") == (
        "two-\\DIFdelbegin \\DIFdel{moment}\\DIFdelend \\,\\DIFaddbegin \\DIFadd{layer}\\DIFaddend{} scheme")


def test_a_word_with_a_letter_added_marks_only_the_letter() -> None:
    assert narrow_marks(pair("model ", "models ") + "hold") == "model\\DIFaddbegin \\DIFadd{s}\\DIFaddend{} hold"


def test_words_with_nothing_in_common_stay_as_latexdiff_marked_them() -> None:
    same = pair("Extreme ", "small ") + "rain"
    assert narrow_marks(same) == "\\DIFdelbegin \\DIFdel{Extreme}\\DIFdelend \\,\\DIFaddbegin \\DIFadd{small}\\DIFaddend{} rain"
    whole = pair("Extreme", "small") + "rain"
    assert narrow_marks(whole) == whole


@pytest.mark.parametrize("old,new", [
    ("a \\emph{b} ", "a \\emph{c} "),
    ("the $x$ value ", "the $y$ value "),
    ("\\cite{a} here ", "\\cite{b} here "),
    ("a {group} ", "a {other} "),
])
def test_anything_with_commands_or_math_is_left_to_latexdiff(old: str, new: str) -> None:
    text = pair(old, new)
    assert narrow_marks(text) == text


def test_a_blank_line_is_never_put_into_a_mark() -> None:
    text = pair("one\n\ntwo ", "one\n\nthree ")
    assert narrow_marks(text) == text


def test_an_escaped_sign_stays_whole() -> None:
    marked = narrow_marks(pair("about 12.3\\% more ", "about 13.3\\% more ") + "rain")
    assert marked.count("\\%") == 1 and "\\DIFdel{2}" in marked and "\\DIFadd{3}" in marked and marked.endswith(".3\\% more rain")


def test_figure_captions_keep_their_own_mark_commands() -> None:
    marked = narrow_marks(pair("two-moment ", "two-layer ", "FL") + "x")
    assert marked == ("two-\\DIFdelbeginFL \\DIFdelFL{moment}\\DIFdelendFL \\,\\DIFaddbeginFL \\DIFaddFL{layer}\\DIFaddendFL{} x")


def test_everything_else_in_the_document_is_untouched() -> None:
    text = "\\providecommand{\\DIFdelbegin}{}\n\\DIFaddbegin \\DIFadd{only added }\\DIFaddend plain \\DIFdelbegin \\DIFdel{only gone }\\DIFdelend x"
    assert narrow_marks(text) == text


DOCUMENT = r"""\documentclass{article}
\begin{document}
We compare a single-moment control, %s with prognostic graupel. The %s holds, and the %s result stands.
\end{document}
"""


def test_a_real_tracked_pdf_keeps_what_was_not_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_TOOL_PATH", f"{TEXBIN}:/usr/bin:/bin")
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "main.tex").write_text(DOCUMENT % ("a single-moment scheme", "model", "well-known"))
    (new / "main.tex").write_text(DOCUMENT % ("a single- other words", "models", "well-established"))
    result = tracked.build_tracked(old, "main.tex", new, "main.tex", tmp_path / "out", macro="", folders=[])
    assert result.ok
    text = (tmp_path / "out" / "tracked.tex").read_text()
    assert "\\DIFdel{moment scheme}" in text and "single-moment scheme" not in text.replace("single-moment control", "")
    assert "\\DIFadd{s}" in text and "\\DIFdel{known}" in text
    # What was kept is in the PDF once, with its spaces: the words do not run together.
    pdf = next((tmp_path / "out").glob("**/tracked.pdf"))
    with pymupdf.open(pdf) as doc:
        words = " ".join(doc[0].get_text().split())
    assert "single-moment control" in words and "prognostic graupel" in words and "holds, and the well-" in words
