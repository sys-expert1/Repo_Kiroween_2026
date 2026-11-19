"""Managed workspace: per-run directories, snapshots and commit pinning.

The source repository is read, never written. A snapshot is a copy of the working tree (so
uncommitted changes are preserved in the copy) without `.git`. Symlinks are never copied
or followed.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from app.config import Settings
from app.execution.runner import build_env, run_command

SKIP_NAMES = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".tox"}
MAX_FILES = 50_000
MAX_BYTES = 500 * 1024**2
CLONE_TIMEOUT_SECONDS = 300
_SHA = re.compile(r"^[0-9a-f]{40}$")


class WorkspaceError(RuntimeError):
    pass


def run_dir(settings: Settings, run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", run_id):
        raise WorkspaceError("Invalid run id.")
    path = settings.data_dir / "runs" / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_head_sha(repo: Path) -> str | None:
    """Resolve HEAD by reading files under `.git`. Never runs git (hooks and fsmonitor settings in
    an untrusted `.git/config` can execute commands)."""
    git = repo / ".git"
    try:
        if not git.is_dir() or git.is_symlink():
            return None
        head = (git / "HEAD").read_text().strip()
        if _SHA.match(head):
            return head
        if not head.startswith("ref: "):
            return None
        ref = head[5:].strip()
        if ".." in ref or ref.startswith("/"):
            return None
        ref_file = git / ref
        if ref_file.is_file() and not ref_file.is_symlink():
            value = ref_file.read_text().strip()
            return value if _SHA.match(value) else None
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                parts = line.split(" ", 1)
                if len(parts) == 2 and parts[1].strip() == ref and _SHA.match(parts[0]):
                    return parts[0]
    except OSError:
        return None
    return None


def snapshot_tree(source: Path, dest: Path) -> None:
    """Copy the working tree of `source` into `dest`, preserving uncommitted changes.

    Symlinks are skipped entirely: a link in the copy that points outside the repository would
    be followed by the analysis tools. Limits bound file count and total size.
    """
    if dest.exists():
        raise WorkspaceError("Snapshot destination already exists.")
    files = 0
    total = 0
    dest.mkdir(parents=True)
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_NAMES and not (Path(dirpath) / d).is_symlink()
        )
        rel = Path(dirpath).relative_to(source)
        (dest / rel).mkdir(parents=True, exist_ok=True)
        for name in sorted(filenames):
            src = Path(dirpath) / name
            target = dest / rel / name
            if src.is_symlink():
                continue
            files += 1
            total += src.stat().st_size
            if files > MAX_FILES or total > MAX_BYTES:
                shutil.rmtree(dest, ignore_errors=True)
                raise WorkspaceError("Repository exceeds the snapshot size limit.")
            shutil.copy2(src, target)


def clone_git(url: str, dest: Path, work_home: Path) -> str:
    """Shallow-clone a validated URL into `dest` and return the pinned commit SHA.

    Hooks and non-https/ssh transports are disabled, prompts are off, no credential helper is
    used, and `.git` is removed after the SHA is read.
    """
    if dest.exists():
        raise WorkspaceError("Clone destination already exists.")
    work_home.mkdir(parents=True, exist_ok=True)
    argv = [
        "git",
        "-c", "core.hooksPath=/dev/null",
        "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always",
        "-c", "protocol.ssh.allow=always",
        "-c", "credential.helper=",
        "clone", "--depth", "1", "--no-tags", "--single-branch", "--", url, str(dest),
    ]  # fmt: skip
    result = run_command(argv, work_home, CLONE_TIMEOUT_SECONDS, build_env(work_home))
    if result.exit_code != 0:
        shutil.rmtree(dest, ignore_errors=True)
        detail = (result.stderr or result.error or "git clone failed").strip().splitlines()
        raise WorkspaceError(f"Clone failed: {detail[-1] if detail else 'unknown error'}")
    sha = read_head_sha(dest)
    if sha is None:
        shutil.rmtree(dest, ignore_errors=True)
        raise WorkspaceError("Could not resolve the cloned commit.")
    shutil.rmtree(dest / ".git", ignore_errors=True)
    return sha
