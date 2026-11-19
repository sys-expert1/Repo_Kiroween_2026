from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import FIXTURES_DIR, Settings
from app.main import create_app


@pytest.fixture
def allowed_root(tmp_path: Path) -> Path:
    root = tmp_path / "allowed"
    root.mkdir()
    return root


@pytest.fixture
def settings(tmp_path: Path, allowed_root: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        allowed_roots=(allowed_root.resolve(), FIXTURES_DIR.resolve()),
        fixtures_dir=FIXTURES_DIR.resolve(),
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
