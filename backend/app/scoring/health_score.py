"""Vitality Score: H = 0.20 D + 0.30 T + 0.20 Q + 0.15 S + 0.15 C.

A dimension that was not measured is `None` (unknown). It is excluded from the aggregate and the
remaining weights are renormalised, so the aggregate is `sum(w*s) / sum(w)` over measured
dimensions. `measured_weight` reports how much of the model that covers. Nothing is ever scored
100 because it is missing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.adapters.models import AutopsyReport, BaselineReport

WEIGHTS: dict[str, float] = {
    "dependencies": 0.20,
    "tests": 0.30,
    "code_quality": 0.20,
    "security": 0.15,
    "ci_docker": 0.15,
}
SECURITY_PENALTY = {"critical": 40, "high": 25, "medium": 10, "low": 3, "info": 0}
CheckStatus = Literal["passed", "failed", "not_measured", "not_configured"]


class Dimension(BaseModel):
    name: str
    weight: float
    score: float | None  # None = not measured
    basis: str


class VitalityScore(BaseModel):
    dimensions: list[Dimension]
    score: float | None
    measured_weight: float
    measured: list[str]
    unmeasured: list[str]
    note: str


def _round(x: float) -> float:
    return round(max(0.0, min(100.0, x)), 1)


def _dependencies(r: AutopsyReport) -> tuple[float | None, str]:
    if r.known_vulnerabilities is None or not r.audited_packages:
        return None, "Not measured: no dependency audit result for pinned packages."
    bad = r.vulnerable_packages or 0
    return _round(100 * (1 - bad / r.audited_packages)), (
        f"{bad} of {r.audited_packages} audited packages have known vulnerabilities "
        f"({r.known_vulnerabilities} advisories). Unpinned packages are not audited."
    )


def _tests(b: BaselineReport) -> tuple[float | None, str]:
    if b.status != "completed" or b.counts is None:
        return None, f"Not measured: tests were {b.status.replace('_', ' ')}."
    c = b.counts
    executed = c.passed + c.failed + c.errors
    if executed == 0:
        return 0.0, "No tests ran. Coverage was not measured."
    return _round(100 * c.passed / executed), (
        f"{c.passed} of {executed} executed tests passed ({c.skipped} skipped). "
        "Coverage was not measured."
    )


def _quality(r: AutopsyReport) -> tuple[float | None, str]:
    if r.lint_violations is None:
        return None, "Not measured: Ruff did not produce a result."
    density = r.lint_violations * 1000 / max(r.python_lines, 1)
    return _round(100 / (1 + density / 10)), (
        f"{r.lint_violations} Ruff violations in {r.python_lines} lines "
        f"({density:.1f} per 1000 lines). Score = 100 / (1 + density / 10)."
    )


def _security(r: AutopsyReport) -> tuple[float | None, str]:
    if r.python_files == 0:
        return None, "Not measured: no Python files were scanned."
    hits = [f for f in r.findings if f.category == "security" and f.source in ("ast", "secrets")]
    penalty = sum(SECURITY_PENALTY[f.severity] for f in hits)
    return _round(100 - penalty), (
        f"{len(hits)} static security findings (AST and secret patterns). This is a static scan, "
        "not a "
        "complete security assessment."
    )


def _ci_docker(
    r: AutopsyReport, ci_validation: CheckStatus, docker_build: CheckStatus
) -> tuple[float | None, str]:
    ci = 0.0
    if r.has_ci and ci_validation != "failed":
        ci = 1.0 if ci_validation == "passed" else 0.5
    dock = 0.0
    if r.has_dockerfile and docker_build != "failed":
        dock = 1.0 if docker_build == "passed" else 0.5
    return _round(50 * ci + 50 * dock), (
        f"CI: {'present' if r.has_ci else 'absent'}, validation {ci_validation}. "
        f"Docker: {'present' if r.has_dockerfile else 'absent'}, build {docker_build}. "
        "Present but unverified counts half."
    )


def compute_vitality(
    report: AutopsyReport,
    baseline: BaselineReport,
    *,
    ci_validation: CheckStatus = "not_measured",
    docker_build: CheckStatus = "not_measured",
) -> VitalityScore:
    parts = {
        "dependencies": _dependencies(report),
        "tests": _tests(baseline),
        "code_quality": _quality(report),
        "security": _security(report),
        "ci_docker": _ci_docker(report, ci_validation, docker_build),
    }
    dims = [
        Dimension(name=n, weight=WEIGHTS[n], score=parts[n][0], basis=parts[n][1]) for n in WEIGHTS
    ]
    measured = [d for d in dims if d.score is not None]
    weight = round(sum(d.weight for d in measured), 2)
    score = (
        round(sum(d.weight * d.score for d in measured) / weight, 1)  # type: ignore[operator]
        if measured
        else None
    )
    unmeasured = [d.name for d in dims if d.score is None]
    if score is None:
        note = "Nothing could be measured, so there is no score."
    elif unmeasured:
        note = (
            f"Computed from {len(measured)} of 5 dimensions ({weight * 100:.0f}% of the model); "
            f"weights are renormalised. Not measured: {', '.join(unmeasured)}."
        )
    else:
        note = "All five dimensions were measured."
    return VitalityScore(
        dimensions=dims, score=score, measured_weight=weight,
        measured=[d.name for d in measured], unmeasured=unmeasured, note=note,
    )  # fmt: skip


def comparison_warning(before: VitalityScore, after: VitalityScore) -> str | None:
    """Warn when two scores were computed from different sets of measured dimensions."""
    if set(before.measured) == set(after.measured):
        return None
    return (
        "The before and after scores use different measured dimensions "
        f"(before: {', '.join(before.measured) or 'none'}; after: "
        f"{', '.join(after.measured) or 'none'}), so they are not directly comparable."
    )
