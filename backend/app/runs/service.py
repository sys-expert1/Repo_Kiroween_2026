"""Run lifecycle: creation, status transitions, approval, cancellation, artifacts.

Pure database logic with no HTTP or LangGraph concerns. The workflow drives a run through
`transition`; the API drives it through `start_run`, `approve` and `cancel`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import ApprovalRow, ArtifactRow, RepositoryRow, RunRow
from app.runs.events import append_event
from app.schemas import (
    TERMINAL_STATUSES,
    ApprovalRequest,
    ArtifactOut,
    EventType,
    ExecutionMode,
    RunOut,
    RunStatus,
    Stage,
    StartRunRequest,
)

LOCAL_ACTOR = "local"

_STATUS_EVENT = {
    RunStatus.RUNNING: EventType.STAGE_CHANGED,
    RunStatus.AWAITING_APPROVAL: EventType.HITL_REQUIRED,
    RunStatus.COMPLETED: EventType.RUN_COMPLETED,
    RunStatus.FAILED: EventType.RUN_FAILED,
    RunStatus.CANCELLED: EventType.RUN_CANCELLED,
}


class RunNotFoundError(LookupError):
    pass


class RepositoryMissingError(LookupError):
    pass


class ArtifactNotFoundError(LookupError):
    pass


class InvalidStateError(RuntimeError):
    """The requested action is not allowed in the run's current state (HTTP 409)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _out(row: RunRow) -> RunOut:
    return RunOut(
        id=row.id,
        repository_id=row.repository_id,
        mode=ExecutionMode(row.mode),
        status=RunStatus(row.status),
        current_stage=Stage(row.current_stage) if row.current_stage else None,
        cancel_requested=row.cancel_requested,
        roadmap_version=row.roadmap_version,
        patch_set_digest=row.patch_set_digest,
        pinned_commit=row.pinned_commit,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _get(db: Session, run_id: str) -> RunRow:
    row = db.get(RunRow, run_id)
    if row is None:
        raise RunNotFoundError(run_id)
    return row


def get_run(db: Session, run_id: str) -> RunOut:
    return _out(_get(db, run_id))


def start_run(db: Session, req: StartRunRequest) -> RunOut:
    if db.get(RepositoryRow, req.repository_id) is None:
        raise RepositoryMissingError(req.repository_id)
    row = RunRow(
        id=str(uuid.uuid4()),
        repository_id=req.repository_id,
        mode=req.mode.value,
        status=RunStatus.QUEUED.value,
        cancel_requested=False,
    )
    db.add(row)
    db.flush()
    append_event(
        db,
        row.id,
        EventType.RUN_STARTED,
        {"mode": row.mode, "repository_id": row.repository_id},
    )
    db.commit()
    return _out(row)


def transition(
    db: Session,
    run_id: str,
    status: RunStatus,
    stage: Stage | None = None,
    payload: dict | None = None,
    error: str | None = None,
) -> RunOut:
    """Workflow entry point for status changes. Terminal statuses are final."""
    row = _get(db, run_id)
    if RunStatus(row.status) in TERMINAL_STATUSES:
        raise InvalidStateError(f"Run is already {row.status}.")
    row.status = status.value
    if stage is not None:
        row.current_stage = stage.value
    if error is not None:
        row.error = error
    row.updated_at = _now()
    if status in TERMINAL_STATUSES:
        row.completed_at = row.updated_at
    body = {"stage": row.current_stage, "status": status.value, **(payload or {})}
    if error is not None:
        body["error"] = error
    append_event(db, run_id, _STATUS_EVENT[status], body)
    db.commit()
    return _out(row)


def set_roadmap(db: Session, run_id: str, version: int, digest: str) -> None:
    row = _get(db, run_id)
    row.roadmap_version = version
    row.patch_set_digest = digest
    row.updated_at = _now()
    db.commit()


def set_pinned_commit(db: Session, run_id: str, sha: str | None) -> None:
    row = _get(db, run_id)
    row.pinned_commit = sha
    db.commit()


def is_cancel_requested(db: Session, run_id: str) -> bool:
    row = _get(db, run_id)
    return row.cancel_requested or RunStatus(row.status) is RunStatus.CANCELLED


def log_event(db: Session, run_id: str, type_: EventType, payload: dict) -> None:
    _get(db, run_id)
    append_event(db, run_id, type_, payload)
    db.commit()


def approve(db: Session, run_id: str, req: ApprovalRequest) -> RunOut:
    """Record a decision. Valid only while the run awaits approval for the stated roadmap."""
    row = _get(db, run_id)
    if RunStatus(row.status) is not RunStatus.AWAITING_APPROVAL:
        raise InvalidStateError(
            f"Run is {row.status}; approval is only valid while AWAITING_APPROVAL."
        )
    if row.roadmap_version is None:
        raise InvalidStateError("Run has no roadmap to approve.")
    if req.roadmap_version != row.roadmap_version:
        raise InvalidStateError(
            f"roadmap_version must equal the awaited version ({row.roadmap_version})."
        )
    db.add(
        ApprovalRow(
            id=str(uuid.uuid4()),
            run_id=run_id,
            decision=req.decision,
            note=req.note,
            roadmap_version=row.roadmap_version,
            patch_set_digest=row.patch_set_digest,
            actor=LOCAL_ACTOR,
        )
    )
    append_event(
        db,
        run_id,
        EventType.APPROVAL_RECEIVED,
        {
            "decision": req.decision,
            "note": req.note,
            "roadmap_version": row.roadmap_version,
            "patch_set_digest": row.patch_set_digest,
            "excluded_task_ids": req.excluded_task_ids,
            "actor": LOCAL_ACTOR,
        },
    )
    row.updated_at = _now()
    if req.decision == "reject":
        row.status = RunStatus.CANCELLED.value
        row.completed_at = row.updated_at
        append_event(
            db,
            run_id,
            EventType.RUN_CANCELLED,
            {"stage": row.current_stage, "status": row.status, "reason": "rejected"},
        )
    else:
        row.status = RunStatus.RUNNING.value
        append_event(
            db,
            run_id,
            EventType.STAGE_CHANGED,
            {"stage": row.current_stage, "status": row.status},
        )
    db.commit()
    return _out(row)


def cancel(db: Session, run_id: str) -> RunOut:
    """Waiting runs are cancelled immediately. A RUNNING run gets a cancel request that the
    workflow honours between steps, so in-flight work is never killed mid-write."""
    row = _get(db, run_id)
    status = RunStatus(row.status)
    if status in TERMINAL_STATUSES:
        raise InvalidStateError(f"Run is already {row.status}.")
    row.cancel_requested = True
    row.updated_at = _now()
    if status is RunStatus.RUNNING:
        append_event(db, run_id, EventType.CANCEL_REQUESTED, {"stage": row.current_stage})
    else:
        row.status = RunStatus.CANCELLED.value
        row.completed_at = row.updated_at
        append_event(
            db,
            run_id,
            EventType.RUN_CANCELLED,
            {"stage": row.current_stage, "status": row.status, "reason": "cancelled"},
        )
    db.commit()
    return _out(row)


def add_artifact(
    db: Session, run_id: str, type_: str, name: str, path: Path, content: bytes
) -> ArtifactOut:
    """Record a stored artifact. The caller has already written `content` to `path`."""
    _get(db, run_id)
    row = ArtifactRow(
        id=str(uuid.uuid4()),
        run_id=run_id,
        kind=type_,
        name=name,
        path=str(path),
        checksum=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    db.add(row)
    db.commit()
    return _artifact_out(row)


def _artifact_out(r: ArtifactRow) -> ArtifactOut:
    return ArtifactOut(
        id=r.id, type=r.kind, name=r.name, checksum=r.checksum, size=r.size, created_at=r.created_at
    )


def list_artifacts(db: Session, run_id: str) -> list[ArtifactOut]:
    _get(db, run_id)
    rows = db.scalars(
        select(ArtifactRow).where(ArtifactRow.run_id == run_id).order_by(ArtifactRow.created_at)
    ).all()
    return [_artifact_out(r) for r in rows]


def read_artifact(db: Session, run_id: str, artifact_id: str) -> tuple[ArtifactOut, bytes]:
    """Return an artifact and its bytes after verifying the stored checksum."""
    _get(db, run_id)
    row = db.get(ArtifactRow, artifact_id)
    if row is None or row.run_id != run_id:
        raise ArtifactNotFoundError(artifact_id)
    data = Path(row.path).read_bytes()
    if hashlib.sha256(data).hexdigest() != row.checksum:
        raise InvalidStateError("Artifact checksum mismatch; the stored file was modified.")
    return _artifact_out(row), data


def approvals_for(db: Session, run_id: str) -> list[dict]:
    rows = db.scalars(
        select(ApprovalRow).where(ApprovalRow.run_id == run_id).order_by(ApprovalRow.created_at)
    ).all()
    return [
        {
            "decision": r.decision,
            "note": r.note,
            "roadmap_version": r.roadmap_version,
            "patch_set_digest": r.patch_set_digest,
            "actor": r.actor,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def json_bytes(obj: object) -> bytes:
    return json.dumps(obj, indent=2, sort_keys=True, default=str).encode()
