from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.db import ApprovalRow
from app.schemas import EventType
from tests.helpers import parse_sse

FIXTURE = {"fixture_id": "legacy-python-cli"}


def start(client: TestClient, mode: str = "REVIVE") -> dict:
    repo = client.post("/api/repositories/import", json=FIXTURE).json()["id"]
    r = client.post("/api/revive/start", json={"repository_id": repo, "mode": mode})
    assert r.status_code == 201, r.text
    return r.json()


def approve(client: TestClient, run: dict, decision: str = "approve", **extra) -> object:
    body = {"decision": decision, "roadmap_version": run["roadmap_version"], **extra}
    return client.post(f"/api/revive/approve/{run['id']}", json=body)


def test_start_validation(client: TestClient):
    assert client.post("/api/revive/start", json={"repository_id": "missing"}).status_code == 404
    assert client.post("/api/revive/start", json={}).status_code == 422
    bad = {"repository_id": "x", "mode": "DESTROY"}
    assert client.post("/api/revive/start", json=bad).status_code == 422


def test_default_mode_is_inspect(client: TestClient):
    repo = client.post("/api/repositories/import", json=FIXTURE).json()["id"]
    run = client.post("/api/revive/start", json={"repository_id": repo}).json()
    assert run["mode"] == "INSPECT" and run["status"] == "COMPLETED"


def test_approve_only_when_awaiting(client: TestClient):
    run = start(client, "INSPECT")  # completed, never awaiting
    r = client.post(f"/api/revive/approve/{run['id']}", json={"decision": "approve"})
    assert r.status_code == 409
    with client.app.state.session_factory() as db:
        assert db.query(ApprovalRow).count() == 0


def test_approval_requires_matching_roadmap_version(client: TestClient):
    run = start(client)
    assert run["status"] == "AWAITING_APPROVAL"
    missing = client.post(f"/api/revive/approve/{run['id']}", json={"decision": "approve"})
    stale = approve(client, {**run, "roadmap_version": 99})
    assert missing.status_code == 409 and stale.status_code == 409
    with client.app.state.session_factory() as db:
        assert db.query(ApprovalRow).count() == 0


def test_approve_records_digest_and_cannot_be_replayed(client: TestClient):
    run = start(client)
    r = approve(client, run, note="looks right")
    assert r.status_code == 200
    with client.app.state.session_factory() as db:
        rows = db.query(ApprovalRow).all()
        assert len(rows) == 1
        row = rows[0]
        assert (row.decision, row.note, row.roadmap_version, row.actor) == (
            "approve", "looks right", 1, "local",
        )  # fmt: skip
        assert row.patch_set_digest == run["patch_set_digest"] and len(row.patch_set_digest) == 64
    assert approve(client, run).status_code == 409  # run is no longer awaiting


def test_approve_applies_patches_verifies_and_completes(client: TestClient):
    run = start(client)
    final = approve(client, run).json()
    assert final["status"] == "COMPLETED" and final["current_stage"] == "CONSECRATION"
    assert final["error"] is None and final["completed_at"] is not None
    events = parse_sse(client.get(f"/api/revive/stream/{run['id']}").text)
    types = [e["type"] for e in events]
    assert types.index("hitl_required") < types.index("approval_received")
    assert types.index("approval_received") < types.index("patch_applied")
    assert types.index("patch_applied") < types.index("verification_completed")
    assert types[-1] == "run_completed"
    stages = [e["payload"]["stage"] for e in events if e["type"] == "stage_changed"]
    assert stages == [
        "AUTOPSY", "CHARACTERIZATION", "REANIMATION", "REANIMATION", "CONSECRATION",
    ]  # fmt: skip
    scores = [e["payload"] for e in events if e["type"] == "health_score_updated"]
    assert [s["phase"] for s in scores] == ["before", "after"]
    assert scores[1]["score"] > scores[0]["score"]
    applied = [e["payload"] for e in events if e["type"] == "patch_applied"]
    assert applied and all(p["target"] == "isolated working copy" for p in applied)
    done = events[-1]["payload"]
    assert done["verdict"] == "verified" and done["patches_applied"] == len(applied)


def test_final_artifacts_and_report(client: TestClient):
    run = start(client)
    approve(client, run)
    names = {a["name"] for a in client.get(f"/api/revive/{run['id']}/artifacts").json()}
    assert {"PATCHSET.v1.diff", "VERIFICATION_REPORT.md", "VITALITY_REPORT.json",
            "RUN_AUDIT.json"} <= names  # fmt: skip
    report = _artifact(client, run["id"], "VERIFICATION_REPORT.md")
    assert "Verdict: **verified**" in report and "original repository was not modified" in report
    assert "Pre-existing" not in report and "| tests | passed |" in report
    vit = json.loads(_artifact(client, run["id"], "VITALITY_REPORT.json"))
    assert vit["after"]["score"] > vit["before"]["score"] and vit["comparison_warning"] is None
    audit = json.loads(_artifact(client, run["id"], "RUN_AUDIT.json"))
    assert audit["approvals"][0]["decision"] == "approve"
    assert audit["approvals"][0]["patch_set_digest"] == run["patch_set_digest"]
    assert audit["patches_applied"] and any("pytest" in c["command"] for c in audit["commands"])
    assert all(c["exit_code"] is not None for c in audit["commands"])


def test_edit_creates_new_roadmap_version_and_awaits_again(client: TestClient):
    run = start(client)
    roadmap = json.loads(_artifact(client, run["id"], "roadmap.v1.json"))
    drop = roadmap["tasks"][-1]["id"]
    r = approve(client, run, "edit", excluded_task_ids=[drop])
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "AWAITING_APPROVAL" and body["roadmap_version"] == 2
    assert body["patch_set_digest"] != run["patch_set_digest"]
    v2 = json.loads(_artifact(client, run["id"], "roadmap.v2.json"))
    assert {t["id"]: t["status"] for t in v2["tasks"]}[drop] == "excluded"
    # The old version can no longer be approved.
    assert approve(client, run).status_code == 409


def test_edit_rejects_unknown_task_and_empty_result(client: TestClient):
    run = start(client)
    roadmap = json.loads(_artifact(client, run["id"], "roadmap.v1.json"))
    unknown = approve(client, run, "edit", excluded_task_ids=["RT-999"])
    everything = approve(client, run, "edit", excluded_task_ids=[t["id"] for t in roadmap["tasks"]])
    assert unknown.status_code == 422 and everything.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.query(ApprovalRow).count() == 0  # invalid edits are not recorded
    assert client.get(f"/api/revive/{run['id']}/artifacts").status_code == 200


def test_reject_cancels_and_ends_the_graph(client: TestClient):
    run = start(client)
    body = approve(client, run, "reject").json()
    assert body["status"] == "CANCELLED"
    assert not client.app.state.workflow.is_waiting(run["id"])
    events = parse_sse(client.get(f"/api/revive/stream/{run['id']}").text)
    assert [e["type"] for e in events[-2:]] == ["approval_received", "run_cancelled"]


def test_approve_bad_input(client: TestClient):
    run = start(client)
    assert approve(client, run, "maybe").status_code == 422
    nope = client.post("/api/revive/approve/nope", json={"decision": "approve"})
    assert nope.status_code == 404


def test_cancel_awaiting_run_closes_graph(client: TestClient):
    run = start(client)
    r = client.post(f"/api/revive/{run['id']}/cancel").json()
    assert r["status"] == "CANCELLED" and r["cancel_requested"] is True
    assert not client.app.state.workflow.is_waiting(run["id"])
    assert client.post(f"/api/revive/{run['id']}/cancel").status_code == 409


def test_cancel_before_start_skips_all_work(deferred_client, fake_runner):
    client, executor = deferred_client
    repo = client.post("/api/repositories/import", json=FIXTURE).json()["id"]
    run = client.post("/api/revive/start", json={"repository_id": repo, "mode": "REVIVE"}).json()
    assert run["status"] == "QUEUED"
    assert client.post(f"/api/revive/{run['id']}/cancel").json()["status"] == "CANCELLED"
    executor.run_all()
    assert fake_runner.calls == []  # nothing ran
    assert client.get(f"/api/revive/{run['id']}/artifacts").json() == []


def test_cancel_unknown_404(client: TestClient):
    assert client.post("/api/revive/nope/cancel").status_code == 404


def _artifact(client: TestClient, run_id: str, name: str) -> str:
    arts = client.get(f"/api/revive/{run_id}/artifacts").json()
    art = next(a for a in arts if a["name"] == name)
    return client.get(f"/api/revive/{run_id}/artifacts/{art['id']}").text


def test_artifacts_listed_with_checksums_and_downloadable(client: TestClient):
    run = start(client)
    arts = client.get(f"/api/revive/{run['id']}/artifacts").json()
    names = {a["name"] for a in arts}
    assert {"AUTOPSY_REPORT.md", "CHARACTERIZATION_REPORT.md", "REVIVAL_ROADMAP.v1.md"} <= names
    assert all(len(a["checksum"]) == 64 and a["size"] > 0 for a in arts)
    md = _artifact(client, run["id"], "AUTOPSY_REPORT.md")
    assert (
        "# Autopsy Report" in md and "legacy-token-do-not-commit" not in md
    )  # secret value never reported


def test_artifact_tampering_is_detected(client: TestClient):
    run = start(client)
    arts = client.get(f"/api/revive/{run['id']}/artifacts").json()
    art = arts[0]
    with client.app.state.session_factory() as db:
        from app.db import ArtifactRow

        path = db.get(ArtifactRow, art["id"]).path
    open(path, "ab").write(b"tampered")
    r = client.get(f"/api/revive/{run['id']}/artifacts/{art['id']}")
    assert r.status_code == 409


def test_artifact_access_is_scoped_to_run(client: TestClient):
    a = start(client, "INSPECT")
    b = start(client, "INSPECT")
    art = client.get(f"/api/revive/{a['id']}/artifacts").json()[0]
    assert client.get(f"/api/revive/{b['id']}/artifacts/{art['id']}").status_code == 404
    assert client.get(f"/api/revive/{a['id']}/artifacts/nope").status_code == 404
    assert client.get("/api/revive/nope/artifacts").status_code == 404


def test_stream_has_blueprint_envelope_and_ordered_events(client: TestClient):
    run = start(client, "PREVIEW")
    r = client.get(f"/api/revive/stream/{run['id']}")
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert [e["id"] for e in events] == [str(e["sequence"]) for e in events]
    first = events[0]
    assert set(first) >= {"event_id", "run_id", "sequence", "type", "timestamp", "payload"}
    assert first["type"] == "run_started" and first["event_id"] == f"{run['id']}:1"
    assert {e["type"] for e in events} <= {t.value for t in EventType}
    assert events[-1]["type"] == "run_completed"
    stages = [e["payload"]["stage"] for e in events if e["type"] == "stage_changed"]
    assert stages == ["AUTOPSY", "CHARACTERIZATION", "REANIMATION"]


def test_stream_terminal_log_carries_real_command_and_exit_code(client: TestClient):
    run = start(client, "INSPECT")
    events = parse_sse(client.get(f"/api/revive/stream/{run['id']}").text)
    logs = [e["payload"] for e in events if e["type"] == "terminal_log"]
    assert any("ruff" in p["command"] and p["exit_code"] == 1 for p in logs)
    assert any("pytest" in p["command"] and p["exit_code"] == 1 for p in logs)
    assert all(isinstance(p["duration_seconds"], float) for p in logs)


def test_stream_resume_with_last_event_id_and_after(client: TestClient):
    run = start(client, "INSPECT")
    full = parse_sse(client.get(f"/api/revive/stream/{run['id']}").text)
    resumed = parse_sse(
        client.get(f"/api/revive/stream/{run['id']}", headers={"Last-Event-ID": "3"}).text
    )
    assert [e["sequence"] for e in resumed] == [e["sequence"] for e in full if e["sequence"] > 3]
    last = full[-1]["sequence"]
    assert parse_sse(client.get(f"/api/revive/stream/{run['id']}?after={last}").text) == []


def test_stream_errors(client: TestClient):
    run = start(client, "INSPECT")
    assert client.get("/api/revive/stream/nope").status_code == 404
    for bad in ("abc", "-1"):
        r = client.get(f"/api/revive/stream/{run['id']}", headers={"Last-Event-ID": bad})
        assert r.status_code == 400


def test_events_are_isolated_per_run(client: TestClient):
    a, b = start(client, "INSPECT"), start(client, "INSPECT")
    for run in (a, b):
        events = parse_sse(client.get(f"/api/revive/stream/{run['id']}").text)
        assert {e["run_id"] for e in events} == {run["id"]}
        assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))


@pytest.mark.parametrize("mode", ["INSPECT", "PREVIEW"])
def test_non_revive_modes_never_ask_for_approval(client: TestClient, mode: str):
    run = start(client, mode)
    events = parse_sse(client.get(f"/api/revive/stream/{run['id']}").text)
    assert run["status"] == "COMPLETED"
    assert "hitl_required" not in {e["type"] for e in events}
    assert "patch_applied" not in {e["type"] for e in events}
    assert events[-1]["payload"]["patches_generated"] is (mode == "PREVIEW")
    assert events[-1]["payload"].get("patches_applied", False) is False
