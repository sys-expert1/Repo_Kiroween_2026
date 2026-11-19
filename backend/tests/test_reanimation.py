from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents.patches import PatchApplyError
from app.config import FIXTURES_DIR, Settings
from app.db import RunRow
from app.graph.runner import InlineExecutor
from app.main import create_app
from app.runs.events import events_after
from tests.conftest import OfflineAdapter, SequencedRunner, canned_output
from tests.helpers import parse_sse

FIXTURE = {"fixture_id": "legacy-python-cli"}
OLD = "tests/test_inventory.py::test_total_items_known_failure"
NEW = "tests/test_inventory.py::test_add_item_accumulates"


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if "__pycache__" in p.parts:
            continue
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def make_client(settings: Settings, runner) -> TestClient:
    app = create_app(
        settings, executor=InlineExecutor(),
        runner_factory=lambda st, home: runner if st == "fixture" else None,
        adapter=OfflineAdapter(),
    )  # fmt: skip
    app.state.sse_poll_seconds = 0.01
    return TestClient(app)


def revive(client: TestClient, source: dict = FIXTURE, mode: str = "REVIVE") -> dict:
    repo = client.post("/api/repositories/import", json=source).json()["id"]
    return client.post("/api/revive/start", json={"repository_id": repo, "mode": mode}).json()


def approve(client: TestClient, run: dict, **kw) -> dict:
    body = {"decision": "approve", "roadmap_version": run["roadmap_version"], **kw}
    return client.post(f"/api/revive/approve/{run['id']}", json=body).json()


def events(client: TestClient, run_id: str) -> list[dict]:
    return parse_sse(client.get(f"/api/revive/stream/{run_id}").text)


def stored_events(client: TestClient, run_id: str) -> list:
    """Persisted events, without opening a stream (a waiting run keeps its stream open)."""
    with client.app.state.session_factory() as db:
        return events_after(db, run_id, 0)


def run_path(settings: Settings, run_id: str, *parts: str) -> Path:
    return settings.data_dir.joinpath("runs", run_id, *parts)


def test_patches_land_in_the_working_copy_only(settings: Settings, fake_runner):
    client = make_client(settings, fake_runner)
    before = tree_hash(FIXTURES_DIR / "legacy-python-cli")
    run = revive(client)
    assert not run_path(settings, run["id"], "work").exists()  # nothing applied before approval
    final = approve(client, run)
    assert final["status"] == "COMPLETED"
    work, snap = run_path(settings, run["id"], "work"), run_path(settings, run["id"], "snapshot")
    assert "os.environ.get" in (work / "inventory.py").read_text()
    assert "legacy-token" in (snap / "inventory.py").read_text()  # snapshot is pristine
    assert (work / "Dockerfile").exists() and not (snap / "Dockerfile").exists()
    assert tree_hash(FIXTURES_DIR / "legacy-python-cli") == before  # the source never changes


def test_preview_generates_diffs_but_applies_nothing(settings: Settings, fake_runner):
    client = make_client(settings, fake_runner)
    run = revive(client, mode="PREVIEW")
    assert run["status"] == "COMPLETED" and run["patch_set_digest"]
    assert not run_path(settings, run["id"], "work").exists()
    ev = events(client, run["id"])
    diff = next(e for e in ev if e["type"] == "diff_generated")["payload"]
    assert diff["patches"] and "inventory.py" in diff["files_changed"]
    assert diff["skipped"] and "never edited" in diff["skipped"][0]["reason"]
    assert not any(e["type"] in ("hitl_required", "patch_applied") for e in ev)


def test_approval_request_describes_the_patch_scope(settings: Settings, fake_runner):
    client = make_client(settings, fake_runner)
    run = revive(client)
    hitl = next(e for e in stored_events(client, run["id"]) if e.type == "hitl_required").payload
    assert hitl["patch_set_digest"] == run["patch_set_digest"]
    assert hitl["files_affected"] == 4 and hitl["security_fixes"] == 2
    assert hitl["execution_target"] == "isolated working copy"
    assert any("requests" in d for d in hitl["dependency_changes"])


def test_regression_fails_verification_and_rolls_back(settings: Settings):
    runner = SequencedRunner([canned_output(1, [OLD]), canned_output(0, [OLD, NEW])])
    client = make_client(settings, runner)
    run = revive(client)
    final = approve(client, run)
    assert final["status"] == "FAILED" and "rolled back" in final["error"]
    assert f"New failing test: {NEW}" in final["error"]
    work = run_path(settings, run["id"], "work")
    snap = run_path(settings, run["id"], "snapshot")
    failed = run_path(settings, run["id"], "work-failed")
    assert tree_hash(work) == tree_hash(snap)  # restored to the pre-patch snapshot
    assert "os.environ.get" in (failed / "inventory.py").read_text()  # evidence preserved
    ev = events(client, run["id"])
    types = [e["type"] for e in ev]
    assert types.index("verification_completed") < types.index("rollback_completed")
    assert (
        next(e for e in ev if e["type"] == "verification_completed")["payload"]["verdict"]
        == "failed"
    )
    assert types[-1] == "run_failed"
    report = _text(client, run["id"], "VERIFICATION_REPORT.md")
    assert "Verdict: **failed**" in report and NEW in report  # failure is reported, not hidden


def test_baseline_failures_are_not_blamed_on_the_patch(settings: Settings):
    runner = SequencedRunner([canned_output(1, [OLD]), canned_output(1, [OLD])])
    client = make_client(settings, runner)
    final = approve(client, revive(client))
    assert final["status"] == "COMPLETED"


def test_failure_while_applying_rolls_back_and_fails_the_run(
    settings: Settings, fake_runner, monkeypatch
):
    def broken_apply(patchset, workdir):
        (workdir / "half-written.txt").write_text("partial")
        raise PatchApplyError("RT-002: requirements.txt changed since approval.")

    monkeypatch.setattr("app.graph.nodes.apply_patchset", broken_apply)
    client = make_client(settings, fake_runner)
    run = revive(client)
    final = approve(client, run)
    assert final["status"] == "FAILED" and "rolled back" in final["error"]
    work = run_path(settings, run["id"], "work")
    assert not (work / "half-written.txt").exists()
    assert tree_hash(work) == tree_hash(run_path(settings, run["id"], "snapshot"))
    assert (run_path(settings, run["id"], "work-failed") / "half-written.txt").exists()
    assert "rollback_completed" in [e["type"] for e in events(client, run["id"])]


def test_nothing_is_applied_if_the_approval_does_not_match_the_patch_set(
    settings: Settings, fake_runner
):
    client = make_client(settings, fake_runner)
    run = revive(client)
    with client.app.state.session_factory() as db:  # tamper with the digest the approval records
        db.get(RunRow, run["id"]).patch_set_digest = "0" * 64
        db.commit()
    final = approve(client, run)
    assert final["status"] == "FAILED" and "No approval matches" in final["error"]
    assert not run_path(settings, run["id"], "work").exists()
    assert not any(e["type"] == "patch_applied" for e in events(client, run["id"]))


def test_edit_regenerates_patches_for_the_reduced_scope(settings: Settings, fake_runner):
    client = make_client(settings, fake_runner)
    run = revive(client)
    plan = json.loads(_text(client, run["id"], "roadmap.v1.json"))
    docker_task = next(t["id"] for t in plan["tasks"] if t["title"] == "Add a Dockerfile")
    edited = approve(client, run, **{"decision": "edit", "excluded_task_ids": [docker_task]})
    assert edited["status"] == "AWAITING_APPROVAL" and edited["roadmap_version"] == 2
    assert edited["patch_set_digest"] != run["patch_set_digest"]
    final = approve(client, edited)
    assert final["status"] == "COMPLETED"
    work = run_path(settings, run["id"], "work")
    assert (
        not (work / "Dockerfile").exists() and (work / ".github" / "workflows" / "ci.yml").exists()
    )
    v2 = json.loads(_text(client, run["id"], "patches.v2.json"))
    assert all(p["path"] != "Dockerfile" for p in v2["patches"])


def test_local_repo_without_a_sandbox_is_patched_but_tests_stay_unmeasured(
    settings: Settings, allowed_root: Path, fake_runner
):
    repo = allowed_root / "proj"
    repo.mkdir()
    (repo / "app.py").write_text('import os\nTOKEN = "abc"\n')
    (repo / "test_app.py").write_text("def test_a():\n    assert True\n")
    client = make_client(settings, fake_runner)
    run = revive(client, {"local_path": str(repo)})
    final = approve(client, run)
    assert final["status"] == "COMPLETED"
    assert fake_runner.calls == []  # untrusted code was never executed
    verification = json.loads(_text(client, run["id"], "verification.json"))
    tests = next(c for c in verification["checks"] if c["name"] == "tests")
    assert tests["status"] == "not_measured" and "tests" in verification["unmeasured"]
    vit = json.loads(_text(client, run["id"], "VITALITY_REPORT.json"))
    assert "tests" in vit["after"]["unmeasured"]
    assert next(d for d in vit["after"]["dimensions"] if d["name"] == "tests")["score"] is None
    assert (repo / "app.py").read_text() == 'import os\nTOKEN = "abc"\n'  # source untouched


def test_run_with_nothing_patchable_completes_without_asking_for_approval(
    settings: Settings, allowed_root: Path, fake_runner
):
    repo = allowed_root / "proj"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("on: [push]\njobs:\n  t: {}\n")
    (repo / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (repo / "app.py").write_text("def f(x):\n    return eval(x)\n")
    (repo / "test_app.py").write_text("def test_a():\n    assert True\n")
    client = make_client(settings, fake_runner)
    run = revive(client, {"local_path": str(repo)})
    assert run["status"] == "COMPLETED" and run["patch_set_digest"] is None
    done = events(client, run["id"])[-1]["payload"]
    assert done["patches_generated"] is False and "nothing to approve" in done["note"]


def _text(client: TestClient, run_id: str, name: str) -> str:
    arts = client.get(f"/api/revive/{run_id}/artifacts").json()
    art = next(a for a in arts if a["name"] == name)
    return client.get(f"/api/revive/{run_id}/artifacts/{art['id']}").text


@pytest.mark.parametrize("decision", ["reject"])
def test_reject_never_creates_a_working_copy(settings: Settings, fake_runner, decision):
    client = make_client(settings, fake_runner)
    run = revive(client)
    out = approve(client, run, decision=decision)
    assert out["status"] == "CANCELLED"
    assert not run_path(settings, run["id"], "work").exists()
