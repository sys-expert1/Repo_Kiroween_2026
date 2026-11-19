"""Patch proposal, digest and application. Independent of LangGraph.

A patch is a whole-file change bound to the SHA-256 of the file it was made against. Patches are
generated against a snapshot and never touch it. The digest of a patch set covers every
(task, path, before, after) so that approving a digest approves exactly those changes.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.adapters.models import AutopsyReport, Finding
from app.adapters.python_edit import (
    EditNotApplicable,
    bump_requirement,
    choose_fix_version,
    replace_popen,
    replace_secret_with_env,
)
from app.agents.llm_patches import LLMPatcher
from app.agents.roadmap import Roadmap, RoadmapTask

# Receives {path: current text} and returns only the files it changed.
LintFixer = Callable[[dict[str, str]], dict[str, str]]

CI_WORKFLOW = """name: CI
on: [push, pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install {requirements}pytest ruff
      - run: ruff check .
      - run: pytest -q
"""

DOCKERFILE = """FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
{requirements_copy}COPY . .
USER app
CMD [{cmd}]
"""
ENTRYPOINTS = ("cli.py", "main.py", "app.py", "__main__.py")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FilePatch(BaseModel):
    task_id: str
    path: str
    action: Literal["modify", "create"]
    before_sha256: str | None
    after_sha256: str
    after_content: str
    diff: str
    rationale: str
    origin: Literal["rule", "llm"] = "rule"
    model: str | None = None  # set when origin is "llm"


class SkippedTask(BaseModel):
    task_id: str
    title: str
    reason: str


class LLMStats(BaseModel):
    model: str
    requests: int
    accepted: int
    rejected: int


class PatchSet(BaseModel):
    roadmap_version: int
    patches: list[FilePatch]
    skipped: list[SkippedTask]
    llm: LLMStats | None = None  # present only when AI-assisted generation was enabled

    @property
    def digest(self) -> str:
        payload = [
            [p.task_id, p.path, p.action, p.before_sha256, p.after_sha256, p.origin]
            for p in self.patches
        ]
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()

    @property
    def files_changed(self) -> list[str]:
        return sorted({p.path for p in self.patches})

    @property
    def combined_diff(self) -> str:
        return "".join(f"# {p.task_id}: {p.rationale}\n{p.diff}" for p in self.patches)


class PatchApplyError(RuntimeError):
    pass


def _unified(path: str, old: str | None, new: str) -> str:
    a = "/dev/null" if old is None else f"a/{path}"
    return "".join(
        difflib.unified_diff((old or "").splitlines(True), new.splitlines(True), a, f"b/{path}")
    )


def _safe_path(root: Path, rel: str) -> Path:
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise EditNotApplicable(f"Unsafe path: {rel!r}")
    target = root / rel
    probe = target
    while probe != root:
        if probe.is_symlink():
            raise EditNotApplicable(f"Path goes through a symlink: {rel!r}")
        probe = probe.parent
    if root.resolve() not in target.resolve().parents:
        raise EditNotApplicable(f"Path escapes the repository: {rel!r}")
    return target


class _Workspace:
    """Virtual file state over a snapshot. The snapshot on disk is never written."""

    def __init__(self, snapshot: Path) -> None:
        self.snapshot = snapshot
        self.files: dict[str, str | None] = {}

    def read(self, rel: str) -> str | None:
        if rel not in self.files:
            path = _safe_path(self.snapshot, rel)
            self.files[rel] = (
                path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
            )
        return self.files[rel]


def generate_patchset(
    roadmap: Roadmap,
    report: AutopsyReport,
    snapshot: Path,
    fix_lint: LintFixer,
    llm: LLMPatcher | None = None,
) -> PatchSet:
    ws = _Workspace(snapshot)
    findings = {f.id: f for f in report.findings}
    patches: list[FilePatch] = []
    skipped: list[SkippedTask] = []

    def emit(
        task: RoadmapTask,
        path: str,
        new: str,
        why: str,
        origin: str = "rule",
        model: str | None = None,
    ) -> None:
        old = ws.read(path)
        if old == new:
            return
        patches.append(
            FilePatch(
                task_id=task.id, path=path, action="modify" if old is not None else "create",
                before_sha256=sha256(old) if old is not None else None,
                after_sha256=sha256(new), after_content=new, diff=_unified(path, old, new),
                rationale=why, origin=origin, model=model,  # type: ignore[arg-type]
            )
        )  # fmt: skip
        ws.files[path] = new

    for task in roadmap.included:
        before = len(patches)
        try:
            _generate_for_task(task, findings, ws, emit, fix_lint, report, llm)
            if len(patches) == before:
                raise EditNotApplicable("The change produced no difference.")
        except EditNotApplicable as exc:
            skipped.append(SkippedTask(task_id=task.id, title=task.title, reason=str(exc)))
    stats = (
        LLMStats(
            model=llm.model, requests=llm.requests, accepted=llm.accepted, rejected=llm.rejected
        )
        if llm
        else None
    )
    return PatchSet(roadmap_version=roadmap.version, patches=patches, skipped=skipped, llm=stats)


def _generate_for_task(task, findings, ws, emit, fix_lint, report, llm) -> None:  # noqa: ANN001
    title = task.title
    linked: list[Finding] = [findings[e] for e in task.evidence if e in findings]
    if task.category == "dependencies" and title.startswith("Upgrade "):
        _dependency(task, linked, ws, emit)
    elif task.category == "security" and title.startswith("Hardcoded secret"):
        f = _only(linked)
        why = "Read the secret from the environment, not source code. Rotate the old value."
        emit(task, _path(f), replace_secret_with_env(_text(ws, f), _line(f)), why)
    elif task.category == "security" and "os.popen" in title:
        f = _only(linked)
        emit(task, _path(f), replace_popen(_text(ws, f), _line(f)),
             "Run the command without a shell so the argument cannot inject commands.")  # fmt: skip
    elif task.category == "security":
        if llm is None:
            raise EditNotApplicable(
                "No automatic fix exists for this construct. A person must decide."
            )
        f = _only(linked)
        new, why = llm.propose(_text(ws, f), f, _path(f))
        emit(
            task, _path(f), new, f"AI-proposed ({llm.model}): {why}", origin="llm", model=llm.model
        )
    elif title == "Resolve lint findings":
        _lint(task, ws, emit, fix_lint)
    elif task.category == "tests":
        raise EditNotApplicable(
            "Tests are never edited automatically, so failures are not hidden or rewritten."
        )
    elif title == "Add a CI workflow":
        reqs = "-r requirements.txt " if ws.read("requirements.txt") is not None else ""
        emit(task, ".github/workflows/ci.yml", CI_WORKFLOW.format(requirements=reqs),
             "Run lint and tests on every push.")  # fmt: skip
    elif title == "Add a Dockerfile":
        entry = next((e for e in ENTRYPOINTS if ws.read(e) is not None), None)
        has_reqs = ws.read("requirements.txt") is not None
        copy = (
            "COPY requirements.txt ./\nRUN pip install --no-cache-dir -r requirements.txt\n"
            if has_reqs else ""
        )  # fmt: skip
        cmd = f'"python", "{entry}"' if entry else '"python"'
        emit(task, "Dockerfile", DOCKERFILE.format(requirements_copy=copy, cmd=cmd),
             "Build and run as a non-root user.")  # fmt: skip
    else:
        raise EditNotApplicable("This task needs a person. No automatic patch is defined.")


def _only(findings: list[Finding]) -> Finding:
    if len(findings) != 1:
        raise EditNotApplicable("The task is not linked to exactly one finding.")
    return findings[0]


def _path(f: Finding) -> str:
    if not f.file_path or f.file_path == ".":
        raise EditNotApplicable("The finding has no file path.")
    return f.file_path


def _line(f: Finding) -> int:
    if not f.line:
        raise EditNotApplicable("The finding has no line number.")
    return f.line


def _text(ws: _Workspace, f: Finding) -> str:
    text = ws.read(_path(f))
    if text is None:
        raise EditNotApplicable(f"{_path(f)} does not exist in the snapshot.")
    return text


def _dependency(task, linked, ws, emit) -> None:  # noqa: ANN001
    f = _only([x for x in linked if x.source == "pip-audit"])
    if not (f.package and f.installed_version):
        raise EditNotApplicable("The finding lacks package and version data.")
    target = choose_fix_version(f.installed_version, f.fix_versions)
    new = bump_requirement(_text(ws, f), f.package, f.installed_version, target)
    emit(task, _path(f), new,
         f"Raise {f.package} from {f.installed_version} to {target}, the highest fix version "
         "listed by pip-audit. Only the repository tests (and the Docker build, if enabled) "
         "check compatibility.")  # fmt: skip


def _lint(task, ws, emit, fix_lint) -> None:  # noqa: ANN001
    current = {}
    for path in task.affected_files:
        if path.endswith(".py") and (text := ws.read(path)) is not None:
            current[path] = text
    if not current:
        raise EditNotApplicable("No Python files to fix.")
    for path, new in sorted(fix_lint(current).items()):
        emit(task, path, new, "Apply Ruff's safe automatic fixes only.")


def apply_patchset(patchset: PatchSet, workdir: Path) -> list[str]:
    """Apply `patchset` to `workdir`. Every patch is checked against the hash it was made for.

    Raises PatchApplyError before writing a patch whose precondition fails. Earlier patches are
    already written, so the caller must roll back by recreating the working copy.
    """
    applied: list[str] = []
    for patch in patchset.patches:
        try:
            target = _safe_path(workdir, patch.path)
        except EditNotApplicable as exc:
            raise PatchApplyError(f"{patch.task_id}: {exc}") from None
        if patch.action == "create":
            if target.exists():
                raise PatchApplyError(f"{patch.task_id}: {patch.path} already exists.")
        else:
            if not target.is_file():
                raise PatchApplyError(f"{patch.task_id}: {patch.path} is missing.")
            current = sha256(target.read_text(encoding="utf-8", errors="replace"))
            if current != patch.before_sha256:
                raise PatchApplyError(f"{patch.task_id}: {patch.path} changed since approval.")
        if sha256(patch.after_content) != patch.after_sha256:
            raise PatchApplyError(f"{patch.task_id}: patch content does not match its hash.")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".necro-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(patch.after_content)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        applied.append(f"{patch.task_id}:{patch.path}")
    return applied
