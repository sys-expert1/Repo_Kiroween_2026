"""Choose the language adapter for a repository."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.adapters.base import LanguageAdapter


def choose_adapter(adapters: Sequence[LanguageAdapter], root: Path) -> LanguageAdapter | None:
    """The adapter with the highest score for `root`, or None. Ties go to the earlier adapter, so
    a mixed repository is analysed as its dominant language."""
    best: LanguageAdapter | None = None
    best_score = 0
    for adapter in adapters:
        score = adapter.score(root)
        if score > best_score:
            best, best_score = adapter, score
    return best
