from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thor.server import create_app
from thor.settings import MemorySecret, SettingsStore
from thor.first_run import FirstRun
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
                     setup=setup)
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


def test_settings(client: TestClient, secret: MemorySecret) -> None:
    got = client.get("/api/settings").json()
    assert got["agent"]["model"] == "claude-opus-5-5" and got["agent"]["auth"] == "claude_plan"
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
