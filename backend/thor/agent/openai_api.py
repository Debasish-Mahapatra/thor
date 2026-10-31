"""The agent through the OpenAI API, or any server that speaks its chat API: Ollama, LM Studio, OpenRouter, and
others (bring your own model). Thor runs the conversation and the tools itself.

A model that cannot call tools still works: it is shown the files and answers with edit blocks (textmode.py).
"""

from __future__ import annotations

import json

import httpx

from .base import AgentError, Turn
from .textmode import RULES, BlockFilter, full_prompt, report
from .tools import FILE_TOOLS, PAPER_TOOLS, ToolError, check_args, describe

OPENAI_URL = "https://api.openai.com/v1"
MAX_ROUNDS = 80
KEEP_LOADED = "30m"
TIMEOUT = httpx.Timeout(connect=15, read=600, write=60, pool=15)


class NoTools(Exception):
    """This model cannot call tools."""


def tool_specs() -> list[dict]:
    return [{"type": "function", "function": {"name": s["name"], "description": s["description"], "parameters": s["parameters"]}}
            for s in (*FILE_TOOLS, *PAPER_TOOLS)]


def explain_failure(name: str, status: int, body: str, model: str) -> AgentError:
    if status in (401, 403):
        return AgentError(f"{name} refused the API key. Check it in Settings.")
    if status == 404:
        return AgentError(f"{name} does not know the model {model!r}, or the address is wrong. Check both in Settings.")
    if status == 429:
        return AgentError(f"{name}'s rate limit or quota was reached. Wait a minute, or check your account.")
    return AgentError(f"{name} answered with an error ({status}): {body[:400]}")


def stream_reply(client: httpx.Client, url: str, headers: dict, body: dict, turn: Turn, on_text, name: str):
    """One streamed answer: (text, tool calls, usage). The chat API sends tool calls in fragments."""
    text: list[str] = []
    calls: dict[int, dict] = {}
    usage: dict = {}
    with client.stream("POST", f"{url}/chat/completions", headers=headers, json=body) as response:
        if response.status_code != 200:
            detail = response.read().decode(errors="replace")
            if response.status_code == 400 and "tools" in body and "tool" in detail.lower():
                raise NoTools
            raise explain_failure(name, response.status_code, detail, body["model"])
        for line in response.iter_lines():
            if turn.cancelled.is_set():
                break
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            usage = chunk.get("usage") or usage
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    text.append(delta["content"])
                    on_text(delta["content"])
                for fragment in delta.get("tool_calls") or []:
                    call = calls.setdefault(fragment.get("index", 0), {"id": "", "name": "", "arguments": ""})
                    call["id"] = fragment.get("id") or call["id"]
                    function = fragment.get("function") or {}
                    call["name"] += function.get("name") or ""
                    call["arguments"] += function.get("arguments") or ""
    return "".join(text), [calls[i] for i in sorted(calls)], usage


def text_mode(client: httpx.Client, url: str, headers: dict, model: str, turn: Turn, name: str) -> None:
    turn.emit({"type": "notice", "text": f"{model} cannot call tools, so it sees the open files and proposes edits as text."})
    blocks = BlockFilter()
    pending = [""]

    def on_text(chunk: str) -> None:
        # Blocks are recognized by whole lines.
        pending[0] += chunk
        *lines, pending[0] = pending[0].split("\n")
        for line in lines:
            turn.text(blocks.feed(f"{line}\n"))

    body = {"model": model, "stream": True, "messages": [{"role": "system", "content": f"{turn.instructions}\n\n{RULES}"},
                                                          {"role": "user", "content": full_prompt(turn)}]}
    answer, _, usage = stream_reply(client, url, headers, body, turn, on_text, name)
    if pending[0]:
        turn.text(blocks.feed(pending[0]))
    report(turn, blocks)
    turn.history = [*turn.history, {"role": "user", "content": turn.prompt}, {"role": "assistant", "content": answer}]
    if usage:
        turn.usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


def keep_loaded(url: str, model: str, turn: Turn | None = None) -> None:
    """Asks Ollama to load the model and keep it for a while: its chat API has no such setting. A model that is
    already loaded answers at once. Failing here costs only the wait, so the turn goes on and reports any real
    problem itself."""
    base = url.rstrip("/").removesuffix("/v1")
    try:
        running = httpx.get(f"{base}/api/ps", timeout=5).json().get("models", [])
        if turn and not any(m.get("name") == model for m in running):
            turn.activity(f"Loading {model}")
        httpx.post(f"{base}/api/generate", json={"model": model, "keep_alive": KEEP_LOADED}, timeout=300)
    except (httpx.HTTPError, ValueError):
        return


def run(turn: Turn, url: str, key: str | None, name: str, send_effort: bool = False, no_thinking: bool = False) -> None:
    url = url.rstrip("/")
    if not turn.model:
        raise AgentError(f"No model is chosen for {name}. Choose one in Settings.")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    schemas = {s["name"]: s["parameters"] for s in (*FILE_TOOLS, *PAPER_TOOLS)}
    if not turn.history:
        turn.history = [{"role": "system", "content": turn.instructions}]
        if turn.recap:
            turn.history.append({"role": "user", "content": f"The conversation so far:\n{turn.recap}"})
    turn.history.append({"role": "user", "content": turn.prompt})
    body: dict = {"model": turn.model, "stream": True, "tools": tool_specs(), "stream_options": {"include_usage": True}}
    if send_effort:
        body["reasoning_effort"] = {"max": "xhigh"}.get(turn.effort, turn.effort)
    if no_thinking:
        body["reasoning_effort"] = "none"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            for _ in range(MAX_ROUNDS):
                try:
                    text, calls, usage = stream_reply(client, url, headers, {**body, "messages": turn.history}, turn, turn.text, name)
                except NoTools:
                    turn.history.pop()
                    text_mode(client, url, headers, turn.model, turn, name)
                    return
                if turn.cancelled.is_set():
                    return
                if usage:
                    turn.usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
                message: dict = {"role": "assistant", "content": text}
                if calls:
                    message["tool_calls"] = [{"id": c["id"], "type": "function",
                                              "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]
                turn.history.append(message)
                if not calls:
                    return
                for call in calls:
                    try:
                        args = json.loads(call["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = None
                    turn.activity(describe(call["name"], args if isinstance(args, dict) else {}), call["id"])
                    try:
                        problem = "The arguments were not valid JSON." if args is None else (
                            check_args(schemas[call["name"]], args) if call["name"] in schemas else f"No tool {call['name']}.")
                        if problem:
                            raise ToolError(f"{problem} Try the call again.")
                        result = turn.box.call(call["name"], args)
                        turn.activity_done(call["id"])
                    except ToolError as e:
                        result = f"Error: {e}"
                        turn.activity_done(call["id"], False, str(e)[:300])
                    turn.history.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            raise AgentError(f"The agent stopped after {MAX_ROUNDS} rounds of tool calls. Send another message to continue.")
    except httpx.ConnectError as e:
        raise AgentError(f"Could not reach {name} at {url}. Is it running?") from e
    except httpx.TimeoutException as e:
        raise AgentError(f"{name} did not answer in time.") from e
    except httpx.HTTPError as e:
        raise AgentError(f"The connection to {name} failed: {e}") from e


def list_models(url: str, key: str | None, timeout: float = 10) -> list[dict]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    response = httpx.get(f"{url.rstrip('/')}/models", headers=headers, timeout=timeout)
    response.raise_for_status()
    return sorted(({"id": m["id"], "name": m["id"]} for m in response.json().get("data", [])), key=lambda m: m["id"])
