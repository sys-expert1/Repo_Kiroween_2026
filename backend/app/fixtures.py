"""Demo fixture registry. Each fixture is a directory with a fixture.json manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.schemas import FixtureOut

_ID = re.compile(r"^[a-z0-9_-]{1,64}$")


def list_fixtures(fixtures_dir: Path) -> list[FixtureOut]:
    out: list[FixtureOut] = []
    if not fixtures_dir.is_dir():
        return out
    for child in sorted(fixtures_dir.iterdir()):
        manifest = child / "fixture.json"
        if child.is_dir() and not child.is_symlink() and manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            out.append(
                FixtureOut(
                    id=child.name,
                    name=data["name"],
                    description=data["description"],
                    language=data["language"],
                )
            )
    return out


def fixture_path(fixtures_dir: Path, fixture_id: str) -> Path:
    """Return the fixture directory, or raise KeyError. The id is matched against a strict
    pattern and against the listed fixtures, so it can never address anything else."""
    if not _ID.match(fixture_id):
        raise KeyError(fixture_id)
    if fixture_id not in {f.id for f in list_fixtures(fixtures_dir)}:
        raise KeyError(fixture_id)
    return (fixtures_dir / fixture_id).resolve()
