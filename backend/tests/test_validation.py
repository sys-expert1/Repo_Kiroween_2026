from __future__ import annotations

from pathlib import Path

import pytest

from app.repositories.paths import PathValidationError, resolve_local_path
from app.repositories.urls import UrlValidationError, validate_git_url


def test_path_inside_allowed_root_ok(allowed_root: Path):
    project = allowed_root / "proj"
    project.mkdir()
    assert resolve_local_path(str(project), [allowed_root.resolve()]) == project.resolve()


def test_allowed_root_itself_ok(allowed_root: Path):
    assert resolve_local_path(str(allowed_root), [allowed_root.resolve()]) == allowed_root.resolve()


@pytest.mark.parametrize("raw", ["", "   ", "a\x00b"])
def test_empty_or_nul_rejected(raw: str, allowed_root: Path):
    with pytest.raises(PathValidationError):
        resolve_local_path(raw, [allowed_root.resolve()])


def test_dotdot_rejected_even_if_it_stays_inside(allowed_root: Path):
    (allowed_root / "a").mkdir()
    (allowed_root / "b").mkdir()
    with pytest.raises(PathValidationError, match="traversal"):
        resolve_local_path(str(allowed_root / "a" / ".." / "b"), [allowed_root.resolve()])


def test_outside_root_rejected(tmp_path: Path, allowed_root: Path):
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(PathValidationError, match="outside"):
        resolve_local_path(str(other), [allowed_root.resolve()])


def test_sibling_with_shared_prefix_rejected(tmp_path: Path, allowed_root: Path):
    sibling = tmp_path / "allowed-evil"
    sibling.mkdir()
    with pytest.raises(PathValidationError, match="outside"):
        resolve_local_path(str(sibling), [allowed_root.resolve()])


def test_symlink_escape_rejected(tmp_path: Path, allowed_root: Path):
    outside = tmp_path / "secret"
    outside.mkdir()
    (allowed_root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathValidationError, match="outside"):
        resolve_local_path(str(allowed_root / "link"), [allowed_root.resolve()])


def test_file_and_missing_rejected(allowed_root: Path):
    f = allowed_root / "f.txt"
    f.write_text("x")
    with pytest.raises(PathValidationError, match="not a directory"):
        resolve_local_path(str(f), [allowed_root.resolve()])
    with pytest.raises(PathValidationError, match="does not exist"):
        resolve_local_path(str(allowed_root / "nope"), [allowed_root.resolve()])


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo.git",
        "https://gitlab.com/group/sub/project",
        "ssh://git@github.com/org/repo.git",
        "git@github.com:org/repo.git",
    ],
)
def test_valid_git_urls(url: str):
    assert validate_git_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///etc/passwd",
        "ext::sh -c 'touch /tmp/pwn'",
        "--upload-pack=touch /tmp/pwn",
        "-oProxyCommand=evil",
        "http://github.com/org/repo",
        "https://github.com/org/repo with space",
        "https://github.com/../etc",
        "/local/path",
        "git://github.com/org/repo",
    ],
)
def test_invalid_git_urls(url: str):
    with pytest.raises(UrlValidationError):
        validate_git_url(url)
