"""Artifact storage: files under the run directory plus a database record with a checksum."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.config import Settings
from app.runs import service
from app.schemas import ArtifactOut
from app.workspace import run_dir

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def write_artifact(
    settings: Settings, db: Session, run_id: str, type_: str, name: str, content: bytes
) -> ArtifactOut:
    if not _NAME.match(name):
        raise ValueError(f"Invalid artifact name: {name!r}")
    directory = run_dir(settings, run_id) / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content)
    return service.add_artifact(db, run_id, type_, name, path, content)
