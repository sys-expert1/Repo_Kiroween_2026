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
