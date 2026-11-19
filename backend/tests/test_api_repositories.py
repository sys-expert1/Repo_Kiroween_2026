from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

FIXTURE = {"fixture_id": "legacy-python-cli"}


def test_health(client: TestClient):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["sandbox"] == {
        "mode": "off", "available": False, "reason": "disabled", "host_fixture_exec": True,
    }  # fmt: skip


def test_fixtures_listed(client: TestClient):
    body = client.get("/api/fixtures").json()
    assert [f["id"] for f in body] == ["legacy-python-cli"]
    assert body[0]["language"] == "python"


def test_inspect_fixture_detects_real_signals(client: TestClient):
    r = client.post("/api/repositories/inspect", json=FIXTURE)
    assert r.status_code == 200
    body = r.json()
    assert body["source_type"] == "fixture"
    s = body["signals"]
    assert s["primary_language"] == "Python"
    assert s["dependency_files"] == ["requirements.txt"]
    assert s["has_tests"] is True and s["test_files"] == 1
    assert s["has_dockerfile"] is False and s["has_ci"] is False
    assert s["is_git_repo"] is False


def test_inspect_does_not_persist(client: TestClient):
    client.post("/api/repositories/inspect", json=FIXTURE)
    assert client.get("/api/repositories").json() == []


def test_inspect_git_url_has_unknown_signals(client: TestClient):
    r = client.post(
        "/api/repositories/inspect", json={"git_url": "https://github.com/org/repo.git"}
    )
    assert r.status_code == 200
    assert r.json()["signals"] is None
    assert r.json()["display_name"] == "repo"


def test_import_list_get_and_idempotent(client: TestClient):
    first = client.post("/api/repositories/import", json=FIXTURE)
    assert first.status_code == 201
    repo = first.json()
    second = client.post("/api/repositories/import", json=FIXTURE).json()
    assert second["id"] == repo["id"]
    assert len(client.get("/api/repositories").json()) == 1
    assert client.get(f"/api/repositories/{repo['id']}").json()["signals"]["has_tests"] is True


def test_get_unknown_repo_404(client: TestClient):
    assert client.get("/api/repositories/nope").status_code == 404


def test_local_path_inside_allowed_root(client: TestClient, allowed_root: Path):
    proj = allowed_root / "proj"
    proj.mkdir()
    (proj / "app.py").write_text("print('x')\n")
    (proj / ".github" / "workflows").mkdir(parents=True)
    (proj / "Dockerfile").write_text("FROM python:3.12\n")
    r = client.post("/api/repositories/import", json={"local_path": str(proj)})
    assert r.status_code == 201
    s = r.json()["signals"]
    assert s["has_ci"] and s["has_dockerfile"] and s["primary_language"] == "Python"


def test_local_path_rejections_return_422(client: TestClient, tmp_path: Path, allowed_root: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    for bad in [str(outside), str(allowed_root / ".." / "outside"), "/etc", "/"]:
        r = client.post("/api/repositories/inspect", json={"local_path": bad})
        assert r.status_code == 422, bad


def test_bad_git_urls_and_unknown_fixture_return_422(client: TestClient):
    for body in [
        {"git_url": "file:///etc/passwd"},
        {"git_url": "--upload-pack=evil"},
        {"fixture_id": "../../etc"},
        {"fixture_id": "missing"},
    ]:
        assert client.post("/api/repositories/import", json=body).status_code == 422, body


def test_exactly_one_source_required(client: TestClient):
    assert client.post("/api/repositories/inspect", json={}).status_code == 422
    both = {"fixture_id": "legacy-python-cli", "git_url": "https://github.com/o/r"}
    assert client.post("/api/repositories/inspect", json=both).status_code == 422
    assert client.post("/api/repositories/inspect", json={**FIXTURE, "x": 1}).status_code == 422


def test_inspect_never_modifies_source(client: TestClient, allowed_root: Path):
    proj = allowed_root / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\n")
    before = sorted(p.name for p in proj.rglob("*"))
    client.post("/api/repositories/import", json={"local_path": str(proj)})
    assert sorted(p.name for p in proj.rglob("*")) == before
