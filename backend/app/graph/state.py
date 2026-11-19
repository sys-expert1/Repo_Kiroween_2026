"""Typed LangGraph state and the shared workflow context."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from sqlalchemy.orm import Session, sessionmaker

from app.adapters.base import LanguageAdapter
from app.config import Settings
from app.execution.runner import CommandRunner


class WorkflowState(TypedDict, total=False):
    """Serializable state persisted in every checkpoint. Large results live in artifacts too."""

    run_id: str
    mode: str  # INSPECT | PREVIEW | REVIVE
    repository_name: str
    source_type: str
    snapshot_dir: str
    commit: str | None
    autopsy: dict
    baseline: dict
    roadmap: dict
    patchset: dict
    score_before: dict
    work_dir: str
    applied: list[str]
    decision: dict
    halt: bool


class WorkflowError(RuntimeError):
    """A deterministic failure that must not be retried."""


# Returns a runner for a source type, or None when no sandbox may execute that source.
RunnerFactory = Callable[[str, Path], CommandRunner | None]


@dataclass
class WorkflowContext:
    settings: Settings
    session_factory: sessionmaker[Session]
    adapter: LanguageAdapter
    runner_for: RunnerFactory
    baseline_timeout: float = 300.0
    # Explains why a source type has no runner (shown in the report instead of a bare "not run").
    no_runner_reason: Callable[[str], str] = lambda source_type: (  # noqa: E731
        "No sandbox is available for this repository, so its tests were not run."
    )
