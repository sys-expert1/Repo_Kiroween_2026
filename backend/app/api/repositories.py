from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.config import Settings
from app.repositories import service
from app.schemas import ErrorOut, FixtureOut, InspectResult, RepositoryOut, RepositorySource

router = APIRouter(prefix="/api")

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]


@router.post(
    "/repositories/inspect",
    response_model=InspectResult,
    responses={422: {"model": ErrorOut}},
)
def inspect(source: RepositorySource, settings: SettingsDep) -> InspectResult:
    try:
        return service.inspect_repository(source, settings)
    except service.IntakeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post(
    "/repositories/import",
    response_model=RepositoryOut,
    status_code=201,
    responses={422: {"model": ErrorOut}},
)
def import_repo(source: RepositorySource, settings: SettingsDep, db: DbDep) -> RepositoryOut:
    try:
        return service.import_repository(source, settings, db)
    except service.IntakeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/repositories", response_model=list[RepositoryOut])
def list_repos(db: DbDep) -> list[RepositoryOut]:
    return service.list_repositories(db)


@router.get(
    "/repositories/{repo_id}",
    response_model=RepositoryOut,
    responses={404: {"model": ErrorOut}},
)
def get_repo(repo_id: str, db: DbDep) -> RepositoryOut:
    try:
        return service.get_repository(repo_id, db)
    except service.NotFoundError:
        raise HTTPException(status_code=404, detail="Repository not found.") from None


@router.get("/fixtures", response_model=list[FixtureOut])
def fixtures(settings: SettingsDep) -> list[FixtureOut]:
    from app.fixtures import list_fixtures

    return list_fixtures(settings.fixtures_dir)
