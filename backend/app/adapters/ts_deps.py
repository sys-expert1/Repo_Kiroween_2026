"""package.json and npm lockfile parsing. Static only: no script is ever run."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.adapters.models import Dependency

LOCKFILES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"}
_EXACT = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")

FRAMEWORKS = {
    "react": "React", "next": "Next.js", "vue": "Vue", "@angular/core": "Angular",
    "svelte": "Svelte", "express": "Express", "fastify": "Fastify", "koa": "Koa",
    "@nestjs/core": "NestJS", "vite": "Vite", "jest": "Jest", "vitest": "Vitest",
    "mocha": "Mocha", "ava": "AVA", "@playwright/test": "Playwright", "typescript": "TypeScript",
}  # fmt: skip


def parse_package_json(text: str, rel: str) -> tuple[list[Dependency], dict]:
    """Return (dependencies, package metadata). Raises ValueError for unusable JSON."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc.msg} at line {exc.lineno}") from None
    if not isinstance(data, dict):
        raise ValueError("package.json is not an object")
    deps: list[Dependency] = []
    for section in SECTIONS:
        table = data.get(section) or {}
        if not isinstance(table, dict):
            continue
        for name, spec in table.items():
            spec = str(spec).strip()
            deps.append(
                Dependency(
                    name=str(name), specifier=spec,
                    pinned_version=spec if _EXACT.match(spec) else None, source_file=rel,
                )
            )  # fmt: skip
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    return deps, {"name": data.get("name"), "scripts": scripts, "type": data.get("type")}


def lock_versions(text: str) -> dict[str, str]:
    """Installed version per package name from package-lock.json (v1, v2 and v3)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    versions: dict[str, str] = {}
    for key, info in (data.get("packages") or {}).items():
        if key.startswith("node_modules/") and isinstance(info, dict) and "version" in info:
            versions.setdefault(key.rsplit("node_modules/", 1)[-1], str(info["version"]))
    for name, info in (data.get("dependencies") or {}).items():
        if isinstance(info, dict) and "version" in info:
            versions.setdefault(name, str(info["version"]))
    return versions


def read_manifests(root: Path, manifests: list[str]) -> tuple[list[Dependency], dict, list[str]]:
    deps: list[Dependency] = []
    meta: dict = {}
    errors: list[str] = []
    for rel in manifests:
        path = root / rel
        if path.is_symlink() or path.stat().st_size > 1_000_000:
            continue
        try:
            found, info = parse_package_json(
                path.read_text(encoding="utf-8", errors="replace"), rel
            )
        except ValueError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        deps.extend(found)
        if rel == "package.json":
            meta = info
    return deps, meta, errors
