"""Run lifecycle: creation, approval, cancellation. No HTTP concerns here.

The workflow executor (phase 4) will drive status/phase through `transition`. Until then a
run stays QUEUED.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import ApprovalRow, ArtifactRow, RepositoryRow, RunRow
from app.runs.events import append_event
from app.schemas import (
    TERMINAL_STATUSES,
    ApprovalRequest,
    ArtifactOut,
    ExecutionMode,
    Phase,
    RunOut,
    RunStatus,
    StartRunRequest,
)


class RunNotFoundError(LookupError):
    pass


class RepositoryMissingError(LookupError):
    pass


class InvalidStateError(RuntimeError):
    """The requested action is not allowed in the run's current status (HTTP 409)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _out(row: RunRow) -> RunOut:
    return RunOut(
        id=row.id,
        repository_id=row.repository_id,
        mode=ExecutionMode(row.mode),
        status=RunStatus(row.status),
        phase=Phase(row.phase) if row.phase else None,
        cancel_requested=row.cancel_requested,
        created_at=row.created_at,
        updated_at=row.updated_at,
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
    append_event(db, row.id, "run.created", {"mode": row.mode, "repository_id": row.repository_id})
    db.commit()
    return _out(row)


def transition(
    db: Session,
    run_id: str,
    status: RunStatus,
    phase: Phase | None = None,
    data: dict | None = None,
) -> RunOut:
    """Executor entry point. Terminal statuses are final."""
    row = _get(db, run_id)
    if RunStatus(row.status) in TERMINAL_STATUSES:
        raise InvalidStateError(f"Run is already {row.status}.")
    row.status = status.value
    if phase is not None:
        row.phase = phase.value
    row.updated_at = _now()
    payload = {"status": status.value, "phase": row.phase, **(data or {})}
    append_event(db, run_id, "run.status", payload)
    db.commit()
    return _out(row)


def approve(db: Session, run_id: str, req: ApprovalRequest) -> RunOut:
    row = _get(db, run_id)
    if RunStatus(row.status) is not RunStatus.AWAITING_APPROVAL:
        raise InvalidStateError(
            f"Run is {row.status}; approval is only valid while AWAITING_APPROVAL."
        )
    db.add(
        ApprovalRow(id=str(uuid.uuid4()), run_id=run_id, decision=req.decision, note=req.note)
    )
    append_event(db, run_id, "approval.recorded", {"decision": req.decision, "note": req.note})
    row.status = (RunStatus.RUNNING if req.decision == "approve" else RunStatus.CANCELLED).value
    row.updated_at = _now()
    append_event(db, run_id, "run.status", {"status": row.status, "phase": row.phase})
    db.commit()
    return _out(row)


def cancel(db: Session, run_id: str) -> RunOut:
    """Waiting runs are cancelled immediately. A RUNNING run gets a cancel request that the
    executor honours between steps, so in-flight work is never killed mid-write."""
    row = _get(db, run_id)
    status = RunStatus(row.status)
    if status in TERMINAL_STATUSES:
        raise InvalidStateError(f"Run is already {row.status}.")
    row.cancel_requested = True
    row.updated_at = _now()
    if status is RunStatus.RUNNING:
        append_event(db, run_id, "run.cancel_requested", {})
    else:
        row.status = RunStatus.CANCELLED.value
        append_event(db, run_id, "run.status", {"status": row.status, "phase": row.phase})
    db.commit()
    return _out(row)


def list_artifacts(db: Session, run_id: str) -> list[ArtifactOut]:
    _get(db, run_id)
    rows = db.scalars(
        select(ArtifactRow).where(ArtifactRow.run_id == run_id).order_by(ArtifactRow.created_at)
    ).all()
    return [ArtifactOut(id=r.id, kind=r.kind, name=r.name, created_at=r.created_at) for r in rows]
