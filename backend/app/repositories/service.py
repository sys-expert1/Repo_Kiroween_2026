"""Repository intake business logic. No HTTP concerns here."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import RepositoryRow
from app.fixtures import fixture_path
from app.repositories.detect import detect_signals
from app.repositories.paths import PathValidationError, resolve_local_path
from app.repositories.urls import UrlValidationError, validate_git_url
from app.schemas import (
    InspectResult,
    RepositoryOut,
    RepositorySignals,
    RepositorySource,
    SourceType,
)


class IntakeError(ValueError):
    """Invalid user input (maps to HTTP 422)."""


class NotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class _Resolved:
    source_type: SourceType
    name: str
    location: str
    path: Path | None


def _resolve(source: RepositorySource, settings: Settings) -> _Resolved:
    try:
        if source.git_url:
            url = validate_git_url(source.git_url)
            name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or url
            return _Resolved(SourceType.GIT, name, url, None)
        if source.fixture_id:
            try:
                path = fixture_path(settings.fixtures_dir, source.fixture_id)
            except KeyError:
                raise IntakeError("Unknown fixture.") from None
            return _Resolved(SourceType.FIXTURE, source.fixture_id, str(path), path)
        assert source.local_path
        path = resolve_local_path(source.local_path, settings.allowed_roots)
        return _Resolved(SourceType.LOCAL, path.name, str(path), path)
    except (PathValidationError, UrlValidationError) as exc:
        raise IntakeError(str(exc)) from None


def _signals(resolved: _Resolved) -> RepositorySignals | None:
    return detect_signals(resolved.path) if resolved.path else None


def inspect_repository(source: RepositorySource, settings: Settings) -> InspectResult:
    resolved = _resolve(source, settings)
    return InspectResult(
        source_type=resolved.source_type,
        display_name=resolved.name,
        location=resolved.location,
        signals=_signals(resolved),
    )


def _to_out(row: RepositoryRow) -> RepositoryOut:
    return RepositoryOut(
        id=row.id,
        name=row.name,
        source_type=SourceType(row.source_type),
        location=row.location,
        created_at=row.created_at,
        signals=row.signals_dict(),
    )


def import_repository(source: RepositorySource, settings: Settings, db: Session) -> RepositoryOut:
    resolved = _resolve(source, settings)
    existing = db.scalar(
        select(RepositoryRow).where(
            RepositoryRow.source_type == resolved.source_type.value,
            RepositoryRow.location == resolved.location,
        )
    )
    signals = _signals(resolved)
    if existing:
        existing.signals_json = signals.model_dump_json() if signals else None
        db.commit()
        return _to_out(existing)
    row = RepositoryRow(
        id=str(uuid.uuid4()),
        name=resolved.name,
        source_type=resolved.source_type.value,
        location=resolved.location,
        signals_json=signals.model_dump_json() if signals else None,
    )
    db.add(row)
    db.commit()
    return _to_out(row)


def list_repositories(db: Session) -> list[RepositoryOut]:
    rows = db.scalars(select(RepositoryRow).order_by(RepositoryRow.created_at.desc())).all()
    return [_to_out(r) for r in rows]


def get_repository(repo_id: str, db: Session) -> RepositoryOut:
    row = db.get(RepositoryRow, repo_id)
    if row is None:
        raise NotFoundError(repo_id)
    return _to_out(row)
