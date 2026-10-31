"""The agent through the installed Claude Code, on the user's Claude plan (Agent SDK with cli_path).

setting_sources=[] keeps the user's own Claude Code settings and instructions out; what the agent is told is
passed explicitly. CLAUDE_CONFIG_DIR is left alone, or the sign-in would be lost. A hook confines edits to the
agent's copy, and Claude Code's sandbox confines Bash to it, with no network.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .. import claude_cli
from .base import AgentError, Turn
from .staging import LATEXMK_RC
from .tools import PAPER_TOOLS, ToolError, describe

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_PATH_TOOLS = {"Read", "Glob", "Grep"}
# WebFetch is left out on purpose, and so is reading outside the paper (see guard_read). A paper holds text from other
# people, such as a co-author's comments. If some of it told the agent to read a private file and open a web address
# with its contents in it, the user would first see that address and be asked.
ALLOWED_TOOLS = [*sorted(READ_PATH_TOOLS), "WebSearch", "TodoWrite", "Edit", "Write", "MultiEdit"]
LOOK_ONLY_TOOLS = [*sorted(READ_PATH_TOOLS), "WebSearch", "TodoWrite"]
# LaTeX's own classes and packages hold nothing private, and the agent often has to read them.
TEX_FOLDERS = ("/usr/local/texlive", "/Library/TeX")


def guard_path(path: str, work: Path) -> str | None:
    """Why the agent may not edit this file; None if it may."""
    target = (work / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    root = work.resolve()
    if root not in target.parents:
        return f"{path} is outside your copy of the paper. Edit only inside {root}."
    if target.name in LATEXMK_RC:
        return "latexmk runs this file as code, so the agent may not change it. Tell the user what should change."
    return None


def may_read(path: str, work: Path, readable: list[Path]) -> bool:
    """True for the agent's copy, the paper's results and scripts, and LaTeX's own files."""
    target = (work / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    allowed = [work.resolve(), *(p.resolve() for p in readable), *map(Path, TEX_FOLDERS)]
    return any(target == folder or folder in target.parents for folder in allowed)


NOT_SIGNED_IN = "Claude Code is not signed in. Run `claude` in Terminal and sign in."


def error_text(message) -> str:
    said = "; ".join(str(e) for e in message.errors or []) or message.result or ""
    if message.api_error_status == 401 or "not logged in" in said.lower():
        return NOT_SIGNED_IN
    if said:
        return said
    if message.api_error_status == 429:
        return "Your Claude plan's limit has been reached for now. Try again later, or choose another model."
    return f"Claude Code stopped ({message.subtype})."


async def _run(turn: Turn, path: str) -> None:
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, PermissionResultAllow, PermissionResultDeny,
        ResultMessage, StreamEvent, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage, create_sdk_mcp_server, tool,
    )

    box = turn.box

    def wrap(spec: dict):
        @tool(spec["name"], spec["description"], spec["parameters"])
        async def call(args: dict) -> dict:
            try:
                text = await asyncio.to_thread(box.call, spec["name"], args)
            except ToolError as e:
                return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
            return {"content": [{"type": "text", "text": text}]}
        return call

    # The file tools here are Claude Code's own.
    specs = [s for s in box.specs() if s in PAPER_TOOLS or (box.submission and s is box.submission.spec)]
    server = create_sdk_mcp_server("thor", tools=[wrap(spec) for spec in specs])

    async def guard(hook_input: dict, _id: str | None, _context) -> dict:
        tool_input = hook_input.get("tool_input") or {}
        problem = guard_path(str(tool_input.get("file_path") or tool_input.get("notebook_path") or ""), turn.cwd)
        if problem:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": problem}}
        return {}

    async def guard_read(hook_input: dict, _id: str | None, _context) -> dict:
        tool_input = hook_input.get("tool_input") or {}
        path = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if path and not may_read(path, turn.cwd, turn.readable):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                           "permissionDecisionReason": f"{path} is outside the paper."}}
        return {}

    async def can_use_tool(name: str, args: dict, context):
        # Anything not allowed below comes here, and the user decides.
        summary = getattr(context, "title", None) or describe(name, args)
        detail = args.get("command") or args.get("file_path") or args.get("path") or args.get("url") or ""
        allowed = await asyncio.to_thread(turn.ask, summary, str(detail))
        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message="The user said no.")

    options = ClaudeAgentOptions(
        cli_path=path,
        cwd=str(turn.cwd),
        add_dirs=[str(p) for p in turn.readable],
        model=turn.model,
        effort=turn.effort,
        setting_sources=[],
        # Said outright: left unset, Claude Code may use its auto mode, where a classifier answers instead of the user.
        permission_mode="default",
        system_prompt={"type": "preset", "preset": "claude_code", "append": turn.instructions},
        mcp_servers={"thor": server},
        allowed_tools=[*(LOOK_ONLY_TOOLS if turn.read_only else ALLOWED_TOOLS),
                       # run_script asks the user itself, so it is listed here to keep it from asking twice.
                       *(f"mcp__thor__{s['name']}" for s in specs)],
        disallowed_tools=[*sorted(EDIT_TOOLS), "Bash"] if turn.read_only else ["NotebookEdit"],
        include_partial_messages=True,
        hooks={"PreToolUse": [HookMatcher(matcher="|".join(sorted(EDIT_TOOLS)), hooks=[guard]),
                               HookMatcher(matcher="|".join(sorted(READ_PATH_TOOLS)), hooks=[guard_read])]},
        can_use_tool=can_use_tool,
        sandbox={"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False},
        resume=turn.session,
    )
    async with ClaudeSDKClient(options) as client:
        async def watch_for_stop() -> None:
            while not turn.cancelled.is_set():
                await asyncio.sleep(0.25)
            await client.interrupt()

        watcher = asyncio.create_task(watch_for_stop())
        names: dict[str, str] = {}
        try:
            prompt = turn.prompt if turn.session or not turn.recap else f"The conversation so far:\n{turn.recap}\n\n{turn.prompt}"
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, StreamEvent):
                    event = message.event
                    if event.get("type") == "content_block_delta" and event["delta"].get("type") == "text_delta":
                        turn.text(event["delta"].get("text", ""))
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            names[block.id] = block.name
                            if block.name != "TodoWrite":
                                turn.activity(describe(block.name, block.input), block.id)
                            if block.name in EDIT_TOOLS or block.name == "Bash":
                                box.wrote = True
                elif isinstance(message, UserMessage) and isinstance(message.content, list):
                    for block in message.content:
                        if isinstance(block, ToolResultBlock) and names.get(block.tool_use_id) != "TodoWrite":
                            detail = block.content if isinstance(block.content, str) else ""
                            turn.activity_done(block.tool_use_id, not block.is_error, detail[:300] if block.is_error else "")
                elif isinstance(message, ResultMessage):
                    turn.session = message.session_id or turn.session
                    usage = message.usage or {}
                    turn.usage(usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                               + usage.get("cache_creation_input_tokens", 0), usage.get("output_tokens", 0))
                    if message.is_error and not turn.cancelled.is_set():
                        raise AgentError(error_text(message))
        finally:
            watcher.cancel()


def run(turn: Turn, custom_path: str | None) -> None:
    path = claude_cli.locate(custom_path)
    if not path:
        raise AgentError("Claude Code was not found. Install it with `curl -fsSL https://claude.ai/install.sh | bash`, "
                         "or set its location in Settings.")
    claude_cli.hide_api_keys()
    try:
        asyncio.run(_run(turn, path))
    except AgentError:
        raise
    except Exception as e:  # noqa: BLE001
        # The SDK raises its own kinds of error when the process cannot start or dies; the user needs the words.
        raise AgentError(NOT_SIGNED_IN if "not logged in" in str(e).lower() else f"Claude Code stopped: {e}") from e
