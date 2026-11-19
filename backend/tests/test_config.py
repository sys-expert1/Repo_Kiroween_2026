from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_settings


def test_default_allows_only_fixtures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("NECRO_ALLOWED_ROOTS", raising=False)
    monkeypatch.setenv("NECRO_DATA_DIR", str(tmp_path))
    s = load_settings()
    assert s.allowed_roots == (s.fixtures_dir,)


@pytest.mark.parametrize("root", ["/", str(Path.home())])
def test_root_and_home_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root: str):
    monkeypatch.setenv("NECRO_ALLOWED_ROOTS", root)
    monkeypatch.setenv("NECRO_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="Refusing"):
        load_settings()


def test_extra_root_accepted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    extra = tmp_path / "work"
    extra.mkdir()
    monkeypatch.setenv("NECRO_ALLOWED_ROOTS", str(extra))
    monkeypatch.setenv("NECRO_DATA_DIR", str(tmp_path / "d"))
    assert extra.resolve() in load_settings().allowed_roots
