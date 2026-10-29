from __future__ import annotations

from thor import latexlog

LOG = r"""This is pdfTeX, Version 3.141592653-2.6-1.40.29 (TeX Live 2026) (preloaded format=pdflatex)
(./main.tex
LaTeX2e <2025-11-01>
(/usr/local/texlive/2026/texmf-dist/tex/latex/base/article.cls
Document Class: article 2024/06/29 v1.4n Standard LaTeX document class
(/usr/local/texlive/2026/texmf-dist/tex/latex/base/size11.clo))
LaTeX Warning: Writing or overwriting file `references.bib'.

Package natbib Warning: Citation `Price1994' on page 1 undefined on input line 12.

LaTeX Warning: Reference `fig:map' on page 1 undefined on input line 14.

LaTeX Warning: Citation 'thor_hammer_2024' on page 1 undefined on input line 15.

./main.tex:20: Undefined control sequence.
l.20 Text with a \badmacro
                           here.
(./sections/results.tex
Overfull \hbox (12.5pt too wide) in paragraph at lines 7--9
[]\T1/lmr/m/n/10.95 Some very long line
Package hyperref Warning: Token not allowed in a PDF string (Unicode):
(hyperref)                removing `math shift' on input line 3.

)
Overfull \hbox (3.0pt too wide) in paragraph at lines 30--30
LaTeX Warning: Reference `fig:map' on page 2 undefined on input line 14.

LaTeX Warning: There were undefined references.
LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right.
)
! Emergency stop.
<*> main.tex

*** (job aborted, no legal \end found)
"""


def by_kind(problems: list[latexlog.Problem], kind: str) -> list[latexlog.Problem]:
    return [p for p in problems if p.kind == kind]


def test_parses_every_kind() -> None:
    problems = latexlog.parse(LOG, "/proj/manuscript")
    assert latexlog.counts(problems) == {
        "error": 2, "undefined_reference": 1, "undefined_citation": 2, "overfull": 2, "warning": 1}


def test_errors_with_file_line_and_context() -> None:
    error = by_kind(latexlog.parse(LOG), "error")[0]
    assert (error.message, error.file, error.line) == ("Undefined control sequence.", "main.tex", 20)
    assert error.detail.startswith("l.20 Text with a \\badmacro")


def test_undefined_references_and_citations_once_each() -> None:
    problems = latexlog.parse(LOG)
    ref = by_kind(problems, "undefined_reference")
    assert [(p.message, p.file, p.line) for p in ref] == [("Undefined reference fig:map", "main.tex", 14)]
    cite = by_kind(problems, "undefined_citation")[0]
    assert (cite.message, cite.line) == ("Undefined citation Price1994", 12)


def test_overfull_boxes_follow_the_open_file() -> None:
    boxes = by_kind(latexlog.parse(LOG), "overfull")
    assert [(b.file, b.line) for b in boxes] == [("sections/results.tex", 7), ("main.tex", 30)]
    assert boxes[0].message == "Overfull box, 12.5pt too wide, in paragraph, lines 7–9"
    assert boxes[1].message.endswith("lines 30")


def test_package_warning_continuation_lines() -> None:
    warning = by_kind(latexlog.parse(LOG), "warning")[0]
    assert warning.message == ("hyperref: Token not allowed in a PDF string (Unicode): removing `math shift' "
                               "on input line 3.")
    assert (warning.file, warning.line) == ("sections/results.tex", 3)


def test_files_outside_the_project_have_no_path() -> None:
    log = "(/usr/local/texlive/x/pkg.sty\n/usr/local/texlive/x/pkg.sty:5: Package error.\n)"
    error = by_kind(latexlog.parse(log, "/proj/manuscript"), "error")[0]
    assert error.file is None and error.line == 5
    assert latexlog.relative("/proj/manuscript/sub/a.tex", "/proj/manuscript") == "sub/a.tex"
    assert latexlog.relative("./main.tex", "/proj/manuscript") == "main.tex"


def test_errors_in_generated_files_are_named() -> None:
    log = "(/builds/p/work/main.aux\n/builds/p/work/main.aux:7: Paragraph ended before \\@newl@bel was complete.\n"
    problems = latexlog.parse(log, "/proj/manuscript")
    assert [(p.message, p.file, p.line) for p in problems] == [
        ("Paragraph ended before \\@newl@bel was complete. (in main.aux)", None, None)]
    assert latexlog.generated_errors(log) == ["main.aux"]
    assert latexlog.generated_errors("./main.tex:3: Undefined control sequence.") == []
