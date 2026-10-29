"""Revision folders with real builds: skipped where MacTeX is not installed."""

from __future__ import annotations

import hashlib
import shutil
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
    assert [s.name for s in status.steps] == ["Source snapshot", "Clean PDF", "Working copies", "Tracked PDF", "Change log"]
    assert "identical" in status.steps[2].detail
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
