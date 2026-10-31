"""Conversations with the agent: one folder per chat under Thor's home, holding its events, what each provider
remembers, and the staging copy.

Each turn runs on its own thread. The interface reads the events as they come. Text goes out in whole lines, or
at the latest every second: the transcript must grow in steady steps, not flicker word by word.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import build, numbers, references, scripts, zotero
from ..history import History
from ..paths import APP_NAME, app_home
from ..projects import ProjectConfig, inside
from ..settings import SecretStore, Settings, SettingsStore
from . import providers
from .base import AgentError, Turn, instructions, read_if_exists
from .staging import Staging, StagingConflict
from .tools import Submission, ToolBox

CHAT_ID = re.compile(r"^[0-9a-f]{12}$")
FLUSH_SECONDS = 1.0
APPROVAL_TIMEOUT = 1800
MAX_RECAP = 30_000
MAX_PROBLEMS = 40
MAX_PASSAGES = 40
MAX_PASSAGE_CHARS = 40_000
# Providers that remember nothing between turns get the conversation again each time.
FORGETFUL = ("command:",)
# Where a model that cannot call Thor's tools finds the comments, inside its copy. Hidden, so it is never an edit.
COMMENTS_FOLDER = ".thor-comments"
NO_COMMENTS = "No comments have been imported."


class AgentBusy(Exception):
    pass


class UnknownChat(Exception):
    pass


@dataclass
class Task:
    """A turn with a job of its own: it can only read, and answers through a tool. It starts no provider session and
    leaves none behind, because it needs nothing from the chat, and its prompt holds all it has to know."""
    submission: Submission
    # Called with what was handed in; what it returns is said in the transcript.
    on_submit: Callable[[dict, str], str]
    # Shown in the transcript instead of the long prompt.
    title: str
    missing: str


@dataclass
class Run:
    cancelled: threading.Event = field(default_factory=threading.Event)
    approvals: dict[str, tuple[threading.Event, list[bool]]] = field(default_factory=dict)
    buffer: str = ""
    flushed: float = 0.0


def agent_build_id(chat_id: str) -> str:
    return f"agent-{chat_id}"


def staging_has_new_file(root: Path, project_path: str) -> bool:
    return (root / project_path).is_file()


def compile_report(result: build.BuildResult) -> str:
    """A compile, as told to the model."""
    lines = [result.summary, f"{result.passes} LaTeX pass{'es' if result.passes != 1 else ''}."]
    for problem in result.problems[:MAX_PROBLEMS]:
        where = f"{problem['file']}:{problem['line']}: " if problem.get("file") and problem.get("line") else ""
        lines.append(f"{problem['kind']}: {where}{problem['message']}")
    if len(result.problems) > MAX_PROBLEMS:
        lines.append(f"[{len(result.problems) - MAX_PROBLEMS} more not shown.]")
    return "\n".join(lines)


def describe_context(context: dict | None) -> str:
    """What the user is looking at, in words the model can use."""
    if not context:
        return ""
    parts = []
    if context.get("file"):
        line = f", with the cursor on line {context['line']}" if context.get("line") else ""
        parts.append(f"The user has {context['file']} open{line}.")
    selection = context.get("selection") or {}
    added = [item for item in (context.get("selections") or [])[:MAX_PASSAGES] if isinstance(item, dict) and item.get("text")]
    for item in added:
        parts.append(f"They attached this passage from {item.get('file')}, lines {item.get('from')}-{item.get('to')}:\n"
                     f"<passage>\n{str(item['text'])[:MAX_PASSAGE_CHARS]}\n</passage>")
    # What is selected right now, unless it is one of the passages already.
    if selection.get("text") and not any(item["text"] == selection["text"] for item in added):
        parts.append(f"They have selected lines {selection.get('from')}-{selection.get('to')}:\n"
                     f"<selection>\n{selection['text']}\n</selection>")
    if context.get("build"):
        parts.append(f"Their last compile: {context['build']}")
    problems = context.get("problems") or []
    if problems:
        parts.append("Its problems:\n" + "\n".join(f"- {p}" for p in problems[:MAX_PROBLEMS]))
    return "\n".join(parts)


class AgentManager:
    def __init__(self, settings_store: SettingsStore, secret: SecretStore, history: History) -> None:
        self.settings_store = settings_store
        self.secret = secret
        self.history = history
        self._lock = threading.RLock()
        self._runs: dict[str, Run] = {}
        self._events: dict[str, list[dict]] = {}

    # Where things are

    def _folder(self, project_id: str, chat_id: str) -> Path:
        if not CHAT_ID.match(chat_id):
            raise UnknownChat(chat_id)
        return app_home() / "agent" / project_id / chat_id

    def _meta(self, project_id: str, chat_id: str) -> dict:
        try:
            return json.loads((self._folder(project_id, chat_id) / "chat.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError) as e:
            raise UnknownChat(chat_id) from e

    def _save_meta(self, project_id: str, meta: dict) -> None:
        path = self._folder(project_id, meta["id"]) / "chat.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta))
        tmp.replace(path)

    def _staging(self, project_id: str, chat_id: str, root: Path, cfg: ProjectConfig) -> Staging:
        return Staging(self._folder(project_id, chat_id), root, cfg)

    # Chats

    def list_chats(self, project_id: str) -> list[dict]:
        folder = app_home() / "agent" / project_id
        chats = []
        for path in folder.iterdir() if folder.is_dir() else []:
            try:
                meta = json.loads((path / "chat.json").read_text())
            except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
                continue
            chats.append({"id": meta["id"], "title": meta["title"], "updated": meta["updated"],
                          "provider": meta.get("provider"), "running": meta["id"] in self._runs})
        return sorted(chats, key=lambda c: -c["updated"])

    def create_chat(self, project_id: str) -> dict:
        chat_id = secrets.token_hex(6)
        now = time.time()
        meta = {"id": chat_id, "title": "New chat", "created": now, "updated": now, "provider": None, "model": None,
                "sessions": {}, "rejected": []}
        self._folder(project_id, chat_id).mkdir(parents=True)
        self._save_meta(project_id, meta)
        return {**meta, "events": [], "running": False}

    def chat(self, project_id: str, chat_id: str) -> dict:
        meta = self._meta(project_id, chat_id)
        return {**meta, "events": self._load_events(project_id, chat_id), "running": chat_id in self._runs}

    def delete_chat(self, project_id: str, chat_id: str) -> None:
        self._meta(project_id, chat_id)
        if chat_id in self._runs:
            raise AgentBusy("The agent is working in this chat. Stop it first.")
        build.discard_spare(agent_build_id(chat_id))
        shutil.rmtree(self._folder(project_id, chat_id), ignore_errors=True)
        shutil.rmtree(build.builds_dir(agent_build_id(chat_id)), ignore_errors=True)
        self._events.pop(chat_id, None)

    # Events

    def _load_events(self, project_id: str, chat_id: str) -> list[dict]:
        with self._lock:
            if chat_id not in self._events:
                path = self._folder(project_id, chat_id) / "events.jsonl"
                events = []
                if path.is_file():
                    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
                self._events[chat_id] = events
            return self._events[chat_id]

    def events_after(self, project_id: str, chat_id: str, seq: int) -> list[dict]:
        return [e for e in self._load_events(project_id, chat_id) if e["seq"] > seq]

    def running(self, chat_id: str) -> bool:
        return chat_id in self._runs

    def _append(self, project_id: str, chat_id: str, event: dict) -> None:
        with self._lock:
            events = self._load_events(project_id, chat_id)
            event = {**event, "seq": (events[-1]["seq"] + 1) if events else 1, "time": time.time()}
            events.append(event)
            with (self._folder(project_id, chat_id) / "events.jsonl").open("a") as f:
                f.write(json.dumps(event) + "\n")

    def _emitter(self, project_id: str, chat_id: str, run: Run):
        def flush() -> None:
            if run.buffer:
                self._append(project_id, chat_id, {"type": "text", "text": run.buffer})
                run.buffer = ""
            run.flushed = time.monotonic()

        def emit(event: dict) -> None:
            if event["type"] == "text":
                run.buffer += event["text"]
                if "\n" in run.buffer or time.monotonic() - run.flushed > FLUSH_SECONDS:
                    cut = run.buffer.rfind("\n") + 1 or len(run.buffer)
                    head, run.buffer = run.buffer[:cut], run.buffer[cut:]
                    self._append(project_id, chat_id, {"type": "text", "text": head})
                    run.flushed = time.monotonic()
            else:
                flush()
                self._append(project_id, chat_id, event)

        return emit, flush

    # A turn

    def send(self, project_id: str, chat_id: str, root: Path, cfg: ProjectConfig, text: str, context: dict | None,
             provider: str | None, model: str | None, fast: bool = False, task: Task | None = None) -> None:
        meta = self._meta(project_id, chat_id)
        with self._lock:
            if chat_id in self._runs:
                raise AgentBusy("The agent is still working in this chat.")
            run = Run(flushed=time.monotonic())
            self._runs[chat_id] = run
        agent = self.settings_store.load().agent
        same_provider = provider in (None, meta.get("provider"))
        provider = provider or meta.get("provider") or agent.provider
        # The chat's model unless another was chosen, which may be one a provider switched to by itself.
        model = model or (meta.get("model") if same_provider else None) or providers.model_of(provider, agent)
        shown = task.title if task else text
        if meta["title"] == "New chat":
            meta["title"] = shown.strip().splitlines()[0][:70] if shown.strip() else "New chat"
        meta.update(provider=provider, model=model, updated=time.time())
        self._save_meta(project_id, meta)
        self._append(project_id, chat_id, {"type": "user", "text": shown, "context": context, "provider": provider,
                                           "provider_name": providers.name_of(provider, agent), "model": model})
        threading.Thread(target=self._turn, args=(project_id, chat_id, root, cfg, text, context, provider, model, run, fast, task),
                         daemon=True).start()

    def stop(self, chat_id: str) -> None:
        run = self._runs.get(chat_id)
        if run:
            run.cancelled.set()
            for event, _ in run.approvals.values():
                event.set()

    def decide(self, chat_id: str, approval_id: str, allow: bool) -> bool:
        run = self._runs.get(chat_id)
        found = run.approvals.get(approval_id) if run else None
        if not found:
            return False
        found[1].append(allow)
        found[0].set()
        return True

    def _recap(self, project_id: str, chat_id: str) -> str:
        lines: list[str] = []
        for event in self._load_events(project_id, chat_id)[:-1]:
            if event["type"] == "user":
                lines.append(f"\nUser: {event['text']}\nAgent: ")
            elif event["type"] == "text":
                lines.append(event["text"])
        return "".join(lines).strip()[-MAX_RECAP:]

    def _compile(self, staging: Staging, cfg: ProjectConfig, chat_id: str) -> build.BuildResult:
        # latexmk would run a changed latexmkrc as code, in Thor's process, outside the agent's sandbox.
        staging.restore_rc()
        return build.build(staging.work_root, cfg, agent_build_id(chat_id))

    def _comments(self, root: Path, cfg: ProjectConfig) -> str:
        found = []
        revisions = inside(root, cfg.revisions_dir) if cfg.revisions_dir else root
        for path in sorted(revisions.glob("revision*/supporting_files/comments/*/comments.txt")):
            found.append(f"--- {path.relative_to(root)}\n{read_if_exists(path)}")
        return "\n\n".join(found[-3:]) or NO_COMMENTS

    def _history_path(self, project_id: str, chat_id: str, provider: str) -> Path:
        slug = re.sub(r"[^a-z0-9-]", "-", provider.lower())
        return self._folder(project_id, chat_id) / f"messages-{slug}.json"

    def _turn(self, project_id: str, chat_id: str, root: Path, cfg: ProjectConfig, text: str, context: dict | None,
              provider: str, model: str, run: Run, fast: bool = False, task: Task | None = None) -> None:
        emit, flush = self._emitter(project_id, chat_id, run)
        meta = self._meta(project_id, chat_id)
        staging = self._staging(project_id, chat_id, root, cfg)
        agent = self.settings_store.load().agent
        box: ToolBox | None = None
        try:
            notes = []
            if staging.exists():
                dropped = staging.sync()
                if dropped:
                    notes.append("The user changed the text under these waiting edits of yours, so they were dropped: "
                                 + "; ".join(dropped))
            else:
                staging.create()
            if meta.get("rejected"):
                notes.append("The user rejected these edits of yours, and they have been taken out of your copy. Do not "
                             "make them again unless asked:\n" + "\n".join(f"- {n}" for n in meta["rejected"]))
                meta["rejected"] = []
            readable = [p for p in (inside(root, name) for name in (cfg.results_dir, cfg.scripts_dir) if name)
                        if p.is_dir()]

            def compile_fn() -> str:
                result = self._compile(staging, cfg, chat_id)
                if box:
                    box.wrote = False
                emit({"type": "compile", "summary": result.summary, "ok": result.ok, "build": result.id})
                return compile_report(result)

            box = ToolBox(staging.work, readable, compile_fn, lambda: self._comments(root, cfg),
                          results_fn=lambda query: numbers.search_results(root, cfg, query),
                          reference_fn=lambda doi: references.open_text(doi, agent.unpaywall_email),
                          zotero_fn=zotero.search_text,
                          script_fn=lambda path: self._run_script(staging, root, cfg, path, emit, run),
                          submission=task.submission if task else None, read_only=bool(task))
            comments = self._comments(root, cfg)
            has_comments = comments != NO_COMMENTS
            if has_comments:
                folder = staging.work / COMMENTS_FOLDER
                folder.mkdir(exist_ok=True)
                (folder / "comments.txt").write_text(comments)
            standing = read_if_exists(inside(root, cfg.standing_instructions)) if cfg.standing_instructions else ""
            style = read_if_exists(inside(root, cfg.style_rules)) if cfg.style_rules else ""
            described = describe_context(context)
            prompt = "\n\n".join(p for p in ["\n".join(notes), text, described and f"[From the editor]\n{described}"] if p)
            open_files = []
            attached = [item.get("file") for item in (context or {}).get("selections") or [] if isinstance(item, dict)]
            for name in (cfg.main, *(staging.relative(f) for f in [(context or {}).get("file"), *attached] if f)):
                if name and (staging.work / name).is_file():
                    open_files.append(staging.work / name)
            history_path = self._history_path(project_id, chat_id, provider)
            history = json.loads(history_path.read_text()) if history_path.is_file() and not task else []
            session = None if task else meta["sessions"].get(provider)
            remembers = bool(session or history) and not provider.startswith(FORGETFUL)
            turn = Turn(
                prompt=prompt,
                instructions=instructions(cfg.name or root.name, cfg.main, cfg.engine,
                                          providers.how_to_compile(provider, agent, has_comments, task.submission.spec["name"] if task else ""), standing,
                                          style, readable),
                cwd=staging.work, box=box, model=model or "", effort=agent.effort, budget_usd=agent.budget_usd,
                emit=emit, cancelled=run.cancelled, ask=lambda s, d: self._ask(run, emit, s, d), readable=readable,
                recap="" if remembers or task else self._recap(project_id, chat_id), open_files=open_files, session=session,
                history=history, write_root=staging.work_root, fast=fast,
                comments=comments if has_comments and (task or "comment" in text.lower()) else "", read_only=bool(task),
            )
            try:
                providers.run_turn(provider, agent, self.secret, turn)
            finally:
                # Saved even after a failure: a provider's memory of the turn so far is still true.
                if turn.history and not task:
                    history_path.write_text(json.dumps(turn.history))
                if turn.session and not task:
                    meta["sessions"][provider] = turn.session
                meta["model"] = turn.model
                # Codex changed to a model that works: new chats should not have to find out again.
                if provider == "codex" and turn.model and not model:
                    fresh = self.settings_store.load()
                    if not fresh.agent.codex_model:
                        fresh.agent.codex_model = turn.model
                        self.settings_store.save(Settings(agent=fresh.agent))
            flush()
            if run.cancelled.is_set():
                emit({"type": "notice", "text": "Stopped."})
            elif task:
                if task.submission.received is None:
                    raise AgentError(task.missing)
                emit({"type": "notice", "text": task.on_submit(task.submission.received, turn.model)})
        except AgentError as e:
            emit({"type": "error", "text": str(e)})
        except Exception as e:  # noqa: BLE001
            # A thread nobody waits on: an unexpected failure must still reach the transcript.
            emit({"type": "error", "text": f"{APP_NAME} could not finish this: {type(e).__name__}: {e}"})
        finally:
            flush()
            try:
                self._after_turn(staging, cfg, chat_id, box, emit)
            except Exception as e:  # noqa: BLE001
                emit({"type": "error", "text": f"{APP_NAME} could not check the agent's edits: {e}"})
            meta["updated"] = time.time()
            self._save_meta(project_id, meta)
            with self._lock:
                emit({"type": "done"})
                self._runs.pop(chat_id, None)

    def _run_script(self, staging: Staging, root: Path, cfg: ProjectConfig, path: str, emit, run: Run) -> str:
        """Asks first, then runs a figure script on the agent's copy under the sandbox."""
        script = scripts.in_copy(staging, root, cfg, path)
        if not self._ask(run, emit, f"Run {script.name}?", f"{script} runs on the agent's copy of the paper. It can write "
                         "only there, and what it makes waits for you in Changes."):
            return "The user said no."
        return scripts.run_in_copy(staging, root, cfg, script, run.cancelled)

    def _after_turn(self, staging: Staging, cfg: ProjectConfig, chat_id: str, box: ToolBox | None, emit) -> None:
        if not staging.exists():
            return
        changes = staging.changes()
        if changes and box and box.wrote:
            result = self._compile(staging, cfg, chat_id)
            emit({"type": "compile", "summary": result.summary, "ok": result.ok, "build": result.id})
        if changes:
            emit({"type": "changes", "files": len(changes), "additions": sum(c.additions for c in changes),
                  "deletions": sum(c.deletions for c in changes),
                  "hunks": sum(max(1, len(c.hunks)) for c in changes)})

    def _ask(self, run: Run, emit, summary: str, detail: str) -> bool:
        ident = uuid.uuid4().hex[:12]
        waiting = threading.Event()
        answer: list[bool] = []
        run.approvals[ident] = (waiting, answer)
        emit({"type": "approval", "id": ident, "summary": summary, "detail": detail})
        waiting.wait(APPROVAL_TIMEOUT)
        run.approvals.pop(ident, None)
        allowed = bool(answer and answer[0])
        emit({"type": "approval_done", "id": ident, "allowed": allowed})
        return allowed

    # The edits waiting for the user

    def changes(self, project_id: str, chat_id: str, root: Path, cfg: ProjectConfig) -> dict:
        self._meta(project_id, chat_id)
        staging = self._staging(project_id, chat_id, root, cfg)
        files = staging.changes() if staging.exists() else []
        last = build.latest(agent_build_id(chat_id))
        return {"files": [f.to_dict() for f in files], "running": chat_id in self._runs,
                "build": {"id": last.id, "ok": last.ok, "summary": last.summary, "pdf": last.pdf} if last else None}

    def _record(self, project_id: str, project_path: str, before: bytes | None, root: Path) -> None:
        if before is not None:
            self.history.record(project_id, project_path, before)
        target = root / project_path
        if target.is_file():
            self.history.record(project_id, project_path, target.read_bytes(), label="Agent edit accepted")

    def accept(self, project_id: str, chat_id: str, root: Path, cfg: ProjectConfig, path: str | None,
               hunk: str | None) -> list[str]:
        """Takes one hunk, one file, or everything into the project; returns the files written."""
        self._meta(project_id, chat_id)
        if chat_id in self._runs:
            raise AgentBusy("The agent is still working. Stop it, or wait, before accepting edits.")
        staging = self._staging(project_id, chat_id, root, cfg)
        targets = [path] if path else [c.path for c in staging.changes() if not c.refused]
        written = []
        for target in targets:
            # Only a single hunk asked for by name may be a flagged one.
            done, before = staging.accept(target, hunk, skip_flagged=hunk is None)
            if before is not None or not path or staging_has_new_file(root, done):
                self._record(project_id, done, before, root)
                written.append(done)
        return written

    def reject(self, project_id: str, chat_id: str, root: Path, cfg: ProjectConfig, path: str | None,
               hunk: str | None) -> None:
        meta = self._meta(project_id, chat_id)
        if chat_id in self._runs:
            raise AgentBusy("The agent is still working. Stop it, or wait, before rejecting edits.")
        staging = self._staging(project_id, chat_id, root, cfg)
        targets = [path] if path else [c.path for c in staging.changes()]
        for target in targets:
            meta["rejected"] = [*meta.get("rejected", []), *staging.reject(target, hunk)]
        self._save_meta(project_id, meta)

    def pdf(self, chat_id: str) -> Path | None:
        """The PDF of the agent's copy, from Thor's last compile of it."""
        last = build.latest(agent_build_id(chat_id))
        if not last or not last.pdf:
            return None
        return build.output_file(agent_build_id(chat_id), last.id, Path(last.main).stem + ".pdf")


__all__ = ["AgentBusy", "AgentManager", "StagingConflict", "Task", "UnknownChat"]
