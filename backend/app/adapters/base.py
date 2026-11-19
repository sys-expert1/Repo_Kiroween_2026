"""Shared language adapter interface. Adapters never import the LangGraph workflow."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.adapters.models import AutopsyReport, BaselineReport
from app.execution.runner import CommandResult, CommandRunner

# Receives every command an adapter runs so the caller can stream it to the Revival Terminal.
CommandObserver = Callable[[CommandResult], None]


class LanguageAdapter(Protocol):
    language: str

    def detect(self, root: Path) -> bool:
        """True if this adapter applies to the repository at `root`."""

    def autopsy(
        self,
        root: Path,
        name: str,
        scratch: Path,
        observe: CommandObserver,
    ) -> AutopsyReport:
        """Read-only inspection. `scratch` is a directory the adapter may write temp files to."""

    def characterize(
        self,
        root: Path,
        runner: CommandRunner | None,
        observe: CommandObserver,
        timeout: float,
    ) -> BaselineReport:
        """Run baseline tests with `runner`. `root` must be a disposable copy."""

    def fix_lint(
        self, files: dict[str, str], scratch: Path, observe: CommandObserver
    ) -> dict[str, str]:
        """Apply safe automatic lint fixes to copies of `files`; return only changed files."""
