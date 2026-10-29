"""The tracked PDF on a sample paper with the hard cases: removed, replaced, new and renamed-but-identical
figures, a moved table, a changed caption with a citation, a new table, a filecontents bibliography,
multibib and a supplement. Skipped where MacTeX is not installed."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import pymupdf
import pytest

from thor import build, paths, projects, tracked

TEXBIN = "/Library/TeX/texbin"
pytestmark = pytest.mark.skipif(not shutil.which("latexdiff", path=TEXBIN), reason="MacTeX is not installed")

PREAMBLE = r"""\begin{filecontents*}[overwrite]{references.bib}
@article{Price1994, author = {Price, Colin}, title = {Lightning and climate}, journal = {JGR}, year = {1994}}
@article{Holle2016, author = {Holle, Ronald}, title = {Lightning deaths}, journal = {NH}, year = {2016}}
%s\end{filecontents*}
\documentclass{article}
\usepackage{graphicx}
\usepackage[numbers]{natbib}
\usepackage{multibib}
\newcites{S}{Supplementary references}
\usepackage{lineno}
\newcommand{\figfile}[2]{%%
  \IfFileExists{figures/#2.pdf}{\includegraphics[#1]{figures/#2.pdf}}{%%
  \includegraphics[#1]{supplementary/#2.pdf}}}
\begin{document}
\linenumbers
"""

ENDING = r"""
\bibliographystyle{unsrtnat}
\bibliography{references}
\clearpage
\nolinenumbers
{\Large\bfseries Supplementary Information}

Supplementary text \citeS{Holle2016}.
\bibliographystyleS{unsrtnat}
\bibliographyS{references}
\end{document}
"""

OLD_BODY = r"""\section{Results}
Lightning is common \citep{Price1994}. Figure~\ref{fig:old} shows the old map and Figure~\ref{fig:same} the cycle.

\begin{figure}[h]\centering\figfile{width=0.25\textwidth}{fig_old}\caption{Old map.}\label{fig:old}\end{figure}
\begin{figure}[h]\centering\figfile{width=0.25\textwidth}{fig_replaced}\caption{A figure that is replaced.}\label{fig:replaced}\end{figure}
\begin{figure}[h]\centering\figfile{width=0.25\textwidth}{fig_same}\caption{The diurnal cycle.}\label{fig:same}\end{figure}

\begin{table}[h]\centering\caption{First table.}\label{tab:first}\begin{tabular}{ll}a & b\\ \end{tabular}\end{table}

Text between the tables.

\begin{table}[h]\centering\caption{Second table, deaths.}\label{tab:second}\begin{tabular}{ll}c & d\\ \end{tabular}\end{table}
"""

NEW_BODY = r"""\section{Results}
Lightning is very common \citep{Price1994}. Figure~\ref{fig:same} shows the cycle.

\begin{table}[h]\centering\caption{Second table, deaths by state \citep{Holle2016}.}\label{tab:second}\begin{tabular}{ll}c & d\\ \end{tabular}\end{table}

\begin{figure}[h]\centering\figfile{width=0.25\textwidth}{fig_replaced}\caption{A figure that is replaced.}\label{fig:replaced}\end{figure}
\begin{figure}[h]\centering\figfile{width=0.25\textwidth}{fig_cycle}\caption{The diurnal cycle.}\label{fig:same}\end{figure}
\begin{figure}[h]\centering\figfile{width=0.25\textwidth}{fig_new}\caption{A new figure.}\label{fig:new}\end{figure}

\begin{table}[h]\centering\caption{First table.}\label{tab:first}\begin{tabular}{ll}a & b\\ \end{tabular}\end{table}

Text between the tables.

\begin{table}[h]\centering\caption{A new table.}\label{tab:third}\begin{tabular}{ll}e & f\\ \end{tabular}\end{table}
"""


def figure_pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=120)
    page.draw_rect(pymupdf.Rect(10, 10, 190, 110), color=(0, 0, 0.6), width=2)
    page.insert_text((30, 65), text, fontsize=16)
    doc.save(path)


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def tex_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_TOOL_PATH", f"{TEXBIN}:/usr/bin:/bin")


@pytest.fixture
def revisions(tmp_path: Path) -> tuple[Path, Path]:
    old, new = tmp_path / "old", tmp_path / "new"
    figure_pdf(old / "figures" / "fig_old.pdf", "old map")
    figure_pdf(old / "figures" / "fig_replaced.pdf", "version one")
    figure_pdf(old / "figures" / "fig_same.pdf", "cycle")
    (old / "main.tex").write_text(PREAMBLE % "" + OLD_BODY + ENDING)
    figure_pdf(new / "figures" / "fig_replaced.pdf", "version two")
    (new / "figures").mkdir(exist_ok=True)
    shutil.copyfile(old / "figures" / "fig_same.pdf", new / "figures" / "fig_cycle.pdf")  # renamed, identical
    figure_pdf(new / "figures" / "fig_new.pdf", "brand new")
    extra = "@article{Cecil2014, author = {Cecil, Daniel}, title = {LIS}, journal = {AR}, year = {2014}}\n"
    (new / "main.tex").write_text(PREAMBLE % extra + NEW_BODY + ENDING)
    return old, new


def run(old: Path, new: Path, out: Path) -> tracked.TrackedResult:
    return tracked.build_tracked(old, "main.tex", new, "main.tex", out, macro="figfile", folders=["figures", "supplementary"],
                                 extra_lists=["S"])


def test_tracked_pdf_handles_the_hard_cases(revisions: tuple[Path, Path], tmp_path: Path) -> None:
    old, new = revisions
    out = tmp_path / "tracked"
    result = run(old, new, out)
    assert result.ok, result.problems
    assert result.counts["undefined_reference"] == 0 and result.counts["undefined_citation"] == 0
    assert result.pages >= 2 and result.additions > 0 and result.deletions > 0
    s = (out / "tracked.tex").read_text()

    # The renamed but identical figure is not marked; the replaced one is crossed out and framed.
    same = f"fig/{md5(old / 'figures' / 'fig_same.pdf')[:12]}.pdf"
    assert re.search(r"\\includegraphics\[[^\]]*\]\{" + re.escape(same) + "}", s)
    assert not re.search(r"\\DIF(?:add|del)FL\{\\includegraphics\[[^\]]*\]\{" + re.escape(same), s)
    replaced_old = f"fig/{md5(old / 'figures' / 'fig_replaced.pdf')[:12]}.pdf"
    replaced_new = f"fig/{md5(new / 'figures' / 'fig_replaced.pdf')[:12]}.pdf"
    assert re.search(r"\\DIFdelFL\{\\includegraphics\[[^\]]*\]\{" + re.escape(replaced_old), s)
    assert re.search(r"\\DIFaddFL\{\\includegraphics\[[^\]]*\]\{" + re.escape(replaced_new), s)
    assert result.figures == {"unchanged": 1, "new or replaced": 2, "removed or replaced": 2}

    # Tables: each once, the moved one in its new place, the caption change marked with a protected citation.
    for label in ("tab:first", "tab:second", "tab:third"):
        assert s.count(f"\\label{{{label}}}") == 1, label
    assert s.index("\\label{tab:second}") < s.index("\\label{tab:first}")
    caption = s[s.index("Second table"):s.index("\\label{tab:second}")]
    assert "\\DIFaddFL{" in caption and "\\protect\\citep" in caption
    assert sorted(result.tables) == ["tab:first: unchanged", "tab:second: caption changed", "tab:third: new"]

    # The bibliography block is the clean one from the new source.
    clean = re.search(r"\\begin\{filecontents\*\}.*?\\end\{filecontents\*\}", (new / "main.tex").read_text(), re.S)
    assert clean.group(0) in s
    # The removed figure, still cited in deleted text, keeps its baseline number.
    assert re.search(r"^\\newlabel\{fig:old\}\{\{1\}", s, re.M)
    assert "\\renewcommand{\\DIFadd}[1]{{\\protect\\color{DIFGREEN}\\uline{#1}}}" in s
    # Nothing is written next to either source.
    assert sorted(p.name for p in new.iterdir()) == ["figures", "main.tex"]


def test_baseline_build_is_cached(revisions: tuple[Path, Path], tmp_path: Path) -> None:
    old, new = revisions
    cache = tmp_path / "baseline"
    first = tracked.baseline_aux(old, "main.tex", "pdflatex", ["S"], cache)
    stamp = (cache / "source.sha256").stat().st_mtime_ns
    assert tracked.baseline_aux(old, "main.tex", "pdflatex", ["S"], cache) == first
    assert (cache / "source.sha256").stat().st_mtime_ns == stamp


def test_clean_build_resolves_multibib_and_finds_the_supplement(revisions: tuple[Path, Path], tmp_path: Path) -> None:
    _, new = revisions
    root = tmp_path / "project"
    shutil.copytree(new, root / "tex")
    cfg = projects.load_config(root)
    assert cfg.bib_extra_lists == ["S"] and cfg.figure_macro == "figfile"
    result = build.build(root, cfg, "multibib")
    assert result.ok and result.counts["undefined_citation"] == 0, result.problems
    assert result.si_start is not None and result.si_start > 1


def test_helpers() -> None:
    assert tracked.caption_span(r"\begin{table}\caption[s]{A {nested} \{x\} cap}\label{t}") == (24, 46)
    src, tables = tracked.strip_tables("x\n\\begin{longtable}{ll}\\caption{L}\\label{tab:l}\\\\ a&b\\end{longtable}\ny")
    assert list(tables.values())[0].label == "tab:l" and tracked.table_key("tab:l") in src
    assert tracked.protect_citations("\\mbox{%DIFAUXCMD\n\\citep{x}}") == "\\mbox{%DIFAUXCMD\n\\protect\\citep{x}}"


def test_inline_inputs(tmp_path: Path) -> None:
    (tmp_path / "rows.tex").write_text("a & b \\\\\n")
    src = "\\begin{longtable}{ll}\n\\input{rows}\n% \\input{rows}\n\\input{glyphtounicode}\n\\end{longtable}"
    out = tracked.inline_inputs(src, tmp_path)
    assert "a & b" in out and "\\input{glyphtounicode}" in out and "% \\input{rows}" in out


def test_deleted_text_keeps_commands_the_new_source_dropped(tmp_path: Path) -> None:
    r"""The baseline had multibib (\citeS) and a \kmsq macro; the new source has neither, but deleted text uses both."""
    old, new = tmp_path / "old", tmp_path / "new"
    bib = ("\\begin{filecontents*}[overwrite]{references.bib}\n"
           "@article{Price1994, author = {Price, C.}, title = {L}, journal = {J}, year = {1994}}\n"
           "@article{Holle2016, author = {Holle, R.}, title = {D}, journal = {N}, year = {2016}}\n"
           "\\end{filecontents*}\n")
    old.mkdir()
    old_bib = bib.replace("\\end{filecontents*}", "@article{Gone2001, author = {Gone, A.}, title = {O}, journal = {J}, year = {2001}}\n\\end{filecontents*}")
    (old / "main.tex").write_text(old_bib + "\\documentclass{article}\n\\usepackage[numbers]{natbib}\n\\usepackage{multibib}\n"
        "\\newcites{S}{Supplementary references}\n\\newcommand{\\kmsq}{km$^{-2}$}\n\\begin{document}\n"
        "Density is 2 flashes \\kmsq{} \\citep{Price1994}. Deaths are rare \\citeS{Holle2016}. "
        "An old study \\citep{Gone2001} is dropped.\n\n"
        "\\bibliographystyle{unsrtnat}\n\\bibliography{references}\n"
        "\\bibliographystyleS{unsrtnat}\n\\bibliographyS{references}\n\\end{document}\n")
    new.mkdir()
    (new / "main.tex").write_text(bib + "\\documentclass{article}\n\\usepackage[numbers]{natbib}\n\\begin{document}\n"
        "Density is high \\citep{Price1994}.\n\n\\bibliographystyle{unsrtnat}\n\\bibliography{references}\n\\end{document}\n")
    result = tracked.build_tracked(old, "main.tex", new, "main.tex", tmp_path / "out", macro="", folders=[])
    assert result.ok and result.counts["error"] == 0, result.problems
    s = (tmp_path / "out" / "tracked.tex").read_text()
    assert "\\providecommand{\\kmsq}{km$^{-2}$}" in s and "\\providecommand{\\citeS}[2][]{[#2]}" in s
    # The reference removed from the bibliography keeps its baseline number in the deleted text.
    assert result.counts["undefined_citation"] == 0
    assert re.search(r"\\AtBeginDocument\{\\bibcite\{Gone2001\}\{\{2\}", s)


def test_biblatex_with_a_separate_bib_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """biblatex and biber, with references.bib beside the new source only (the frozen baseline folder has none)."""
    thin = paths.thin_binary_dir(shutil.which("biber", path=TEXBIN))
    monkeypatch.setenv("THOR_TOOL_PATH", ":".join(d for d in (thin, TEXBIN, "/usr/bin", "/bin") if d))
    old, new = tmp_path / "old", tmp_path / "new"
    head = ("\\documentclass{article}\n\\usepackage[backend=biber,style=authoryear]{biblatex}\n"
            "\\addbibresource{references.bib}\n\\begin{document}\n")
    old.mkdir()
    (old / "main.tex").write_text(head + "Thunder follows lightning \\parencite{Price1994}.\n\n\\printbibliography\n\\end{document}\n")
    new.mkdir()
    (new / "references.bib").write_text(
        "@article{Price1994, author = {Price, Colin}, title = {Lightning and climate}, journal = {JGR}, year = {1994}}\n"
        "@article{Holle2016, author = {Holle, Ron}, title = {Deaths}, journal = {NH}, year = {2016}}\n")
    (new / "main.tex").write_text(head + "Thunder follows lightning \\parencite{Price1994}, and it can kill "
                                  "\\parencite{Holle2016}.\n\n\\printbibliography\n\\end{document}\n")
    result = tracked.build_tracked(old, "main.tex", new, "main.tex", tmp_path / "out", macro="", folders=[])
    assert result.ok and result.counts["undefined_citation"] == 0, result.problems
    with pymupdf.open(tmp_path / "out" / "build" / "tracked.pdf") as doc:
        text = " ".join(page.get_text() for page in doc)
    assert "References" in text and "Holle" in text and "Price" in text
