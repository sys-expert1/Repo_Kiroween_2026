"""Persisted per-run event log. Source of truth for SSE replay."""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import RunEventRow
from app.schemas import EventType, RunEventOut


def append_event(
    db: Session, run_id: str, type_: EventType, payload: dict | None = None
) -> RunEventRow:
    """Append an event with the next sequence number. The caller commits.

    The unique (run_id, seq) constraint rejects a concurrent duplicate sequence.
    """
    last = db.scalar(select(func.max(RunEventRow.seq)).where(RunEventRow.run_id == run_id))
    row = RunEventRow(
        run_id=run_id,
        seq=(last or 0) + 1,
        type=type_.value,
        data_json=json.dumps(payload or {}, sort_keys=True, default=str),
    )
    db.add(row)
    db.flush()
    return row


def events_after(db: Session, run_id: str, after_sequence: int) -> list[RunEventOut]:
    rows = db.scalars(
        select(RunEventRow)
        .where(RunEventRow.run_id == run_id, RunEventRow.seq > after_sequence)
        .order_by(RunEventRow.seq)
    ).all()
    return [to_event_out(r) for r in rows]


def to_event_out(row: RunEventRow) -> RunEventOut:
    return RunEventOut(
        event_id=f"{row.run_id}:{row.seq}",
        run_id=row.run_id,
        sequence=row.seq,
        type=EventType(row.type),
        timestamp=row.ts,
        payload=json.loads(row.data_json),
    )


def format_sse(event: RunEventOut) -> str:
    return f"id: {event.sequence}\nevent: {event.type.value}\ndata: {event.model_dump_json()}\n\n"
