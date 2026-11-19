"""Connects run lifecycle (database) with the workflow runner (LangGraph).

Keeps API handlers free of business logic and keeps `runs.service` free of LangGraph.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.roadmap import Roadmap, RoadmapEditError, revise_roadmap
from app.db import ArtifactRow
from app.graph.runner import WorkflowRunner
from app.runs import service
from app.schemas import ApprovalRequest, RunOut, RunStatus, StartRunRequest


class InvalidDecisionError(ValueError):
    """The decision is well-formed but not acceptable for this roadmap (HTTP 422)."""


class RunOrchestrator:
    def __init__(self, runner: WorkflowRunner) -> None:
        self._runner = runner

    def start(self, db: Session, req: StartRunRequest) -> RunOut:
        run = service.start_run(db, req)
        self._runner.start(run.id, req.mode.value)
        return service.get_run(db, run.id)

    def approve(self, db: Session, run_id: str, req: ApprovalRequest) -> RunOut:
        run = service.get_run(db, run_id)
        if req.decision == "edit" and run.status is RunStatus.AWAITING_APPROVAL:
            self._validate_edit(db, run_id, req)
        out = service.approve(db, run_id, req)
        self._runner.resume(
            run_id,
            {"decision": req.decision, "excluded_task_ids": req.excluded_task_ids},
        )
        return service.get_run(db, out.id)

    def cancel(self, db: Session, run_id: str) -> RunOut:
        was_waiting = service.get_run(db, run_id).status is RunStatus.AWAITING_APPROVAL
        out = service.cancel(db, run_id)
        if was_waiting:
            # Let the paused graph finish cleanly; the run is already CANCELLED.
            self._runner.resume(run_id, {"decision": "reject"})
        return out

    @staticmethod
    def _validate_edit(db: Session, run_id: str, req: ApprovalRequest) -> None:
        row = db.scalars(
            select(ArtifactRow)
            .where(ArtifactRow.run_id == run_id, ArtifactRow.kind == "roadmap_data")
            .order_by(ArtifactRow.created_at.desc())
        ).first()
        if row is None:
            raise InvalidDecisionError("No roadmap exists to edit.")
        roadmap = Roadmap(**json.loads(Path(row.path).read_text()))
        try:
            revise_roadmap(roadmap, req.excluded_task_ids)
        except RoadmapEditError as exc:
            raise InvalidDecisionError(str(exc)) from None
