"""Working around MacTeX's universal biber, and saying why a bibliography tool failed."""

from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path

import pytest

from thor import build, paths, projects

TEXBIN = "/Library/TeX/texbin"


def universal(path: Path, slices: dict[str, bytes]) -> None:
    """A universal binary's header followed by its slices, one per machine."""
    header = struct.pack(">II", 0xCAFEBABE, len(slices))
    offset = 4096
    entries, body = b"", b""
    for machine, data in slices.items():
        entries += struct.pack(">iiIII", paths.CPU_TYPES[machine], 0, offset + len(body), len(data), 12)
        body += data
    path.write_bytes((header + entries).ljust(offset, b"\0") + body)
    path.chmod(0o755)


def script(text: str) -> bytes:
    return f"#!/bin/sh\n{text}\n".encode()


def test_slice_of_this_mac_is_cut_out_and_kept(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    other = "x86_64" if os.uname().machine == "arm64" else "arm64"
    universal(tmp_path / "biber", {other: script("exit 9"), os.uname().machine: script(f'echo run >> "{runs}"')})
    folder = paths.thin_binary_dir(str(tmp_path / "biber"))
    assert folder and (Path(folder) / "biber").read_bytes() == script(f'echo run >> "{runs}"')
    assert paths.thin_binary_dir(str(tmp_path / "biber")) == folder
    assert runs.read_text() == "run\n"  # checked once, then reused


def test_slice_that_does_not_run_is_not_used(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    universal(tmp_path / "biber", {os.uname().machine: script(f'echo run >> "{runs}"; exit 1')})
    assert paths.thin_binary_dir(str(tmp_path / "biber")) is None
    assert paths.thin_binary_dir(str(tmp_path / "biber")) is None
    assert runs.read_text() == "run\n"  # not tried again until the program changes


def test_ordinary_programs_are_left_alone(tmp_path: Path) -> None:
    (tmp_path / "biber").write_bytes(script("exit 0"))
    assert paths.thin_binary_dir(str(tmp_path / "biber")) is None
    assert paths.thin_binary_dir(str(tmp_path / "missing")) is None


LIPO_FAILURE = """Latexmk: Errors, so I did not complete making targets
Collected error summary (may duplicate other messages):
  biber /builds/p/1/main: Could not open biber log file '/builds/p/1/main.blg'
Latexmk: Using biber to make bibliography file(s).
usage: lipo <input_file> <command> [<options> ...]
biber: extracting arm64 binary with lipo failed (wstatus=256)
Latexmk: Could not open biber log file '/builds/p/1/main.blg'
  Citation 'Price1994' on page 2 undefined on input line 116"""

BIBER_ERROR = """INFO - This is Biber 2.21
INFO - Looking for bibtex file 'refs.bib' for section 0
ERROR - Cannot find 'refs.bib'!
INFO - ERRORS: 1
Collected error summary (may duplicate other messages):
  biber main: Run of biber reported errors"""


def test_bibliography_tool_failures_are_explained() -> None:
    lipo = build.tool_failure(LIPO_FAILURE)
    assert lipo.message == "biber failed: extracting arm64 binary with lipo failed (wstatus=256)"
    assert "Could not open biber log file" in lipo.detail
    assert build.tool_failure(BIBER_ERROR).message == "biber failed: Cannot find 'refs.bib'!"
    assert build.tool_failure("Latexmk: All targets are up-to-date") is None


BIBLATEX = r"""\begin{filecontents*}[overwrite]{references.bib}
@article{Price1994, author = {Price, Colin}, title = {Lightning and climate}, journal = {JGR}, year = {1994}}
\end{filecontents*}
\documentclass{article}
\usepackage[backend=biber]{biblatex}
\addbibresource{references.bib}
\begin{document}
Lightning follows convection \autocite{Price1994}.
\printbibliography
\end{document}
"""


@pytest.mark.skipif(not shutil.which("biber", path=TEXBIN), reason="MacTeX is not installed")
def test_biblatex_paper_builds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thin = paths.thin_binary_dir(shutil.which("biber", path=TEXBIN))
    monkeypatch.setenv("THOR_TOOL_PATH", ":".join(d for d in (thin, TEXBIN, "/usr/bin", "/bin") if d))
    root = tmp_path / "paper"
    root.mkdir()
    (root / "main.tex").write_text(BIBLATEX)
    result = build.build(root, projects.load_config(root), "biblatex")
    assert result.ok, result.problems
    assert result.counts.get("undefined_citation", 0) == 0
