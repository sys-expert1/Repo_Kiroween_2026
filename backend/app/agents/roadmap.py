"""Revival Roadmap generation. Deterministic and evidence-linked: every task cites findings.

Every task states its evidence, affected files, risk, effort and how it will be verified.
Roadmap versions are immutable; an edit produces a new version.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel

from app.adapters.models import AutopsyReport, BaselineReport, Finding

Priority = Literal["P0", "P1", "P2", "P3"]
_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_SEVERITY_PRIORITY: dict[str, Priority] = {
    "critical": "P0", "high": "P1", "medium": "P2", "low": "P3", "info": "P3",
}  # fmt: skip


class RoadmapTask(BaseModel):
    id: str
    title: str
    priority: Priority
    category: str
    evidence: list[str]  # finding IDs and short factual statements
    affected_files: list[str]
    proposed_action: str
    risk: Literal["low", "medium", "high"]
    estimated_effort: Literal["S", "M", "L"]
    verification: str
    status: Literal["proposed", "excluded"] = "proposed"


class Roadmap(BaseModel):
    version: int
    repository_name: str
    tasks: list[RoadmapTask]

    @property
    def included(self) -> list[RoadmapTask]:
        return [t for t in self.tasks if t.status == "proposed"]

    @property
    def digest(self) -> str:
        """SHA-256 over the included tasks' identity and intended change. Becomes the patch-set
        digest once patches exist (task 7)."""
        payload = [
            {"id": t.id, "action": t.proposed_action, "files": sorted(t.affected_files)}
            for t in sorted(self.included, key=lambda t: t.id)
        ]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _prio(findings: list[Finding]) -> Priority:
    return min((_SEVERITY_PRIORITY[f.severity] for f in findings), key=_PRIORITY_ORDER.__getitem__)


def build_roadmap(report: AutopsyReport, baseline: BaselineReport) -> Roadmap:
    tasks: list[RoadmapTask] = []

    def add(title: str, category: str, findings: list[Finding], files: list[str], action: str,
            risk: str, effort: str, verification: str, evidence: list[str] | None = None,
            priority: Priority | None = None) -> None:  # fmt: skip
        tasks.append(
            RoadmapTask(
                id="",
                title=title,
                priority=priority or _prio(findings),
                category=category,
                evidence=[f.id for f in findings] + (evidence or []),
                affected_files=sorted(set(files)),
                proposed_action=action,
                risk=risk,  # type: ignore[arg-type]
                estimated_effort=effort,  # type: ignore[arg-type]
                verification=verification,
            )
        )

    for f in (x for x in report.findings if x.source == "pip-audit"):
        pkg = f.title.split(" ")[0]
        fixed = re.search(r"Fixed in: (.+?)\.(?: pip-audit|$)", f.evidence)
        fixes = fixed.group(1) if fixed else "see the advisories"
        add(
            f"Upgrade {pkg} to a patched release", "dependencies", [f],
            [f.file_path] if f.file_path else [],
            f"Raise the pin for {pkg} to a release that fixes every listed advisory "
            f"(fix versions across advisories: {fixes}), then re-run tests.",
            "medium", "S",
            f"pip-audit reports no known vulnerabilities for {pkg}; the test baseline does not "
            "regress.",
        )  # fmt: skip
    for f in (
        x for x in report.findings if x.source in ("ast", "secrets") and x.category == "security"
    ):
        if f.source == "secrets":
            action = (
                "Remove the value from the file, load it from the environment or a secrets store, "
                "and rotate the credential, because it is already in the repository history. "
                "Keep local env files out of version control."
            )
            effort, risk = "S", "low"
        elif "secret" in f.title.lower():
            action = (
                "Remove the literal from source and read it from the environment or a secrets "
                "store. Rotate the exposed value."
            )
            effort, risk = "S", "low"
        else:
            action = (
                "Replace the call with a safe API (argument-list subprocess without a shell, "
                "or a library call) and validate the input."
            )
            effort, risk = "M", "medium"
        add(
            f.title, "security", [f], [f.file_path] if f.file_path else [], action, risk, effort,
            "The scan no longer reports this finding; the test baseline does not regress.",
        )  # fmt: skip
    parse_fail = [x for x in report.findings if x.title.startswith("File does not parse")]
    if parse_fail:
        add(
            "Port Python 2 syntax to Python 3", "code_quality", parse_fail,
            [f.file_path for f in parse_fail if f.file_path and f.file_path != "."],
            "Convert Python 2 constructs (print statements, except syntax, etc.) so every "
            "file parses under Python 3.",
            "high", "L", "All files parse with ast.parse under Python 3 and tests run.",
        )  # fmt: skip
    lint = [x for x in report.findings if x.source == "ruff"]
    if lint:
        add(
            "Resolve lint findings", "code_quality", lint,
            [f.file_path for f in lint if f.file_path],
            "Fix the reported Ruff rule violations (E, F, B rule sets).",
            "low", "S", "`ruff check` reports zero violations for the selected rules.",
        )  # fmt: skip
    if baseline.status == "completed" and baseline.failing_tests:
        add(
            f"Investigate {len(baseline.failing_tests)} pre-existing failing test(s)", "tests", [],
            [t.split("::")[0] for t in baseline.failing_tests],
            "Determine for each failing test whether the production code or the test expectation "
            "is wrong. Do not edit a test only to make it pass.",
            "medium", "M",
            "Each failure is explained, and any fix is confirmed against intended behaviour.",
            evidence=[f"Baseline failure: {t}" for t in baseline.failing_tests],
            priority="P2",
        )  # fmt: skip
    structure = {f.title: f for f in report.findings if f.source == "detect"}
    if "No tests were found" in structure:
        add(
            "Add characterization tests", "tests", [structure["No tests were found"]], [],
            "Add tests that pin current behaviour of the main modules before any refactor.",
            "low", "L", "pytest collects and passes the new tests.",
        )  # fmt: skip
    if "No CI workflow found" in structure:
        add(
            "Add a CI workflow", "ci_docker", [structure["No CI workflow found"]],
            [".github/workflows/ci.yml"],
            "Add a workflow that installs dependencies, runs lint and tests.",
            "low", "S", "The workflow file validates and runs the same commands used locally.",
        )  # fmt: skip
    if "No Dockerfile found" in structure:
        add(
            "Add a Dockerfile", "ci_docker", [structure["No Dockerfile found"]], ["Dockerfile"],
            "Add a minimal non-root Dockerfile that installs pinned dependencies.",
            "low", "S", "`docker build` succeeds.",
        )  # fmt: skip

    tasks.sort(key=lambda t: (_PRIORITY_ORDER[t.priority], t.category, t.title))
    for i, task in enumerate(tasks, start=1):
        task.id = f"RT-{i:03d}"
    return Roadmap(version=1, repository_name=report.repository_name, tasks=tasks)


class RoadmapEditError(ValueError):
    pass


def revise_roadmap(roadmap: Roadmap, excluded_task_ids: list[str]) -> Roadmap:
    """Return a new version with the given tasks excluded. Raises RoadmapEditError if invalid."""
    known = {t.id for t in roadmap.tasks}
    unknown = sorted(set(excluded_task_ids) - known)
    if unknown:
        raise RoadmapEditError(f"Unknown task id(s): {', '.join(unknown)}.")
    remaining = [t for t in roadmap.included if t.id not in set(excluded_task_ids)]
    if not remaining:
        raise RoadmapEditError("An edit must leave at least one task. Reject the roadmap instead.")
    tasks = [
        t.model_copy(update={"status": "excluded"}) if t.id in set(excluded_task_ids) else t
        for t in roadmap.tasks
    ]
    return Roadmap(
        version=roadmap.version + 1, repository_name=roadmap.repository_name, tasks=tasks
    )
