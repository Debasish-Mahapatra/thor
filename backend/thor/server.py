"""The HTTP API the UI talks to, plus the built UI itself.

The server listens on 127.0.0.1 only. Every /api request must carry the per-launch token, as the cookie
that /auth sets or as a bearer header, and requests for any other host name are refused, so other web
pages and other users' processes cannot drive it.
"""

from __future__ import annotations

import asyncio
import json
import secrets as pysecrets
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, aids, build, comments, deps, files, projects, style, synctex
from .agent import providers
from .agent.base import AgentError
from .agent.runs import CHAT_ID, AgentBusy, AgentManager, UnknownChat
from .agent.staging import StagingConflict
from .history import History, unified_diff
from .paths import APP_NAME
from .settings import (EFFORTS, KEYCHAIN_ACCOUNT, MODELS, OPENAI_ACCOUNT, SecretStore, Settings, SettingsError, SettingsStore,
                       endpoint_account)
from .first_run import FirstRun
from .revisions import RevisionError, Revisions, folder_of
from .setup_tex import TexInstaller
from .store import Store

COOKIE = "thor_token"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


class OpenRequest(BaseModel):
    path: str
    main: str | None = None


class ApiKeyRequest(BaseModel):
    key: str


class SaveRequest(BaseModel):
    path: str
    text: str
    # The version the edit started from; None for a new file.
    base_hash: str | None


class ForwardRequest(BaseModel):
    file: str
    line: int
    column: int = 0


class BackwardRequest(BaseModel):
    page: int
    x: float
    y: float


class ImportRequest(BaseModel):
    path: str
    email: str = ""
    name: str = ""


class AliasesRequest(BaseModel):
    aliases: dict[str, str]


class AnswerRequest(BaseModel):
    id: int
    answer: str


class MessageRequest(BaseModel):
    text: str
    context: dict | None = None
    provider: str | None = None
    model: str | None = None
    fast: bool = False


class ReplaceRequest(BaseModel):
    query: str
    replacement: str = ""
    regex: bool = False
    case: bool = False
    paths: list[str]


class DecisionRequest(BaseModel):
    allow: bool


class EditChoice(BaseModel):
    # Neither: every waiting edit. A path alone: all of that file's.
    path: str | None = None
    hunk: str | None = None


class InterfaceFiles(StaticFiles):
    """The built interface. Its assets have hashed names and never change, so they are kept for good; the page that
    names them is checked each time, or an updated app would go on showing the old interface."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
        return response


def host_name(header: str) -> str:
    if header.startswith("["):
        return header[1:].split("]", 1)[0]
    return header.rsplit(":", 1)[0]


def create_app(token: str, secret: SecretStore, ui_dir: Path | None = None, store: Store | None = None,
               settings_store: SettingsStore | None = None, setup: FirstRun | None = None,
               history: History | None = None) -> FastAPI:
    app = FastAPI(title=APP_NAME, version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    store = store or Store()
    settings_store = settings_store or SettingsStore()
    setup = setup or FirstRun(TexInstaller())
    history = history or History()
    revisions = Revisions(store)
    agent = AgentManager(settings_store, secret, history)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if host_name(request.headers.get("host", "")) not in LOCAL_HOSTS:
            return PlainTextResponse(f"{APP_NAME} only answers on 127.0.0.1.", status_code=400)
        if request.url.path.startswith("/api/"):
            supplied = request.cookies.get(COOKIE) or request.headers.get("authorization", "").removeprefix("Bearer ")
            if not pysecrets.compare_digest(supplied.encode(), token.encode()):
                return JSONResponse({"detail": f"Not signed in to this {APP_NAME} session."}, status_code=401)
        return await call_next(request)

    @app.get("/auth")
    def auth(supplied: str = Query("", alias="token")):
        if not pysecrets.compare_digest(supplied.encode(), token.encode()):
            return PlainTextResponse("Wrong or missing token.", status_code=401)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict")
        return response

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/deps")
    def dependency_report():
        checks = deps.run_checks(settings_store.load().agent, secret)
        return {"checks": [c.to_dict() for c in checks], "summary": deps.summary(checks)}

    @app.get("/api/settings")
    def get_settings():
        return {**settings_store.load().to_dict(), "models": MODELS, "efforts": EFFORTS,
                "api_key_saved": bool(secret.get())}

    @app.put("/api/settings")
    def put_settings(body: dict):
        try:
            settings = Settings.from_dict(body)
            settings_store.save(settings)
        except (SettingsError, TypeError, ValueError) as e:
            raise HTTPException(422, str(e)) from e
        return get_settings()

    @app.get("/api/settings/layout")
    def get_layout():
        return settings_store.layout()

    @app.put("/api/settings/layout")
    def put_layout(body: dict):
        try:
            return settings_store.save_layout(body)
        except SettingsError as e:
            raise HTTPException(422, str(e)) from e

    @app.put("/api/settings/api-key")
    def put_api_key(body: ApiKeyRequest):
        key = body.key.strip()
        if not key.startswith("sk-ant-"):
            raise HTTPException(422, "That does not look like an Anthropic API key (sk-ant-...).")
        secret.set(key)
        return {"api_key_saved": True}

    @app.delete("/api/settings/api-key")
    def delete_api_key():
        secret.delete()
        return {"api_key_saved": False}

    def key_account(provider: str) -> str:
        if provider == "anthropic_api":
            return KEYCHAIN_ACCOUNT
        if provider == "openai_api":
            return OPENAI_ACCOUNT
        if provider.startswith("endpoint:") and any(e.id == provider.split(":", 1)[1]
                                                    for e in settings_store.load().agent.endpoints):
            return endpoint_account(provider.split(":", 1)[1])
        raise HTTPException(404, "That provider has no API key.")

    @app.put("/api/agent/keys/{provider}")
    def put_key(provider: str, body: ApiKeyRequest):
        key = body.key.strip()
        if not key:
            raise HTTPException(422, "The key is empty.")
        secret.set(key, key_account(provider))
        return {"saved": True}

    @app.delete("/api/agent/keys/{provider}")
    def delete_key(provider: str):
        secret.delete(key_account(provider))
        return {"saved": False}

    @app.get("/api/agent/providers")
    def agent_providers():
        current = settings_store.load().agent
        found = providers.catalog(current, secret)
        return {"current": providers.effective(current, found), "providers": found}

    @app.post("/api/agent/providers/{provider}/warm")
    def warm_provider(provider: str):
        threading.Thread(target=providers.warm, args=(provider, settings_store.load().agent), daemon=True).start()
        return {"ok": True}

    @app.get("/api/agent/providers/{provider}/models")
    def provider_models(provider: str):
        try:
            return {"models": providers.list_models(provider, settings_store.load().agent, secret)}
        except AgentError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:  # noqa: BLE001
            # Each provider and SDK fails in its own way here; the user is asking whether this one answers.
            raise HTTPException(502, f"The provider did not list its models: {e}") from e

    @app.get("/api/setup")
    def setup_status():
        return setup.status()

    @app.post("/api/setup/start")
    def setup_start():
        """Runs the first-run setup again, after a failed step."""
        setup.start()
        return setup.status()

    @app.get("/api/projects/recent")
    def recent_projects():
        return {"projects": store.recent_projects()}

    def opened(root: str, main: str | None = None) -> dict:
        try:
            project = projects.open_project(Path(root), main)
        except projects.ProjectError as e:
            raise HTTPException(422, str(e)) from e
        store.remember_project(project["id"], project["root"], project["name"])
        return project

    def root_of(project_id: str) -> Path:
        root = store.project_root(project_id)
        if not root:
            raise HTTPException(404, "Unknown project; open its folder again.")
        return Path(root)

    @app.post("/api/projects/open")
    def open_project(body: OpenRequest):
        return opened(body.path, body.main)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        return opened(str(root_of(project_id)))

    @app.delete("/api/projects/{project_id}")
    def forget_project(project_id: str):
        store.forget_project(project_id)
        return {"ok": True}

    @app.put("/api/projects/{project_id}/config")
    def put_config(project_id: str, body: dict):
        root = root_of(project_id)
        known = projects.ProjectConfig.__dataclass_fields__
        try:
            cfg = projects.ProjectConfig(**{k: v for k, v in body.items() if k in known})
            projects.save_config(root, cfg)
        except (projects.ProjectError, TypeError) as e:
            raise HTTPException(422, str(e)) from e
        return opened(str(root))

    def config_of(root: Path) -> projects.ProjectConfig:
        try:
            return projects.load_config(root)
        except projects.ProjectError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/projects/{project_id}/file")
    def read_file(project_id: str, path: str):
        try:
            opened = files.read_text(root_of(project_id), path)
        except projects.ProjectError as e:
            raise HTTPException(422, str(e)) from e
        # Opening records the version on disk, so changes other programs made are in the history too.
        history.record(project_id, path, opened["text"].encode())
        return opened

    @app.get("/api/projects/{project_id}/file/hash")
    def file_hash(project_id: str, path: str):
        try:
            return {"path": path, "hash": files.file_hash(root_of(project_id), path)}
        except projects.ProjectError as e:
            raise HTTPException(422, str(e)) from e

    @app.put("/api/projects/{project_id}/file")
    def save_file(project_id: str, body: SaveRequest):
        try:
            saved = files.write_text(root_of(project_id), body.path, body.text, body.base_hash)
            history.record(project_id, body.path, body.text.encode())
            return saved
        except files.Conflict as e:
            return JSONResponse({"detail": str(e), "current_hash": e.current_hash}, status_code=409)
        except projects.ProjectError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/projects/{project_id}/raw")
    def raw_file(project_id: str, path: str):
        try:
            target = projects.inside(root_of(project_id), path)
        except projects.ProjectError as e:
            raise HTTPException(422, str(e)) from e
        if not target.is_file():
            raise HTTPException(404, f"{path} is not a file.")
        return FileResponse(target, headers={"Cache-Control": "no-store"})

    @app.post("/api/projects/{project_id}/build")
    def run_build(project_id: str, clean: bool = False):
        root = root_of(project_id)
        cfg = config_of(root)
        result = build.build(root, cfg, project_id, clean=clean)
        # The sync rule: a successful build brings the active revision folder up to date, in the background.
        return {**result.to_dict(), "revision_update": revisions.enqueue(root, cfg, project_id, result)}

    @app.get("/api/projects/{project_id}/revision")
    def revision_overview(project_id: str):
        root = root_of(project_id)
        return revisions.overview(root, config_of(root), project_id)

    def record_start(project_id: str, root: Path, cfg: projects.ProjectConfig, label: str) -> None:
        main = projects.inside(root, cfg.latex_dir) / cfg.main
        history.record(project_id, str(main.relative_to(root)), main.read_bytes(), label=label)

    @app.post("/api/projects/{project_id}/revision/start")
    def revision_start(project_id: str):
        root = root_of(project_id)
        cfg = config_of(root)
        try:
            number = revisions.start(root, cfg, project_id)
        except (RevisionError, FileExistsError) as e:
            raise HTTPException(409, str(e)) from e
        record_start(project_id, root, cfg, f"Revision {number} started")
        revisions.warm_baseline(root, cfg, number)
        return revisions.overview(root, cfg, project_id)

    @app.post("/api/projects/{project_id}/revision/continue")
    def revision_continue(project_id: str):
        root = root_of(project_id)
        cfg = config_of(root)
        try:
            number = revisions.continue_latest(root, cfg, project_id)
        except RevisionError as e:
            raise HTTPException(409, str(e)) from e
        record_start(project_id, root, cfg, f"Revision {number} continued")
        revisions.warm_baseline(root, cfg, number)
        return revisions.overview(root, cfg, project_id)

    @app.post("/api/projects/{project_id}/revision/update")
    def revision_update(project_id: str):
        """Builds and updates the active revision now (after a failed update, say)."""
        root = root_of(project_id)
        cfg = config_of(root)
        result = build.build(root, cfg, project_id)
        if not result.ok:
            raise HTTPException(409, f"The build failed, so the revision was not updated: {result.summary}")
        if not revisions.enqueue(root, cfg, project_id, result):
            raise HTTPException(409, "No revision is active.")
        return revisions.overview(root, cfg, project_id)

    @app.get("/api/projects/{project_id}/revision/baseline")
    def revision_baseline(project_id: str, path: str):
        """The text a file had when the active revision started, which the editor marks changes against."""
        root = root_of(project_id)
        cfg = config_of(root)
        active = revisions.overview(root, cfg, project_id)["active"]
        if not active:
            raise HTTPException(404, "No revision is active.")
        latex_dir = projects.inside(root, cfg.latex_dir)
        try:
            rel = projects.inside(root, path).relative_to(latex_dir)
            old = projects.inside(folder_of(root, cfg, active), "supporting_files/tracked_build/old")
            target = projects.inside(old, str(rel))
        except (projects.ProjectError, ValueError) as e:
            raise HTTPException(404, "This file is not part of the revision's baseline.") from e
        if not target.is_file():
            raise HTTPException(404, "This file is not part of the revision's baseline.")
        return {"path": path, "text": target.read_text(errors="replace")}

    @app.get("/api/projects/{project_id}/revision/tracked-preview")
    def tracked_preview(project_id: str):
        path = revisions.tracked_pdf(project_id)
        if not path:
            raise HTTPException(404, "The tracked PDF does not exist yet.")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.get("/api/projects/{project_id}/revision/files/{name}")
    def revision_file(project_id: str, name: str):
        if name not in ("main_draft.pdf", "supplementary_draft.pdf", "revision_clean.pdf", "revision_tracked.pdf"):
            raise HTTPException(404, "Not a revision deliverable.")
        root = root_of(project_id)
        cfg = config_of(root)
        active = revisions.overview(root, cfg, project_id)["active"]
        path = projects.inside(root, cfg.revisions_dir) / f"revision{active}" / name if active else None
        if not path or not path.is_file():
            raise HTTPException(404, f"{name} does not exist yet.")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    def comments_root(project_id: str) -> tuple[Path, projects.ProjectConfig, int, Path]:
        root = root_of(project_id)
        cfg = config_of(root)
        active = revisions.overview(root, cfg, project_id)["active"]
        if not active:
            raise HTTPException(409, "Start a revision first: comments are imported into the active revision.")
        folder = projects.inside(root, cfg.revisions_dir) / f"revision{active}" / "supporting_files" / "comments"
        return root, cfg, active, folder

    def comment_set(project_id: str, name: str) -> Path:
        *_, folder = comments_root(project_id)
        target = folder / name
        if "/" in name or name.startswith(".") or not (target / "comments.json").is_file():
            raise HTTPException(404, "No such set of comments.")
        return target

    def comment_view(target: Path) -> dict:
        data = comments.load(target)
        return {**data, "persons": comments.persons(data)}

    @app.get("/api/projects/{project_id}/comments")
    def list_comments(project_id: str):
        root = root_of(project_id)
        cfg = config_of(root)
        active = revisions.overview(root, cfg, project_id)["active"]
        if not active:
            return {"active": None, "sets": []}
        *_, folder = comments_root(project_id)
        sets = []
        for target in sorted(folder.iterdir()) if folder.is_dir() else []:
            if (target / "comments.json").is_file():
                data = comments.load(target)
                sets.append({"name": target.name, "source_pdf": data["source_pdf"], "imported": data["imported"],
                             "count": len(data["comments"]), "persons": comments.persons(data),
                             "questions": sum(1 for c in data["comments"] if c.get("question") and not c.get("answer"))})
        return {"active": active, "sets": sets}

    @app.post("/api/projects/{project_id}/comments/import")
    def import_comments(project_id: str, body: ImportRequest):
        root, cfg, active, folder = comments_root(project_id)
        pdf = Path(body.path).expanduser()
        name = comments.slug(body.name or pdf.stem)
        baseline = revisions.baseline_pdf(root, cfg, active)
        label = str(baseline.relative_to(root)) if baseline else ""
        try:
            data = comments.import_pdf(folder / name, pdf, body.email, baseline, label)
        except comments.CommentsError as e:
            raise HTTPException(422, str(e)) from e
        return {**data, "persons": comments.persons(data)}

    @app.get("/api/projects/{project_id}/comments/{name}")
    def get_comments(project_id: str, name: str):
        return comment_view(comment_set(project_id, name))

    @app.put("/api/projects/{project_id}/comments/{name}/aliases")
    def set_aliases(project_id: str, name: str, body: AliasesRequest):
        target = comment_set(project_id, name)
        data = comments.load(target)
        data["aliases"] = {k: v for k, v in body.aliases.items() if k and v and k != v}
        comments.save(target, data)
        return comment_view(target)

    @app.put("/api/projects/{project_id}/comments/{name}/answer")
    def answer_comment(project_id: str, name: str, body: AnswerRequest):
        target = comment_set(project_id, name)
        data = comments.load(target)
        found = next((c for c in data["comments"] if c["id"] == body.id), None)
        if not found:
            raise HTTPException(404, f"There is no comment {body.id}.")
        found["answer"] = body.answer.strip() or None
        comments.save(target, data)
        return comment_view(target)

    @app.get("/api/projects/{project_id}/comments/{name}/pdf")
    def annotated_pdf(project_id: str, name: str):
        return FileResponse(comment_set(project_id, name) / "annotated.pdf", headers={"Cache-Control": "no-store"})

    @app.get("/api/projects/{project_id}/comments/{name}/{comment_id}/now")
    def comment_now(project_id: str, name: str, comment_id: int):
        """Where the comment's text is in the latest build."""
        target = comment_set(project_id, name)
        found = next((c for c in comments.load(target)["comments"] if c["id"] == comment_id), None)
        if not found:
            raise HTTPException(404, f"There is no comment {comment_id}.")
        result = build.latest(project_id)
        pdf = result and result.pdf and build.output_file(project_id, result.id, Path(result.main).stem + ".pdf")
        if not pdf:
            return {"boxes": [], "build": None}
        fields = comments.Comment.__dataclass_fields__
        boxes = comments.find_in(pdf, comments.Comment(**{k: v for k, v in found.items() if k in fields}))
        return {"boxes": boxes, "build": result.id}

    @app.get("/api/projects/{project_id}/history")
    def file_history(project_id: str, path: str):
        root_of(project_id)
        return {"path": path, "versions": history.versions(project_id, path)}

    @app.get("/api/projects/{project_id}/history/{version_id}")
    def history_version(project_id: str, version_id: int):
        try:
            path, text = history.text(project_id, version_id)
        except KeyError as e:
            raise HTTPException(404, "That version does not exist.") from e
        root = root_of(project_id)
        try:
            current = files.read_text(root, path)["text"]
        except projects.ProjectError:
            current = ""
        # The diff shows what restoring this version would change in the file as it is now.
        return {"path": path, "text": text, "diff": unified_diff(current, text)}

    @app.get("/api/projects/{project_id}/build")
    def latest_build(project_id: str):
        result = build.latest(project_id)
        return result.to_dict() if result else None

    @app.get("/api/projects/{project_id}/builds/{build_id}/{name}")
    def build_file(project_id: str, build_id: str, name: str):
        path = build.output_file(project_id, build_id, name)
        if not path:
            raise HTTPException(404, "That build output is gone; compile again.")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    def latest_pdf(project_id: str, root: Path) -> tuple[Path, Path]:
        result = build.latest(project_id)
        pdf = result and result.pdf and build.output_file(project_id, result.id, Path(result.main).stem + ".pdf")
        if not pdf:
            raise HTTPException(409, "Compile first.")
        return pdf, projects.inside(root, config_of(root).latex_dir)

    @app.post("/api/projects/{project_id}/synctex/forward")
    def sync_forward(project_id: str, body: ForwardRequest):
        root = root_of(project_id)
        pdf, latex_dir = latest_pdf(project_id, root)
        return {"boxes": synctex.forward(pdf, latex_dir, body.file, body.line, body.column)}

    @app.post("/api/projects/{project_id}/synctex/backward")
    def sync_backward(project_id: str, body: BackwardRequest):
        root = root_of(project_id)
        pdf, latex_dir = latest_pdf(project_id, root)
        found = synctex.backward(pdf, latex_dir, body.page, body.x, body.y)
        if not found:
            raise HTTPException(404, "No source line found at that point.")
        return found

    @app.get("/api/projects/{project_id}/build/progress")
    def build_progress(project_id: str):
        return {"text": build.progress(project_id)}

    def agent_call(call, *args):
        """Runs an agent action, turning its failures into answers the interface can show."""
        try:
            return call(*args)
        except UnknownChat as e:
            raise HTTPException(404, "That chat no longer exists.") from e
        except AgentBusy as e:
            raise HTTPException(409, str(e)) from e
        except (StagingConflict, projects.ProjectError) as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/projects/{project_id}/agent/chats")
    def list_chats(project_id: str):
        root_of(project_id)
        return {"chats": agent.list_chats(project_id)}

    @app.post("/api/projects/{project_id}/agent/chats")
    def create_chat(project_id: str):
        root_of(project_id)
        return agent.create_chat(project_id)

    @app.get("/api/projects/{project_id}/agent/chats/{chat_id}")
    def get_chat(project_id: str, chat_id: str):
        root_of(project_id)
        return agent_call(agent.chat, project_id, chat_id)

    @app.delete("/api/projects/{project_id}/agent/chats/{chat_id}")
    def delete_chat(project_id: str, chat_id: str):
        root_of(project_id)
        agent_call(agent.delete_chat, project_id, chat_id)
        return {"ok": True}

    @app.post("/api/projects/{project_id}/agent/chats/{chat_id}/messages")
    def send_message(project_id: str, chat_id: str, body: MessageRequest):
        root = root_of(project_id)
        if not body.text.strip():
            raise HTTPException(422, "The message is empty.")
        agent_call(agent.send, project_id, chat_id, root, config_of(root), body.text, body.context, body.provider, body.model,
                   body.fast)
        return {"ok": True}

    @app.post("/api/projects/{project_id}/agent/chats/{chat_id}/stop")
    def stop_chat(project_id: str, chat_id: str):
        root_of(project_id)
        agent.stop(chat_id)
        return {"ok": True}

    @app.post("/api/projects/{project_id}/agent/chats/{chat_id}/approvals/{approval_id}")
    def decide_approval(project_id: str, chat_id: str, approval_id: str, body: DecisionRequest):
        root_of(project_id)
        if not agent.decide(chat_id, approval_id, body.allow):
            raise HTTPException(404, "That question is no longer waiting.")
        return {"ok": True}

    @app.get("/api/projects/{project_id}/agent/chats/{chat_id}/events")
    async def chat_events(request: Request, project_id: str, chat_id: str, after: int = 0):
        """The chat's events as they happen, as server-sent events. It ends when the agent is done."""

        async def stream():
            seq = after
            quiet = 0
            while True:
                new = await asyncio.to_thread(agent.events_after, project_id, chat_id, seq)
                for event in new:
                    seq = event["seq"]
                    yield f"data: {json.dumps(event)}\n\n"
                if not new and not agent.running(chat_id):
                    yield "event: end\ndata: {}\n\n"
                    return
                quiet = 0 if new else quiet + 1
                if quiet > 75:
                    quiet = 0
                    yield ": still here\n\n"
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.2)

        agent_call(agent.chat, project_id, chat_id)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/projects/{project_id}/agent/chats/{chat_id}/changes")
    def chat_changes(project_id: str, chat_id: str):
        root = root_of(project_id)
        return agent_call(agent.changes, project_id, chat_id, root, config_of(root))

    @app.post("/api/projects/{project_id}/agent/chats/{chat_id}/changes/accept")
    def accept_changes(project_id: str, chat_id: str, body: EditChoice):
        root = root_of(project_id)
        cfg = config_of(root)
        written = agent_call(agent.accept, project_id, chat_id, root, cfg, body.path, body.hunk)
        return {"written": written, **agent.changes(project_id, chat_id, root, cfg)}

    @app.post("/api/projects/{project_id}/agent/chats/{chat_id}/changes/reject")
    def reject_changes(project_id: str, chat_id: str, body: EditChoice):
        root = root_of(project_id)
        cfg = config_of(root)
        agent_call(agent.reject, project_id, chat_id, root, cfg, body.path, body.hunk)
        return agent.changes(project_id, chat_id, root, cfg)

    @app.get("/api/projects/{project_id}/agent/chats/{chat_id}/pdf")
    def chat_pdf(project_id: str, chat_id: str):
        root_of(project_id)
        path = agent.pdf(chat_id) if CHAT_ID.match(chat_id) else None
        if not path:
            raise HTTPException(404, "The agent's copy has not been compiled yet.")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.get("/api/projects/{project_id}/aids")
    def editor_aids(project_id: str):
        root = root_of(project_id)
        return aids.completions(root, config_of(root))

    @app.get("/api/projects/{project_id}/style-rules")
    def style_rules(project_id: str):
        root = root_of(project_id)
        return {"rules": style.rules(root, config_of(root))}

    @app.get("/api/projects/{project_id}/search")
    def search_project(project_id: str, q: str = "", regex: bool = False, case: bool = False, glob: str = ""):
        root = root_of(project_id)
        try:
            return aids.search(root, config_of(root), q, regex, case, glob)
        except projects.ProjectError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/projects/{project_id}/replace")
    def replace_in_project(project_id: str, body: ReplaceRequest):
        root = root_of(project_id)
        try:
            return aids.replace(root, config_of(root), project_id, history, body.query, body.replacement, body.regex,
                                body.case, body.paths)
        except files.Conflict as e:
            raise HTTPException(409, "A file changed on disk just now. Search again.") from e
        except projects.ProjectError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/projects/{project_id}/wordcount")
    def word_count(project_id: str):
        root = root_of(project_id)
        return build.word_count(root, config_of(root))

    @app.get("/api/projects/{project_id}/tree")
    def tree(project_id: str, path: str = ""):
        try:
            return projects.list_dir(root_of(project_id), path)
        except projects.ProjectError as e:
            raise HTTPException(422, str(e)) from e

    if ui_dir and (ui_dir / "index.html").is_file():
        app.mount("/", InterfaceFiles(directory=ui_dir, html=True), name="ui")
    else:
        @app.get("/")
        def no_ui():
            return PlainTextResponse("The interface is not built yet. Run `make setup` in the app's source folder.")

    return app
