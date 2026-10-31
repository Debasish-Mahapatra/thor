"""App-wide settings (which model the agent uses, effort, spending cap) and API keys in the macOS Keychain."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from .paths import APP_NAME, app_home

MODELS = [
    {"id": "claude-opus-5-5", "name": "Claude Opus 5.5"},
    {"id": "claude-fable-5-1", "name": "Claude Fable 5.1"},
    {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5"},
    {"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5"},
]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
# The fixed providers; bring-your-own ones are "endpoint:<id>" and "command:<id>".
PROVIDERS = ["claude_code", "codex", "anthropic_api", "openai_api"]
# Model servers on this Mac, found by asking them.
LOCAL_PROVIDERS = ["ollama", "lmstudio"]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

KEYCHAIN_SERVICE = APP_NAME
KEYCHAIN_ACCOUNT = "anthropic-api-key"
OPENAI_ACCOUNT = "openai-api-key"
ENDPOINT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class SettingsError(ValueError):
    pass


def endpoint_account(endpoint_id: str) -> str:
    return f"endpoint-{endpoint_id}"


def check_base_url(url: str) -> None:
    """A key goes to this address, so it must travel encrypted unless the server is on this Mac."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SettingsError(f"{url!r} is not a web address. It should look like https://host/v1.")
    if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
        raise SettingsError(f"{url} is not encrypted. Use https, or a server on this Mac (127.0.0.1).")


@dataclass
class Endpoint:
    """Bring your own model: a server that speaks the OpenAI chat API (Ollama, LM Studio, OpenRouter, ...)."""

    id: str
    name: str
    base_url: str
    model: str = ""


@dataclass
class Command:
    """Bring your own model: a command-line program. The prompt goes to its stdin, or replaces {prompt} in
    the command. edits_files says it changes files in its working folder itself; otherwise it prints text, and
    the edit blocks in that text are applied."""

    id: str
    name: str
    command: str
    edits_files: bool = False
    # Runs inside the Mac's sandbox (agent/sandbox.py). The user can turn this off for a program that needs more.
    sandboxed: bool = True


@dataclass
class AgentSettings:
    provider: str = "claude_code"
    # For Claude Code and the Anthropic API.
    model: str = "claude-opus-5-5"
    # Empty: the model Codex itself is set to use.
    codex_model: str = ""
    openai_model: str = ""
    # Empty: the first model on the server that runs on this Mac.
    ollama_model: str = ""
    lmstudio_model: str = ""
    # Keeps an Ollama model in memory between turns: the first message does not wait, but it holds many GB.
    local_keep_loaded: bool = True
    # Claude Opus 5.5 defaults to medium; revisions want more thought.
    effort: str = "high"
    # Applies only to APIs billed per token with a known price; a plan has its own limits.
    budget_usd: float = 5.0
    # Empty: look for the program in the usual places.
    claude_path: str = ""
    codex_path: str = ""
    endpoints: list[Endpoint] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)

    def validate(self) -> None:
        known = PROVIDERS + LOCAL_PROVIDERS + [f"endpoint:{e.id}" for e in self.endpoints] + [f"command:{c.id}" for c in self.commands]
        if self.provider not in known:
            raise SettingsError(f"Unknown model provider {self.provider!r}.")
        if not self.model.strip():
            raise SettingsError("The model ID is empty.")
        if self.effort not in EFFORTS:
            raise SettingsError(f"Effort must be one of {', '.join(EFFORTS)}.")
        if not 0 < self.budget_usd <= 500:
            raise SettingsError("The spending cap must be between $0 and $500 per run.")
        for item in (*self.endpoints, *self.commands):
            if not ENDPOINT_ID.match(item.id):
                raise SettingsError(f"{item.id!r} cannot be used as an ID.")
            if not item.name.strip():
                raise SettingsError("Every provider you add needs a name.")
        for endpoint in self.endpoints:
            check_base_url(endpoint.base_url)
        for command in self.commands:
            if not command.command.strip():
                raise SettingsError(f"{command.name} has no command.")


def _fields(kind: type, data: dict):
    known = kind.__dataclass_fields__
    return kind(**{k: v for k, v in data.items() if k in known})


@dataclass
class Settings:
    agent: AgentSettings = field(default_factory=AgentSettings)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        known = AgentSettings.__dataclass_fields__
        raw = dict(data.get("agent") or {})
        # Settings saved before there were several providers.
        if "provider" not in raw and "auth" in raw:
            raw["provider"] = {"api_key": "anthropic_api"}.get(raw["auth"], "claude_code")
        agent = AgentSettings(**{k: v for k, v in raw.items() if k in known})
        agent.budget_usd = float(agent.budget_usd)
        agent.endpoints = [_fields(Endpoint, e) for e in raw.get("endpoints") or []]
        agent.commands = [_fields(Command, c) for c in raw.get("commands") or []]
        return cls(agent=agent)


class SettingsStore:
    def __init__(self) -> None:
        self.path = app_home() / "settings.json"
        self._lock = threading.Lock()

    def load(self) -> Settings:
        try:
            return Settings.from_dict(json.loads(self.path.read_text()))
        except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
            return Settings()

    def save(self, settings: Settings) -> None:
        settings.agent.validate()
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(settings.to_dict(), indent=2) + "\n")
            tmp.replace(self.path)

    def layout(self) -> dict:
        """The window's layout choices. These live here, not in browser storage, because the app's port changes
        with each launch and browser storage goes with the port."""
        try:
            found = json.loads((app_home() / "layout.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return found if isinstance(found, dict) else {}

    def save_layout(self, changes: dict) -> dict:
        for key, value in changes.items():
            if not (isinstance(key, str) and len(key) <= 40 and isinstance(value, (bool, int, float, str))
                    and len(str(value)) <= 40):
                raise SettingsError(f"{key!r} is not a layout setting.")
        with self._lock:
            merged = {**self.layout(), **changes}
            if len(merged) > 60:
                raise SettingsError("Too many layout settings.")
            path = app_home() / "layout.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(merged))
            tmp.replace(path)
        return merged


class SecretStore(Protocol):
    def get(self, account: str = KEYCHAIN_ACCOUNT) -> str | None: ...
    def set(self, value: str, account: str = KEYCHAIN_ACCOUNT) -> None: ...
    def delete(self, account: str = KEYCHAIN_ACCOUNT) -> None: ...


class KeychainSecret:
    """API keys, kept in the login Keychain and never written to a file."""

    def get(self, account: str = KEYCHAIN_ACCOUNT) -> str | None:
        import keyring

        return keyring.get_password(KEYCHAIN_SERVICE, account)

    def set(self, value: str, account: str = KEYCHAIN_ACCOUNT) -> None:
        import keyring

        keyring.set_password(KEYCHAIN_SERVICE, account, value)

    def delete(self, account: str = KEYCHAIN_ACCOUNT) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(KEYCHAIN_SERVICE, account)
        except PasswordDeleteError:
            pass


class MemorySecret:
    def __init__(self, value: str | None = None) -> None:
        self.values = {KEYCHAIN_ACCOUNT: value} if value else {}

    def get(self, account: str = KEYCHAIN_ACCOUNT) -> str | None:
        return self.values.get(account)

    def set(self, value: str, account: str = KEYCHAIN_ACCOUNT) -> None:
        self.values[account] = value

    def delete(self, account: str = KEYCHAIN_ACCOUNT) -> None:
        self.values.pop(account, None)
