from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

MAIN_TEX = r"""% Thunder Over Asgard: How Often Thor's Hammer Strikes. Compile with pdfLaTeX.
\begin{filecontents*}[overwrite]{references.bib}
@article{Price1994, author = {Price, Colin}, title = {{Lightning}}, year = {1994}}
\end{filecontents*}
\documentclass[11pt]{article}
\usepackage{graphicx}
\usepackage{lineno}
\usepackage[numbers,sort&compress]{natbib}
% \usepackage{fontspec}   a commented-out package must not change the engine
\linenumbers
\newcommand{\kmsq}{km$^{-2}$}
\newcommand{\figfile}[2]{%
  \IfFileExists{figures/#2.pdf}{\includegraphics[#1]{figures/#2.pdf}}{%
  \IfFileExists{supplementary/#2.pdf}{\includegraphics[#1]{supplementary/#2.pdf}}{%
  \includegraphics[#1]{figures/#2.png}}}}
\begin{document}
Text that mentions the Supplementary Information before the references.
\figfile{width=\textwidth}{fig1}
\bibliographystyle{unsrtnat}
\bibliography{references}
\clearpage
{\Large\bfseries Supplementary Information}
\end{document}
"""


def write_executable(path: Path, script: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """App state in a temporary folder, and a PATH holding only the fake tools a test puts there."""
    home = tmp_path / "thor-home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("THOR_HOME", str(home))
    monkeypatch.setenv("THOR_TOOL_PATH", str(bin_dir))
    return bin_dir


@pytest.fixture
def bin_dir(isolated: Path) -> Path:
    return isolated


@pytest.fixture
def paper(tmp_path: Path) -> Path:
    """A typical paper layout: the LaTeX in manuscript/, revisions at the top, decoys that must be skipped."""
    root = tmp_path / "paper"
    tex = root / "manuscript"
    (tex / "figures").mkdir(parents=True)
    (tex / "supplementary").mkdir()
    (tex / "figures" / "fig1.png").write_bytes(b"\x89PNG")
    (tex / "main.tex").write_text(MAIN_TEX)
    (tex / "main_before_rewrite.tex").write_text("\\documentclass{article}\n\\begin{document}x\\end{document}\n")
    (root / "old").mkdir()
    (root / "old" / "main.tex").write_text("\\documentclass{article}\n")
    (root / "results").mkdir()
    (root / "scripts").mkdir()
    (root / "CLAUDE.md").write_text("Standing instructions.\n")
    (root / "revision1").mkdir()
    (root / "revision1" / "main_draft.pdf").write_bytes(b"%PDF-1.5")
    (root / "revision1" / "main.tex").write_text("\\documentclass{article}\n")
    (root / "revision2" / "supporting_files").mkdir(parents=True)
    for name in ("main_draft.pdf", "supplementary_draft.pdf", "revision_clean.pdf", "revision_tracked.pdf"):
        (root / "revision2" / name).write_bytes(b"%PDF-1.5")
    os.utime(tex / "main_before_rewrite.tex", (2_000_000_000, 2_000_000_000))  # newer than main.tex
    return root
