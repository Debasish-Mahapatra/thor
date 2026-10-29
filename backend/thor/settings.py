"""App-wide settings (agent sign-in, model, effort, spending cap) and the API key in the macOS Keychain."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from typing import Protocol

from .paths import APP_NAME, app_home

MODELS = [
    {"id": "claude-opus-5-5", "name": "Claude Opus 5.5"},
    {"id": "claude-fable-5-1", "name": "Claude Fable 5.1"},
    {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5"},
    {"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5"},
]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
AUTH_METHODS = ["claude_plan", "api_key"]

KEYCHAIN_SERVICE = APP_NAME
KEYCHAIN_ACCOUNT = "anthropic-api-key"


class SettingsError(ValueError):
    pass


@dataclass
class AgentSettings:
    # "claude_plan": the installed Claude Code and its sign-in. "api_key": a key from the Keychain.
    auth: str = "claude_plan"
    model: str = "claude-opus-5-5"
    # Claude Opus 5.5 defaults to medium; revisions want more thought.
    effort: str = "high"
    # Applies only with an API key, which is billed per token; a Claude plan has its own limits.
    budget_usd: float = 5.0
    # Empty: look for `claude` in the usual places.
    claude_path: str = ""

    def validate(self) -> None:
        if self.auth not in AUTH_METHODS:
            raise SettingsError(f"Unknown sign-in method {self.auth!r}.")
        if not self.model.strip():
            raise SettingsError("The model ID is empty.")
        if self.effort not in EFFORTS:
            raise SettingsError(f"Effort must be one of {', '.join(EFFORTS)}.")
        if not 0 < self.budget_usd <= 500:
            raise SettingsError("The spending cap must be between $0 and $500 per run.")


@dataclass
class Settings:
    agent: AgentSettings = field(default_factory=AgentSettings)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        known = AgentSettings.__dataclass_fields__
        agent = AgentSettings(**{k: v for k, v in (data.get("agent") or {}).items() if k in known})
        agent.budget_usd = float(agent.budget_usd)
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


class SecretStore(Protocol):
    def get(self) -> str | None: ...
    def set(self, value: str) -> None: ...
    def delete(self) -> None: ...


class KeychainSecret:
    """The Anthropic API key, kept in the login Keychain and never written to a file."""

    def get(self) -> str | None:
        import keyring

        return keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)

    def set(self, value: str) -> None:
        import keyring

        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, value)

    def delete(self) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        except PasswordDeleteError:
            pass


class MemorySecret:
    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def get(self) -> str | None:
        return self.value

    def set(self, value: str) -> None:
        self.value = value

    def delete(self) -> None:
        self.value = None
