"""The models the agent can use, and whether each is ready.

Command-line tools use the plan they are signed in to: Claude Code and Codex. API providers use a key from the
Keychain. A model server running on this Mac (Ollama, LM Studio) is found by asking it, with nothing to set up.
Bring your own model means any other server that speaks the OpenAI chat API (endpoint:<id>), or any command-line
program (command:<id>). The backends load only when used: the Anthropic SDK and the Agent SDK take
a second each to import, which must not slow the app's start.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .. import claude_cli, codex_cli
from ..settings import (KEYCHAIN_ACCOUNT, MODELS, OPENAI_ACCOUNT, AgentSettings, Command, Endpoint, SecretStore,
                        endpoint_account)
from .base import AgentError, Turn

OPENAI_NAME = "OpenAI API"
# Model servers on this Mac: (name, address, field in the settings).
LOCAL_SERVERS = {
    "ollama": ("Ollama", "http://127.0.0.1:11434/v1", "ollama_model"),
    "lmstudio": ("LM Studio", "http://127.0.0.1:1234/v1", "lmstudio_model"),
}
# A server that is not there refuses at once; this only bounds one that is stuck.
LOCAL_ASK_SECONDS = 0.5
COMPILE_WITH_TOOL = ("Use your tools to read and edit the files; paths are relative to your working folder. To compile "
                     "your copy of the paper, use the compile tool: it reports errors and the page count. Use "
                     "find_references and get_bibtex to confirm citations, and read_comments for co-authors' comments.")
COMPILE_WITH_LATEXMK = ("To compile, run latexmk with -pdf (or -xelatex, -lualatex for those engines), "
                        "-interaction=nonstopmode, -file-line-error and -outdir=.thor-build, and read the log in "
                        ".thor-build. Keep generated files in .thor-build.")
COMPILE_BY_THOR = "You cannot compile. The app compiles the paper after your edits and shows the user the result."


def _find(items: list, ident: str):
    return next((item for item in items if item.id == ident), None)


def _claude(agent: AgentSettings) -> dict:
    st = claude_cli.status(agent.claude_path or None)
    who = ", ".join(x for x in (st.email, st.plan and f"Claude {st.plan.capitalize()}") if x)
    detail = st.detail or (f"Signed in as {who}." if who else "Found.")
    return {"id": "claude_code", "kind": "cli", "name": "Claude Code", "billing": "your Claude plan", "model": agent.model,
            "models": MODELS, "ready": bool(st.path) and st.logged_in is not False, "detail": detail, "path": st.path,
            "version": st.version,
            "fix": None if st.path and st.logged_in is not False else "Run `claude` in Terminal and sign in, or install it "
                   "with `curl -fsSL https://claude.ai/install.sh | bash`."}


def _codex(agent: AgentSettings) -> dict:
    st = codex_cli.status(agent.codex_path or None)
    return {"id": "codex", "kind": "cli", "name": "Codex", "billing": "your ChatGPT plan", "model": agent.codex_model,
            "models": codex_cli.models(), "ready": bool(st.path) and st.logged_in is not False, "detail": st.detail,
            "path": st.path, "version": st.version, "placeholder": "The model Codex is set to use",
            "fix": None if st.path and st.logged_in is not False else "Run `codex login` in Terminal. To install it, get the "
                   "ChatGPT app, or run `npm install -g @openai/codex`."}


def is_cloud_model(model_id: str) -> bool:
    """Ollama can also pass requests on to a cloud service, under names like glm-5.2:cloud. A paper must not
    leave the Mac that way unless the user picks such a model by name."""
    return model_id.endswith(("-cloud", ":cloud"))


def first_local_model(models: list[dict]) -> str:
    return next((m["id"] for m in models if not is_cloud_model(m["id"])), "")


def local_server(provider: str, agent: AgentSettings) -> dict | None:
    """A model server on this Mac, if one is running; None if not, so it does not clutter the list."""
    name, url, field = LOCAL_SERVERS[provider]
    from . import openai_api
    try:
        models = openai_api.list_models(url, None, LOCAL_ASK_SECONDS)
    except Exception:  # noqa: BLE001
        return None
    chosen = getattr(agent, field)
    model = chosen or first_local_model(models)
    shown = [{**m, "name": f"{m['name']} (sends your text to a cloud service)" if is_cloud_model(m["id"]) else m["name"]}
             for m in models]
    return {"id": provider, "kind": "local", "name": name, "billing": "runs on this Mac", "model": model, "models": shown,
            "ready": bool(model), "base_url": url, "detail": f"{len(models)} model{'' if len(models) == 1 else 's'} at {url}.",
            "fix": None if model else f"{name} has no model that runs on this Mac. Download one there."}


def catalog(agent: AgentSettings, secret: SecretStore) -> list[dict]:
    with ThreadPoolExecutor(max_workers=2 + len(LOCAL_SERVERS)) as pool:
        claude, codex = pool.submit(_claude, agent), pool.submit(_codex, agent)
        local = [pool.submit(local_server, provider, agent) for provider in LOCAL_SERVERS]
        found = [claude.result(), codex.result()]
        found_local = [item for item in (f.result() for f in local) if item]
    has_anthropic, has_openai = bool(secret.get()), bool(secret.get(OPENAI_ACCOUNT))
    found.append({"id": "anthropic_api", "kind": "api", "name": "Anthropic API", "billing": "billed per token by Anthropic",
                  "model": agent.model, "models": MODELS, "ready": has_anthropic, "has_key": has_anthropic,
                  "detail": "A key is saved in your Keychain." if has_anthropic else "No key saved.",
                  "fix": None if has_anthropic else "Add an Anthropic API key in Settings."})
    found.append({"id": "openai_api", "kind": "api", "name": OPENAI_NAME, "billing": "billed per token by OpenAI",
                  "model": agent.openai_model, "models": [], "ready": has_openai and bool(agent.openai_model),
                  "has_key": has_openai, "placeholder": "Model ID",
                  "detail": ("A key is saved in your Keychain." if has_openai else "No key saved.")
                  + ("" if agent.openai_model else " No model chosen."),
                  "fix": None if has_openai and agent.openai_model else "Add an OpenAI API key and a model in Settings."})
    found.extend(found_local)
    for e in agent.endpoints:
        has_key = bool(secret.get(endpoint_account(e.id)))
        found.append({"id": f"endpoint:{e.id}", "kind": "endpoint", "name": e.name, "billing": e.base_url, "model": e.model,
                      "models": [], "ready": bool(e.model), "has_key": has_key, "base_url": e.base_url,
                      "placeholder": "Model ID", "detail": e.base_url if e.model else "No model chosen.",
                      "fix": None if e.model else f"Choose a model for {e.name} in Settings."})
    for c in agent.commands:
        found.append({"id": f"command:{c.id}", "kind": "command", "name": c.name, "billing": "your own command", "model": "",
                      "models": [], "ready": True, "detail": c.command, "command": c.command, "edits_files": c.edits_files})
    return found


def effective(agent: AgentSettings, found: list[dict]) -> str:
    """The provider to start with: the chosen one if it works, or else the first that does. An agent that could
    answer must not sit idle because of a sign-in the user never meant to use."""
    chosen = next((p for p in found if p["id"] == agent.provider), None)
    if chosen and chosen["ready"]:
        return chosen["id"]
    return next((p["id"] for p in found if p["ready"]), agent.provider)


def model_of(provider: str, agent: AgentSettings) -> str:
    if provider in LOCAL_SERVERS:
        chosen = getattr(agent, LOCAL_SERVERS[provider][2])
        if chosen:
            return chosen
        from . import openai_api
        try:
            return first_local_model(openai_api.list_models(LOCAL_SERVERS[provider][1], None, LOCAL_ASK_SECONDS))
        except Exception:  # noqa: BLE001
            return ""
    if provider in ("claude_code", "anthropic_api"):
        return agent.model
    if provider == "codex":
        return agent.codex_model
    if provider == "openai_api":
        return agent.openai_model
    if provider.startswith("endpoint:"):
        endpoint = _find(agent.endpoints, provider.split(":", 1)[1])
        return endpoint.model if endpoint else ""
    return ""


def name_of(provider: str, agent: AgentSettings) -> str:
    fixed = {"claude_code": "Claude Code", "codex": "Codex", "anthropic_api": "Anthropic API", "openai_api": OPENAI_NAME,
             **{k: v[0] for k, v in LOCAL_SERVERS.items()}}
    if provider in fixed:
        return fixed[provider]
    kind, _, ident = provider.partition(":")
    item = _find(agent.endpoints if kind == "endpoint" else agent.commands, ident)
    return item.name if item else provider


def how_to_compile(provider: str, agent: AgentSettings, has_comments: bool = False) -> str:
    comments = " Co-authors' comments are in .thor-comments/comments.txt." if has_comments else ""
    if provider in ("codex",):
        return f"{COMPILE_WITH_LATEXMK} You have no network access in this sandbox.{comments}"
    if provider.startswith("command:"):
        command = _find(agent.commands, provider.split(":", 1)[1])
        return f"{COMPILE_WITH_LATEXMK}{comments}" if command and command.edits_files else COMPILE_BY_THOR
    return COMPILE_WITH_TOOL


def _endpoint(agent: AgentSettings, ident: str) -> Endpoint:
    endpoint = _find(agent.endpoints, ident)
    if not endpoint:
        raise AgentError("This chat's model provider is no longer in Settings. Choose another.")
    return endpoint


def _command(agent: AgentSettings, ident: str) -> Command:
    command = _find(agent.commands, ident)
    if not command:
        raise AgentError("This chat's model provider is no longer in Settings. Choose another.")
    return command


def run_turn(provider: str, agent: AgentSettings, secret: SecretStore, turn: Turn) -> None:
    if provider == "claude_code":
        from . import claude_code
        claude_code.run(turn, agent.claude_path or None)
    elif provider == "codex":
        from . import codex
        codex.run(turn, agent.codex_path or None)
    elif provider == "anthropic_api":
        key = secret.get(KEYCHAIN_ACCOUNT)
        if not key:
            raise AgentError("No Anthropic API key is saved. Add one in Settings.")
        from . import anthropic_api
        anthropic_api.run(turn, key)
    elif provider == "openai_api":
        key = secret.get(OPENAI_ACCOUNT)
        if not key:
            raise AgentError("No OpenAI API key is saved. Add one in Settings.")
        from . import openai_api
        openai_api.run(turn, openai_api.OPENAI_URL, key, OPENAI_NAME, send_effort=True)
    elif provider in LOCAL_SERVERS:
        from . import openai_api
        url = LOCAL_SERVERS[provider][1]
        if provider == "ollama" and agent.local_keep_loaded and turn.model:
            openai_api.keep_loaded(url, turn.model, turn)
        openai_api.run(turn, url, None, LOCAL_SERVERS[provider][0], no_thinking=turn.fast)
    elif provider.startswith("endpoint:"):
        ident = provider.split(":", 1)[1]
        endpoint = _endpoint(agent, ident)
        from . import openai_api
        openai_api.run(turn, endpoint.base_url, secret.get(endpoint_account(ident)), endpoint.name)
    elif provider.startswith("command:"):
        from . import command
        command.run(turn, _command(agent, provider.split(":", 1)[1]))
    else:
        raise AgentError(f"Unknown model provider {provider}.")


def warm(provider: str, agent: AgentSettings) -> None:
    """Loads a local model before the first message, so that message does not wait for it."""
    if provider == "ollama" and agent.local_keep_loaded and provider in LOCAL_SERVERS:
        model = model_of(provider, agent)
        if model and not is_cloud_model(model):
            from . import openai_api
            openai_api.keep_loaded(LOCAL_SERVERS[provider][1], model)


def list_models(provider: str, agent: AgentSettings, secret: SecretStore) -> list[dict]:
    """The models a provider offers, asked of the provider itself. Raises when it cannot be reached."""
    if provider == "claude_code":
        return MODELS
    if provider == "codex":
        return codex_cli.models()
    if provider.startswith("command:"):
        return []
    if provider == "anthropic_api":
        key = secret.get(KEYCHAIN_ACCOUNT)
        if not key:
            raise AgentError("No Anthropic API key is saved.")
        from . import anthropic_api
        return anthropic_api.list_models(key)
    from . import openai_api
    if provider in LOCAL_SERVERS:
        return openai_api.list_models(LOCAL_SERVERS[provider][1], None)
    if provider == "openai_api":
        key = secret.get(OPENAI_ACCOUNT)
        if not key:
            raise AgentError("No OpenAI API key is saved.")
        return openai_api.list_models(openai_api.OPENAI_URL, key)
    ident = provider.split(":", 1)[1]
    return openai_api.list_models(_endpoint(agent, ident).base_url, secret.get(endpoint_account(ident)))
