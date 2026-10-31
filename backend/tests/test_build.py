"""Real builds with MacTeX: skipped where latexmk is not installed."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from thor import build, figures, projects, synctex

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
    ids = []
    for i in range(build.KEEP_BUILDS + 1):
        (root / "tex" / "main.tex").write_text(f"\\documentclass{{article}}\n\\begin{{document}}\nHi {i}\n\\end{{document}}\n")
        ids.append(build.build(root, cfg, "p4").id)
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
    # One pass finds nothing moved, and it has written the PDF.
    assert edited.ok and edited.passes == 1 and edited.counts == first.counts
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
    # The pass stops on the broken file, so the compile starts again from scratch.
    assert again.ok and again.counts["error"] == 0 and again.pdf


def test_a_failed_compile_does_not_reuse_the_old_pdf(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, "\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n")
    assert build.build(root, cfg, "p7").pdf
    (root / "tex" / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\n\\end{document}\n")
    empty = build.build(root, cfg, "p7")
    assert not empty.pdf and build.output_file("p7", empty.id, "main.pdf") is None


def text_of(project: str, result: build.BuildResult) -> str:
    with pymupdf.open(build.output_file(project, result.id, "main.pdf")) as doc:
        return " ".join(page.get_text() for page in doc)


def edit(root: Path, old: str, new: str) -> None:
    main = root / "tex" / "main.tex"
    main.write_text(main.read_text().replace(old, new))


def wait_for_spare(project: str) -> None:
    build._spares[project].ready.wait(30)


def test_a_compile_wakes_the_latex_that_read_the_preamble(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    first = build.build(root, cfg, "w1")
    assert first.ok and not first.warm
    wait_for_spare("w1")
    edit(root, "More detail.", "Text added while LaTeX waited.")
    woken = build.build(root, cfg, "w1")
    assert woken.ok and woken.warm and woken.passes == 1
    assert "Text added while LaTeX waited." in text_of("w1", woken)
    assert woken.counts == first.counts and woken.si_start == first.si_start


def test_an_error_after_waking_names_the_right_line(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    assert build.build(root, cfg, "w2").ok
    wait_for_spare("w2")
    edit(root, "More detail.", "More detail.\n\\undefinedcommand")
    failed = build.build(root, cfg, "w2")
    line = (root / "tex" / "main.tex").read_text().splitlines().index("\\undefinedcommand") + 1
    errors = [p for p in failed.problems if p["kind"] == "error"]
    assert not failed.ok and errors[0]["file"] == "main.tex" and errors[0]["line"] == line
    # Fixing it needs nothing from the failed pass.
    edit(root, "\\undefinedcommand", "")
    assert build.build(root, cfg, "w2").ok


def test_synctex_works_after_waking(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    assert build.build(root, cfg, "w3").ok
    wait_for_spare("w3")
    edit(root, "More detail.", "More detail, after waking.")
    woken = build.build(root, cfg, "w3")
    assert woken.warm
    pdf = build.output_file("w3", woken.id, "main.pdf")
    line = (root / "tex" / "main.tex").read_text().splitlines().index("More detail, after waking.") + 1
    boxes = synctex.forward(pdf, root / "tex", "main.tex", line)
    assert boxes
    found = synctex.backward(pdf, root / "tex", boxes[0]["page"], boxes[0]["left"] + 5, boxes[0]["top"] + 2)
    assert found and found["file"] == "main.tex" and found["line"] == line


def test_a_changed_preamble_is_not_woken(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    assert build.build(root, cfg, "w4").ok
    wait_for_spare("w4")
    edit(root, "\\linenumbers\n", "\\linenumbers\n\\newcommand{\\added}{Defined in the new preamble.}\n")
    edit(root, "More detail.", "\\added")
    again = build.build(root, cfg, "w4")
    assert again.ok and not again.warm and "Defined in the new preamble." in text_of("w4", again)


def test_a_changed_file_the_preamble_read_is_not_woken(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    style = root / "tex" / "own.sty"
    style.write_text("\\newcommand{\\own}{First version.}\n")
    edit(root, "\\linenumbers\n", "\\linenumbers\n\\usepackage{own}\n")
    edit(root, "More detail.", "\\own")
    assert build.build(root, cfg, "w5").ok
    wait_for_spare("w5")
    style.write_text("\\newcommand{\\own}{Second version.}\n")
    again = build.build(root, cfg, "w5")
    assert again.ok and not again.warm and "Second version." in text_of("w5", again)


def test_moved_references_get_another_pass(tmp_path: Path) -> None:
    source = ("\\documentclass{article}\n\\begin{document}\n\\section{A}\\label{a}\nSee section \\ref{b}.\n"
              "\\section{B}\\label{b}\nText.\n\\end{document}\n")
    root, cfg = make_project(tmp_path, source)
    assert build.build(root, cfg, "w6").ok
    edit(root, "\\section{A}\\label{a}", "\\section{New}\n\\section{A}\\label{a}")
    moved = build.build(root, cfg, "w6")
    assert moved.ok and moved.passes == 2 and "See section 3" in text_of("w6", moved)


def test_a_new_citation_runs_bibtex(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    first = build.build(root, cfg, "w7")
    assert first.ok and first.counts["undefined_citation"] == 1
    edit(root, "A missing figure is", "As \\citet{Second2001} found, a missing figure is")
    edit(root, "@article{Price1994,", "@article{Second2001, author = {Second, A.}, title = {Another}, journal = {JGR}, year = {2001}}\n@article{Price1994,")
    cited = build.build(root, cfg, "w7")
    assert cited.ok and cited.counts["undefined_citation"] == 1 and "Another" in text_of("w7", cited)


def test_an_edited_bib_file_runs_bibtex(tmp_path: Path) -> None:
    source = ("\\documentclass{article}\n\\usepackage{natbib}\n\\begin{document}\n\\citet{a}\n"
              "\\bibliographystyle{plainnat}\\bibliography{refs}\n\\end{document}\n")
    root, cfg = make_project(tmp_path, source)
    bib = root / "tex" / "refs.bib"
    bib.write_text("@article{a, author={A, B}, title={First title}, journal={J}, year={2001}}\n")
    assert build.build(root, cfg, "w8").ok
    bib.write_text("@article{a, author={A, B}, title={Better title}, journal={J}, year={2001}}\n")
    again = build.build(root, cfg, "w8")
    assert again.ok and "Better title" in text_of("w8", again)


def test_latexmkrc_keeps_latexmk_in_charge(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    (root / "tex" / "latexmkrc").write_text("# A project's own rules.\n")
    assert build.build(root, cfg, "w9").ok
    assert "w9" not in build._spares
    edit(root, "More detail.", "More detail still.")
    again = build.build(root, cfg, "w9")
    assert again.ok and not again.warm and again.passes == 2


def test_a_discarded_spare_leaves_the_log_for_latexmk(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    assert build.build(root, cfg, "w10").ok
    wait_for_spare("w10")
    build.discard_spare("w10")
    assert "Output written" in (build.builds_dir("w10") / "work" / "main.log").read_text(errors="replace")


@pytest.mark.parametrize("engine", ["xelatex", "lualatex"])
def test_other_engines_wake_too(tmp_path: Path, engine: str) -> None:
    source = ("\\documentclass{article}\n\\usepackage{fontspec}\n\\title{\u03a9mega \u4e2d\u6587 preamble}\n"
              "\\begin{document}\nFirst text.\n\\end{document}\n")
    root, cfg = make_project(tmp_path, source)
    cfg.engine = engine
    assert build.build(root, cfg, f"w-{engine}").ok
    wait_for_spare(f"w-{engine}")
    edit(root, "First text.", "Second text.")
    woken = build.build(root, cfg, f"w-{engine}")
    assert woken.ok and woken.warm and "Second text." in text_of(f"w-{engine}", woken)


MULTIBIB = r"""\begin{filecontents*}[overwrite]{references.bib}
@article{Main2001, author = {Main, A.}, title = {In the main list}, journal = {J}, year = {2001}}
@article{Supp2002, author = {Supp, B.}, title = {In the second list}, journal = {J}, year = {2002}}
\end{filecontents*}
\documentclass{article}
\usepackage{natbib}
\usepackage{multibib}
\newcites{S}{Supplementary references}
\begin{document}
Main text \citep{Main2001}, and more \citepS{Supp2002}.

First text.
\bibliographystyle{plainnat}
\bibliography{references}
\bibliographystyleS{plainnat}
\bibliographyS{references}
\end{document}
"""


def test_a_multibib_paper_stays_on_the_fast_path(tmp_path: Path) -> None:
    """\\newcites empties S.aux as the preamble is read. A waiting LaTeX must not make that look like a change."""
    root, cfg = make_project(tmp_path, MULTIBIB)
    cfg.bib_extra_lists = ["S"]
    first = build.build(root, cfg, "m1")
    assert first.ok and "In the second list" in text_of("m1", first)
    wait_for_spare("m1")
    unchanged = build.build(root, cfg, "m1")
    assert unchanged.passes == 0
    edit(root, "First text.", "Second text.")
    woken = build.build(root, cfg, "m1")
    assert woken.ok and woken.warm and woken.passes == 1
    assert "Second text." in text_of("m1", woken) and "In the second list" in text_of("m1", woken)


def test_a_discarded_spare_puts_back_what_the_preamble_emptied(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, MULTIBIB)
    cfg.bib_extra_lists = ["S"]
    assert build.build(root, cfg, "m2").ok
    wait_for_spare("m2")
    build.discard_spare("m2")
    assert "Supp2002" in (build.builds_dir("m2") / "work" / "S.aux").read_text()


def png_with_alpha(path: Path, opaque: bool) -> None:
    image = Image.effect_noise((900, 900), 60).convert("RGBA")
    if not opaque:
        image.putpixel((3, 3), (0, 0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, dpi=(150, 150))


def test_figures_get_fast_copies_only_when_it_changes_nothing(tmp_path: Path) -> None:
    (latex := tmp_path / "tex").mkdir()
    png_with_alpha(latex / "figs" / "solid.png", opaque=True)
    png_with_alpha(latex / "figs" / "clear.png", opaque=False)
    shadow = tmp_path / "shadow"
    assert figures.make_copy(latex / "figs" / "solid.png", shadow / "figs" / "solid.png")
    assert not figures.make_copy(latex / "figs" / "clear.png", shadow / "figs" / "clear.png")
    with Image.open(latex / "figs" / "solid.png") as a, Image.open(shadow / "figs" / "solid.png") as b:
        assert b.mode == "RGB" and a.size == b.size and tuple(round(x) for x in b.info["dpi"]) == (150, 150)
        assert a.convert("RGB").tobytes() == b.tobytes()


def test_a_compile_uses_fast_figures_and_reports_the_originals(tmp_path: Path) -> None:
    source = ("\\documentclass{article}\n\\usepackage{graphicx}\n\\begin{document}\n"
              "\\includegraphics[width=3cm]{figs/solid.png}\nFirst.\n\\end{document}\n")
    root, cfg = make_project(tmp_path, source)
    png_with_alpha(root / "tex" / "figs" / "solid.png", opaque=True)
    first = build.build(root, cfg, "f1")
    assert first.ok
    shadow = build.builds_dir("f1") / "figures"
    for _ in range(200):
        if (shadow / "manifest.json").is_file():
            break
        time.sleep(0.05)
    assert (shadow / "figs" / "solid.png").is_file()
    edit(root, "First.", "Second.")
    second = build.build(root, cfg, "f1")
    assert second.ok
    fls = build.output_file("f1", second.id, "main.fls").read_text()
    assert str(root / "tex" / "figs" / "solid.png") in fls and str(shadow) not in fls
    # Replacing the figure must not show its old copy.
    png_with_alpha(root / "tex" / "figs" / "solid.png", opaque=False)
    third = build.build(root, cfg, "f1")
    assert third.ok and not (shadow / "figs" / "solid.png").exists()


def test_word_count(tmp_path: Path) -> None:
    root, cfg = make_project(tmp_path, PAPER)
    count = build.word_count(root, cfg)
    assert count["ok"] and count["text"] > 10 and count["headers"] >= 2


def test_decode_label() -> None:
    assert build.decode_label("<FEFF0053>1") == "S1"
    assert build.decode_label("12") == "12"


def test_xelatex_font_from_tex_live(tmp_path: Path) -> None:
    """Overleaf's XeLaTeX finds TeX Live's fonts by name; on a Mac it finds only macOS's, so say what to do."""
    root, cfg = make_project(tmp_path, "\\documentclass{article}\n\\usepackage{fontspec}\n\\setmainfont{TeX Gyre Termes}\n"
                                       "\\begin{document}\nThunder.\n\\end{document}\n")
    assert cfg.engine == "xelatex"
    result = build.build(root, cfg, "font")
    if result.ok:
        pytest.skip("TeX Gyre Termes is installed in macOS here")
    assert result.problems[0]["message"].startswith("XeLaTeX cannot find the font “TeX Gyre Termes”")
    cfg.engine = "lualatex"
    assert build.build(root, cfg, "font").ok
