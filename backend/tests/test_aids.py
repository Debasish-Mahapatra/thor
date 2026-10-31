from __future__ import annotations

from pathlib import Path

import pytest

from thor import aids, bib, projects, style
from thor.history import History
from thor.projects import ProjectError

BIB = r"""@comment{not an entry}
@string{jgr = "JGR"}
@article{Price1994,
  author = {Price, Colin and Rind, {David}},
  title = "Modeling {Lightning}",
  journal = jgr,
  year = 1994,
  doi = {https://doi.org/10.1029/94JD01124},
}
@book{Other, title={A {nested {deep}} title}}
"""


def make(tmp_path: Path) -> tuple[Path, projects.ProjectConfig]:
    root = tmp_path / "paper"
    tex = root / "tex"
    (tex / "figs").mkdir(parents=True)
    (tex / "main.tex").write_text(
        "\\begin{filecontents*}{inline.bib}\n@misc{Inline2020, title={From filecontents}}\n\\end{filecontents*}\n"
        "\\documentclass{article}\n\\newcommand{\\kmsq}{km^{-2}}\n\\newcommand{\\two}[2]{#1+#2}\n\\begin{document}\n"
        "\\section{Intro}\\label{sec:intro}\nA first word.\n% \\label{sec:commented}\n\\input{part}\n\\end{document}\n")
    (tex / "part.tex").write_text("\\section{Part}\n\\begin{equation}\\label{eq:one}\nE=mc^2\n\\end{equation}\nAnother word here.\n")
    (tex / "refs.bib").write_text(BIB)
    (tex / "figs" / "one.png").write_bytes(b"x")
    (tex / "main.aux").write_text("word")
    (tex / ".hidden.tex").write_text("word")
    return root, projects.ProjectConfig(name="P", latex_dir="tex", main="main.tex")


def test_bib_fields_of_every_shape() -> None:
    entries = bib.parse(BIB)
    assert [e.key for e in entries] == ["Price1994", "Other"]
    price = entries[0]
    assert price.title == "Modeling Lightning" and price.authors == "Price, Colin and Rind, David"
    assert price.year == "1994" and price.doi == "10.1029/94JD01124" and price.fields["journal"] == "jgr"
    assert entries[1].title == "A nested deep title" and price.raw.startswith("@article{Price1994")


def test_a_broken_bib_entry_does_not_stop_the_rest() -> None:
    assert [e.key for e in bib.parse("@article{Broken, title={no end\n@article{Good, title={ok}}\n")] != []


def test_completions_know_labels_cites_files_and_macros(tmp_path: Path) -> None:
    root, cfg = make(tmp_path)
    found = aids.completions(root, cfg)
    labels = {x["key"]: x for x in found["labels"]}
    assert set(labels) == {"sec:intro", "eq:one"} and labels["eq:one"]["file"] == "part.tex" and labels["sec:intro"]["near"] == "Intro"
    assert {c["key"] for c in found["cites"]} == {"Price1994", "Other", "Inline2020"}
    assert found["images"] == ["figs/one.png"] and found["tex"] == ["main.tex", "part.tex"] and found["bibs"] == ["refs.bib"]
    assert found["macros"]["kmsq"] == "km^{-2}" and found["macros"]["two"] == "#1+#2"


def test_search_finds_across_files_and_skips_what_latex_writes(tmp_path: Path) -> None:
    root, cfg = make(tmp_path)
    found = aids.search(root, cfg, "WORD")
    assert found["matches"] == 2 and [f["path"] for f in found["files"]] == ["tex/main.tex", "tex/part.tex"]
    assert found["files"][1]["hits"][0]["line"] == 5 and found["files"][1]["hits"][0]["from"] == 8
    assert aids.search(root, cfg, "WORD", case=True)["matches"] == 0
    assert aids.search(root, cfg, r"\bw\w+d\b", regex=True)["matches"] == 2
    assert aids.search(root, cfg, "word", glob="part.tex")["matches"] == 1
    # Text that looks like a regular expression is only text, unless asked otherwise.
    assert aids.search(root, cfg, "E=mc^2")["matches"] == 1
    with pytest.raises(ProjectError, match="regular expression"):
        aids.search(root, cfg, "(", regex=True)


def test_replace_keeps_the_old_text_in_the_history(tmp_path: Path) -> None:
    root, cfg = make(tmp_path)
    history = History(tmp_path / "history")
    done = aids.replace(root, cfg, "p", history, "word", "term", False, False, ["tex/main.tex", "tex/part.tex"])
    assert done == {"changed": ["tex/main.tex", "tex/part.tex"], "replacements": 2}
    assert "A first term." in (root / "tex" / "main.tex").read_text()
    versions = history.versions("p", "tex/part.tex")
    assert versions[0]["label"] == "Replace across files"
    assert "Another word here." in history.text("p", versions[1]["id"])[1]


def test_replace_treats_plain_text_as_plain_and_regex_as_regex(tmp_path: Path) -> None:
    root, cfg = make(tmp_path)
    history = History(tmp_path / "history")
    aids.replace(root, cfg, "p", history, "first", r"\textbf{first}", False, False, ["tex/main.tex"])
    assert r"A \textbf{first} word." in (root / "tex" / "main.tex").read_text()
    aids.replace(root, cfg, "p", history, r"(Another) (word)", r"\2 \1", True, False, ["tex/part.tex"])
    assert "word Another here." in (root / "tex" / "part.tex").read_text()


def test_replace_only_touches_files_search_would_show(tmp_path: Path) -> None:
    root, cfg = make(tmp_path)
    for path in ("tex/.hidden.tex", "tex/main.aux", "../outside.tex"):
        with pytest.raises(ProjectError):
            aids.replace(root, cfg, "p", History(tmp_path / "h"), "word", "x", False, False, [path])
    assert (root / "tex" / ".hidden.tex").read_text() == "word"


def test_style_rules_from_prose_and_explicit_lines(tmp_path: Path) -> None:
    root, cfg = make(tmp_path)
    (root / "style.md").write_text("Avoid em dashes in the text.\nforbid: /utili[sz]e/i Say use.\nforbid: /(/ broken\n")
    cfg.style_rules = "style.md"
    found = style.rules(root, cfg)
    messages = [r["message"] for r in found]
    assert any("em dash" in m for m in messages) and "Say use." in messages and len(found) == 4
    text = "\\documentclass{article}\n--- in the preamble\n\\begin{document}\nWe utilise the the data --- TODO fix.\n% the the comment\n\\end{document}\n"
    hits = style.check(text, found)
    assert {h["line"] for h in hits} == {4}
    assert {h["text"] for h in hits} >= {"utilise", "the the", "---", "TODO"}


def test_style_rules_without_a_file_are_the_common_ones(tmp_path: Path) -> None:
    root, cfg = make(tmp_path)
    assert len(style.rules(root, cfg)) == len(style.COMMON)
