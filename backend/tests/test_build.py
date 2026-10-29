"""Real builds with MacTeX: skipped where latexmk is not installed."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from thor import build, projects, synctex

TEXBIN = "/Library/TeX/texbin"
pytestmark = pytest.mark.skipif(not shutil.which("latexmk", path=TEXBIN), reason="MacTeX is not installed")

PAPER = r"""\begin{filecontents*}[overwrite]{references.bib}
@article{Price1994, author = {Price, Colin}, title = {Lightning and climate}, journal = {JGR}, year = {1994}}
\end{filecontents*}
\documentclass{article}
\usepackage{lineno}
\usepackage[numbers]{natbib}
\linenumbers
\begin{document}
\section{Results}\label{sec:results}
Lightning follows convection \citep{Price1994}, as Section~\ref{sec:results} shows.
A missing figure is Fig.~\ref{fig:missing}, and a missing paper is \citep{Nobody2020}.

\noindent\hbox to 1.2\textwidth{An overfull line that is wider than the text block on purpose.\hss}

\bibliographystyle{unsrtnat}
\bibliography{references}
\clearpage
\nolinenumbers
{\Large\bfseries Supplementary Information}

\section{Supplementary Note 1}
More detail.
\end{document}
"""


@pytest.fixture(autouse=True)
def tex_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_TOOL_PATH", f"{TEXBIN}:/usr/bin:/bin")


def make_project(tmp_path: Path, source: str) -> tuple[Path, projects.ProjectConfig]:
    root = tmp_path / "paper"
    (root / "tex").mkdir(parents=True)
    (root / "tex" / "main.tex").write_text(source)
    return root, projects.load_config(root)


def test_build_reports_pages_split_and_problems(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    before = sorted(p.name for p in (root / "tex").iterdir())
    result = build.build(root, cfg, "p1")
    assert result.ok and result.exit_code == 0 and result.pdf
    assert result.pages == 2 and result.si_start == 2
    assert result.counts["undefined_reference"] == 1 and result.counts["undefined_citation"] == 1
    assert result.counts["overfull"] == 1 and result.counts["error"] == 0
    missing = {p["message"]: p for p in result.problems}
    assert missing["Undefined reference fig:missing"]["file"] == "main.tex"
    assert missing["Undefined reference fig:missing"]["line"] == 11
    assert result.summary == ("Compiled · 2 pages (1 + 1 SI) · 1 undefined reference, 1 undefined citation,"
                              " 1 overfull box")
    # Nothing is written next to the source, not even the filecontents bibliography.
    assert sorted(p.name for p in (root / "tex").iterdir()) == before
    assert build.latest("p1").id == result.id
    assert build.output_file("p1", result.id, "main.pdf") is not None
    assert build.output_file("p1", result.id, "../x") is None


def test_failed_build(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, "\\documentclass{article}\n\\begin{document}\nText \\nosuchmacro{} \\ref{x}\n\\end{document}\n")
    result = build.build(root, cfg, "p2")
    assert not result.ok and result.exit_code != 0
    # The undefined reference is a side effect of the stopped run, so it is not reported.
    assert result.counts["undefined_reference"] == 0 and result.counts["error"] == 1
    error = next(p for p in result.problems if p["kind"] == "error")
    assert error["message"] == "Undefined control sequence." and (error["file"], error["line"]) == ("main.tex", 3)
    assert result.summary.startswith("Failed")


def test_synctex_round_trip(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    result = build.build(root, cfg, "p3")
    pdf = build.output_file("p3", result.id, "main.pdf")
    boxes = synctex.forward(pdf, root / "tex", "main.tex", 10)
    assert boxes and boxes[0]["page"] == 1 and boxes[0]["width"] > 0
    box = boxes[0]
    back = synctex.backward(pdf, root / "tex", box["page"], box["left"] + 5, box["top"] + box["height"] / 2)
    assert back == {"file": "main.tex", "line": 10}


def test_old_builds_are_pruned(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, "\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n")
    ids = [build.build(root, cfg, "p4").id for _ in range(build.KEEP_BUILDS + 1)]
    assert [d.name for d in build.build_folders(build.builds_dir("p4"))] == ids[1:]
    assert (build.builds_dir("p4") / "work").is_dir() and build.output_file("p4", "work", "main.pdf") is None


def test_later_compiles_reuse_earlier_passes(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    first = build.build(root, cfg, "p5")
    assert first.ok and first.passes >= 2
    unchanged = build.build(root, cfg, "p5")
    assert unchanged.passes == 0 and unchanged.counts == first.counts and unchanged.pdf
    main = root / "tex" / "main.tex"
    main.write_text(main.read_text().replace("More detail.", "More detail, and a new sentence."))
    edited = build.build(root, cfg, "p5")
    # One draft pass finds nothing moved; the final pass writes the PDF.
    assert edited.ok and edited.passes == 2 and edited.counts == first.counts
    assert edited.pages == 2 and edited.si_start == 2
    scratch = build.build(root, cfg, "p5", clean=True)
    assert scratch.ok and scratch.passes >= 2


def test_a_broken_kept_file_is_cleared(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    assert build.build(root, cfg, "p6").ok
    aux = build.builds_dir("p6") / "work" / "main.aux"
    aux.write_text(aux.read_text() + "\\newlabel{broken}{{1}{\n\\par\n")
    main = root / "tex" / "main.tex"
    main.write_text(main.read_text().replace("More detail.", "More detail again."))
    again = build.build(root, cfg, "p6")
    # The first draft pass reads the broken file and writes a good one, so the final pass is clean.
    assert again.ok and again.counts["error"] == 0 and again.pdf


def test_a_failed_compile_does_not_reuse_the_old_pdf(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, "\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n")
    assert build.build(root, cfg, "p7").pdf
    (root / "tex" / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\n\\end{document}\n")
    empty = build.build(root, cfg, "p7")
    assert not empty.pdf and build.output_file("p7", empty.id, "main.pdf") is None


def test_word_count(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    count = build.word_count(root, cfg)
    assert count["ok"] and count["text"] > 10 and count["headers"] >= 2


def test_decode_label() -> None:
    assert build.decode_label("<FEFF0053>1") == "S1"
    assert build.decode_label("12") == "12"
