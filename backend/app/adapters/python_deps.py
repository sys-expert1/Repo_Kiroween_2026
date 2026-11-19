"""Dependency manifest parsing. Static only: setup.py and other code are never executed."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from app.adapters.models import Dependency

LOCKFILES = {"poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock"}
_REQ = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*"
    r"(?P<spec>(?:(?:<=|>=|==|!=|~=|===|<|>)\s*[^;#,\s]+\s*,?\s*)+)?"
)
_EXACT = re.compile(r"^===?\s*(?P<ver>[0-9][A-Za-z0-9._+!-]*)$")
_PLAIN_VERSION = re.compile(r"^[0-9]+(\.[0-9]+)*$")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pinned(spec: str) -> str | None:
    spec = spec.strip().rstrip(",").strip()
    match = _EXACT.match(spec)
    if match and "*" not in spec:
        return match.group("ver")
    return None


def parse_requirement_line(line: str, source_file: str) -> Dependency | None:
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith("-") or "://" in line or " @ " in line:
        return None
    match = _REQ.match(line)
    if not match:
        return None
    spec = (match.group("spec") or "").strip()
    return Dependency(
        name=_norm(match.group("name")),
        specifier=spec,
        pinned_version=_pinned(spec) if spec else None,
        source_file=source_file,
    )


def parse_requirements(text: str, source_file: str) -> list[Dependency]:
    deps = (parse_requirement_line(line, source_file) for line in text.splitlines())
    return [d for d in deps if d]


def parse_pyproject(text: str, source_file: str) -> list[Dependency]:
    """Return dependencies from PEP 621 and Poetry tables. Raises ValueError if unparsable."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML: {exc}") from None
    deps: list[Dependency] = []
    project = data.get("project", {})
    for line in project.get("dependencies", []):
        if dep := parse_requirement_line(str(line), source_file):
            deps.append(dep)
    for group in project.get("optional-dependencies", {}).values():
        for line in group:
            if dep := parse_requirement_line(str(line), source_file):
                deps.append(dep)
    poetry = data.get("tool", {}).get("poetry", {})
    tables = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})]
    for group in poetry.get("group", {}).values():
        tables.append(group.get("dependencies", {}))
    for table in tables:
        for name, value in table.items():
            if name.lower() == "python":
                continue
            spec = value if isinstance(value, str) else str(value.get("version", ""))
            exact = spec.strip() if _PLAIN_VERSION.match(spec.strip()) else None
            deps.append(
                Dependency(
                    name=_norm(name),
                    specifier=f"=={exact}" if exact else spec,
                    pinned_version=exact,
                    source_file=source_file,
                )
            )
    return deps


def read_dependencies(root: Path, manifests: list[str]) -> tuple[list[Dependency], list[str]]:
    """Parse the given manifest paths (relative to root). Returns (dependencies, parse_errors)."""
    deps: list[Dependency] = []
    errors: list[str] = []
    for rel in manifests:
        path = root / rel
        if path.is_symlink() or path.stat().st_size > 1_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        name = path.name
        if name.startswith("requirements") and name.endswith(".txt"):
            deps.extend(parse_requirements(text, rel))
        elif name == "pyproject.toml":
            try:
                deps.extend(parse_pyproject(text, rel))
            except ValueError as exc:
                errors.append(f"{rel}: {exc}")
    return deps, errors
