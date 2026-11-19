"""LangGraph node implementations. Each node is a small function over the workflow state."""

from __future__ import annotations

import ast
import shutil
from collections.abc import Callable
from functools import partial
from pathlib import Path

from sqlalchemy import select

from app.adapters.models import AutopsyReport, BaselineReport
from app.agents.patches import PatchApplyError, PatchSet, apply_patchset, generate_patchset
from app.agents.roadmap import Roadmap, build_roadmap, revise_roadmap
from app.artifacts import write_artifact
from app.config import Settings
from app.consecration import docker_build_check, validate_ci_workflows, verify
from app.db import RepositoryRow
from app.execution.runner import CommandResult
from app.fixtures import fixture_path
from app.graph.state import WorkflowContext, WorkflowError, WorkflowState
from app.reports import (
    autopsy_md,
    characterization_md,
    patchset_md,
    roadmap_md,
    verification_md,
)
from app.repositories.paths import PathValidationError, resolve_local_path
from app.repositories.urls import UrlValidationError, validate_git_url
from app.runs import service
from app.runs.events import events_after
from app.schemas import TERMINAL_STATUSES, EventType, RunStatus, Stage
from app.scoring.health_score import VitalityScore, compute_vitality
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
        report = self._run_tests(state, work, Stage.CHARACTERIZATION)
        shutil.rmtree(work, ignore_errors=True)
        self._store(run_id, "characterization_report", "CHARACTERIZATION_REPORT.md",
                    characterization_md(report).encode())  # fmt: skip
        self._store(run_id, "characterization_data", "characterization.json", _json(report))
        score = compute_vitality(AutopsyReport(**state["autopsy"]), report)
        with self.ctx.session_factory() as db:
            service.log_event(
                db,
                run_id,
                EventType.HEALTH_SCORE_UPDATED,
                {"phase": "before", **score.model_dump()},
            )
        return {"baseline": report.model_dump(), "score_before": score.model_dump()}

    def _run_tests(self, state: WorkflowState, copy: Path, stage: Stage) -> BaselineReport:
        run_id = state["run_id"]
        home = run_dir(self.ctx.settings, run_id) / "scratch" / "home"
        runner = self.ctx.runner_for(state["source_type"], home)
        report = self.ctx.adapter.characterize(
            copy, runner, self._observer(run_id, stage), self.ctx.baseline_timeout
        )
        if report.status == "not_run":
            report.reason = self.ctx.no_runner_reason(state["source_type"])
        return report

    def complete_inspect(self, state: WorkflowState) -> dict:
        with self.ctx.session_factory() as db:
            service.transition(
                db, state["run_id"], RunStatus.COMPLETED,
                payload={
                    "mode": state["mode"],
                    "stages_completed": ["AUTOPSY", "CHARACTERIZATION"],
                    "patches_generated": False,
                    "score_before": state["score_before"]["score"],
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
            service.set_roadmap(db, run_id, v)
            service.log_event(
                db, run_id, EventType.ROADMAP_READY,
                {
                    "roadmap_version": v,
                    "roadmap_digest": roadmap.digest,
                    "task_count": len(roadmap.included),
                },
            )  # fmt: skip

    def generate_patches(self, state: WorkflowState) -> dict:
        """Propose patches against the snapshot. Nothing is written to any working copy."""
        run_id = state["run_id"]
        roadmap = Roadmap(**state["roadmap"])
        scratch = run_dir(self.ctx.settings, run_id) / "scratch"
        fixer = partial(
            self.ctx.adapter.fix_lint, scratch=scratch,
            observe=self._observer(run_id, Stage.REANIMATION),
        )  # fmt: skip
        ps = generate_patchset(
            roadmap, AutopsyReport(**state["autopsy"]), Path(state["snapshot_dir"]), fixer
        )
        v = roadmap.version
        self._store(run_id, "patch_set", f"PATCHSET.v{v}.diff", ps.combined_diff.encode())
        self._store(run_id, "patch_set_summary", f"PATCHSET.v{v}.md", patchset_md(ps).encode())
        self._store(run_id, "patch_set_data", f"patches.v{v}.json", _json(ps))
        with self.ctx.session_factory() as db:
            service.set_patch_set(db, run_id, ps.digest if ps.patches else None)
            service.log_event(
                db, run_id, EventType.DIFF_GENERATED,
                {
                    "roadmap_version": v,
                    "patch_set_digest": ps.digest if ps.patches else None,
                    "files_changed": ps.files_changed,
                    "patches": [
                        {"task_id": p.task_id, "path": p.path, "action": p.action}
                        for p in ps.patches
                    ],
                    "skipped": [s.model_dump() for s in ps.skipped],
                },
            )  # fmt: skip
        return {"patchset": ps.model_dump()}

    def complete_preview(self, state: WorkflowState) -> dict:
        ps = PatchSet(**state["patchset"])
        with self.ctx.session_factory() as db:
            service.transition(
                db, state["run_id"], RunStatus.COMPLETED,
                payload={
                    "mode": state["mode"],
                    "stages_completed": ["AUTOPSY", "CHARACTERIZATION", "REANIMATION"],
                    "roadmap_generated": True,
                    "patches_generated": bool(ps.patches),
                    "patches_applied": False,
                    "patch_set_digest": ps.digest if ps.patches else None,
                    "note": "Preview only: no patch was applied anywhere.",
                },
            )  # fmt: skip
        return {}

    def complete_no_patches(self, state: WorkflowState) -> dict:
        with self.ctx.session_factory() as db:
            service.transition(
                db, state["run_id"], RunStatus.COMPLETED,
                payload={
                    "mode": state["mode"],
                    "patches_generated": False,
                    "patches_applied": False,
                    "note": "No roadmap task could be patched automatically, so there is "
                    "nothing to approve. See the roadmap and patch summary.",
                },
            )  # fmt: skip
        return {}

    def request_approval(self, state: WorkflowState) -> dict:
        roadmap = Roadmap(**state["roadmap"])
        ps = PatchSet(**state["patchset"])
        tasks = {t.id: t for t in roadmap.tasks}
        patched = {p.task_id for p in ps.patches}
        with self.ctx.session_factory() as db:
            service.transition(
                db, state["run_id"], RunStatus.AWAITING_APPROVAL, Stage.REANIMATION,
                payload={
                    "repository": state["repository_name"],
                    "source_commit": state.get("commit"),
                    "roadmap_version": roadmap.version,
                    "patch_set_digest": ps.digest,
                    "task_count": len(roadmap.included),
                    "patched_task_count": len(patched),
                    "files_affected": len(ps.files_changed),
                    "dependency_changes": [
                        p.rationale
                        for p in ps.patches
                        if tasks[p.task_id].category == "dependencies"
                    ],
                    "security_fixes": sum(1 for t in patched if tasks[t].category == "security"),
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

    # ------------------------------------------------------------- reanimation
    def _rollback(self, run_id: str, work: Path, snapshot: Path, reason: str) -> None:
        """Keep the failed working copy as evidence and restore a pristine one."""
        evidence = work.parent / "work-failed"
        shutil.rmtree(evidence, ignore_errors=True)
        if work.exists():
            work.rename(evidence)
        snapshot_tree(snapshot, work)
        with self.ctx.session_factory() as db:
            service.log_event(
                db, run_id, EventType.ROLLBACK_COMPLETED,
                {"reason": reason, "restored_from": "snapshot", "evidence_kept": "work-failed"},
            )  # fmt: skip

    def reanimation(self, state: WorkflowState) -> dict:
        """Apply exactly the approved patch set to a fresh copy of the snapshot."""
        run_id = state["run_id"]
        ps = PatchSet(**state["patchset"])
        with self.ctx.session_factory() as db:
            approvals = service.approvals_for(db, run_id)
        last = approvals[-1] if approvals else None
        if not last or last["decision"] != "approve" or last["patch_set_digest"] != ps.digest:
            raise WorkflowError("No approval matches this patch set, so nothing was applied.")
        base = run_dir(self.ctx.settings, run_id)
        snapshot, work = Path(state["snapshot_dir"]), base / "work"
        shutil.rmtree(work, ignore_errors=True)
        snapshot_tree(snapshot, work)
        try:
            applied = apply_patchset(ps, work)
            for patch in ps.patches:
                if patch.path.endswith(".py"):
                    ast.parse((work / patch.path).read_text(encoding="utf-8"))
        except (PatchApplyError, SyntaxError) as exc:
            self._rollback(run_id, work, snapshot, str(exc))
            raise WorkflowError(f"Patch application failed and was rolled back: {exc}") from None
        with self.ctx.session_factory() as db:
            for patch in ps.patches:
                service.log_event(
                    db, run_id, EventType.PATCH_APPLIED,
                    {"task_id": patch.task_id, "path": patch.path, "action": patch.action,
                     "after_sha256": patch.after_sha256, "target": "isolated working copy"},
                )  # fmt: skip
        return {"work_dir": str(work), "applied": applied}

    # ------------------------------------------------------------ consecration
    def consecration(self, state: WorkflowState) -> dict:
        run_id = state["run_id"]
        self._enter(run_id, Stage.CONSECRATION)
        base = run_dir(self.ctx.settings, run_id)
        work, snapshot = Path(state["work_dir"]), Path(state["snapshot_dir"])
        observe = self._observer(run_id, Stage.CONSECRATION)
        before = AutopsyReport(**state["autopsy"])
        baseline = BaselineReport(**state["baseline"])

        after = self.ctx.adapter.autopsy(
            work, state["repository_name"], base / "scratch" / "after", observe
        )
        final_copy = base / "final-run"
        shutil.rmtree(final_copy, ignore_errors=True)
        snapshot_tree(work, final_copy)  # the working copy stays free of test artifacts
        final = self._run_tests(state, final_copy, Stage.CONSECRATION)
        shutil.rmtree(final_copy, ignore_errors=True)
        ci = validate_ci_workflows(work)
        docker, build = docker_build_check(
            work, base / "scratch" / "home", enabled=self.ctx.settings.allow_docker_build,
            tag=f"necro-verify-{run_id[:8]}",
        )  # fmt: skip
        if build is not None:
            observe(build)

        score_before = VitalityScore(**state["score_before"])
        score_after = compute_vitality(
            after, final, ci_validation=ci.status, docker_build=docker.status
        )
        verification = verify(before, baseline, after, final, ci, docker, score_before, score_after)
        applied = state.get("applied", [])
        report_md = verification_md(verification, score_before, score_after, applied)
        self._store(run_id, "verification_report", "VERIFICATION_REPORT.md", report_md.encode())
        self._store(run_id, "verification_data", "verification.json", _json(verification))
        self._store(run_id, "vitality_report", "VITALITY_REPORT.json", service.json_bytes({
            "before": score_before.model_dump(), "after": score_after.model_dump(),
            "comparison_warning": verification.score_warning,
        }))  # fmt: skip
        with self.ctx.session_factory() as db:
            service.log_event(db, run_id, EventType.HEALTH_SCORE_UPDATED,
                              {"phase": "after", **score_after.model_dump()})  # fmt: skip
            service.log_event(
                db, run_id, EventType.VERIFICATION_COMPLETED,
                {"verdict": verification.verdict,
                 "checks": [c.model_dump() for c in verification.checks],
                 "regressions": verification.regressions},
            )  # fmt: skip
        self._store(run_id, "run_audit", "RUN_AUDIT.json", self._audit(run_id))

        stages = ["AUTOPSY", "CHARACTERIZATION", "REANIMATION", "CONSECRATION"]
        summary = {
            "mode": state["mode"],
            "stages_completed": stages,
            "verdict": verification.verdict,
            "patches_applied": len(applied),
            "score_before": score_before.score,
            "score_after": score_after.score,
        }
        with self.ctx.session_factory() as db:
            if verification.verdict == "verified":
                service.transition(db, run_id, RunStatus.COMPLETED, payload=summary)
                return {}
        self._rollback(run_id, work, snapshot, "; ".join(verification.regressions))
        with self.ctx.session_factory() as db:
            service.transition(
                db, run_id, RunStatus.FAILED, error="Verification failed and the working copy "
                "was rolled back: " + "; ".join(verification.regressions), payload=summary,
            )  # fmt: skip
        return {}

    def _audit(self, run_id: str) -> bytes:
        with self.ctx.session_factory() as db:
            run = service.get_run(db, run_id)
            events = events_after(db, run_id, 0)
            audit = {
                "run": run.model_dump(mode="json"),
                "approvals": service.approvals_for(db, run_id),
                "patches_applied": [e.payload for e in events if e.type is EventType.PATCH_APPLIED],
                "rollbacks": [e.payload for e in events if e.type is EventType.ROLLBACK_COMPLETED],
                "commands": [
                    {
                        k: e.payload.get(k)
                        for k in ("stage", "command", "exit_code", "duration_seconds")
                    }
                    for e in events
                    if e.type is EventType.TERMINAL_LOG
                ],
            }
        return service.json_bytes(audit)


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
