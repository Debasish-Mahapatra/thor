"""Comments from an annotated PDF made in the test: every annotation type, with pen strokes, replies, two
account names for one person, and a baseline in which the text moved down a line."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pymupdf
import pytest

from thor import comments

LINES = [
    "Thunder Over Asgard: How Often Thor's Hammer Strikes",
    "Lightning follows deep convection over land in the afternoon.",
    "The very very old network recorded strikes across the realm.",
    "We count strikes per square league with the old phrase here.",
    "Strikes peak every afternoon when the hammer is thrown.",
    "Rain and lightning rarely arrive together over the mountains.",
    "Farmers and herders are most exposed during the harvest.",
    "Workers in construction are struck more often than sailors.",
    "Fitted cycles peak earlier for lightning than for rain.",
    "The giants dispute these results but offer no data.",
    "Mortality has declined where shelters are widely available.",
    "Warnings reach villages too late in the northern valleys.",
]


def paper(path: Path, shift: int = 0) -> None:
    """A paper with lineno-style margin numbers; shift adds lines at the top so the rest moves down."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    lines = [f"A line added later number {i + 1} pushes the text down." for i in range(shift)] + LINES
    y = 100
    for n, text in enumerate(lines, 1):
        page.insert_text((58, y), str(n), fontsize=7)
        page.insert_text((72, y), text, fontsize=10)
        y += 16
    si = doc.new_page(width=595, height=842)
    si.insert_text((72, 90), "Supplementary Information", fontsize=14)
    si.insert_text((72, 150), "Supplementary Note 1", fontsize=11)
    si.insert_text((72, 172), "The detection efficiency of the network varies by region.", fontsize=10)
    si.insert_text((72, 186), "It is lowest where sensors are sparse in the far north.", fontsize=10)
    si.insert_text((72, 260), "Figure S1: Detection efficiency by region, with sensors", fontsize=9)
    si.insert_text((72, 272), "shown as small hammers on the map of the realm.", fontsize=9)
    doc.save(path)


def word_rect(page: pymupdf.Page, phrase: str) -> pymupdf.Rect:
    hits = page.search_for(phrase)
    assert hits, phrase
    return hits[0]


def info(annot: pymupdf.Annot, author: str, content: str = "") -> None:
    annot.set_info(title=author, content=content)
    annot.update()


def annotate(src: Path, out: Path) -> None:
    doc = pymupdf.open(src)
    page, si = doc[0], doc[1]
    info(page.add_highlight_annot(word_rect(page, "deep convection over land")), "Ada", "Add a reference here.")
    highlight = page.annots().__next__()
    reply = page.add_text_annot((560, 40), "I agree, cite the classic paper.")
    info(reply, "ada.l", "I agree, cite the classic paper.")
    doc.xref_set_key(reply.xref, "IRT", f"{highlight.xref} 0 R")
    info(page.add_strikeout_annot(word_rect(page, "very very")), "Ada")  # a deletion request
    strike = page.add_strikeout_annot(word_rect(page, "the old phrase"))
    info(strike, "Ada")
    caret_at = word_rect(page, "the old phrase")
    caret = page.add_caret_annot(pymupdf.Point(caret_at.x1, caret_at.y1))
    info(caret, "Ada", "a new phrase")
    doc.xref_set_key(strike.xref, "IRT", f"{caret.xref} 0 R")
    doc.xref_set_key(strike.xref, "RT", "/Group")
    info(page.add_underline_annot(word_rect(page, "every afternoon")), "Ada")  # marked, no note: unclear
    info(page.add_squiggly_annot(word_rect(page, "rarely arrive together")), "Ada", "Is this true in the rainy season?")
    # PyMuPDF puts a caret's top-left at the point; Acrobat centres it on the gap, on the line.
    lone = word_rect(page, "herders")
    info(page.add_caret_annot(pymupdf.Point(lone.x1 - 9, lone.y0 - 2)), "Ada", "and fishers")
    # A note on a single word, covering it: "outdoor" on "construction".
    target = word_rect(page, "construction")
    note = page.add_text_annot((target.x0 + 2, target.y0 - 4), "outdoor")
    info(note, "Ada", "outdoor")
    # Notes at the top of the page that refer to lines, as people write them.
    info(page.add_text_annot((540, 10), "line 9: say which rain"), "ada.l", "line 9: say which rain")
    info(page.add_text_annot((515, 10), "L11-12: merge these sentences"), "Ada", "L11-12: merge these sentences")
    # One ink annotation, many strokes: an underline (line 9), a dot (line 9), a strike (line 10),
    # a circle (line 11) and an underline (line 12).
    cycles = word_rect(page, "Fitted cycles peak earlier")
    dot = word_rect(page, "lightning than")
    giants = word_rect(page, "giants")
    shelters = word_rect(page, "shelters")
    late = word_rect(page, "too late")
    circle = [(shelters.x0 + (shelters.width / 2 + 3) * (1 + math.cos(t / 10 * math.pi)) - 3,
               shelters.y0 + shelters.height / 2 + (shelters.height / 2 + 3) * math.sin(t / 10 * math.pi))
              for t in range(21)]
    strokes = [
        [(cycles.x0 + i * cycles.width / 9, cycles.y1 + 2) for i in range(10)],
        [(dot.x1 + 1, dot.y1 - 1)],
        [(giants.x0 - 1 + i * (giants.width + 2) / 5, giants.y0 + giants.height * 0.55) for i in range(6)],
        circle,
        [(late.x0 + i * late.width / 9, late.y1 + 2) for i in range(10)],
    ]
    info(page.add_ink_annot(strokes), "Ada")
    info(page.add_freetext_annot(pymupdf.Rect(400, 700, 560, 740), "Shorten the whole page."), "Ada")
    info(si.add_highlight_annot(word_rect(si, "sensors are sparse")), "Ada", "Which region?")
    info(si.add_highlight_annot(word_rect(si, "small hammers")), "Ada", "Use dots instead.")
    doc.save(out)


@pytest.fixture
def pdfs(tmp_path: Path) -> tuple[Path, Path]:
    paper(tmp_path / "paper.pdf")
    annotate(tmp_path / "paper.pdf", tmp_path / "annotated.pdf")
    paper(tmp_path / "baseline.pdf", shift=1)
    return tmp_path / "annotated.pdf", tmp_path / "baseline.pdf"


def by_text(found: list[comments.Comment], text: str) -> comments.Comment:
    return next(c for c in found if text in c.marked or text in c.note or text in c.insert)


def test_every_annotation_type(pdfs: tuple[Path, Path]) -> None:
    found = comments.extract(pdfs[0])
    assert [c.id for c in found] == list(range(1, len(found) + 1))
    kinds = sorted(c.kind for c in found)
    assert kinds.count("pen") == 3 and "deletion" in kinds and "replacement" in kinds and "text box" in kinds

    highlight = by_text(found, "deep convection over land")
    assert (highlight.kind, highlight.page, highlight.line, highlight.note) == ("highlight", 1, 2, "Add a reference here.")
    assert highlight.replies == [{"author": "ada.l", "note": "I agree, cite the classic paper."}]

    deletion = by_text(found, "very very")
    assert (deletion.kind, deletion.line, deletion.note) == ("deletion", 3, "")
    replacement = by_text(found, "the old phrase")
    assert (replacement.kind, replacement.insert, replacement.line) == ("replacement", "a new phrase", 4)
    insertion = by_text(found, "and fishers")
    assert (insertion.kind, insertion.anchor) == ("insertion", "herders")
    assert by_text(found, "rarely arrive together").kind == "squiggly"


def test_pen_strokes_are_classified_and_paired(pdfs: tuple[Path, Path]) -> None:
    found = comments.extract(pdfs[0])
    rain = by_text(found, "say which rain")
    assert rain.kind == "pen" and rain.line == 9
    assert sorted(m["kind"] for m in rain.marks) == ["dot", "underline"]
    assert "Fitted cycles peak earlier" in rain.marked
    merge = by_text(found, "merge these sentences")
    assert (merge.line, merge.line_end) == (11, 12)
    assert sorted(m["kind"] for m in merge.marks) == ["circle", "underline"]
    assert any(m["kind"] == "circle" and "shelters" in m["text"] for m in merge.marks)
    # The strike on line 10 has no note naming its line, and line 9 ends its sentence, so it stands alone.
    strike = next(c for c in found if c.kind == "pen" and any(m["kind"] == "strike" for m in c.marks))
    assert "giants" in strike.marked and strike.line == 10


def test_locations_off_the_numbered_lines(pdfs: tuple[Path, Path]) -> None:
    found = comments.extract(pdfs[0])
    assert by_text(found, "sensors are sparse").element == "Supplementary Note 1"
    assert by_text(found, "small hammers").element == "Figure S1 caption"


def test_unclear_comments_get_a_question(pdfs: tuple[Path, Path]) -> None:
    found = comments.extract(pdfs[0])
    outdoor = by_text(found, "outdoor")
    assert outdoor.anchor == "construction"
    assert outdoor.question.startswith('A note of only "outdoor" on "construction"')
    assert by_text(found, "every afternoon").question  # underlined with no note
    assert by_text(found, "deep convection over land").question is None


def test_baseline_shift_is_reported(pdfs: tuple[Path, Path]) -> None:
    annotated, baseline = pdfs
    found = comments.extract(annotated)
    comments.verify(found, baseline)
    deletion = by_text(found, "very very")
    assert deletion.baseline == {"status": "moved", "page": 1, "line": 4}
    assert by_text(found, "sensors are sparse").baseline["status"] == "same"
    comments.verify(found, annotated)
    assert {c.baseline["status"] for c in found} <= {"same", "unchecked"}


def test_import_writes_json_and_text(pdfs: tuple[Path, Path], tmp_path: Path) -> None:
    annotated, baseline = pdfs
    email = ("Dear all,\n\nThe introduction is too long and should say what is new in the first paragraph.\n\n"
             "Thanks,\nAda")
    target = tmp_path / "revision2" / "supporting_files" / "comments" / "ada"
    data = comments.import_pdf(target, annotated, email, baseline, "revision1/revision_clean.pdf")
    assert (target / "annotated.pdf").is_file() and (target / "email.txt").read_text() == email
    assert data["comments"][-1]["kind"] == "email" and "introduction is too long" in data["comments"][-1]["note"]
    assert sum(c["kind"] == "email" for c in data["comments"]) == 1  # greeting and sign-off left out
    # The reply by ada.l belongs to the highlight; ada.l's own comment is the line 9 note.
    assert comments.persons(data) == {"Ada": len(data["comments"]) - 1, "ada.l": 1}
    # Merging the two account names.
    data["aliases"] = {"ada.l": "Ada"}
    comments.save(target, data)
    assert comments.persons(comments.load(target)) == {"Ada": len(data["comments"])}
    text = (target / "comments.txt").read_text()
    assert text.startswith("Comments from Ada (")
    assert "Comment 1  page 1, line" in text and 'Insert: "a new phrase"' in text
    assert "In the baseline: page 1, line 4 (moved)" in text
    assert json.loads((target / "comments.json").read_text())["baseline"] == "revision1/revision_clean.pdf"


def test_import_again_keeps_answers_and_names(pdfs: tuple[Path, Path], tmp_path: Path) -> None:
    annotated, baseline = pdfs
    target = tmp_path / "comments" / "ada"
    data = comments.import_pdf(target, annotated, "The abstract should name the realm in its first line.", baseline)
    outdoor = next(c for c in data["comments"] if c["note"] == "outdoor")
    outdoor["answer"] = "Insert it: outdoor construction workers."
    data["aliases"] = {"ada.l": "Ada"}
    comments.save(target, data)
    again = comments.import_pdf(target, annotated, "", baseline)
    assert next(c for c in again["comments"] if c["note"] == "outdoor")["answer"] == outdoor["answer"]
    assert comments.persons(again) == {"Ada": len(again["comments"])}
    assert not (target / "email.txt").exists() and all(c["kind"] != "email" for c in again["comments"])


def test_import_refuses_other_files(tmp_path: Path) -> None:
    (tmp_path / "x.pdf").write_text("not a pdf")
    with pytest.raises(comments.CommentsError, match="not a PDF"):
        comments.import_pdf(tmp_path / "out", tmp_path / "x.pdf")
    with pytest.raises(comments.CommentsError, match="There is no file"):
        comments.import_pdf(tmp_path / "out", tmp_path / "missing.pdf")
