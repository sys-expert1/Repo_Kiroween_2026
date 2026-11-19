"""Consecration: compare the baseline with the final state and decide whether the patch set is
verified. Pure functions over typed models; the workflow runs the tools and passes results in.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from app.adapters.models import AutopsyReport, BaselineReport
from app.execution.runner import CommandResult, build_env, run_command
from app.scoring.health_score import CheckStatus, VitalityScore, comparison_warning

DOCKER_BUILD_TIMEOUT = 600


class Check(BaseModel):
    name: str
    status: CheckStatus
    detail: str
    command: str | None = None
    exit_code: int | None = None


class Verification(BaseModel):
    verdict: Literal["verified", "failed"]
    checks: list[Check]
    regressions: list[str]
    resolved_findings: list[str]
    new_findings: list[str]
    unmeasured: list[str]
    score_warning: str | None = None


def validate_ci_workflows(root: Path) -> Check:
    directory = root / ".github" / "workflows"
    workflows = (
        sorted(p for p in directory.glob("*.y*ml") if p.is_file() and not p.is_symlink())
        if directory.is_dir()
        else []
    )
    if not workflows:
        return Check(name="ci_workflows", status="not_configured", detail="No workflow files.")
    problems: list[str] = []
    for path in workflows:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{path.name}: invalid YAML ({str(exc).splitlines()[0]})")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict) or not data["jobs"]:
            problems.append(f"{path.name}: no jobs defined")
        elif not any(k in data for k in ("on", True)):
            problems.append(f"{path.name}: no trigger defined")
    if problems:
        return Check(name="ci_workflows", status="failed", detail="; ".join(problems))
    return Check(
        name="ci_workflows", status="passed",
        detail=f"{len(workflows)} workflow file(s) parse and define jobs. Syntax check only; "
        "the workflows were not executed.",
    )  # fmt: skip


def docker_build_check(
    root: Path, home: Path, *, enabled: bool, docker_bin: str = "docker", tag: str = "necro-verify"
) -> tuple[Check, CommandResult | None]:
    """Build the repository's Dockerfile. A Dockerfile's RUN steps are untrusted code that the
    daemon runs with network access, so this is opt-in (`NECRO_ALLOW_DOCKER_BUILD=1`)."""
    if not (root / "Dockerfile").is_file():
        return Check(name="docker_build", status="not_configured", detail="No Dockerfile."), None
    if not enabled:
        return (
            Check(
                name="docker_build", status="not_measured",
                detail="Not run: building an untrusted Dockerfile is disabled by default. "
                "Set NECRO_ALLOW_DOCKER_BUILD=1 to enable it.",
            ),
            None,
        )  # fmt: skip
    home.mkdir(parents=True, exist_ok=True)
    env = build_env(home)
    info = run_command([docker_bin, "info", "--format", "{{.ServerVersion}}"], home, 10, env)
    if info.exit_code != 0:
        return (
            Check(name="docker_build", status="not_measured",
                  detail="Not run: the Docker daemon is unavailable."),
            None,
        )  # fmt: skip
    argv = [docker_bin, "build", "--pull=false", "-t", tag, "."]
    result = run_command(argv, root, DOCKER_BUILD_TIMEOUT, env)
    run_command([docker_bin, "rmi", "-f", tag], home, 30, env)
    if result.exit_code == 0:
        return Check(name="docker_build", status="passed", detail="Image built.",
                     command=result.command, exit_code=0), result  # fmt: skip
    why = (result.error or result.stderr or "docker build failed").strip().splitlines()[-1][:200]
    status: CheckStatus = "failed" if result.exit_code else "not_measured"
    return Check(name="docker_build", status=status, detail=why, command=result.command,
                 exit_code=result.exit_code), result  # fmt: skip


_OCCURRENCES = re.compile(r"\s*\(\d+ occurrences?\)")


def _finding_keys(report: AutopsyReport) -> dict[str, str]:
    """Stable identity of a finding that does not depend on its sequential id."""
    keys: dict[str, str] = {}
    for f in report.findings:
        title = _OCCURRENCES.sub("", f.title)
        keys[f"{f.source}|{f.category}|{title}|{f.file_path}"] = f.title
    return keys


def verify(
    before: AutopsyReport,
    baseline: BaselineReport,
    after: AutopsyReport,
    final_tests: BaselineReport,
    ci: Check,
    docker: Check,
    before_score: VitalityScore,
    after_score: VitalityScore,
) -> Verification:
    checks: list[Check] = []
    regressions: list[str] = []

    # Tests: pre-existing failures are not regressions.
    if final_tests.status == "completed" and final_tests.counts is not None:
        if baseline.status == "completed":
            new_failures = sorted(set(final_tests.failing_tests) - set(baseline.failing_tests))
            regressions += [f"New failing test: {t}" for t in new_failures]
            pre = len(set(final_tests.failing_tests) & set(baseline.failing_tests))
            detail = (
                f"{final_tests.counts.passed} passed, {final_tests.counts.failed} failed, "
                f"{final_tests.counts.errors} errors. {pre} failure(s) existed in the baseline; "
                f"{len(new_failures)} new."
            )
            status: CheckStatus = "failed" if new_failures else "passed"
        else:
            detail = "Baseline was not measured, so regressions cannot be determined."
            status = "not_measured"
    elif baseline.status == "completed":
        regressions.append(f"Tests could not run after the patch ({final_tests.status}).")
        detail, status = f"Tests {final_tests.status}: {final_tests.reason}", "failed"
    else:
        detail = final_tests.reason or "Tests were not run."
        status = "not_measured"
    checks.append(Check(name="tests", status=status, detail=detail,
                        command=final_tests.command, exit_code=final_tests.exit_code))  # fmt: skip

    # Lint must not get worse.
    if before.lint_violations is None or after.lint_violations is None:
        checks.append(Check(name="lint", status="not_measured", detail="Ruff gave no result."))
    else:
        worse = after.lint_violations > before.lint_violations
        if worse:
            regressions.append(
                f"Lint violations rose from {before.lint_violations} to {after.lint_violations}."
            )
        checks.append(Check(
            name="lint", status="failed" if worse else "passed",
            detail=(
                f"Ruff violations: {before.lint_violations} before, "
                f"{after.lint_violations} after."
            ),
        ))  # fmt: skip

    # Dependency audit: reported. A package still vulnerable is not a regression.
    if after.known_vulnerabilities is None:
        checks.append(
            Check(
                name="dependency_audit",
                status="not_measured",
                detail="pip-audit gave no result (for example, no network).",
            )
        )
    else:
        was = before.known_vulnerabilities
        regress = was is not None and after.known_vulnerabilities > was
        if regress:
            regressions.append(
                f"Known vulnerabilities rose from {was} to {after.known_vulnerabilities}."
            )
        checks.append(Check(
            name="dependency_audit", status="failed" if regress else "passed",
            detail=f"Known vulnerabilities: {_n(was)} before, {after.known_vulnerabilities} after.",
        ))  # fmt: skip

    checks += [ci, docker]
    for c in (ci, docker):
        if c.status == "failed":
            regressions.append(f"{c.name} failed: {c.detail}")

    before_keys, after_keys = _finding_keys(before), _finding_keys(after)
    return Verification(
        verdict="failed" if regressions else "verified",
        checks=checks,
        regressions=regressions,
        resolved_findings=sorted(before_keys[k] for k in before_keys.keys() - after_keys.keys()),
        new_findings=sorted(after_keys[k] for k in after_keys.keys() - before_keys.keys()),
        unmeasured=[c.name for c in checks if c.status == "not_measured"],
        score_warning=comparison_warning(before_score, after_score),
    )


def _n(value: int | None) -> str:
    return "not measured" if value is None else str(value)
