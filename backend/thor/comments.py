"""Reads a co-author's annotated PDF into numbered comments, each with its place in the paper.

What PDF viewers write, and how it becomes comments:
- Highlight, Underline, Squiggly, StrikeOut: the words under their quads. A strike-out without a note is a
  deletion request.
- Caret: an insertion. A caret grouped with a strike-out (IRT with RT /Group, as "replace text" tools make
  them) reads as a replacement.
- Text (sticky note) and FreeText. A note with IRT and no /Group is a reply to the comment it points to.
- Ink: one annotation can hold many strokes. Each stroke is classified from its shape: a flat line under
  text is an underline (of the text above it), a short line through a word a strike, a closed loop a
  circle (of the text inside), a single point an insertion dot. Pen marks are paired with the note that
  refers to their line, since notes are often placed away from the text and say "line 9-10: ...".

Each comment gets the page, the margin line number from the lineno package (or the caption, table or
Supplementary Note it sits in), and is then looked up in the baseline revision's PDF, which may differ from
the build the co-author annotated.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf

MARKUP = {"Highlight": "highlight", "Underline": "underline", "Squiggly": "squiggly", "StrikeOut": "strike-out"}
LINE_REF = re.compile(r"^\s*(?:lines?|ll?\.?)\s*(\d+)(?:\s*(?:-|–|to|and)\s*(\d+))?\s*[:,.)-]?", re.I)
CAPTION = re.compile(r"^(?:Supplementary\s+)?(Fig(?:ure)?|Table)\.?\s*(S?\d+)", re.I)
NOTE_HEADING = re.compile(r"^Supplementary\s+Note\s+(\d+)", re.I)
LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "–": "-", "—": "-", "−": "-", "’": "'",
             "‘": "'", "“": '"', "”": '"', "­": "", " ": " "}


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block: int
    line: int
    number: int

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Mark:
    """One pen stroke, classified."""
    kind: str  # underline, strike, circle, dot, stroke
    text: str
    line: int | None
    rect: list[float]


@dataclass
class Comment:
    id: int = 0
    kind: str = ""
    author: str = ""
    page: int | None = None
    line: int | None = None
    line_end: int | None = None
    element: str | None = None
    marked: str = ""
    note: str = ""
    insert: str = ""
    anchor: str = ""
    marks: list[dict] = field(default_factory=list)
    replies: list[dict] = field(default_factory=list)
    rects: list[list[float]] = field(default_factory=list)
    context: str = ""
    # The margin number of the context line, which the baseline check compares.
    context_line: int | None = None
    baseline: dict | None = None
    question: str | None = None
    answer: str | None = None
    created: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def normalize(text: str) -> str:
    for a, b in LIGATURES.items():
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def clean_note(text: str | None) -> str:
    return normalize((text or "").replace("\r", "\n"))


class PageText:
    """The words of a page, its margin line numbers, and the lines of text they number."""

    def __init__(self, page: pymupdf.Page) -> None:
        self.page = page
        raw = [Word(*w[:8]) for w in page.get_text("words", sort=True)]
        body = [w for w in raw if not w.text.isdigit()]
        left = sorted(w.x0 for w in body)[len(body) // 20] if body else 0
        # lineno prints the numbers in the left margin, right of nothing and left of the text block.
        self.numbers = sorted(((int(w.text), w.cy) for w in raw if w.text.isdigit() and w.x1 < left - 1),
                              key=lambda n: n[1])
        number_words = {id(w) for w in raw if w.text.isdigit() and w.x1 < left - 1}
        self.words = [w for w in raw if id(w) not in number_words]
        gaps = [b[1] - a[1] for a, b in zip(self.numbers, self.numbers[1:]) if b[0] - a[0] == 1]
        self.spacing = statistics.median(gaps) if gaps else 12.0
        # lineno numbers every line, or only every n-th line (modulo); between sparse numbers lines are counted.
        steps = [b[0] - a[0] for a, b in zip(self.numbers, self.numbers[1:])]
        self.sparse = bool(steps) and statistics.median(steps) > 1

    def line_at(self, y: float) -> int | None:
        """The margin number beside the text line at height y. Lines without one, such as captions, have none;
        when only every n-th line is numbered, the lines between are counted from the nearest number."""
        if not self.numbers:
            return None
        number, ny = min(self.numbers, key=lambda n: abs(n[1] - y))
        if abs(ny - y) <= 4.0:
            return number
        if not self.sparse or abs(ny - y) > 6 * self.spacing:
            return None
        return number + round((y - ny) / self.spacing)

    def words_in(self, rect: pymupdf.Rect, pad: float = 1.0) -> list[Word]:
        r = pymupdf.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)
        return [w for w in self.words if r.contains(pymupdf.Point(w.cx, w.cy))]

    def line_words(self, y: float) -> list[Word]:
        """The words of the text line through height y."""
        hits = [w for w in self.words if w.y0 - 1 <= y <= w.y1 + 1]
        if not hits:
            return []
        key = min(hits, key=lambda w: abs(w.cy - y))
        return [w for w in self.words if w.block == key.block and w.line == key.line]

    def covered_word(self, rect: pymupdf.Rect) -> Word | None:
        """The word a note's icon covers most; else the word nearest the icon's centre."""
        def overlap(w: Word) -> float:
            ix = min(w.x1, rect.x1) - max(w.x0, rect.x0)
            iy = min(w.y1, rect.y1) - max(w.y0, rect.y0)
            return ix * iy if ix > 0 and iy > 0 else 0.0
        covering = [w for w in self.words if overlap(w) > 0]
        if covering:
            return max(covering, key=overlap)
        return self.nearest_word(pymupdf.Point(rect.x0 + rect.width / 2, rect.y0 + rect.height / 2))

    def numbered_line(self, number: int) -> tuple[str, float] | None:
        """The text and height of the line with this margin number."""
        for text_, n, y in self.lines():
            if n == number:
                return text_, y
        return None

    def nearest_word(self, point: pymupdf.Point) -> Word | None:
        if not self.words:
            return None
        return min(self.words, key=lambda w: math.dist((w.cx, w.cy), (point.x, point.y)))

    def element_at(self, y: float) -> str | None:
        """The caption a height falls in (Figure 3, Table S2), else None. A caption can run over several
        text blocks; blocks without line numbers right below a caption block continue it."""
        current, last_bottom = None, None
        for block in sorted(self.page.get_text("blocks"), key=lambda b: b[1]):
            x0, y0, x1, y1, text = block[:5]
            m = CAPTION.match(normalize(text))
            numbered = any(y0 - 2 <= ny <= y1 + 2 for _, ny in self.numbers)
            if m:
                kind = "Figure" if m.group(1).lower().startswith("fig") else "Table"
                current = f"{kind} {m.group(2)} caption"
            elif numbered or last_bottom is None or y0 - last_bottom > 12:
                current = None
            last_bottom = y1
            if current and y0 - 2 <= y <= y1 + 2:
                return current
        return None

    def nearest_line(self, y: float) -> int | None:
        """The margin number nearest in height, within one line spacing (for pen dots, drawn on a line)."""
        if not self.numbers:
            return None
        number, ny = min(self.numbers, key=lambda n: abs(n[1] - y))
        return number if abs(ny - y) <= self.spacing else None

    def headings(self) -> list[tuple[float, str]]:
        """(height, "Supplementary Note N") of each note heading on the page."""
        found = []
        for block in self.page.get_text("blocks"):
            m = NOTE_HEADING.match(normalize(block[4]))
            if m:
                found.append((block[1], f"Supplementary Note {m.group(1)}"))
        return found

    def note_heading_above(self, y: float, carried: str | None = None) -> str | None:
        """The last note heading above y on this page, or the one carried over from earlier pages."""
        above = [name for top, name in self.headings() if top <= y]
        return above[-1] if above else carried

    def lines(self) -> list[tuple[str, int | None, float]]:
        """(text, margin number, height) of each text line."""
        groups: dict[tuple[int, int], list[Word]] = {}
        for w in self.words:
            groups.setdefault((w.block, w.line), []).append(w)
        out = []
        for ws in groups.values():
            y = statistics.median(w.cy for w in ws)
            out.append((normalize(" ".join(w.text for w in ws)), self.line_at(y), y))
        return out


def joined(words: list[Word]) -> str:
    return normalize(" ".join(w.text for w in sorted(words, key=lambda w: (w.block, w.line, w.number))))


def quads_of(annot: pymupdf.Annot) -> list[pymupdf.Rect]:
    v = annot.vertices or []
    if len(v) >= 4 and not isinstance(v[0][0], (list, tuple)):
        return [pymupdf.Quad(v[i:i + 4]).rect for i in range(0, len(v) - 3, 4)]
    return [annot.rect]


def classify_stroke(points: list, text: PageText) -> Mark:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    rect = pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))
    length = sum(math.dist(points[k], points[k + 1]) for k in range(len(points) - 1))
    closure = math.dist(points[0], points[-1])
    if length < 3:
        line = text.nearest_line(ys[0])
        on_line = [w for w in text.words if line is not None and text.line_at(w.cy) == line]
        candidates = on_line or text.words
        word = min(candidates, key=lambda w: math.dist((w.cx, w.cy), (xs[0], ys[0]))) if candidates else None
        return Mark("dot", word.text if word else "", line, list(rect))
    if closure < 0.4 * length and rect.width > 6 and rect.height > 6:
        inside = text.words_in(rect, pad=0)
        return Mark("circle", joined(inside), text.line_at(rect.y0 + rect.height / 2), list(rect))
    y = statistics.median(ys)
    across = [w for w in text.words if w.x0 < rect.x1 and w.x1 > rect.x0]
    through = [w for w in across if w.y0 + 0.3 * (w.y1 - w.y0) <= y <= w.y1 - 0.2 * (w.y1 - w.y0)]
    if rect.height < 0.3 * max(rect.width, 1) and through:
        return Mark("strike", joined(through), text.line_at(statistics.median(w.cy for w in through)), list(rect))
    # A flat line under text marks the line just above it.
    above = [w for w in across if w.y1 - 0.2 * (w.y1 - w.y0) <= y <= w.y1 + 0.9 * (w.y1 - w.y0)]
    if rect.height < 0.3 * max(rect.width, 1) and above:
        return Mark("underline", joined(above), text.line_at(statistics.median(w.cy for w in above)), list(rect))
    near = text.words_in(rect, pad=2)
    return Mark("stroke", joined(near), text.line_at(y), list(rect))


def line_reference(note: str) -> tuple[int, int] | None:
    m = LINE_REF.match(note)
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else a
    return (a, b) if b >= a else (a, a)


def locate(c: Comment, text: PageText, y: float, carried: str | None = None) -> None:
    """Line number, or else the caption or Supplementary Note the comment sits in."""
    if c.line is None:
        c.line = text.line_at(y)
    if c.line is None:
        c.element = text.element_at(y) or text.note_heading_above(y, carried)
    if not c.context:
        set_context(c, text, y)


def set_context(c: Comment, text: PageText, y: float) -> None:
    words = text.line_words(y)
    c.context = joined(words)
    c.context_line = text.line_at(statistics.median(w.cy for w in words)) if words else None


def context_from_marks(c: Comment, text: PageText) -> None:
    """The context of pen marks: the text line of the first mark that has a line number."""
    for m in c.marks:
        found = text.numbered_line(m["line"]) if m.get("line") is not None else None
        if found:
            c.context, c.context_line = found[0], m["line"]
            return
    if c.rects:
        r = c.rects[0]
        set_context(c, text, (r[1] + r[3]) / 2)


def question_for(c: Comment) -> str | None:
    """The comments whose meaning a person should settle before anyone acts on them."""
    words = c.note.split()
    if c.kind == "pen" and not c.note:
        kinds = ", ".join(sorted({m["kind"] for m in c.marks}))
        return f"A pen mark ({kinds}) with no note: what should change?"
    if c.kind == "insertion" and not c.insert:
        return "An insertion mark with no text: what should be inserted here?"
    if c.kind in ("note", "text box") and 0 < len(words) <= 2 and not c.note.endswith("?") and c.anchor:
        return (f'A note of only "{c.note}" on "{c.anchor}": insert it next to "{c.anchor}", '
                f'replace "{c.anchor}" with it, or is it a remark?')
    if c.kind in ("highlight", "underline", "squiggly") and not c.note:
        return f'"{c.marked[:60]}" is marked with no note: what should change?'
    return None


def extract(pdf: Path) -> list[Comment]:
    doc = pymupdf.open(pdf)
    comments: list[Comment] = []
    by_xref: dict[int, Comment] = {}
    pending_replies: list[tuple[int, dict]] = []
    groups: list[tuple[int, int]] = []  # (strike-out xref, caret xref)
    pen_notes: list[tuple[Comment, int]] = []

    carried: str | None = None  # the last Supplementary Note heading of the pages before
    for page in doc:
        text = PageText(page)
        page_pen: list[tuple[Mark, Comment]] = []
        page_notes: list[Comment] = []
        for annot in page.annots():
            kind = annot.type[1]
            if kind == "Popup":
                continue
            info = annot.info
            author = (info.get("title") or "").strip()
            note = clean_note(info.get("content"))
            irt = annot.irt_xref
            rt = doc.xref_get_key(annot.xref, "RT")[1]
            if irt and rt != "/Group" and kind in ("Text", "FreeText"):
                pending_replies.append((irt, {"author": author, "note": note}))
                continue
            c = Comment(author=author, note=note, page=page.number + 1, created=info.get("creationDate", ""))
            if kind in MARKUP:
                rects = quads_of(annot)
                words = [w for r in rects for w in text.words_in(r)]
                c.kind = MARKUP[kind]
                c.marked = joined(words)
                c.rects = [list(r) for r in rects]
                ys = [w.cy for w in words] or [rects[0].y0 + rects[0].height / 2]
                c.line, c.line_end = text.line_at(min(ys)), text.line_at(max(ys))
                if c.line_end == c.line:
                    c.line_end = None
                if kind == "StrikeOut" and not note:
                    c.kind = "deletion"
                if irt and rt == "/Group":
                    groups.append((annot.xref, irt))
                locate(c, text, ys[0], carried)
            elif kind == "Caret":
                c.kind = "insertion"
                c.insert, c.note = note, ""
                r = annot.rect
                # A caret sits on the line, centred on the gap where the text goes: anchor it to the word
                # that ends there (insert after it).
                cx, cy = r.x0 + r.width / 2, r.y0 + r.height / 2
                on_line = [w for w in text.words if w.y0 - 2 <= cy <= w.y1 + 2]
                word = (min(on_line, key=lambda w: abs(w.x1 - cx)) if on_line
                        else text.nearest_word(pymupdf.Point(cx, cy)))
                c.anchor = word.text.strip(",.;:()") if word else ""
                c.rects = [list(r)]
                locate(c, text, word.cy if word else r.y0, carried)
                if irt and rt == "/Group":
                    groups.append((irt, annot.xref))
            elif kind in ("Text", "FreeText"):
                c.kind = "note" if kind == "Text" else "text box"
                r = annot.rect
                c.rects = [list(r)]
                ref = line_reference(note)
                if ref:
                    c.line, c.line_end = ref[0], (ref[1] if ref[1] != ref[0] else None)
                    page_notes.append(c)
                else:
                    word = text.covered_word(r)
                    in_text = word is not None and math.dist((word.cx, word.cy), (r.x0 + r.width / 2,
                                                                                   r.y0 + r.height / 2)) < 40
                    c.anchor = word.text.strip(",.;:()") if in_text else ""
                    locate(c, text, word.cy if in_text else r.y0 + r.height / 2, carried)
            elif kind == "Ink":
                strokes = [classify_stroke(s, text) for s in (annot.vertices or []) if s]
                owner = Comment(kind="pen", author=author, note=note, page=page.number + 1,
                                created=info.get("creationDate", ""))
                for mark in strokes:
                    page_pen.append((mark, owner))
                if note:
                    pen_notes.append((owner, annot.xref))
                by_xref[annot.xref] = owner
                continue
            else:
                c.kind = kind.lower()
                c.rects = [list(annot.rect)]
                locate(c, text, annot.rect.y0, carried)
            by_xref[annot.xref] = c
            comments.append(c)

        comments.extend(pair_pen_marks(page_pen, page_notes, text))
        for c in comments:
            if c.page == page.number + 1 and c.line is None and not c.element and c.rects:
                c.element = text.element_at(c.rects[0][1]) or text.note_heading_above(c.rects[0][1], carried)
        headings = text.headings()
        if headings:
            carried = headings[-1][1]

    # Replacements: the strike-out and its caret become one comment.
    for strike_xref, caret_xref in groups:
        strike, caret = by_xref.get(strike_xref), by_xref.get(caret_xref)
        if not strike or not caret or caret not in comments or strike not in comments:
            continue
        strike.kind = "replacement"
        strike.insert = caret.insert
        strike.note = "\n".join(n for n in (strike.note, caret.note) if n)
        strike.rects += caret.rects
        comments.remove(caret)
    for parent_xref, reply in pending_replies:
        parent = by_xref.get(parent_xref)
        if parent is not None:
            parent.replies.append(reply)
        else:
            comments.append(Comment(kind="note", author=reply["author"], note=reply["note"]))

    for c in comments:
        # A note written as a reply to one's own mark is that mark's note.
        if not c.note and c.replies:
            c.note = c.replies.pop(0)["note"]
            if c.kind == "deletion":
                c.kind = "strike-out"
    comments = [c for c in comments if c.kind != "pen" or c.marks]
    for c in comments:
        c.question = question_for(c)
    # Page order: by the top of the comment's highest mark on its page.
    comments.sort(key=lambda c: (c.page or 10**6, min((r[1] for r in c.rects), default=0),
                                 min((r[0] for r in c.rects), default=0)))
    for i, c in enumerate(comments, 1):
        c.id = i
    return comments


def pair_pen_marks(marks: list[tuple[Mark, Comment]], notes: list[Comment], text: PageText) -> list[Comment]:
    """Gives each pen mark the note it belongs to; marks left over become comments of their own.

    Notes refer to lines ("line 9-10: ..."). A mark on a referenced line goes with that note. The note an ink
    annotation carries itself goes with its own marks: by its line reference, or else by the words it shares
    with them. A mark no note claims joins a note on the line before or after, since marks follow sentences
    across line breaks: one near the start of its line continues the line before, one near the end the next.
    """
    targets = list(notes)
    owners: list[Comment] = []
    claimed: dict[int, Comment] = {}

    def span(c: Comment) -> tuple[int, int]:
        return c.line, c.line_end or c.line

    def words_of(text_: str) -> set[str]:
        return {w.strip(",.;:?!()").lower() for w in text_.split() if len(w.strip(",.;:?!()")) > 3}

    # 1. An ink annotation's own note claims its own marks: those on the lines it names, or else those on
    #    the line of the mark it shares most words with.
    for _, owner in marks:
        if not owner.note or owner in owners:
            continue
        owners.append(owner)
        mine = [i for i, (_, o) in enumerate(marks) if o is owner]
        ref = line_reference(owner.note)
        if ref:
            owner.line, owner.line_end = ref[0], (ref[1] if ref[1] != ref[0] else None)
            chosen = [i for i in mine if marks[i][0].line is not None and ref[0] <= marks[i][0].line <= ref[1]]
        else:
            note_words = words_of(owner.note)
            best = max(mine, key=lambda i: len(note_words & words_of(marks[i][0].text)))
            owner.line = marks[best][0].line
            chosen = [i for i in mine if marks[i][0].line == owner.line]
        for i in chosen:
            claimed[i] = owner
        targets.append(owner)
    # 2. Sticky notes claim the marks on the lines they name.
    for i, (m, _) in enumerate(marks):
        if i in claimed or m.line is None:
            continue
        holders = [t for t in notes if t.line is not None and span(t)[0] <= m.line <= span(t)[1]]
        if holders:
            claimed[i] = min(holders, key=lambda t: span(t)[1] - span(t)[0])
    # 3. Marks no note claims join a note on the line before or after.
    middle = statistics.median([w.cx for w in text.words]) if text.words else 300
    for i, (m, _) in enumerate(marks):
        if i in claimed or m.line is None:
            continue
        # Only across a line break inside a sentence.
        previous, own = text.numbered_line(m.line - 1), text.numbered_line(m.line)
        runs_in = previous is not None and not previous[0].rstrip().endswith((".", "?", "!"))
        runs_on = own is not None and not own[0].rstrip().endswith((".", "?", "!"))
        before = [t for t in targets if t.line is not None and span(t)[1] == m.line - 1] if runs_in else []
        after = [t for t in targets if t.line is not None and span(t)[0] == m.line + 1] if runs_on else []
        x = (m.rect[0] + m.rect[2]) / 2
        if before and (not after or x < middle):
            claimed[i] = before[0]
        elif after:
            claimed[i] = after[0]

    made: list[Comment] = []
    for target in targets:
        mine = [i for i in sorted(claimed) if claimed[i] is target]
        if mine:
            target.kind = "pen"
            target.marks = [asdict(marks[i][0]) for i in mine]
            target.rects = [marks[i][0].rect for i in mine]
            target.marked = " / ".join(marks[i][0].text for i in mine if marks[i][0].text)
            lines = [marks[i][0].line for i in mine if marks[i][0].line is not None] + [target.line]
            target.line = min(x for x in lines if x is not None)
            last = max(x for x in lines + [target.line_end] if x is not None)
            target.line_end = last if last != target.line else None
        elif target.line is not None:
            # A note with no mark: highlight the line it refers to.
            hits = [y for _, n, y in text.lines() if n == target.line]
            words = text.line_words(hits[0]) if hits else []
            if words:
                target.rects = [[min(w.x0 for w in words), min(w.y0 for w in words),
                                 max(w.x1 for w in words), max(w.y1 for w in words)]]
        if target.marks:
            context_from_marks(target, text)
        elif target.rects:
            r = target.rects[0]
            set_context(target, text, (r[1] + r[3]) / 2)
        if target in owners:
            made.append(target)
    leftover = [i for i in range(len(marks)) if i not in claimed]
    for group in split_runs(leftover, marks):
        owner = marks[group[0]][1]
        c = Comment(kind="pen", author=owner.author, page=owner.page, created=owner.created)
        c.marks = [asdict(marks[i][0]) for i in group]
        c.rects = [marks[i][0].rect for i in group]
        c.marked = " / ".join(marks[i][0].text for i in group if marks[i][0].text)
        lines = [marks[i][0].line for i in group if marks[i][0].line is not None]
        c.line = min(lines) if lines else None
        c.line_end = max(lines) if lines and max(lines) != min(lines) else None
        context_from_marks(c, text)
        made.append(c)
    return made


def split_runs(indexes: list[int], marks: list[tuple[Mark, Comment]]) -> list[list[int]]:
    """Groups marks whose lines follow each other (a dot beside an underline, a two-line underline)."""
    ordered = sorted(indexes, key=lambda i: (marks[i][0].line or 0, marks[i][0].rect[1]))
    runs: list[list[int]] = []
    for i in ordered:
        line = marks[i][0].line
        if runs and line is not None and marks[runs[-1][-1]][0].line is not None \
                and line - marks[runs[-1][-1]][0].line <= 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def verify(comments: list[Comment], baseline: Path) -> None:
    """Finds each comment's line of text in the baseline PDF and records any page or line shift."""
    doc = pymupdf.open(baseline)
    index = []  # (page, line number, normalized text)
    for page in doc:
        for line_text, number, _ in PageText(page).lines():
            if len(line_text) >= 12:
                index.append((page.number + 1, number, line_text.lower()))
    exact: dict[str, list[tuple[int, int | None]]] = {}
    for p, n, t in index:
        exact.setdefault(t, []).append((p, n))
    for c in comments:
        needle = (c.context or "").lower()
        if len(needle) < 12:
            c.baseline = {"status": "unchecked", "page": None, "line": None}
            continue
        hits = exact.get(needle)
        if not hits:
            best, score = None, 0.0
            for p, n, t in index:
                if abs(p - (c.page or p)) > 4:
                    continue
                s = SequenceMatcher(None, needle, t, autojunk=False).ratio()
                if s > score:
                    best, score = (p, n), s
            hits = [best] if best and score >= 0.8 else []
        if not hits:
            c.baseline = {"status": "not found", "page": None, "line": None}
            continue
        page, line = min(hits, key=lambda h: abs(h[0] - (c.page or h[0])))
        same = page == c.page and (line == c.context_line or c.context_line is None)
        c.baseline = {"status": "same" if same else "moved", "page": page, "line": line}


def find_in(pdf: Path, c: Comment) -> list[dict]:
    """Boxes for a comment's text in another build (the current PDF), searched near its page."""
    doc = pymupdf.open(pdf)
    snippets = [s for s in (c.marked, c.context) if s and len(s) >= 6]
    pages = sorted(range(doc.page_count), key=lambda i: abs(i + 1 - (c.page or 1)))
    for snippet in snippets:
        for phrase in (snippet[:80], " ".join(snippet.split()[:6])):
            for i in pages[:12]:
                hits = doc[i].search_for(phrase)
                if hits:
                    return [{"page": i + 1, "left": r.x0, "top": r.y0, "width": r.width, "height": r.height}
                            for r in hits[:4]]
    return []


def readable(comments: list[Comment], header: str) -> str:
    out = [header, "=" * min(len(header), 100), ""]
    for c in comments:
        where = f"page {c.page}" if c.page else "email"
        if c.line:
            where += f", line {c.line}" + (f"-{c.line_end}" if c.line_end else "")
        elif c.element:
            where += f", {c.element}"
        out.append(f"Comment {c.id}  {where}  [{c.kind}]  {c.author}")
        if c.marked:
            out.append(f'  Marked: "{c.marked}"')
        if c.insert:
            out.append(f'  Insert: "{c.insert}"')
        if c.note:
            out.append("  Note: " + c.note.replace("\n", "\n        "))
        for r in c.replies:
            out.append(f"  Reply ({r['author']}): {r['note']}")
        b = c.baseline or {}
        if b.get("status") == "moved":
            out.append(f"  In the baseline: page {b['page']}, line {b['line']} (moved)")
        elif b.get("status") == "not found":
            out.append("  In the baseline: this line was not found")
        if c.question:
            out.append(f"  Unclear: {c.question}")
        if c.answer:
            out.append(f"  Answer: {c.answer}")
        out.append("")
    return "\n".join(out)


def email_comments(text: str, first_id: int, author: str) -> list[Comment]:
    """One comment per paragraph of a pasted email, leaving out greetings and sign-offs."""
    paragraphs = [normalize(p) for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    out = []
    for p in paragraphs:
        if len(p.split()) < 6:
            continue
        out.append(Comment(id=first_id + len(out), kind="email", author=author, note=p))
    return out


# Imports: each annotated PDF becomes a folder in the active revision,
# supporting_files/comments/<name>/ with the PDF, comments.json and comments.txt.

class CommentsError(ValueError):
    pass


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:60] or "comments"


def persons(data: dict) -> dict[str, int]:
    """How many comments each person made, with merged account names counted together."""
    counts: dict[str, int] = {}
    for c in data["comments"]:
        name = data["aliases"].get(c["author"], c["author"]) or "unknown"
        counts[name] = counts.get(name, 0) + 1
    return counts


def save(folder: Path, data: dict) -> None:
    import json

    for c in data["comments"]:
        c["person"] = data["aliases"].get(c["author"], c["author"])
    (folder / "comments.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
    people = ", ".join(f"{p} ({n})" for p, n in persons(data).items())
    header = (f"Comments from {people} on {data['source_pdf']}, imported {data['imported']}"
              + (f"; located in {data['baseline']}" if data.get("baseline") else ""))
    objects = [Comment(**{k: v for k, v in c.items() if k in Comment.__dataclass_fields__}) for c in data["comments"]]
    for obj, raw in zip(objects, data["comments"]):
        obj.author = raw["person"]
    (folder / "comments.txt").write_text(readable(objects, header))


def load(folder: Path) -> dict:
    import json

    return json.loads((folder / "comments.json").read_text())


def import_pdf(target: Path, pdf: Path, email: str = "", baseline: Path | None = None,
               baseline_label: str = "") -> dict:
    """Copies the annotated PDF into target, reads its comments, checks them against the baseline."""
    import shutil
    import time

    if not pdf.is_file():
        raise CommentsError(f"There is no file at {pdf}.")
    with pdf.open("rb") as f:
        if f.read(5) != b"%PDF-":
            raise CommentsError(f"{pdf} is not a PDF file.")
    # Importing the same set again keeps the merged names and the answers already given.
    old = load(target) if (target / "comments.json").is_file() else None
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf, target / "annotated.pdf")
    found = extract(target / "annotated.pdf")
    (target / "email.txt").unlink(missing_ok=True)
    if email.strip():
        (target / "email.txt").write_text(email)
        authors = [c.author for c in found if c.author]
        author = max(set(authors), key=authors.count) if authors else "email"
        found += email_comments(email, len(found) + 1, author)
    if baseline and baseline.is_file():
        verify([c for c in found if c.page], baseline)
    if old:
        def key(c: dict) -> tuple:
            return c["kind"], c["page"], c["marked"], c["note"], c["insert"]

        answers = {key(c): c["answer"] for c in old["comments"] if c.get("answer")}
        for c in found:
            c.answer = answers.get(key(c.to_dict()), c.answer)
    data = {
        "name": target.name,
        "source_pdf": pdf.name,
        "imported": time.strftime("%d %B %Y %H:%M").lstrip("0"),
        "baseline": baseline_label,
        "aliases": old["aliases"] if old else {},
        "comments": [c.to_dict() for c in found],
    }
    save(target, data)
    return data
