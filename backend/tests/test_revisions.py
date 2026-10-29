"""Revision folders with real builds: skipped where MacTeX is not installed."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from thor import build, projects, revisions
from thor.store import Store

from .test_tracked import ENDING, NEW_BODY, OLD_BODY, PREAMBLE, TEXBIN, figure_pdf

pytestmark = pytest.mark.skipif(not shutil.which("latexdiff", path=TEXBIN), reason="MacTeX is not installed")


@pytest.fixture(autouse=True)
def tex_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_TOOL_PATH", f"{TEXBIN}:/usr/bin:/bin")


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, projects.ProjectConfig]:
    root = tmp_path / "paper"
    tex = root / "manuscript"
    figure_pdf(tex / "figures" / "fig_old.pdf", "old map")
    figure_pdf(tex / "figures" / "fig_replaced.pdf", "version one")
    figure_pdf(tex / "figures" / "fig_same.pdf", "cycle")
    (tex / "main.tex").write_text(PREAMBLE % "" + OLD_BODY + ENDING)
    (tex / "main_draft.pdf").write_bytes(b"%PDF-1.5\n")  # the paper keeps its drafts beside the source
    cfg = projects.load_config(root)
    assert cfg.working_copies == ["manuscript"] and cfg.revisions_dir == "manuscript"
    return root, cfg


def tree_hashes(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def compile_and_sync(root: Path, cfg: projects.ProjectConfig, manager: revisions.Revisions, pid: str) -> revisions.SyncStatus:
    result = build.build(root, cfg, pid)
    assert result.ok, result.problems
    assert manager.enqueue(root, cfg, pid, result)
    manager.wait(pid, 300)
    return manager.status(pid)


def test_revision_lifecycle(project: tuple[Path, projects.ProjectConfig], tmp_path: Path) -> None:
    root, cfg = project
    manager = revisions.Revisions(Store(tmp_path / "state.db"))
    pid = "life"
    assert manager.overview(root, cfg, pid)["active"] is None

    # Revision 1 starts from the working source.
    assert manager.start(root, cfg, pid) == 1
    one = root / "manuscript" / "revision1"
    assert (one / "supporting_files" / "baseline_start.tex").is_file()
    status = compile_and_sync(root, cfg, manager, pid)
    assert status.state == "done", status.steps
    for name in ("main_draft.pdf", "supplementary_draft.pdf", "revision_clean.pdf", "revision_tracked.pdf"):
        assert (one / name).is_file(), name
    assert [s.name for s in status.steps] == ["Source snapshot", "Tracked PDF", "Clean PDF", "Working copies", "Change log"]
    assert "identical" in status.steps[3].detail
    assert (root / "manuscript" / "main_draft.pdf").read_bytes() == (one / "main_draft.pdf").read_bytes()
    # The snapshot compiles on its own.
    snapshot = projects.load_config(one / "supporting_files")
    assert build.build(one / "supporting_files", snapshot, "snapshot").ok

    # Revision 2 starts from revision 1's final source, never from the working copy.
    (root / "manuscript" / "main.tex").write_text(PREAMBLE % "" + OLD_BODY.replace("is common", "is frequent") + ENDING)
    frozen = tree_hashes(one)
    assert manager.start(root, cfg, pid) == 2
    two = root / "manuscript" / "revision2"
    baseline = (two / "supporting_files" / "baseline_revision1.tex").read_text()
    assert "is common" in baseline and baseline == (one / "supporting_files" / "main.tex").read_text()

    # The new figures and text of NEW_BODY, then a sync: revision 1 must not change.
    tex = root / "manuscript"
    (tex / "figures" / "fig_old.pdf").unlink()
    figure_pdf(tex / "figures" / "fig_replaced.pdf", "version two")
    shutil.move(tex / "figures" / "fig_same.pdf", tex / "figures" / "fig_cycle.pdf")
    figure_pdf(tex / "figures" / "fig_new.pdf", "brand new")
    extra = "@article{Cecil2014, author = {Cecil, Daniel}, title = {LIS}, journal = {AR}, year = {2014}}\n"
    (tex / "main.tex").write_text(PREAMBLE % extra + NEW_BODY + ENDING)
    status = compile_and_sync(root, cfg, manager, pid)
    assert status.state == "done", status.steps
    assert tree_hashes(one) == frozen
    assert status.tracked and status.tracked["ok"] and status.tracked["additions"] > 0
    log = (two / "supporting_files" / "change_log.txt").read_text()
    assert "Revision 2 started from revision 1's final source" in log
    assert "Updated from manuscript/main.tex: Bibliography (lines 4); Results (lines" in log
    assert ("Files: renamed figures/fig_same.pdf to figures/fig_cycle.pdf, changed figures/fig_replaced.pdf, "
            "added figures/fig_new.pdf, removed figures/fig_old.pdf.") in log
    assert not (two / "supporting_files" / "figures" / "fig_old.pdf").exists()


def test_bibliography_files_are_in_the_snapshot(tmp_path: Path) -> None:
    """A separate references.bib is read by bibtex, not LaTeX, so the build's .fls does not list it."""
    tex = tmp_path / "paper" / "manuscript"
    tex.mkdir(parents=True)
    (tex / "references.bib").write_text(
        "@article{Price1994, author = {Price, Colin}, title = {Lightning}, journal = {JGR}, year = {1994}}\n"
        "@article{Holle2016, author = {Holle, Ron}, title = {Deaths}, journal = {NH}, year = {2016}}\n")
    body = ("\\documentclass{article}\n\\usepackage[numbers]{natbib}\n\\begin{document}\nLightning \\citep{Price1994}%s.\n\n"
            "\\bibliographystyle{unsrtnat}\n\\bibliography{references}\n\\end{document}\n")
    (tex / "main.tex").write_text(body % "")
    root = tmp_path / "paper"
    cfg = projects.load_config(root)
    manager = revisions.Revisions(Store(tmp_path / "state.db"))
    assert manager.start(root, cfg, "bib") == 1
    (tex / "main.tex").write_text(body % " and kills \\citep{Holle2016}")
    status = compile_and_sync(root, cfg, manager, "bib")
    assert status.state == "done", status.steps
    sf = tex / "revision1" / "supporting_files"
    assert (sf / "references.bib").read_text() == (tex / "references.bib").read_text()
    assert status.tracked["counts"]["undefined_citation"] == 0, status.tracked["problems"]


def test_an_overleaf_journal_template(tmp_path: Path) -> None:
    """A flat project as Overleaf exports it: the journal's own class and bibliography style beside main.tex,
    sections in their own files, images in the top folder."""
    root = tmp_path / "paper"
    (root / "sections").mkdir(parents=True)
    (root / "myjournal.cls").write_text("\\ProvidesClass{myjournal}\n\\LoadClass{article}\n\\RequirePackage[round]{natbib}\n"
                                         "\\RequirePackage{graphicx}\n")
    kpse = subprocess.run([f"{TEXBIN}/kpsewhich", "plainnat.bst"], capture_output=True, text=True).stdout.strip()
    shutil.copyfile(kpse, root / "myjournal.bst")
    (root / "references.bib").write_text(
        "@article{Price1994, author = {Price, Colin}, title = {Lightning}, journal = {JGR}, year = {1994}}\n"
        "@article{Holle2016, author = {Holle, Ron}, title = {Deaths}, journal = {NH}, year = {2016}}\n")
    figure_pdf(root / "map.pdf", "map")
    (root / "sections" / "intro.tex").write_text("\\section{Introduction}\nLightning kills \\citep{Holle2016}.\n")
    (root / "main.tex").write_text(
        "\\documentclass{myjournal}\n\\begin{document}\n\\input{sections/intro}\n\\section{Results}\n"
        "Strikes follow convection \\citep{Price1994}.\n\\includegraphics[width=3cm]{map.pdf}\n"
        "\\bibliographystyle{myjournal}\n\\bibliography{references}\n\\end{document}\n")
    cfg = projects.load_config(root)
    assert cfg.working_copies == [] and cfg.revisions_dir == "."
    manager = revisions.Revisions(Store(tmp_path / "state.db"))
    assert manager.start(root, cfg, "journal") == 1
    old = root / "revision1" / "supporting_files" / "tracked_build" / "old"
    assert (old / "sections" / "intro.tex").is_file() and (old / "myjournal.bst").is_file() and (old / "map.pdf").is_file()
    main = root / "main.tex"
    main.write_text(main.read_text().replace("Strikes follow", "Strikes closely follow"))
    status = compile_and_sync(root, cfg, manager, "journal")
    assert status.state == "done", status.steps
    assert (root / "revision1" / "supporting_files" / "myjournal.bst").is_file()
    # Only the edit is marked: the section in its own file is not shown as new.
    assert status.tracked["ok"] and status.tracked["additions"] == 1, status.tracked
    assert not (root / "main_draft.pdf").exists()


def test_a_newer_compile_cuts_an_update_short(project: tuple[Path, projects.ProjectConfig], tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    root, cfg = project
    manager = revisions.Revisions(Store(tmp_path / "state.db"))
    pid = "busy"
    manager.start(root, cfg, pid)
    result = build.build(root, cfg, pid)
    saved: list[revisions.SyncStatus] = []
    monkeypatch.setattr(manager, "_save_status", lambda _pid, status: saved.append(status))
    tracked_step = manager._tracked

    def compile_during_the_tracked_build(*args):
        step = tracked_step(*args)
        if len(saved) == 0 and project_id_pending(manager, pid) is False:
            manager.enqueue(root, cfg, pid, result)  # stands in for a newer compile
        return step

    monkeypatch.setattr(manager, "_tracked", compile_during_the_tracked_build)
    assert manager.enqueue(root, cfg, pid, result)
    manager.wait(pid, 300)
    assert [s.name for s in saved[0].steps] == ["Source snapshot", "Tracked PDF", "Stopped early"]
    assert [s.name for s in saved[1].steps][-1] == "Change log" and saved[1].state == "done"


def project_id_pending(manager: revisions.Revisions, pid: str) -> bool:
    with manager._lock:
        return pid in manager._pending


def test_continue_a_revision_made_outside_thor(project: tuple[Path, projects.ProjectConfig], tmp_path: Path) -> None:
    root, cfg = project
    tex = root / "manuscript"
    # revision1 with its final source and figures, and revision2 with only its baseline file, as by hand.
    shutil.copytree(tex / "figures", tex / "revision1" / "supporting_files" / "figures")
    shutil.copy2(tex / "main.tex", tex / "revision1" / "supporting_files" / "main.tex")
    (tex / "revision2" / "supporting_files").mkdir(parents=True)
    shutil.copy2(tex / "main.tex", tex / "revision2" / "supporting_files" / "baseline_revision1.tex")
    manager = revisions.Revisions(Store(tmp_path / "state.db"))
    assert manager.continue_latest(root, cfg, "cont") == 2
    old = tex / "revision2" / "supporting_files" / "tracked_build" / "old"
    assert (old / "main.tex").is_file() and (old / "figures" / "fig_same.pdf").is_file()
    status = compile_and_sync(root, cfg, manager, "cont")
    assert status.state == "done", status.steps


def test_start_needs_the_previous_final_source(project: tuple[Path, projects.ProjectConfig], tmp_path: Path) -> None:
    root, cfg = project
    (root / "manuscript" / "revision4").mkdir()
    manager = revisions.Revisions(Store(tmp_path / "state.db"))
    with pytest.raises(revisions.RevisionError, match="revision4/supporting_files/main.tex does not exist"):
        manager.start(root, cfg, "x")
    assert not (root / "manuscript" / "revision5").exists()


def test_no_sync_without_an_active_revision(project: tuple[Path, projects.ProjectConfig], tmp_path: Path) -> None:
    root, cfg = project
    manager = revisions.Revisions(Store(tmp_path / "state.db"))
    result = build.build(root, cfg, "idle")
    assert manager.enqueue(root, cfg, "idle", result) is False


def test_describe_changes() -> None:
    old = "\\section{Introduction}\nA.\nB.\n\\section{Results}\nC.\n"
    new = "\\section{Introduction}\nA.\nB, changed.\n\\section{Results}\nC.\nD.\n"
    assert revisions.describe_changes(old, new) == "Introduction (lines 3); Results (lines 6)"
    assert revisions.describe_changes(old, old) == "no text changes"
