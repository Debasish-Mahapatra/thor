"""The agent through the Anthropic API with a key from the Keychain. No command-line tool is involved: Thor runs
the conversation and the tools itself, in a manual loop, because every turn has to report activity, watch for
the stop button and keep to the spending cap.

Responses go back into the history as they came, thinking blocks included; the API rejects a history whose
earlier turns were edited. The history is only ever appended to.
"""

from __future__ import annotations

import re

from .base import AgentError, Turn
from .tools import FILE_TOOLS, PAPER_TOOLS, ToolError, check_args, describe

MAX_TOKENS = 64_000
MAX_ROUNDS = 80
# Dollars per million tokens: input, output, cache read. Cache writes cost 1.25 times the input price.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
CURRENT = re.compile(r"^claude-(?:opus|sonnet|haiku|fable|mythos)-[5-9]")
HAS_FALLBACKS = re.compile(r"^claude-(?:fable-5-1|opus-5|sonnet-5-5)")
HAS_WEB_SEARCH = re.compile(r"^claude-(?:opus-5|sonnet-5)")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def cost_of(model: str, usage) -> float | None:
    price = PRICES.get(model)
    if not price:
        return None
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (usage.input_tokens * price[0] + write * price[0] * 1.25 + read * price[2]
            + usage.output_tokens * price[1]) / 1_000_000


def request_params(turn: Turn) -> dict:
    tools = [{"name": s["name"], "description": s["description"], "input_schema": s["parameters"],
              "eager_input_streaming": True} for s in (*FILE_TOOLS, *PAPER_TOOLS)]
    params: dict = {"model": turn.model, "max_tokens": MAX_TOKENS, "system": turn.instructions, "tools": tools,
                    "cache_control": {"type": "ephemeral"}}
    if CURRENT.match(turn.model):
        params["thinking"] = {"type": "adaptive"}
        params["output_config"] = {"effort": turn.effort}
    if HAS_WEB_SEARCH.match(turn.model):
        tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 5})
    if HAS_FALLBACKS.match(turn.model):
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


def run(turn: Turn, key: str) -> None:
    import anthropic

    schemas = {s["name"]: s["parameters"] for s in (*FILE_TOOLS, *PAPER_TOOLS)}
    client = anthropic.Anthropic(api_key=key)
    params = request_params(turn)
    prompt = turn.prompt if turn.history or not turn.recap else f"The conversation so far:\n{turn.recap}\n\n{turn.prompt}"
    turn.history.append({"role": "user", "content": prompt})
    spent = 0.0
    bad_json = 0
    try:
        for _ in range(MAX_ROUNDS):
            try:
                with client.beta.messages.stream(messages=turn.history, **params) as stream:
                    for event in stream:
                        if turn.cancelled.is_set():
                            return
                        if event.type == "text":
                            turn.text(event.text)
                    response = stream.get_final_message()
                bad_json = 0
            except ValueError:
                # Tool input the SDK could not parse at all. API errors are not ValueError and go on up.
                bad_json += 1
                if bad_json > 2:
                    raise AgentError("The model kept sending tool calls that could not be read. Try again.") from None
                continue
            usage = response.usage
            cost = cost_of(turn.model, usage)
            spent += cost or 0
            turn.usage((usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0)
                       + (getattr(usage, "cache_creation_input_tokens", 0) or 0), usage.output_tokens or 0, cost)
            turn.history.append({"role": "assistant",
                                 "content": [b.model_dump(mode="json", exclude_none=True) for b in response.content]})
            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason == "refusal":
                raise AgentError("The model declined this request. Rephrase it, or choose another model.")
            uses = [b for b in response.content if b.type == "tool_use"]
            if not uses:
                return
            if response.stop_reason == "max_tokens":
                raise AgentError("The model ran out of room while writing an edit. Ask for a smaller change.")
            if spent >= turn.budget_usd:
                raise AgentError(f"This run reached its spending cap of ${turn.budget_usd:g} (about ${spent:.2f} so far). "
                                 "Raise the cap in Settings, or send another message to continue.")
            results = []
            for use in uses:
                problem = check_args(schemas.get(use.name, {}), use.input) if use.name in schemas else f"No tool {use.name}."
                turn.activity(describe(use.name, use.input if isinstance(use.input, dict) else {}), use.id)
                try:
                    if problem:
                        raise ToolError(f"{problem} Try the call again.")
                    text = turn.box.call(use.name, use.input)
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": text})
                    turn.activity_done(use.id)
                except ToolError as e:
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": str(e), "is_error": True})
                    turn.activity_done(use.id, False, str(e)[:300])
            turn.history.append({"role": "user", "content": results})
        raise AgentError(f"The agent stopped after {MAX_ROUNDS} rounds of tool calls. Send another message to continue.")
    except anthropic.AuthenticationError as e:
        raise AgentError("Anthropic refused the API key. Check it in Settings.") from e
    except anthropic.PermissionDeniedError as e:
        raise AgentError("This API key may not use that model. Choose another in Settings.") from e
    except anthropic.NotFoundError as e:
        raise AgentError(f"Anthropic does not know the model {turn.model}. Choose another in Settings.") from e
    except anthropic.RateLimitError as e:
        raise AgentError("Anthropic's rate limit was reached. Wait a minute and try again.") from e
    except anthropic.APIStatusError as e:
        raise AgentError(f"Anthropic answered with an error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AgentError("Could not reach Anthropic. Check the internet connection.") from e


def list_models(key: str) -> list[dict]:
    import anthropic

    return [{"id": m.id, "name": m.display_name} for m in anthropic.Anthropic(api_key=key).models.list()]
