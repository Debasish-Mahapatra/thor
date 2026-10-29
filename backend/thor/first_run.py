"""First-run setup: installs what is missing as soon as the app starts, with nothing to click.

1. Claude Code, with Anthropic's official installer (no password). If it is not signed in,
   `claude auth login` opens the sign-in page in the browser.
2. MacTeX (setup_tex.TexInstaller). macOS asks for the password once, in its own dialog.

The app starts it with `serve --setup`; the interface shows its progress until it is done.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import claude_cli
from .paths import run_tool
from .setup_tex import TexInstaller

CLAUDE_INSTALL = "curl -fsSL https://claude.ai/install.sh | bash"


@dataclass
class Step:
    id: str
    name: str
    # waiting, running, done, failed
    state: str = "waiting"
    message: str = ""


@dataclass
class SetupState:
    steps: list[Step] = field(default_factory=lambda: [Step("claude", "Claude Code"), Step("mactex", "MacTeX")])
    finished: bool = False


class FirstRun:
    def __init__(
        self,
        tex: TexInstaller,
        claude_status: Callable[[], claude_cli.ClaudeStatus] = claude_cli.status,
        run: Callable[..., tuple[int, str]] = run_tool,
        enabled: bool = False,
    ) -> None:
        self.tex = tex
        self.claude_status = claude_status
        self.run = run
        self.enabled = enabled
        self.state = SetupState()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def needed(self) -> bool:
        return not self.tex.installed() or not claude_cli.locate()

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        with self._lock:
            steps = [asdict(s) for s in self.state.steps]
            finished = self.state.finished
        return {
            "enabled": self.enabled,
            "running": self.running(),
            "finished": finished,
            "failed": any(s["state"] == "failed" for s in steps),
            "steps": steps,
            "tex": self.tex.status(),
        }

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self.state = SetupState()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _step(self, step_id: str, state: str, message: str) -> None:
        with self._lock:
            step = next(s for s in self.state.steps if s.id == step_id)
            step.state, step.message = state, message

    def _run(self) -> None:
        for step_id, action in (("claude", self._claude), ("mactex", self._mactex)):
            try:
                action()
            except Exception as e:  # shown on the setup screen; the other steps still run
                self._step(step_id, "failed", str(e))
        with self._lock:
            self.state.finished = True

    def _claude(self) -> None:
        st = self.claude_status()
        if not st.path:
            self._step("claude", "running", "Installing Claude Code.")
            code, out = self.run(["/bin/bash", "-c", CLAUDE_INSTALL], timeout=900)
            st = self.claude_status()
            if not st.path:
                raise RuntimeError(f"Claude Code did not install: {out.splitlines()[-1] if out else f'exit code {code}'}")
        if st.logged_in is False:
            self._step("claude", "running", "Sign in to Claude in the browser page that opened.")
            code, out = self.run([st.path, "auth", "login"], timeout=1800)
            st = self.claude_status()
            if st.logged_in is False:
                raise RuntimeError("Claude Code is installed but not signed in. Run `claude auth login` in Terminal.")
        self._step("claude", "done", f"Claude Code {st.version or ''} is ready.".replace("  ", " "))

    def _mactex(self) -> None:
        if self.tex.installed():
            self._step("mactex", "done", "MacTeX is installed.")
            return
        self._step("mactex", "running", "Installing MacTeX.")
        self.tex.start()
        self.tex.wait()
        result = self.tex.status()
        if result["step"] == "done":
            self._step("mactex", "done", result["message"])
        elif result["step"] == "cancelled":
            raise RuntimeError("The password dialog was closed, so MacTeX was not installed.")
        else:
            raise RuntimeError(result["message"])
