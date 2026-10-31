"""Bring your own model: any command-line program, run in the agent's copy of the paper."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import threading

from ..claude_cli import hidden_keys
from ..paths import tool_env
from ..settings import Command
from . import sandbox
from .base import ANSI, AgentError, Turn
from .textmode import RULES, BlockFilter, full_prompt, report

TIMEOUT = 3600


def run(turn: Turn, command: Command) -> None:
    try:
        args = shlex.split(command.command)
    except ValueError as e:
        raise AgentError(f"The command for {command.name} cannot be read: {e}. Fix it in Settings.") from e
    if command.edits_files:
        prompt = f"{turn.instructions}\n\n{turn.recap and f'The conversation so far:{chr(10)}{turn.recap}{chr(10)}{chr(10)}'}{turn.prompt}"
    else:
        prompt = f"{turn.instructions}\n\n{RULES}\n\n{full_prompt(turn)}"
    placed = any("{prompt}" in a for a in args)
    args = [a.replace("{prompt}", prompt) for a in args]
    # This is the user's own program, so it keeps what the user's shell gave Thor.
    env = tool_env(**hidden_keys)
    if not shutil.which(args[0], path=env["PATH"]):
        raise AgentError(f"{command.name} could not start: {args[0]} was not found. Check its command in Settings.")
    if command.sandboxed:
        if not sandbox.available():
            raise AgentError(f"{command.name} is set to run in a sandbox, but this Mac has no {sandbox.SANDBOX_EXEC}. "
                             "Turn off its sandbox in Settings if you trust it.")
        root = turn.write_root or turn.cwd
        # Its temporary files stay in the copy, so the sandbox need not open the system's temporary folder.
        temp = root / ".thor-tmp"
        temp.mkdir(exist_ok=True)
        env["TMPDIR"] = str(temp)
        args = sandbox.wrap(args, root)
    try:
        process = subprocess.Popen(args, cwd=turn.cwd, env=env, text=True, errors="replace",
                                   stdin=subprocess.DEVNULL if placed else subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise AgentError(f"{command.name} could not start: {e.strerror or e}. Check its command in Settings.") from e
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(process.stderr), daemon=True).start()
    if not placed:
        def feed() -> None:
            try:
                process.stdin.write(prompt)
                process.stdin.close()
            except OSError:
                pass  # It stopped reading; its exit code says why.
        threading.Thread(target=feed, daemon=True).start()
    timer = threading.Timer(TIMEOUT, process.kill)
    timer.daemon = True
    timer.start()
    threading.Thread(target=lambda: (turn.cancelled.wait(), process.poll() is None and process.kill()), daemon=True).start()
    blocks = BlockFilter()
    wrote_any = False
    for line in process.stdout:
        clean = ANSI.sub("", line)
        wrote_any = wrote_any or bool(clean.strip())
        turn.text(clean if command.edits_files else blocks.feed(clean))
    code = process.wait()
    timer.cancel()
    if turn.cancelled.is_set():
        return
    if not command.edits_files:
        report(turn, blocks)
    else:
        turn.box.wrote = True
    if code != 0:
        tail = ANSI.sub("", "".join(stderr)).strip()[-600:]
        hint = " The sandbox blocks writing outside the paper; you can turn it off for this command in Settings." if (
            command.sandboxed and "not permitted" in tail.lower()) else ""
        raise AgentError(f"{command.name} stopped with exit code {code}.{f' It said: {tail}' if tail else ''}{hint}")
    if not wrote_any:
        raise AgentError(f"{command.name} finished without printing anything.")
