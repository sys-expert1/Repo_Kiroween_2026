"""Pydantic v2 API contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceType(StrEnum):
    GIT = "git"
    LOCAL = "local"
    FIXTURE = "fixture"


class ExecutionMode(StrEnum):
    INSPECT = "INSPECT"
    PREVIEW = "PREVIEW"
    REVIVE = "REVIVE"


class RepositorySource(BaseModel):
    """Exactly one of git_url, local_path or fixture_id must be provided."""

    model_config = ConfigDict(extra="forbid")

    git_url: str | None = Field(default=None, max_length=2048)
    local_path: str | None = Field(default=None, max_length=4096)
    fixture_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _exactly_one(self) -> RepositorySource:
        provided = [v for v in (self.git_url, self.local_path, self.fixture_id) if v]
        if len(provided) != 1:
            raise ValueError("Provide exactly one of git_url, local_path or fixture_id.")
        return self


class LanguageStat(BaseModel):
    language: str
    files: int
    lines: int


class RepositorySignals(BaseModel):
    """Facts read from the file tree. Nothing here executes repository code."""

    languages: list[LanguageStat]
    primary_language: str | None
    dependency_files: list[str]
    has_tests: bool
    test_files: int
    has_dockerfile: bool
    has_ci: bool
    is_git_repo: bool
    files_scanned: int
    truncated: bool


class InspectResult(BaseModel):
    source_type: SourceType
    display_name: str
    location: str
    # Null for Git URLs: nothing is cloned during inspect.
    signals: RepositorySignals | None


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    source_type: SourceType
    location: str
    created_at: datetime
    signals: RepositorySignals | None


class FixtureOut(BaseModel):
    id: str
    name: str
    description: str
    language: Literal["python"]


class Phase(StrEnum):
    AUTOPSY = "AUTOPSY"
    CHARACTERIZATION = "CHARACTERIZATION"
    REANIMATION = "REANIMATION"
    CONSECRATION = "CONSECRATION"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: str = Field(min_length=1, max_length=36)
    mode: ExecutionMode = ExecutionMode.INSPECT


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)


class RunOut(BaseModel):
    id: str
    repository_id: str
    mode: ExecutionMode
    status: RunStatus
    phase: Phase | None
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime


class RunEventOut(BaseModel):
    """Payload of one SSE event. The SSE `id:` field is `seq`."""

    event_id: str
    run_id: str
    seq: int
    ts: datetime
    type: str
    data: dict


class ArtifactOut(BaseModel):
    id: str
    kind: str
    name: str
    created_at: datetime


class ErrorOut(BaseModel):
    detail: str
