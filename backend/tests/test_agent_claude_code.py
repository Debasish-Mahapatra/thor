"""The real Claude Code, through the real Agent SDK, against a fake model server: proves everything between Thor and
the model (options, the edit guard, Thor's tools, events, resuming) without a sign-in, and costs nothing. Skipped
where Claude Code is not installed."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from thor import claude_cli
from thor.agent import claude_code
from thor.agent.base import AgentError, Turn
from thor.agent.tools import ToolBox

CLAUDE = next((str(p) for p in (Path(c).expanduser() for c in claude_cli.CANDIDATES) if p.is_file()), None)
pytestmark = pytest.mark.skipif(not CLAUDE, reason="Claude Code is not installed")


def sse(kind: str, **data) -> bytes:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n".encode()


def message(blocks: list[dict], stop_reason: str) -> bytes:
    out = [sse("message_start", message={"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
                                         "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 1}})]
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            out.append(sse("content_block_start", index=index, content_block={"type": "text", "text": ""}))
            out.append(sse("content_block_delta", index=index, delta={"type": "text_delta", "text": block["text"]}))
        else:
            out.append(sse("content_block_start", index=index, content_block={"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}))
            out.append(sse("content_block_delta", index=index, delta={"type": "input_json_delta", "partial_json": json.dumps(block["input"])}))
        out.append(sse("content_block_stop", index=index))
    out.append(sse("message_delta", delta={"stop_reason": stop_reason, "stop_sequence": None}, usage={"output_tokens": 20}))
    out.append(sse("message_stop"))
    return b"".join(out)


class FakeAnthropic:
    """Answers the conversation's requests in turn. Claude Code also makes small requests of its own, such as naming
    the session; those have no Edit tool, and get a short answer without using up the script."""

    def __init__(self, script: list) -> None:
        self.script = script
        self.seen: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                request = json.loads(body or b"{}")
                if "count_tokens" in self.path or "messages" not in self.path:
                    answer = json.dumps({"input_tokens": 10}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(answer)))
                    self.end_headers()
                    self.wfile.write(answer)
                    return
                names = {t.get("name") for t in request.get("tools", [])}
                if "Edit" in names and fake.script:
                    fake.seen.append(request)
                    blocks, stop = fake.script.pop(0)
                else:
                    blocks, stop = [{"type": "text", "text": "Short answer."}], "end_turn"
                if not request.get("stream"):
                    answer = json.dumps({"id": "msg_2", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                         "content": [{"type": "text", "text": "Short answer."}], "stop_reason": "end_turn",
                                         "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(answer)))
                    self.end_headers()
                    self.wfile.write(answer)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(message(blocks, stop))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def claude_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A sign-in of its own, so nothing here touches the user's; the fake server takes any key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("THOR_TOOL_PATH", "/usr/bin:/bin")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    for name in list(os.environ):
        if name.startswith("CLAUDE_CODE_") and name != "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" or name in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT"):
            monkeypatch.delenv(name)
    return monkeypatch


def make_turn(tmp_path: Path, **kw) -> tuple[Turn, list[dict]]:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    if not (work / "main.tex").exists():
        (work / "main.tex").write_text("Line one is usefull.\nLine two.\n")
    events: list[dict] = []
    compiled: list[int] = []

    def fake_compile() -> str:
        compiled.append(1)
        return "Compiled · 1 page · no errors"

    box = ToolBox(work, [], fake_compile, lambda: "No comments have been imported.")
    box.compiled = compiled
    defaults = dict(prompt="Fix the spelling.", instructions="Be careful.", cwd=work, box=box, model="claude-opus-5-5", effort="high",
                    budget_usd=5.0, emit=events.append, cancelled=threading.Event(), ask=lambda s, d: False)
    return Turn(**{**defaults, **kw}), events


def run(turn: Turn) -> None:
    asyncio.run(claude_code._run(turn, CLAUDE))


def test_a_whole_turn_through_claude_code(tmp_path: Path, claude_env: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    fake = FakeAnthropic([
        ([{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": str(work / "main.tex")}}], "tool_use"),
        ([{"type": "tool_use", "id": "toolu_2", "name": "Edit", "input": {"file_path": str(work / "main.tex"), "old_string": "usefull", "new_string": "useful"}}], "tool_use"),
        ([{"type": "tool_use", "id": "toolu_3", "name": "mcp__thor__compile", "input": {}}], "tool_use"),
        ([{"type": "text", "text": "Fixed the spelling.\n"}], "end_turn"),
    ])
    claude_env.setenv("ANTHROPIC_BASE_URL", fake.url)
    turn, events = make_turn(tmp_path)
    run(turn)
    assert (work / "main.tex").read_text().startswith("Line one is useful.")
    assert turn.box.compiled == [1] and turn.session
    text = "".join(e["text"] for e in events if e["type"] == "text")
    assert "Fixed the spelling." in text
    summaries = [e["summary"] for e in events if e["type"] == "activity"]
    assert summaries == ["Reading main.tex", "Editing main.tex", "Compiling its copy of the paper"]
    assert all(e["ok"] for e in events if e["type"] == "activity_done")
    first = fake.seen[0]
    names = {t["name"] for t in first["tools"]}
    assert {"Edit", "Read", "mcp__thor__compile", "mcp__thor__find_references", "mcp__thor__read_comments"} <= names
    assert "NotebookEdit" not in names and first["model"] == "claude-opus-5-5"
    # Thor's instructions reach the model; the user's own Claude Code settings do not.
    assert "Be careful." in json.dumps(first["system"])


def test_edits_outside_the_copy_are_refused_by_the_guard(tmp_path: Path, claude_env: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    outside = tmp_path / "paper.tex"
    outside.write_text("The real paper.\n")
    fake = FakeAnthropic([
        ([{"type": "tool_use", "id": "toolu_1", "name": "Write", "input": {"file_path": str(outside), "content": "Overwritten."}}], "tool_use"),
        ([{"type": "tool_use", "id": "toolu_2", "name": "Write", "input": {"file_path": str(work / "latexmkrc"), "content": "system('x');"}}], "tool_use"),
        ([{"type": "text", "text": "Could not.\n"}], "end_turn"),
    ])
    claude_env.setenv("ANTHROPIC_BASE_URL", fake.url)
    turn, events = make_turn(tmp_path)
    run(turn)
    assert outside.read_text() == "The real paper.\n" and not (work / "latexmkrc").exists()
    assert [e["ok"] for e in events if e["type"] == "activity_done"] == [False, False]


def test_reading_outside_the_paper_asks_the_user(tmp_path: Path, claude_env: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    work.mkdir()
    private = tmp_path / "private.txt"
    private.write_text("Not for the model.\n")
    (work / "inside.tex").write_text("Part of the paper.\n")
    fake = FakeAnthropic([
        ([{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": str(work / "inside.tex")}}], "tool_use"),
        ([{"type": "tool_use", "id": "toolu_2", "name": "Read", "input": {"file_path": str(private)}}], "tool_use"),
        ([{"type": "text", "text": "Done.\n"}], "end_turn"),
    ])
    claude_env.setenv("ANTHROPIC_BASE_URL", fake.url)
    asked: list[tuple[str, str]] = []

    def ask(summary: str, detail: str) -> bool:
        asked.append((summary, detail))
        return False

    turn, events = make_turn(tmp_path, ask=ask)
    run(turn)
    # Only the private file was asked about, and refused, its contents never reached the model.
    assert len(asked) == 1 and asked[0][1] == str(private)
    assert [e["ok"] for e in events if e["type"] == "activity_done"] == [True, False]
    assert "Part of the paper." in json.dumps(fake.seen[-1]["messages"])
    assert "Not for the model." not in json.dumps(fake.seen[-1]["messages"])


def test_a_web_address_is_shown_to_the_user_first(tmp_path: Path, claude_env: pytest.MonkeyPatch) -> None:
    url = "http://127.0.0.1:9/?stolen=secret"
    fake = FakeAnthropic([
        ([{"type": "tool_use", "id": "toolu_1", "name": "WebFetch", "input": {"url": url, "prompt": "Read it."}}], "tool_use"),
        ([{"type": "text", "text": "Done.\n"}], "end_turn"),
    ])
    claude_env.setenv("ANTHROPIC_BASE_URL", fake.url)
    asked: list[str] = []

    def ask(summary: str, detail: str) -> bool:
        asked.append(detail)
        return False

    turn, _ = make_turn(tmp_path, ask=ask)
    run(turn)
    assert asked == [url]


def test_a_later_turn_resumes_the_same_session(tmp_path: Path, claude_env: pytest.MonkeyPatch) -> None:
    fake = FakeAnthropic([([{"type": "text", "text": "First answer.\n"}], "end_turn"), ([{"type": "text", "text": "Second answer.\n"}], "end_turn")])
    claude_env.setenv("ANTHROPIC_BASE_URL", fake.url)
    turn, _ = make_turn(tmp_path)
    run(turn)
    first_session = turn.session
    turn.prompt = "And now?"
    run(turn)
    assert turn.session and first_session
    assert "First answer." in json.dumps(fake.seen[-1]["messages"])


def test_not_signed_in_is_told_in_words(tmp_path: Path, claude_env: pytest.MonkeyPatch) -> None:
    """The way the user meets it: through run(), which also takes any key out of the environment."""
    fake = FakeAnthropic([])
    claude_env.setenv("ANTHROPIC_BASE_URL", fake.url)
    turn, _ = make_turn(tmp_path)
    with pytest.raises(AgentError) as caught:
        claude_code.run(turn, CLAUDE)
    assert "ANTHROPIC_API_KEY" not in os.environ and "ANTHROPIC_API_KEY" in claude_cli.hidden_keys
    assert str(caught.value) == claude_code.NOT_SIGNED_IN
