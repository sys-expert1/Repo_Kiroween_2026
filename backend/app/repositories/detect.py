"""Read-only file-tree detection. Does not execute repository code or run git."""

from __future__ import annotations

import os
from pathlib import Path

from app.schemas import LanguageStat, RepositorySignals

MAX_FILES = 20_000
MAX_BYTES_PER_FILE = 2_000_000
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", "dist", "build",
}
CI_MARKERS = {".gitlab-ci.yml", ".travis.yml", "Jenkinsfile", ".circleci"}
LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".go": "Go",
    ".rb": "Ruby",
    ".php": "PHP",
    ".rs": "Rust",
    ".c": "C",
    ".cpp": "C++",
    ".cs": "C#",
}
DEPENDENCY_FILES = {
    "requirements.txt",
    "requirements-dev.txt",
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "Pipfile",
    "poetry.lock",
    "package.json",
    "pom.xml",
    "build.gradle",
    "go.mod",
    "Gemfile",
    "Cargo.toml",
    "composer.json",
}


def _count_lines(path: Path) -> int:
    try:
        if path.stat().st_size > MAX_BYTES_PER_FILE:
            return 0
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _is_test_file(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(".py") and (lower.startswith("test_") or lower.endswith("_test.py"))


def detect_signals(root: Path) -> RepositorySignals:
    stats: dict[str, list[int]] = {}
    dep_files: list[str] = []
    test_files = 0
    scanned = 0
    truncated = False
    has_docker = False
    has_ci = False
    root_entries = {p.name for p in root.iterdir()} if root.is_dir() else set()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = Path(dirpath).relative_to(root)
        for fname in sorted(filenames):
            if scanned >= MAX_FILES:
                truncated = True
                break
            full = Path(dirpath) / fname
            if full.is_symlink():
                continue
            scanned += 1
            rel = (rel_dir / fname).as_posix()
            if fname in DEPENDENCY_FILES:
                dep_files.append(rel)
            if fname == "Dockerfile" or fname.endswith(".Dockerfile"):
                has_docker = True
            if _is_test_file(fname):
                test_files += 1
            lang = LANGUAGES.get(Path(fname).suffix.lower())
            if lang:
                entry = stats.setdefault(lang, [0, 0])
                entry[0] += 1
                entry[1] += _count_lines(full)
        if truncated:
            break

    if (root / ".github" / "workflows").is_dir() or CI_MARKERS & root_entries:
        has_ci = True

    languages = sorted(
        (LanguageStat(language=k, files=v[0], lines=v[1]) for k, v in stats.items()),
        key=lambda s: (-s.lines, s.language),
    )
    return RepositorySignals(
        languages=languages,
        primary_language=languages[0].language if languages else None,
        dependency_files=sorted(dep_files),
        has_tests=test_files > 0,
        test_files=test_files,
        has_dockerfile=has_docker,
        has_ci=has_ci,
        is_git_repo=".git" in root_entries,
        files_scanned=scanned,
        truncated=truncated,
    )
