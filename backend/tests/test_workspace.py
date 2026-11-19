from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import workspace
from app.execution.runner import CommandResult
from app.workspace import WorkspaceError, clone_git, read_head_sha, snapshot_tree

SHA = "a" * 40


def make_git(repo: Path, head: str, refs: dict[str, str] | None = None, packed: str | None = None):
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text(head)
    for ref, value in (refs or {}).items():
        (repo / ".git" / ref).write_text(value + "\n")
    if packed:
        (repo / ".git" / "packed-refs").write_text(packed)


def test_head_sha_from_loose_ref(tmp_path: Path):
    make_git(tmp_path, "ref: refs/heads/main\n", {"refs/heads/main": SHA})
    assert read_head_sha(tmp_path) == SHA


def test_head_sha_detached_and_packed(tmp_path: Path):
    make_git(tmp_path, SHA + "\n")
    assert read_head_sha(tmp_path) == SHA
    other = tmp_path / "o"
    other.mkdir()
    make_git(other, "ref: refs/heads/main\n", packed=f"# pack\n{'b' * 40} refs/heads/main\n")
    assert read_head_sha(other) == "b" * 40


@pytest.mark.parametrize(
    "head", ["ref: ../../etc/passwd", "ref: /etc/passwd", "garbage", "ref: refs/heads/missing"]
)
def test_head_sha_rejects_hostile_or_unresolvable(tmp_path: Path, head: str):
    make_git(tmp_path, head)
    assert read_head_sha(tmp_path) is None


def test_head_sha_never_runs_git(tmp_path: Path, monkeypatch):
    make_git(tmp_path, SHA)
    called = []
    monkeypatch.setattr(workspace, "run_command", lambda *a, **k: called.append(a))
    read_head_sha(tmp_path)
    assert called == []


def test_snapshot_copies_uncommitted_files_excludes_git_and_leaves_source(tmp_path: Path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "mod.py").write_text("x = 1  # uncommitted edit\n")
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("[core]\nfsmonitor = evil\n")
    (src / "node_modules").mkdir()
    (src / "node_modules" / "junk.js").write_text("x")
    before = sorted(str(p.relative_to(src)) for p in src.rglob("*"))
    dest = tmp_path / "snap"
    snapshot_tree(src, dest)
    assert (dest / "pkg" / "mod.py").read_text() == "x = 1  # uncommitted edit\n"
    assert not (dest / ".git").exists() and not (dest / "node_modules").exists()
    assert sorted(str(p.relative_to(src)) for p in src.rglob("*")) == before


def test_snapshot_skips_symlinks_so_nothing_outside_is_reachable(tmp_path: Path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.txt").write_text("ok")
    (src / "link.txt").symlink_to(secret)
    (src / "dirlink").symlink_to(tmp_path, target_is_directory=True)
    dest = tmp_path / "snap"
    snapshot_tree(src, dest)
    copied = sorted(p.name for p in dest.rglob("*"))
    assert copied == ["real.txt"]
    assert not any(p.is_symlink() for p in dest.rglob("*"))


def test_snapshot_enforces_size_limit_and_cleans_up(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(workspace, "MAX_FILES", 2)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(3):
        (src / f"f{i}.txt").write_text("x")
    dest = tmp_path / "snap"
    with pytest.raises(WorkspaceError, match="limit"):
        snapshot_tree(src, dest)
    assert not dest.exists()


def test_snapshot_refuses_existing_destination(tmp_path: Path):
    (tmp_path / "s").mkdir()
    (tmp_path / "d").mkdir()
    with pytest.raises(WorkspaceError):
        snapshot_tree(tmp_path / "s", tmp_path / "d")


def test_clone_uses_hardened_options_and_strips_dot_git(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_run(argv, cwd, timeout, env, **kw):
        captured["argv"], captured["env"] = list(argv), env
        dest = Path(argv[-1])
        make_git(dest, "ref: refs/heads/main\n", {"refs/heads/main": SHA})
        (dest / "app.py").write_text("print()\n")
        return CommandResult(
            argv=list(argv), exit_code=0, stdout="", stderr="", duration_seconds=0.1
        )

    monkeypatch.setattr(workspace, "run_command", fake_run)
    dest = tmp_path / "clone"
    sha = clone_git("https://github.com/o/r.git", dest, tmp_path / "home")
    argv = captured["argv"]
    assert sha == SHA and (dest / "app.py").exists() and not (dest / ".git").exists()
    assert "core.hooksPath=/dev/null" in argv and "protocol.allow=never" in argv
    assert "credential.helper=" in argv and "--depth" in argv
    assert argv[-3:] == [
        "--",
        "https://github.com/o/r.git",
        str(dest),
    ]  # `--` blocks option injection
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_clone_failure_is_reported_and_leaves_nothing(tmp_path: Path, monkeypatch):
    def fake_run(argv, cwd, timeout, env, **kw):
        Path(argv[-1]).mkdir()
        return CommandResult(
            argv=list(argv), exit_code=128, stdout="",
            stderr="fatal: repository 'https://x/y' not found", duration_seconds=0.1,
        )  # fmt: skip

    monkeypatch.setattr(workspace, "run_command", fake_run)
    dest = tmp_path / "clone"
    with pytest.raises(WorkspaceError, match="not found"):
        clone_git("https://github.com/o/missing", dest, tmp_path / "home")
    assert not dest.exists()


def test_run_dir_rejects_unsafe_run_ids(settings):
    for bad in ("../x", "a/b", "", "x" * 65):
        with pytest.raises(WorkspaceError):
            workspace.run_dir(settings, bad)
    assert os.path.isdir(workspace.run_dir(settings, "abc-123"))
