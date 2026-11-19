"""Local path validation. Rejects traversal, symlink escapes and paths outside allowed roots."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class PathValidationError(ValueError):
    pass


def resolve_local_path(raw: str, allowed_roots: Iterable[Path]) -> Path:
    if not raw or not raw.strip():
        raise PathValidationError("Path is empty.")
    if "\x00" in raw:
        raise PathValidationError("Path contains a NUL byte.")
    candidate = Path(raw.strip())
    if ".." in candidate.parts:
        raise PathValidationError("Path traversal ('..') is not allowed.")
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        raise PathValidationError("Path does not exist.") from None
    except (OSError, RuntimeError) as exc:
        raise PathValidationError(f"Path cannot be resolved: {exc}") from None
    if not resolved.is_dir():
        raise PathValidationError("Path is not a directory.")
    for root in allowed_roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise PathValidationError("Path is outside the allowed roots.")
