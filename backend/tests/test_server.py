from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thor.server import create_app
from thor.settings import MemorySecret, SettingsStore
from thor.first_run import FirstRun
from thor.history import History
from thor.setup_tex import TexInstaller
from thor.store import Store

TOKEN = "s3cret"
BASE = "http://127.0.0.1:8123"


@pytest.fixture
def secret() -> MemorySecret:
    return MemorySecret()


class IdleSetup(FirstRun):
    def __init__(self, tmp: Path) -> None:
        super().__init__(TexInstaller(downloads=tmp / "dl", search=lambda: [], texbin=tmp / "texbin"), enabled=True)
        self.starts = 0

    def start(self) -> None:
        self.starts += 1


@pytest.fixture
def setup(tmp_path: Path) -> IdleSetup:
    return IdleSetup(tmp_path)


@pytest.fixture
def client(tmp_path: Path, secret: MemorySecret, setup: IdleSetup) -> TestClient:
    app = create_app(TOKEN, secret, ui_dir=None, store=Store(tmp_path / "test.db"), settings_store=SettingsStore(),
                     setup=setup, history=History(tmp_path / "history"))
    return TestClient(app, base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"})


def test_token_required(tmp_path: Path, secret: MemorySecret) -> None:
    app = create_app(TOKEN, secret, store=Store(tmp_path / "t.db"))
    anonymous = TestClient(app, base_url=BASE)
    assert anonymous.get("/api/health").status_code == 401
    assert anonymous.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert anonymous.get("/auth?token=wrong", follow_redirects=False).status_code == 401
    signed = anonymous.get("/auth?token=" + TOKEN, follow_redirects=False)
    assert signed.status_code == 303 and "httponly" in signed.headers["set-cookie"].lower()
    assert anonymous.get("/api/health").json()["ok"] is True  # the cookie now carries the token


def test_other_hosts_refused(client: TestClient) -> None:
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"Host": "localhost:8123"}).status_code == 200


def test_ui_not_built_message(client: TestClient) -> None:
    assert "make setup" in client.get("/").text


def test_the_page_is_checked_each_time_and_its_assets_are_kept(tmp_path: Path, secret: MemorySecret) -> None:
    ui = tmp_path / "dist"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<html></html>")
    (ui / "assets" / "index-abc.js").write_text("1")
    plain = TestClient(create_app(TOKEN, secret, ui_dir=ui, store=Store(tmp_path / "t.db")), base_url=BASE)
    assert plain.get("/").headers["cache-control"] == "no-cache"
    assert "immutable" in plain.get("/assets/index-abc.js").headers["cache-control"]


def test_settings(client: TestClient, secret: MemorySecret) -> None:
    got = client.get("/api/settings").json()
    assert got["agent"]["model"] == "claude-opus-5-5" and got["agent"]["provider"] == "claude_code"
    assert got["api_key_saved"] is False
    got["agent"]["effort"] = "max"
    assert client.put("/api/settings", json={"agent": got["agent"]}).json()["agent"]["effort"] == "max"
    assert client.get("/api/settings").json()["agent"]["effort"] == "max"
    bad = client.put("/api/settings", json={"agent": {**got["agent"], "effort": "huge"}})
    assert bad.status_code == 422 and "Effort" in bad.json()["detail"]
    assert client.put("/api/settings/api-key", json={"key": "nope"}).status_code == 422
    assert client.put("/api/settings/api-key", json={"key": " sk-ant-abc "}).json() == {"api_key_saved": True}
    assert secret.get() == "sk-ant-abc"
    assert client.delete("/api/settings/api-key").json() == {"api_key_saved": False}


def test_layout_choices_are_kept_apart_from_settings(client: TestClient) -> None:
    assert client.get("/api/settings/layout").json() == {}
    assert client.put("/api/settings/layout", json={"agentDock": "bottom", "sidebar": False}).status_code == 200
    assert client.put("/api/settings/layout", json={"agentWidth": 400}).json() == {"agentDock": "bottom", "sidebar": False, "agentWidth": 400}
    # Saving the settings does not disturb them.
    client.put("/api/settings", json={"agent": client.get("/api/settings").json()["agent"]})
    assert client.get("/api/settings/layout").json()["agentDock"] == "bottom"
    assert client.put("/api/settings/layout", json={"x": {"nested": 1}}).status_code == 422
    assert client.put("/api/settings/layout", json={"x": "y" * 100}).status_code == 422


def test_open_project_flow(client: TestClient, paper: Path) -> None:
    opened = client.post("/api/projects/open", json={"path": str(paper)}).json()
    assert opened["config"]["main"] == "main.tex" and opened["config_saved"] is False
    pid = opened["id"]
    assert client.get("/api/projects/recent").json()["projects"][0]["id"] == pid
    tree = client.get(f"/api/projects/{pid}/tree", params={"path": "manuscript"}).json()
    assert [e["name"] for e in tree["entries"]][:2] == ["figures", "supplementary"]
    assert client.get(f"/api/projects/{pid}/tree", params={"path": ".."}).status_code == 422

    cfg = {**opened["config"], "engine": "lualatex"}
    saved = client.put(f"/api/projects/{pid}/config", json=cfg).json()
    assert saved["config_saved"] is True and saved["config"]["engine"] == "lualatex"
    assert (paper / "revision_app.toml").is_file()
    bad = client.put(f"/api/projects/{pid}/config", json={**cfg, "main": "gone.tex"})
    assert bad.status_code == 422

    assert client.get("/api/projects/unknown").status_code == 404
    assert client.post("/api/projects/open", json={"path": str(paper / "nope")}).status_code == 422
    assert client.delete(f"/api/projects/{pid}").json() == {"ok": True}
    assert client.get("/api/projects/recent").json()["projects"] == []


def test_setup_routes(client: TestClient, setup: IdleSetup) -> None:
    status = client.get("/api/setup").json()
    assert status["enabled"] is True and status["finished"] is False
    assert [step["id"] for step in status["steps"]] == ["claude", "mactex"]
    assert status["tex"]["installed"] is False
    client.post("/api/setup/start")
    assert setup.starts == 1


def test_file_routes(client: TestClient, paper: Path) -> None:
    pid = client.post("/api/projects/open", json={"path": str(paper)}).json()["id"]
    opened = client.get(f"/api/projects/{pid}/file", params={"path": "manuscript/main.tex"}).json()
    assert opened["text"].startswith("% Thunder Over Asgard")
    edited = opened["text"] + "% edited\n"
    saved = client.put(f"/api/projects/{pid}/file",
                       json={"path": "manuscript/main.tex", "text": edited, "base_hash": opened["hash"]})
    assert saved.status_code == 200
    stale = client.put(f"/api/projects/{pid}/file",
                       json={"path": "manuscript/main.tex", "text": "lost", "base_hash": opened["hash"]})
    assert stale.status_code == 409 and stale.json()["current_hash"] == saved.json()["hash"]
    assert client.get(f"/api/projects/{pid}/file/hash", params={"path": "manuscript/main.tex"}).json()["hash"] == \
        saved.json()["hash"]
    assert client.get(f"/api/projects/{pid}/file", params={"path": "../x"}).status_code == 422
    assert client.get(f"/api/projects/{pid}/raw", params={"path": "manuscript/figures/fig1.png"}).content == b"\x89PNG"
    assert client.get(f"/api/projects/{pid}/raw", params={"path": "nope.png"}).status_code == 404


def test_synctex_needs_a_build(client: TestClient, paper: Path) -> None:
    pid = client.post("/api/projects/open", json={"path": str(paper)}).json()["id"]
    assert client.get(f"/api/projects/{pid}/build").json() is None
    r = client.post(f"/api/projects/{pid}/synctex/forward", json={"file": "main.tex", "line": 3})
    assert r.status_code == 409 and r.json()["detail"] == "Compile first."
    assert client.get(f"/api/projects/{pid}/builds/x/main.pdf").status_code == 404


def test_history_routes(client: TestClient, paper: Path) -> None:
    pid = client.post("/api/projects/open", json={"path": str(paper)}).json()["id"]
    opened = client.get(f"/api/projects/{pid}/file", params={"path": "manuscript/main.tex"}).json()
    client.put(f"/api/projects/{pid}/file",
               json={"path": "manuscript/main.tex", "text": opened["text"] + "% later\n", "base_hash": opened["hash"]})
    versions = client.get(f"/api/projects/{pid}/history", params={"path": "manuscript/main.tex"}).json()["versions"]
    assert len(versions) == 1  # opened and saved within two minutes: one version, the latest text
    version = client.get(f"/api/projects/{pid}/history/{versions[0]['id']}").json()
    assert version["text"].endswith("% later\n") and version["diff"] == []
    assert client.get(f"/api/projects/{pid}/history/999").status_code == 404


def test_revision_routes_without_tex(client: TestClient, paper: Path) -> None:
    pid = client.post("/api/projects/open", json={"path": str(paper)}).json()["id"]
    overview = client.get(f"/api/projects/{pid}/revision").json()
    assert overview["revisions"] == [1, 2] and overview["active"] is None
    # revision2 has no supporting_files/main.tex, so revision 3 cannot start from it.
    started = client.post(f"/api/projects/{pid}/revision/start")
    assert started.status_code == 409 and "revision2/supporting_files/main.tex" in started.json()["detail"]
    assert client.get(f"/api/projects/{pid}/revision/files/revision_tracked.pdf").status_code == 404
    assert client.get(f"/api/projects/{pid}/revision/files/secret.txt").status_code == 404


def test_revision_baseline_route(client: TestClient, paper: Path, tmp_path: Path) -> None:
    pid = client.post("/api/projects/open", json={"path": str(paper)}).json()["id"]
    main = "manuscript/main.tex"
    assert client.get(f"/api/projects/{pid}/revision/baseline", params={"path": main}).status_code == 404
    Store(tmp_path / "test.db").set_active_revision(pid, 2)
    old = paper / "revision2" / "supporting_files" / "tracked_build" / "old"
    old.mkdir(parents=True)
    (old / "main.tex").write_text("The baseline text.\n")
    got = client.get(f"/api/projects/{pid}/revision/baseline", params={"path": main})
    assert got.status_code == 200 and got.json()["text"] == "The baseline text.\n"
    for other in ("manuscript/sections/results.tex", "../outside.tex", "manuscript/../../x.tex"):
        assert client.get(f"/api/projects/{pid}/revision/baseline", params={"path": other}).status_code == 404


def test_comment_routes(client: TestClient, paper: Path, tmp_path: Path) -> None:
    from .test_comments import annotate, paper as make_paper

    pid = client.post("/api/projects/open", json={"path": str(paper)}).json()["id"]
    assert client.get(f"/api/projects/{pid}/comments").json() == {"active": None, "sets": []}
    assert client.post(f"/api/projects/{pid}/comments/import", json={"path": "x"}).status_code == 409
    Store(tmp_path / "test.db").set_active_revision(pid, 2)
    make_paper(tmp_path / "plain.pdf")
    make_paper(paper / "revision1" / "revision_clean.pdf")  # the baseline the comments are checked against
    annotate(tmp_path / "plain.pdf", tmp_path / "From Ada.pdf")
    imported = client.post(f"/api/projects/{pid}/comments/import", json={"path": str(tmp_path / "From Ada.pdf")})
    assert imported.status_code == 200, imported.text
    data = imported.json()
    assert data["name"] == "from-ada" and data["baseline"] == "revision1/revision_clean.pdf"
    assert (paper / "revision2" / "supporting_files" / "comments" / "from-ada" / "comments.txt").is_file()
    listed = client.get(f"/api/projects/{pid}/comments").json()
    assert listed["active"] == 2 and listed["sets"][0]["questions"] >= 1
    merged = client.put(f"/api/projects/{pid}/comments/from-ada/aliases", json={"aliases": {"ada.l": "Ada"}}).json()
    assert list(merged["persons"]) == ["Ada"]
    first_question = next(c for c in merged["comments"] if c["question"])
    answered = client.put(f"/api/projects/{pid}/comments/from-ada/answer",
                          json={"id": first_question["id"], "answer": "It means: outdoor construction."}).json()
    assert next(c for c in answered["comments"] if c["id"] == first_question["id"])["answer"].startswith("It means")
    assert client.get(f"/api/projects/{pid}/comments/from-ada/pdf").content[:5] == b"%PDF-"
    assert client.get(f"/api/projects/{pid}/comments/../x").status_code == 404
    assert client.get(f"/api/projects/{pid}/comments/from-ada/1/now").json() == {"boxes": [], "build": None}
