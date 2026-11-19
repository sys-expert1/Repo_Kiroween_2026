from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.models import Dependency
from app.config import FIXTURES_DIR, Settings
from app.db import RunRow
from app.graph.runner import InlineExecutor
from app.main import create_app
from tests.conftest import FakeRunner, OfflineAdapter
from tests.helpers import parse_sse


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file() and not p.is_symlink():
            h.update(p.read_bytes())
    return h.hexdigest()


def make_local_repo(root: Path, name: str = "proj", *, sha: str | None = None) -> Path:
    repo = root / name
    repo.mkdir()
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "test_app.py").write_text(
        "from app import add\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    (repo / "requirements.txt").write_text("requests==2.19.1\n")
    if sha:
        (repo / ".git" / "refs" / "heads").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (repo / ".git" / "refs" / "heads" / "main").write_text(sha + "\n")
    return repo


def run(client: TestClient, source: dict, mode: str = "INSPECT") -> dict:
    repo = client.post("/api/repositories/import", json=source).json()["id"]
    r = client.post("/api/revive/start", json={"repository_id": repo, "mode": mode})
    assert r.status_code == 201, r.text
    return r.json()


def artifact_json(client: TestClient, run_id: str, name: str) -> dict:
    arts = client.get(f"/api/revive/{run_id}/artifacts").json()
    art = next(a for a in arts if a["name"] == name)
    return client.get(f"/api/revive/{run_id}/artifacts/{art['id']}").json()


def test_local_repo_is_analysed_but_never_executed_without_a_sandbox(
    client: TestClient, allowed_root: Path, fake_runner: FakeRunner
):
    sha = "c" * 40
    repo = make_local_repo(allowed_root, sha=sha)
    before = tree_hash(repo)
    result = run(client, {"local_path": str(repo)})
    assert result["status"] == "COMPLETED" and result["pinned_commit"] == sha
    baseline = artifact_json(client, result["id"], "characterization.json")
    assert baseline["status"] == "not_run" and baseline["counts"] is None
    assert fake_runner.calls == []  # untrusted code was not executed
    assert tree_hash(repo) == before  # source untouched


def test_source_is_unchanged_after_a_full_revive_run(client: TestClient, settings: Settings):
    before = tree_hash(FIXTURES_DIR / "legacy-python-cli")
    r = run(client, {"fixture_id": "legacy-python-cli"}, "REVIVE")
    client.post(
        f"/api/revive/approve/{r['id']}", json={"decision": "approve", "roadmap_version": 1}
    )
    assert tree_hash(FIXTURES_DIR / "legacy-python-cli") == before


def test_baseline_runs_in_a_disposable_copy_and_snapshot_stays_pristine(
    client: TestClient, settings: Settings, fake_runner: FakeRunner, tmp_path: Path
):
    r = run(client, {"fixture_id": "legacy-python-cli"})
    [(argv, cwd)] = fake_runner.calls
    assert cwd.name == "baseline-run" and not cwd.exists()  # removed after the run
    assert "-p" in argv and "no:cacheprovider" in argv
    expected = tmp_path / "expected"
    shutil.copytree(
        FIXTURES_DIR / "legacy-python-cli",
        expected,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    snapshot = settings.data_dir / "runs" / r["id"] / "snapshot"
    assert tree_hash(snapshot) == tree_hash(expected)


def test_unsupported_language_fails_with_a_clear_reason(client: TestClient, allowed_root: Path):
    repo = allowed_root / "gorepo"
    repo.mkdir()
    (repo / "main.go").write_text("package main\n")
    r = run(client, {"local_path": str(repo)})
    assert r["status"] == "FAILED" and "Unsupported language" in r["error"]


def test_source_is_revalidated_when_the_run_starts(
    client: TestClient, allowed_root: Path, tmp_path: Path
):
    repo = make_local_repo(allowed_root)
    repo_id = client.post("/api/repositories/import", json={"local_path": str(repo)}).json()["id"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_text("x = 1\n")
    shutil.rmtree(repo)
    repo.symlink_to(outside, target_is_directory=True)  # swapped after import
    r = client.post("/api/revive/start", json={"repository_id": repo_id}).json()
    assert r["status"] == "FAILED" and "validation failed" in r["error"]
    assert not (Path(client.app.state.settings.data_dir) / "runs" / r["id"] / "snapshot").exists()


def test_deleted_source_fails_the_run(client: TestClient, allowed_root: Path):
    repo = make_local_repo(allowed_root)
    repo_id = client.post("/api/repositories/import", json={"local_path": str(repo)}).json()["id"]
    shutil.rmtree(repo)
    r = client.post("/api/revive/start", json={"repository_id": repo_id}).json()
    assert r["status"] == "FAILED" and "does not exist" in r["error"]


def test_git_source_is_cloned_and_pinned_but_not_executed(
    client: TestClient, monkeypatch, fake_runner: FakeRunner
):
    sha = "d" * 40

    def fake_clone(url: str, dest: Path, home: Path) -> str:
        assert url == "https://github.com/o/r.git"
        dest.mkdir(parents=True)
        (dest / "m.py").write_text("x = 1\n")
        return sha

    monkeypatch.setattr("app.graph.nodes.clone_git", fake_clone)
    r = run(client, {"git_url": "https://github.com/o/r.git"})
    assert r["status"] == "COMPLETED" and r["pinned_commit"] == sha
    assert artifact_json(client, r["id"], "characterization.json")["status"] == "not_run"
    assert fake_runner.calls == []


def test_clone_failure_fails_the_run_with_the_git_error(client: TestClient, monkeypatch):
    from app.workspace import WorkspaceError

    def boom(url, dest, home):
        raise WorkspaceError("Clone failed: repository not found")

    monkeypatch.setattr("app.graph.nodes.clone_git", boom)
    r = run(client, {"git_url": "https://github.com/o/missing"})
    assert r["status"] == "FAILED" and "repository not found" in r["error"]


def test_approval_survives_a_process_restart(settings: Settings, fake_runner: FakeRunner):
    def build() -> TestClient:
        return TestClient(
            create_app(
                settings,
                executor=InlineExecutor(),
                runner_factory=lambda st, home: fake_runner if st == "fixture" else None,
                adapter=OfflineAdapter(),
            )
        )

    first = build()
    r = run(first, {"fixture_id": "legacy-python-cli"}, "REVIVE")
    assert r["status"] == "AWAITING_APPROVAL"
    first.app.state.workflow.close()  # simulate the process exiting

    second = build()  # a new process: new graph, new connections, same data directory
    assert second.app.state.workflow.is_waiting(r["id"])
    body = second.post(
        f"/api/revive/approve/{r['id']}", json={"decision": "approve", "roadmap_version": 1}
    ).json()
    assert body["status"] == "FAILED" and "not implemented" in body["error"]
    assert not second.app.state.workflow.is_waiting(r["id"])


def test_concurrent_runs_have_independent_state(client: TestClient):
    a = run(client, {"fixture_id": "legacy-python-cli"}, "REVIVE")
    b = run(client, {"fixture_id": "legacy-python-cli"}, "REVIVE")
    assert a["id"] != b["id"]
    client.post(f"/api/revive/approve/{a['id']}", json={"decision": "reject", "roadmap_version": 1})
    b_now = client.post(
        f"/api/revive/{b['id']}/cancel"
    ).json()  # b was still waiting, unaffected by a
    assert b_now["status"] == "CANCELLED"
    assert not client.app.state.workflow.is_waiting(a["id"])
    assert not client.app.state.workflow.is_waiting(b["id"])


class CountingAdapter(OfflineAdapter):
    def __init__(self, behaviour) -> None:
        super().__init__()
        self.calls = 0
        self.behaviour = behaviour

    def autopsy(self, root, name, scratch, observe):
        self.calls += 1
        self.behaviour(self)
        return super().autopsy(root, name, scratch, observe)


def client_with(settings: Settings, adapter, runner: FakeRunner | None = None) -> TestClient:
    runner = runner or FakeRunner()
    app = create_app(
        settings,
        executor=InlineExecutor(),
        runner_factory=lambda st, home: runner if st == "fixture" else None,
        adapter=adapter,
    )
    app.state.sse_poll_seconds = 0.01
    return TestClient(app)


def test_transient_error_is_retried_once_then_succeeds(settings: Settings):
    def flaky(adapter):
        if adapter.calls == 1:
            raise OSError("temporary I/O error")

    adapter = CountingAdapter(flaky)
    r = run(client_with(settings, adapter), {"fixture_id": "legacy-python-cli"})
    assert r["status"] == "COMPLETED" and adapter.calls == 2


def test_retries_are_bounded(settings: Settings):
    def always(adapter):
        raise OSError("disk on fire")

    adapter = CountingAdapter(always)
    r = run(client_with(settings, adapter), {"fixture_id": "legacy-python-cli"})
    assert adapter.calls == 2  # max_attempts=2, never indefinite
    assert r["status"] == "FAILED" and "OSError" in r["error"]


def test_deterministic_errors_are_not_retried_and_are_redacted(settings: Settings):
    def bad(adapter):
        raise RuntimeError("login failed password=hunter2 for https://bob:s3cret@host/x")

    adapter = CountingAdapter(bad)
    r = run(client_with(settings, adapter), {"fixture_id": "legacy-python-cli"})
    assert adapter.calls == 1
    assert r["status"] == "FAILED"
    assert "hunter2" not in r["error"] and "s3cret" not in r["error"]


def test_cancel_during_a_run_stops_before_the_next_stage(settings: Settings):
    runner = FakeRunner()
    client = client_with(settings, CountingAdapter(lambda a: None), runner)

    def cancel_the_run_in_flight(_adapter) -> None:
        with client.app.state.session_factory() as db:
            run_id = db.query(RunRow).one().id
        assert client.post(f"/api/revive/{run_id}/cancel").json()["status"] == "RUNNING"

    client.app.state.workflow._ctx.adapter.behaviour = cancel_the_run_in_flight
    repo = client.post("/api/repositories/import", json={"fixture_id": "legacy-python-cli"}).json()[
        "id"
    ]
    r = client.post("/api/revive/start", json={"repository_id": repo, "mode": "REVIVE"}).json()
    assert r["status"] == "CANCELLED" and r["cancel_requested"] is True
    assert runner.calls == []  # CHARACTERIZATION never started
    types = [e["type"] for e in parse_sse(client.get(f"/api/revive/stream/{r['id']}").text)]
    assert "cancel_requested" in types and types[-1] == "run_cancelled"
    assert "roadmap_ready" not in types


def test_dependency_audit_failure_does_not_fail_the_run(settings: Settings):
    class NoAudit(OfflineAdapter):
        def _pip_audit(self, deps: list[Dependency], scratch, observe):
            from app.adapters.models import ToolRun

            tool = ToolRun(
                tool="pip-audit", command="pip-audit", exit_code=None, measured=False,
                summary="Not measured: network unreachable", duration_seconds=0.0,
            )  # fmt: skip
            return None, None, [], tool

    client = client_with(settings, NoAudit())
    r = run(client, {"fixture_id": "legacy-python-cli"})
    assert r["status"] == "COMPLETED"
    autopsy = artifact_json(client, r["id"], "autopsy.json")
    assert autopsy["known_vulnerabilities"] is None
