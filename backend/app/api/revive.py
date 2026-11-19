from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_orchestrator
from app.runs import service
from app.runs.events import events_after, format_sse
from app.runs.orchestrator import InvalidDecisionError, RunOrchestrator
from app.schemas import (
    TERMINAL_STATUSES,
    ApprovalRequest,
    ArtifactOut,
    ErrorOut,
    RunOut,
    StartRunRequest,
)

router = APIRouter(prefix="/api/revive")

DbDep = Annotated[Session, Depends(get_db)]
OrchDep = Annotated[RunOrchestrator, Depends(get_orchestrator)]
HEARTBEAT_SECONDS = 15.0


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Run not found.")


@router.get("", response_model=list[RunOut])
def list_runs(
    db: DbDep,
    repository_id: Annotated[str | None, Query(max_length=36)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RunOut]:
    """Recent runs, newest first. Additive to the blueprint contract; used to restore the UI."""
    return service.list_runs(db, repository_id, limit)


@router.get("/{run_id}", response_model=RunOut, responses={404: {"model": ErrorOut}})
def get_run(run_id: str, db: DbDep) -> RunOut:
    try:
        return service.get_run(db, run_id)
    except service.RunNotFoundError:
        raise _not_found() from None


@router.post(
    "/start",
    response_model=RunOut,
    status_code=201,
    responses={404: {"model": ErrorOut}},
)
def start(req: StartRunRequest, db: DbDep, orch: OrchDep) -> RunOut:
    try:
        return orch.start(db, req)
    except service.RepositoryMissingError:
        raise HTTPException(status_code=404, detail="Repository not found.") from None


@router.post(
    "/approve/{run_id}",
    response_model=RunOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
)
def approve(run_id: str, req: ApprovalRequest, db: DbDep, orch: OrchDep) -> RunOut:
    try:
        return orch.approve(db, run_id, req)
    except service.RunNotFoundError:
        raise _not_found() from None
    except service.InvalidStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except InvalidDecisionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post(
    "/{run_id}/cancel",
    response_model=RunOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def cancel(run_id: str, db: DbDep, orch: OrchDep) -> RunOut:
    try:
        return orch.cancel(db, run_id)
    except service.RunNotFoundError:
        raise _not_found() from None
    except service.InvalidStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get(
    "/{run_id}/artifacts",
    response_model=list[ArtifactOut],
    responses={404: {"model": ErrorOut}},
)
def artifacts(run_id: str, db: DbDep) -> list[ArtifactOut]:
    try:
        return service.list_artifacts(db, run_id)
    except service.RunNotFoundError:
        raise _not_found() from None


_MEDIA_TYPES = {".md": "text/markdown", ".json": "application/json", ".diff": "text/x-diff"}


@router.get(
    "/{run_id}/artifacts/{artifact_id}",
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def download_artifact(run_id: str, artifact_id: str, db: DbDep) -> Response:
    """Return one artifact's bytes after verifying its recorded checksum."""
    try:
        artifact, data = service.read_artifact(db, run_id, artifact_id)
    except service.RunNotFoundError:
        raise _not_found() from None
    except service.ArtifactNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found.") from None
    except service.InvalidStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    media = _MEDIA_TYPES.get(Path(artifact.name).suffix, "application/octet-stream")
    return Response(
        content=data,
        media_type=media,
        headers={"X-Content-Type-Options": "nosniff", "X-Artifact-Checksum": artifact.checksum},
    )


@router.get("/stream/{run_id}", responses={404: {"model": ErrorOut}})
async def stream(
    run_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
    after: Annotated[int | None, Query(ge=0, description="Replay events with seq > after")] = None,
) -> StreamingResponse:
    """Server-Sent Events. Resume with the `Last-Event-ID` header (browsers send it on
    reconnect) or the `after` query parameter."""
    cursor = after if after is not None else 0
    if after is None and last_event_id:
        try:
            cursor = int(last_event_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Last-Event-ID.") from None
        if cursor < 0:
            raise HTTPException(status_code=400, detail="Invalid Last-Event-ID.")

    session_factory = request.app.state.session_factory
    poll = request.app.state.sse_poll_seconds

    def fetch(seq: int):
        with session_factory() as db:
            try:
                run = service.get_run(db, run_id)
            except service.RunNotFoundError:
                return None, []
            return run, events_after(db, run_id, seq)

    run, _ = await asyncio.to_thread(fetch, cursor)
    if run is None:
        raise _not_found()

    async def gen() -> AsyncIterator[str]:
        seq = cursor
        idle = 0.0
        yield "retry: 2000\n\n"
        while True:
            run, events = await asyncio.to_thread(fetch, seq)
            for event in events:
                seq = event.sequence
                idle = 0.0
                yield format_sse(event)
            if run is None or (run.status in TERMINAL_STATUSES and not events):
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(poll)
            idle += poll
            if idle >= HEARTBEAT_SECONDS:
                idle = 0.0
                yield ": heartbeat\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
