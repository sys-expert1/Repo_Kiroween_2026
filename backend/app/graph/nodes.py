"""LangGraph node implementations. Each node is a small function over the workflow state."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select

from app.adapters.models import AutopsyReport, BaselineReport
from app.agents.roadmap import Roadmap, build_roadmap, revise_roadmap
from app.artifacts import write_artifact
from app.config import Settings
from app.db import RepositoryRow
from app.execution.runner import CommandResult
from app.fixtures import fixture_path
from app.graph.state import WorkflowContext, WorkflowError, WorkflowState
from app.reports import autopsy_md, characterization_md, roadmap_md
from app.repositories.paths import PathValidationError, resolve_local_path
from app.repositories.urls import UrlValidationError, validate_git_url
from app.runs import service
from app.schemas import TERMINAL_STATUSES, EventType, RunStatus, Stage
from app.workspace import WorkspaceError, clone_git, read_head_sha, run_dir, snapshot_tree

Node = Callable[[WorkflowState], dict]
OUTPUT_EXCERPT_LINES = 30


def _json(model: object) -> bytes:
    return service.json_bytes(model.model_dump() if hasattr(model, "model_dump") else model)


class Nodes:
    def __init__(self, ctx: WorkflowContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ helpers
    def guarded(self, fn: Node) -> Node:
        """Skip the node and finish cancellation if the run was cancelled or already halted."""

        def wrapper(state: WorkflowState) -> dict:
            if state.get("halt"):
                return {}
            with self.ctx.session_factory() as db:
                if service.is_cancel_requested(db, state["run_id"]):
                    run = service.get_run(db, state["run_id"])
                    if run.status not in TERMINAL_STATUSES:
                        service.transition(
                            db, state["run_id"], RunStatus.CANCELLED,
                            payload={"reason": "cancelled"},
                        )  # fmt: skip
                    return {"halt": True}
            return fn(state)

        wrapper.__name__ = fn.__name__
        return wrapper

    def _observer(self, run_id: str, stage: Stage):  # noqa: ANN202
        def observe(result: CommandResult) -> None:
            lines = (result.stdout + result.stderr).strip().splitlines()
            with self.ctx.session_factory() as db:
                service.log_event(
                    db, run_id, EventType.TERMINAL_LOG,
                    {
                        "stage": stage.value,
                        "command": result.command,
                        "exit_code": result.exit_code,
                        "duration_seconds": result.duration_seconds,
                        "timed_out": result.timed_out,
                        "output": "\n".join(lines[-OUTPUT_EXCERPT_LINES:]),
                    },
                )  # fmt: skip

        return observe

    def _store(self, run_id: str, type_: str, name: str, content: bytes) -> None:
        with self.ctx.session_factory() as db:
            write_artifact(self.ctx.settings, db, run_id, type_, name, content)

    def _enter(self, run_id: str, stage: Stage) -> None:
        with self.ctx.session_factory() as db:
            service.transition(db, run_id, RunStatus.RUNNING, stage)

    # -------------------------------------------------------------------- nodes
    def validate_and_snapshot(self, state: WorkflowState) -> dict:
        run_id = state["run_id"]
        self._enter(run_id, Stage.AUTOPSY)
        with self.ctx.session_factory() as db:
            run = service.get_run(db, run_id)
            repo = db.scalar(select(RepositoryRow).where(RepositoryRow.id == run.repository_id))
        if repo is None:
            raise WorkflowError("Repository no longer exists.")
        base = run_dir(self.ctx.settings, run_id)
        snapshot = base / "snapshot"
        shutil.rmtree(snapshot, ignore_errors=True)  # idempotent under retry
        try:
            commit = _materialize(self.ctx.settings, repo, snapshot, base / "scratch" / "home")
        except (PathValidationError, UrlValidationError, WorkspaceError, KeyError) as exc:
            raise WorkflowError(f"Repository validation failed: {exc}") from None
        with self.ctx.session_factory() as db:
            service.set_pinned_commit(db, run_id, commit)
        return {
            "snapshot_dir": str(snapshot),
            "commit": commit,
            "repository_name": repo.name,
            "source_type": repo.source_type,
        }

    def autopsy(self, state: WorkflowState) -> dict:
        run_id = state["run_id"]
        root = Path(state["snapshot_dir"])
        if not self.ctx.adapter.detect(root):
            raise WorkflowError(
                f"Unsupported language or toolchain: no {self.ctx.adapter.language} "
                "sources were found."
            )
        report = self.ctx.adapter.autopsy(
            root, state["repository_name"], run_dir(self.ctx.settings, run_id) / "scratch",
            self._observer(run_id, Stage.AUTOPSY),
        )  # fmt: skip
        self._store(run_id, "autopsy_report", "AUTOPSY_REPORT.md",
                    autopsy_md(report, state.get("commit")).encode())  # fmt: skip
        self._store(run_id, "autopsy_data", "autopsy.json", _json(report))
        return {"autopsy": report.model_dump()}

    def characterization(self, state: WorkflowState) -> dict:
        run_id = state["run_id"]
        self._enter(run_id, Stage.CHARACTERIZATION)
        base = run_dir(self.ctx.settings, run_id)
        work = base / "baseline-run"
        shutil.rmtree(work, ignore_errors=True)
        snapshot_tree(Path(state["snapshot_dir"]), work)  # snapshot stays pristine
        runner = self.ctx.runner_for(state["source_type"], base / "scratch" / "home")
        report = self.ctx.adapter.characterize(
            work, runner, self._observer(run_id, Stage.CHARACTERIZATION),
            self.ctx.baseline_timeout,
        )  # fmt: skip
        shutil.rmtree(work, ignore_errors=True)
        if report.status == "not_run":
            report.reason = self.ctx.no_runner_reason(state["source_type"])
        self._store(run_id, "characterization_report", "CHARACTERIZATION_REPORT.md",
                    characterization_md(report).encode())  # fmt: skip
        self._store(run_id, "characterization_data", "characterization.json", _json(report))
        return {"baseline": report.model_dump()}

    def complete_inspect(self, state: WorkflowState) -> dict:
        with self.ctx.session_factory() as db:
            service.transition(
                db, state["run_id"], RunStatus.COMPLETED,
                payload={
                    "mode": state["mode"],
                    "stages_completed": ["AUTOPSY", "CHARACTERIZATION"],
                    "patches_generated": False,
                },
            )  # fmt: skip
        return {}

    def roadmap(self, state: WorkflowState) -> dict:
        run_id = state["run_id"]
        self._enter(run_id, Stage.REANIMATION)
        roadmap = build_roadmap(
            AutopsyReport(**state["autopsy"]), BaselineReport(**state["baseline"])
        )
        self._publish_roadmap(run_id, roadmap)
        return {"roadmap": roadmap.model_dump()}

    def _publish_roadmap(self, run_id: str, roadmap: Roadmap) -> None:
        v = roadmap.version
        self._store(run_id, "roadmap", f"REVIVAL_ROADMAP.v{v}.md", roadmap_md(roadmap).encode())
        self._store(run_id, "roadmap_data", f"roadmap.v{v}.json", _json(roadmap))
        with self.ctx.session_factory() as db:
            service.set_roadmap(db, run_id, v, roadmap.digest)
            service.log_event(
                db, run_id, EventType.ROADMAP_READY,
                {
                    "roadmap_version": v,
                    "patch_set_digest": roadmap.digest,
                    "task_count": len(roadmap.included),
                },
            )  # fmt: skip

    def complete_preview(self, state: WorkflowState) -> dict:
        with self.ctx.session_factory() as db:
            service.transition(
                db, state["run_id"], RunStatus.COMPLETED,
                payload={
                    "mode": state["mode"],
                    "stages_completed": ["AUTOPSY", "CHARACTERIZATION"],
                    "roadmap_generated": True,
                    "patches_generated": False,
                    "note": "Patch generation is not implemented yet (task 7).",
                },
            )  # fmt: skip
        return {}

    def request_approval(self, state: WorkflowState) -> dict:
        roadmap = Roadmap(**state["roadmap"])
        files = {f for t in roadmap.included for f in t.affected_files}
        with self.ctx.session_factory() as db:
            service.transition(
                db, state["run_id"], RunStatus.AWAITING_APPROVAL, Stage.REANIMATION,
                payload={
                    "repository": state["repository_name"],
                    "source_commit": state.get("commit"),
                    "roadmap_version": roadmap.version,
                    "patch_set_digest": roadmap.digest,
                    "task_count": len(roadmap.included),
                    "files_affected": len(files),
                    "execution_target": "isolated working copy",
                },
            )  # fmt: skip
        return {}

    def await_decision(self, state: WorkflowState) -> dict:
        """Only the interrupt lives here: LangGraph re-runs this node on resume."""
        from langgraph.types import interrupt

        decision = interrupt(
            {"awaiting": "approval", "roadmap_version": state["roadmap"]["version"]}
        )
        return {"decision": decision}

    def revise(self, state: WorkflowState) -> dict:
        run_id = state["run_id"]
        revised = revise_roadmap(
            Roadmap(**state["roadmap"]), list(state["decision"].get("excluded_task_ids", []))
        )
        self._publish_roadmap(run_id, revised)
        return {"roadmap": revised.model_dump(), "decision": {}}

    def reanimation_unavailable(self, state: WorkflowState) -> dict:
        message = (
            "Approval was recorded, but Reanimation is not implemented yet (task 7). "
            "No patches were generated or applied and the source was not modified."
        )
        with self.ctx.session_factory() as db:
            service.transition(
                db, state["run_id"], RunStatus.FAILED, Stage.REANIMATION, error=message,
                payload={"reason": "reanimation_not_implemented"},
            )  # fmt: skip
        return {}


def _materialize(settings: Settings, repo: RepositoryRow, snapshot: Path, home: Path) -> str | None:
    """Create the snapshot from a registered repository and return the pinned commit, if known.

    Local and fixture sources are re-validated here, not trusted from import time."""
    if repo.source_type == "git":
        return clone_git(validate_git_url(repo.location), snapshot, home)
    if repo.source_type == "fixture":
        source = fixture_path(settings.fixtures_dir, Path(repo.location).name)
    else:
        source = resolve_local_path(repo.location, settings.allowed_roots)
    commit = read_head_sha(source)
    snapshot_tree(source, snapshot)
    return commit
