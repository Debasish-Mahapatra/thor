"""The agent through the installed Codex, on the user's ChatGPT plan (`codex exec --json`).

Codex's own sandbox keeps its writes inside the agent's copy. Unknown event and item types are ignored: the
stream carries no schema version, and the docs do not list every field.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from .. import codex_cli
from ..paths import tool_env
from .base import AgentError, Turn
from .textmode import take_submission

TIMEOUT = 3600


def strict(schema: dict) -> dict:
    """Codex asks the model for structured output, whose schema must list every property as required."""
    if schema.get("type") == "object":
        properties = {k: strict(v) for k, v in schema.get("properties", {}).items()}
        return {**schema, "properties": properties, "required": list(properties), "additionalProperties": False}
    if schema.get("type") == "array":
        return {**schema, "items": strict(schema.get("items", {}))}
    return schema


def command_line(path: str, turn: Turn, schema_file: Path | None = None) -> tuple[list[str], str | None]:
    """The arguments and what goes to stdin. The flags go before `resume`, where every Codex reads them."""
    sandbox = "read-only" if turn.read_only else "workspace-write"
    args = [path, "exec", "--json", "--skip-git-repo-check", "--sandbox", sandbox, "-C", str(turn.cwd)]
    if schema_file:
        args += ["--output-schema", str(schema_file)]
    if turn.model:
        args += ["-m", turn.model]
    args += ["-c", f"model_reasoning_effort={turn.effort}"]
    if turn.session:
        return [*args, "resume", turn.session, turn.prompt], None
    prompt = f"{turn.instructions}\n\n{turn.recap and f'The conversation so far:{chr(10)}{turn.recap}{chr(10)}{chr(10)}'}{turn.prompt}"
    return [*args, "-"], prompt


def handle_event(event: dict, turn: Turn, errors: list[str]) -> None:
    kind = event.get("type")
    if kind == "thread.started":
        turn.session = event.get("thread_id") or turn.session
    elif kind == "turn.completed":
        usage = event.get("usage") or {}
        turn.usage(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    elif kind == "turn.failed":
        errors.append(str((event.get("error") or {}).get("message") or "The turn failed."))
    elif kind == "error":
        errors.append(str(event.get("message") or "Codex reported an error."))
    elif kind in ("item.started", "item.completed"):
        handle_item(kind, event.get("item") or {}, turn)


SHELL_WRAPPER = re.compile(r"""^\S*sh -l?c (['"])(.*)\1$""", re.S)


def command_words(command: str) -> str:
    """Codex runs everything through a shell; what the reader wants is the command inside."""
    found = SHELL_WRAPPER.match(command.strip())
    return (found.group(2) if found else command).strip()[:120]


def handle_item(kind: str, item: dict, turn: Turn) -> None:
    item_type = item.get("type")
    ident = str(item.get("id", ""))
    if item_type == "agent_message":
        if kind == "item.completed":
            text = item.get("text") or ""
            turn.text(text if text.endswith("\n") else f"{text}\n")
    elif item_type == "command_execution":
        if kind == "item.started":
            turn.activity(f"Running {command_words(str(item.get('command', '')))}", ident)
        else:
            turn.activity_done(ident, item.get("exit_code") in (0, None))
    elif item_type == "file_change":
        turn.box.wrote = True
        if kind == "item.completed":
            paths = [str(c.get("path", "")) for c in item.get("changes") or [] if isinstance(c, dict)]
            turn.activity(f"Edited {', '.join(p.rsplit('/', 1)[-1] for p in paths)}" if paths else "Edited a file")
    elif item_type == "web_search":
        if kind == "item.started":
            turn.activity(f"Searching the web for {item.get('query', '')!r}", ident)
        else:
            turn.activity_done(ident)
    elif item_type == "mcp_tool_call":
        if kind == "item.started":
            turn.activity(f"Using {item.get('tool', 'a tool')}", ident)
        else:
            turn.activity_done(ident)


def plain_error(text: str) -> str:
    """Codex passes on the server's error as a JSON string; the person reading it needs the message inside."""
    try:
        found = json.loads(text)
    except json.JSONDecodeError:
        return text
    while isinstance(found, dict):
        inner = found.get("error") or found.get("message")
        if isinstance(inner, str):
            return inner
        found = inner
    return text


def refused_model(message: str) -> bool:
    words = message.lower()
    return "model" in words and ("not supported" in words or "model_not_found" in words or "does not exist" in words)


def run(turn: Turn, custom_path: str | None) -> None:
    path = codex_cli.locate(custom_path)
    if not path:
        raise AgentError("Codex was not found. Install the ChatGPT app or run `npm install -g @openai/codex`, then "
                         "`codex login`. You can also set its location in Settings.")
    # A thread that already holds the conversation stays; one the failed attempt just started is forgotten.
    earlier_session = turn.session
    try:
        _run_once(turn, path)
    except AgentError as e:
        listed = codex_cli.models()
        # With no model chosen here, Codex uses the one in its own settings, which this account may not have.
        if turn.model or not listed or not refused_model(str(e)) or turn.cancelled.is_set():
            raise
        turn.model = listed[0]["id"]
        turn.session = earlier_session
        turn.emit({"type": "notice", "text": f"The model in your Codex settings is not available on this account, so this "
                                             f"turn uses {listed[0]['name']}. You can choose another in the model menu."})
        _run_once(turn, path)


def _run_once(turn: Turn, path: str) -> None:
    submission = turn.box.submission
    if not submission:
        _run_process(turn, path, None)
        return
    with tempfile.TemporaryDirectory() as folder:
        schema_file = Path(folder) / "schema.json"
        schema_file.write_text(json.dumps(strict(submission.spec["parameters"])))
        said = _run_process(turn, path, schema_file)
    take_submission(turn, said)


def _run_process(turn: Turn, path: str, schema_file: Path | None) -> str:
    """Runs Codex once and returns its last message. With a schema that message is the structured answer, so it is
    kept from the transcript."""
    args, stdin_text = command_line(path, turn, schema_file)
    process = subprocess.Popen(args, cwd=turn.cwd, env=tool_env(drop=codex_cli.API_KEY_VARS), text=True, errors="replace",
                               stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(process.stderr), daemon=True).start()
    if stdin_text is not None:
        def feed() -> None:
            try:
                process.stdin.write(stdin_text)
                process.stdin.close()
            except OSError:
                pass  # It stopped reading; its exit code says why.
        threading.Thread(target=feed, daemon=True).start()
    timer = threading.Timer(TIMEOUT, process.kill)
    timer.daemon = True
    timer.start()
    threading.Thread(target=lambda: (turn.cancelled.wait(), process.poll() is None and process.kill()), daemon=True).start()
    errors: list[str] = []
    last = ""
    for line in process.stdout:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item") or {}
        if schema_file and item.get("type") == "agent_message":
            last = item.get("text") or last
            continue
        handle_event(event, turn, errors)
    code = process.wait()
    timer.cancel()
    if turn.cancelled.is_set():
        return last
    if errors:
        raise AgentError(f"Codex reported: {plain_error(errors[-1])}")
    if code != 0:
        tail = "".join(stderr).strip()[-600:]
        if "login" in tail.lower() or "not logged in" in tail.lower():
            raise AgentError("Codex is not signed in. Run `codex login` in Terminal and sign in.")
        raise AgentError(f"Codex stopped with exit code {code}.{f' It said: {tail}' if tail else ''}")
    return last
