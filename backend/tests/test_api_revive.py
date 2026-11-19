from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.db import ApprovalRow
from app.runs import service
from app.schemas import Phase, RunStatus


@pytest.fixture
def repo_id(client: TestClient) -> str:
    body = {"fixture_id": "legacy_flask_app"}
    return client.post("/api/repositories/import", json=body).json()["id"]


@pytest.fixture
def run_id(client: TestClient, repo_id: str) -> str:
    r = client.post("/api/revive/start", json={"repository_id": repo_id, "mode": "PREVIEW"})
    assert r.status_code == 201
    return r.json()["id"]


def set_status(client: TestClient, run_id: str, status: RunStatus, phase: Phase | None = None):
    with client.app.state.session_factory() as db:
        service.transition(db, run_id, status, phase)


def parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "data" in fields:
            events.append(
                {"id": fields["id"], "event": fields["event"], "data": json.loads(fields["data"])}
            )
    return events


def test_start_creates_queued_run_with_created_event(client: TestClient, repo_id: str):
    r = client.post("/api/revive/start", json={"repository_id": repo_id})
    body = r.json()
    assert body["status"] == "QUEUED" and body["mode"] == "INSPECT" and body["phase"] is None
    assert body["cancel_requested"] is False


def test_start_validation(client: TestClient):
    assert client.post("/api/revive/start", json={"repository_id": "missing"}).status_code == 404
    assert client.post("/api/revive/start", json={}).status_code == 422
    bad_mode = {"repository_id": "x", "mode": "DESTROY"}
    assert client.post("/api/revive/start", json=bad_mode).status_code == 422


def test_approve_only_when_awaiting(client: TestClient, run_id: str):
    r = client.post(f"/api/revive/approve/{run_id}", json={"decision": "approve"})
    assert r.status_code == 409
    with client.app.state.session_factory() as db:
        assert db.query(ApprovalRow).count() == 0


def test_approve_records_decision_and_resumes(client: TestClient, run_id: str):
    set_status(client, run_id, RunStatus.AWAITING_APPROVAL, Phase.REANIMATION)
    r = client.post(f"/api/revive/approve/{run_id}", json={"decision": "approve", "note": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "RUNNING"
    with client.app.state.session_factory() as db:
        rows = db.query(ApprovalRow).all()
        assert [(a.run_id, a.decision, a.note) for a in rows] == [(run_id, "approve", "ok")]
    # A second approval cannot replay the same gate.
    again = client.post(f"/api/revive/approve/{run_id}", json={"decision": "approve"})
    assert again.status_code == 409


def test_reject_cancels_run(client: TestClient, run_id: str):
    set_status(client, run_id, RunStatus.AWAITING_APPROVAL, Phase.REANIMATION)
    r = client.post(f"/api/revive/approve/{run_id}", json={"decision": "reject"})
    assert r.json()["status"] == "CANCELLED"


def test_approve_bad_input(client: TestClient, run_id: str):
    maybe = client.post(f"/api/revive/approve/{run_id}", json={"decision": "maybe"})
    assert maybe.status_code == 422
    missing = client.post("/api/revive/approve/nope", json={"decision": "approve"})
    assert missing.status_code == 404


def test_cancel_queued_is_immediate(client: TestClient, run_id: str):
    r = client.post(f"/api/revive/{run_id}/cancel")
    assert r.json()["status"] == "CANCELLED" and r.json()["cancel_requested"] is True
    assert client.post(f"/api/revive/{run_id}/cancel").status_code == 409


def test_cancel_running_only_requests(client: TestClient, run_id: str):
    set_status(client, run_id, RunStatus.RUNNING, Phase.AUTOPSY)
    r = client.post(f"/api/revive/{run_id}/cancel").json()
    assert r["status"] == "RUNNING" and r["cancel_requested"] is True


def test_cancel_unknown_404(client: TestClient):
    assert client.post("/api/revive/nope/cancel").status_code == 404


def test_terminal_status_is_final(client: TestClient, run_id: str):
    client.post(f"/api/revive/{run_id}/cancel")
    with client.app.state.session_factory() as db, pytest.raises(service.InvalidStateError):
        service.transition(db, run_id, RunStatus.RUNNING)


def test_artifacts(client: TestClient, run_id: str):
    assert client.get(f"/api/revive/{run_id}/artifacts").json() == []
    assert client.get("/api/revive/nope/artifacts").status_code == 404


def test_stream_replays_events_with_ids_and_ends_when_terminal(client: TestClient, run_id: str):
    client.app.state.sse_poll_seconds = 0.01
    set_status(client, run_id, RunStatus.RUNNING, Phase.AUTOPSY)
    client.post(f"/api/revive/{run_id}/cancel")  # request only; executor finalizes
    set_status(client, run_id, RunStatus.CANCELLED)
    r = client.get(f"/api/revive/stream/{run_id}")
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    assert [e["data"]["seq"] for e in events] == [1, 2, 3, 4]
    assert [e["id"] for e in events] == ["1", "2", "3", "4"]
    assert [e["event"] for e in events] == [
        "run.created", "run.status", "run.cancel_requested", "run.status",
    ]
    first = events[0]["data"]
    assert first["run_id"] == run_id and first["event_id"] == f"{run_id}:1"
    assert first["ts"] and first["data"]["mode"] == "PREVIEW"
    assert events[-1]["data"]["data"]["status"] == "CANCELLED"


def test_stream_ends_with_terminal_status(client: TestClient, run_id: str):
    client.app.state.sse_poll_seconds = 0.01
    client.post(f"/api/revive/{run_id}/cancel")
    events = parse_sse(client.get(f"/api/revive/stream/{run_id}").text)
    assert events[-1]["data"]["data"]["status"] == "CANCELLED"


def test_stream_resume_with_last_event_id_and_after(client: TestClient, run_id: str):
    client.app.state.sse_poll_seconds = 0.01
    client.post(f"/api/revive/{run_id}/cancel")  # events: 1 created, 2 cancelled
    resumed = parse_sse(
        client.get(f"/api/revive/stream/{run_id}", headers={"Last-Event-ID": "1"}).text
    )
    assert [e["data"]["seq"] for e in resumed] == [2]
    by_query = parse_sse(client.get(f"/api/revive/stream/{run_id}?after=2").text)
    assert by_query == []


def test_stream_errors(client: TestClient, run_id: str):
    assert client.get("/api/revive/stream/nope").status_code == 404
    bad = client.get(f"/api/revive/stream/{run_id}", headers={"Last-Event-ID": "abc"})
    assert bad.status_code == 400
    neg = client.get(f"/api/revive/stream/{run_id}", headers={"Last-Event-ID": "-1"})
    assert neg.status_code == 400


def test_events_are_isolated_per_run(client: TestClient, repo_id: str):
    client.app.state.sse_poll_seconds = 0.01
    a = client.post("/api/revive/start", json={"repository_id": repo_id}).json()["id"]
    b = client.post("/api/revive/start", json={"repository_id": repo_id}).json()["id"]
    client.post(f"/api/revive/{a}/cancel")
    client.post(f"/api/revive/{b}/cancel")
    for rid in (a, b):
        events = parse_sse(client.get(f"/api/revive/stream/{rid}").text)
        assert {e["data"]["run_id"] for e in events} == {rid}
        assert [e["data"]["seq"] for e in events] == [1, 2]
