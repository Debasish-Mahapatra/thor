from __future__ import annotations

from pathlib import Path

import pytest

from thor import claude_cli, deps
from thor.settings import AgentSettings, MemorySecret

from .conftest import write_executable

FAKE_CLAUDE = """#!/bin/bash
if [ "$1" = "--version" ]; then echo "9.9.9 (Claude Code)"; exit 0; fi
if [ "$1" = "auth" ]; then
  # A key in the environment would take over the sign-in; Thor must drop it.
  if [ -n "$ANTHROPIC_API_KEY" ]; then echo '{"loggedIn": false}'; exit 0; fi
  echo '{"loggedIn": %s, "authMethod": "claude.ai", "email": "me@example.org", "subscriptionType": "max"}'
fi
"""


def fake_claude(folder: Path, logged_in: bool = True) -> Path:
    return write_executable(folder / "claude", FAKE_CLAUDE % ("true" if logged_in else "false"))


def test_tool_found_with_version(bin_dir: Path) -> None:
    write_executable(bin_dir / "latexmk", "#!/bin/bash\necho\necho 'Latexmk, John Collins, Version 4.86'\n")
    check = deps.check_tool("latexmk", "-v", "every build", True, "TeX", deps.MACTEX_FIX)
    assert check.ok and check.version == "Latexmk, John Collins, Version 4.86"
    assert check.path == str(bin_dir / "latexmk")


def test_missing_tool_has_fix() -> None:
    check = deps.check_tool("latexdiff", "--version", "tracked PDFs", True, "TeX", deps.MACTEX_FIX)
    assert not check.ok and check.fix == deps.MACTEX_FIX


def test_missing_tex_packages(bin_dir: Path) -> None:
    write_executable(bin_dir / "kpsewhich", '#!/bin/bash\n[ "$1" = "multibib.sty" ] && exit 1\necho /tex/$1\n')
    check = deps.check_tex_files()
    assert not check.ok
    assert check.detail == "Missing: multibib.sty" and check.fix == "Install them with `sudo tlmgr install multibib`."


def test_summary_counts_required_only() -> None:
    checks = [
        deps.Check("a", "A", "TeX", "", True, False),
        deps.Check("b", "B", "TeX", "", False, False),
        deps.Check("c", "C", "TeX", "", True, True),
    ]
    assert deps.summary(checks) == {"ok": False, "missing": ["A"]}


def test_claude_status_signed_in_ignores_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-shell")
    cli = fake_claude(tmp_path / "custom")
    st = claude_cli.status(str(cli))
    assert (st.path, st.version, st.logged_in, st.plan, st.email) == (str(cli), "9.9.9", True, "max", "me@example.org")


def test_claude_status_signed_out(tmp_path: Path) -> None:
    st = claude_cli.status(str(fake_claude(tmp_path, logged_in=False)))
    assert st.logged_in is False and "not signed in" in st.detail


def test_claude_without_auth_status(tmp_path: Path) -> None:
    cli = write_executable(tmp_path / "claude", "#!/bin/bash\necho 'unknown command'; exit 1\n")
    st = claude_cli.status(str(cli))
    assert st.logged_in is None and "cannot report" in st.detail


def test_claude_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_cli, "CANDIDATES", [])
    st = claude_cli.status()
    assert st.path is None and "not found" in st.detail
    assert claude_cli.status("/nowhere/claude").detail == "Claude Code was not found at /nowhere/claude."


def test_agent_check_claude_plan(tmp_path: Path) -> None:
    agent = AgentSettings(claude_path=str(fake_claude(tmp_path)))
    check = deps.check_agent(agent, MemorySecret())
    assert check.ok and check.detail == "Signed in (me@example.org, Claude Max)."


def test_agent_check_api_key() -> None:
    agent = AgentSettings(provider="anthropic_api")
    assert not deps.check_agent(agent, MemorySecret()).ok
    assert deps.check_agent(agent, MemorySecret("sk-ant-x")).ok


def test_run_checks_covers_every_group(tmp_path: Path) -> None:
    agent = AgentSettings(claude_path=str(fake_claude(tmp_path)))
    checks = deps.run_checks(agent, MemorySecret())
    assert [g for g in dict.fromkeys(c.group for c in checks)] == ["TeX", "Python", "Agent", "Other"]
    by_id = {c.id: c for c in checks}
    assert not by_id["latexmk"].ok and by_id["python"].ok and by_id["agent"].ok
