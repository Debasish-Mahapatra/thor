from __future__ import annotations

from pathlib import Path

import pytest

from thor import claude_cli, first_run
from thor.claude_cli import ClaudeStatus
from thor.first_run import FirstRun
from thor.setup_tex import TexInstaller


class FakeTex(TexInstaller):
    """Installs instantly with the given outcome."""

    def __init__(self, tmp: Path, installed: bool = False, outcome: str = "done") -> None:
        super().__init__(downloads=tmp / "dl", search=lambda: [], texbin=tmp / "texbin")
        self.is_installed = installed
        self.outcome = outcome
        self.starts = 0

    def installed(self) -> bool:
        return self.is_installed

    def start(self) -> dict:
        self.starts += 1
        self.state.step = self.outcome
        self.state.message = {"done": "MacTeX is installed (took about 4 min).", "failed": "Signature wrong."}.get(
            self.outcome, "")
        self.is_installed = self.outcome == "done"
        return self.status()

    def wait(self, timeout: float | None = None) -> None:
        pass


class FakeClaude:
    """claude_cli.status and run_tool for a Mac where Claude Code may be missing or signed out."""

    def __init__(self, installed: bool = True, signed_in: bool = True, install_works: bool = True) -> None:
        self.installed, self.signed_in, self.install_works = installed, signed_in, install_works
        self.commands: list[list[str]] = []

    def status(self) -> ClaudeStatus:
        if not self.installed:
            return ClaudeStatus(detail="not found")
        return ClaudeStatus(path="/fake/claude", version="9.9.9", logged_in=self.signed_in)

    def run(self, args: list[str], timeout: float = 15) -> tuple[int, str]:
        self.commands.append(args)
        if args[:2] == ["/bin/bash", "-c"]:
            self.installed = self.install_works
            return (0, "installed") if self.install_works else (1, "curl: could not resolve host")
        if args[1:] == ["auth", "login"]:
            self.signed_in = True
            return 0, ""
        raise AssertionError(args)


def run_setup(tmp_path: Path, claude: FakeClaude, tex: FakeTex) -> dict:
    setup = FirstRun(tex, claude_status=claude.status, run=claude.run, enabled=True)
    setup.start()
    setup.wait(10)
    return setup.status()


def states(status: dict) -> dict[str, str]:
    return {s["id"]: s["state"] for s in status["steps"]}


def test_nothing_to_do(tmp_path: Path) -> None:
    claude, tex = FakeClaude(), FakeTex(tmp_path, installed=True)
    status = run_setup(tmp_path, claude, tex)
    assert status["finished"] and not status["failed"]
    assert states(status) == {"claude": "done", "mactex": "done"}
    assert claude.commands == [] and tex.starts == 0


def test_installs_claude_code_and_mactex(tmp_path: Path) -> None:
    claude, tex = FakeClaude(installed=False), FakeTex(tmp_path)
    status = run_setup(tmp_path, claude, tex)
    assert states(status) == {"claude": "done", "mactex": "done"}
    assert claude.commands == [["/bin/bash", "-c", first_run.CLAUDE_INSTALL]]
    assert tex.starts == 1
    assert status["steps"][1]["message"] == "MacTeX is installed (took about 4 min)."


def test_signs_in_when_signed_out(tmp_path: Path) -> None:
    claude = FakeClaude(signed_in=False)
    status = run_setup(tmp_path, claude, FakeTex(tmp_path, installed=True))
    assert claude.commands == [["/fake/claude", "auth", "login"]]
    assert states(status)["claude"] == "done"


def test_one_failure_does_not_stop_the_other_step(tmp_path: Path) -> None:
    claude, tex = FakeClaude(installed=False, install_works=False), FakeTex(tmp_path)
    status = run_setup(tmp_path, claude, tex)
    assert states(status) == {"claude": "failed", "mactex": "done"}
    assert "could not resolve host" in status["steps"][0]["message"]
    assert status["failed"] and status["finished"]


@pytest.mark.parametrize("outcome, message", [("cancelled", "password dialog was closed"), ("failed", "Signature wrong.")])
def test_mactex_problems_are_reported(tmp_path: Path, outcome: str, message: str) -> None:
    status = run_setup(tmp_path, FakeClaude(), FakeTex(tmp_path, outcome=outcome))
    assert states(status)["mactex"] == "failed" and message in status["steps"][1]["message"]


def test_needed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_cli, "locate", lambda custom=None: "/fake/claude")
    assert FirstRun(FakeTex(tmp_path, installed=True)).needed() is False
    assert FirstRun(FakeTex(tmp_path, installed=False)).needed() is True
    monkeypatch.setattr(claude_cli, "locate", lambda custom=None: None)
    assert FirstRun(FakeTex(tmp_path, installed=True)).needed() is True


def test_nothing_is_installed_or_signed_in_to_when_another_model_works(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **_) -> tuple[int, str]:
        calls.append(args)
        return 0, ""

    tex = TexInstaller(downloads=tmp_path / "dl", search=lambda: [], texbin=tmp_path / "texbin")
    missing = FirstRun(tex, claude_status=lambda: ClaudeStatus(), run=run, other=lambda: "Codex")
    missing._claude()
    assert calls == [] and missing.state.steps[0].state == "done" and "Codex" in missing.state.steps[0].message
    signed_out = FirstRun(tex, claude_status=lambda: ClaudeStatus(path="/bin/claude", logged_in=False), run=run, other=lambda: "Ollama")
    signed_out._claude()
    assert calls == [] and "Ollama" in signed_out.state.steps[0].message
