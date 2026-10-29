from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from thor import projects
from thor.paths import APP_NAME
from thor.projects import ProjectConfig, ProjectError


def test_detects_a_typical_paper_layout(paper: Path) -> None:
    cfg, candidates, notes = projects.detect(paper)
    # main.tex wins over the newer decoy; old/ and revision folders are not searched.
    assert candidates == ["manuscript/main.tex", "manuscript/main_before_rewrite.tex"]
    assert (cfg.latex_dir, cfg.main) == ("manuscript", "main.tex")
    assert cfg.figure_macro == "figfile"
    assert cfg.figure_folders == ["figures", "supplementary"]
    assert cfg.si_marker == "Supplementary Information"
    assert cfg.line_numbers is True
    assert cfg.engine == "pdflatex"
    assert (cfg.bib_style, cfg.bib_filecontents, cfg.bib_extra_lists) == ("natbib", True, [])
    assert cfg.revisions_dir == "."
    assert (cfg.results_dir, cfg.scripts_dir, cfg.standing_instructions) == ("results", "scripts", "CLAUDE.md")
    assert any("1 other file" in n for n in notes)


def test_engine_from_magic_comment_and_fontspec(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text("% !TEX program = lualatex\n\\documentclass{article}\n")
    assert projects.detect(tmp_path)[0].engine == "lualatex"
    (tmp_path / "main.tex").write_text("\\documentclass{article}\n\\usepackage{amsmath,fontspec}\n")
    assert projects.detect(tmp_path)[0].engine == "xelatex"


def test_multibib_lists_and_graphicspath(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\n\\usepackage{multibib}\n\\newcites{S}{Supplementary references}\n"
        "\\graphicspath{{figs/}{more/}}\n\\bibliography{refs}\n"
    )
    cfg = projects.detect(tmp_path)[0]
    assert cfg.bib_extra_lists == ["S"]
    assert cfg.bib_style == "bibtex"
    assert cfg.figure_folders == ["figs", "more"]
    assert cfg.si_marker == ""


def test_no_revisions_yet_go_in_latex_folder(tmp_path: Path) -> None:
    (tmp_path / "tex").mkdir()
    (tmp_path / "tex" / "main.tex").write_text("\\documentclass{article}\n")
    cfg, _, notes = projects.detect(tmp_path)
    assert cfg.revisions_dir == "tex"
    assert any("No revision folders" in n for n in notes)


def test_no_main_file(tmp_path: Path) -> None:
    cfg, candidates, notes = projects.detect(tmp_path)
    assert candidates == []
    assert "No .tex file" in notes[0]


def test_config_round_trip(paper: Path) -> None:
    cfg = projects.detect(paper)[0]
    cfg.name = 'Thunder "over" \\ Asgard'
    cfg.bib_extra_lists = ["S"]
    path = projects.save_config(paper, cfg)
    text = path.read_text()
    assert text.startswith(f"# {APP_NAME} project settings.")
    assert "[figures]" in text and "# Figure-include macro" in text
    assert tomllib.loads(text)["project"]["name"] == cfg.name
    reopened = projects.open_project(paper)
    assert reopened["config_saved"] is True
    assert reopened["config"] == cfg.to_dict()


def test_saved_config_overrides_detection(paper: Path) -> None:
    (paper / projects.CONFIG_NAME).write_text('[build]\nengine = "xelatex"\n')
    project = projects.open_project(paper)
    assert project["config"]["engine"] == "xelatex"
    assert project["detected"]["engine"] == "pdflatex"
    assert project["config"]["figure_macro"] == "figfile"  # keys not in the file keep their detected value


def test_invalid_toml_is_reported(paper: Path) -> None:
    (paper / projects.CONFIG_NAME).write_text("[build\n")
    with pytest.raises(ProjectError, match="not valid TOML"):
        projects.open_project(paper)


@pytest.mark.parametrize(
    "change, message",
    [
        ({"latex_dir": "../elsewhere"}, "outside the project"),
        ({"latex_dir": "/tmp"}, "must be relative"),
        ({"main": "missing.tex"}, "does not exist"),
        ({"engine": "tectonic"}, "Engine must be"),
        ({"results_dir": "nope"}, "results_dir"),
        ({"line_numbers": "yes"}, "true or false"),
        ({"name": "  "}, "name is empty"),
    ],
)
def test_validation(paper: Path, change: dict, message: str) -> None:
    cfg = ProjectConfig(**{**projects.detect(paper)[0].to_dict(), **change})
    with pytest.raises(ProjectError, match=message):
        projects.save_config(paper, cfg)
    assert not (paper / projects.CONFIG_NAME).exists()


def test_revisions(paper: Path) -> None:
    found = projects.open_project(paper)["revisions"]
    assert [r["number"] for r in found] == [1, 2]
    assert found[0]["missing"] == ["supplementary_draft.pdf", "revision_clean.pdf", "revision_tracked.pdf"]
    assert found[1]["missing"] == [] and found[1]["supporting_files"] is True


def test_list_dir(paper: Path) -> None:
    (paper / ".hidden").mkdir()
    listing = projects.list_dir(paper, "manuscript")
    names = [e["name"] for e in listing["entries"]]
    assert names == ["figures", "supplementary", "main.tex", "main_before_rewrite.tex"]
    assert listing["entries"][2] | {"size": None} == {
        "name": "main.tex", "path": "manuscript/main.tex", "dir": False, "size": None, "kind": "tex"}
    assert ".hidden" not in [e["name"] for e in projects.list_dir(paper)["entries"]]
    with pytest.raises(ProjectError, match="outside"):
        projects.list_dir(paper, "../")


def test_drafts_go_beside_the_source_only_where_they_already_are(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text("\\documentclass{article}\n\\begin{document}x\\end{document}\n")
    assert projects.detect(tmp_path)[0].working_copies == []
    (tmp_path / "main_draft.pdf").write_bytes(b"%PDF-1.5\n")
    assert projects.detect(tmp_path)[0].working_copies == ["."]
