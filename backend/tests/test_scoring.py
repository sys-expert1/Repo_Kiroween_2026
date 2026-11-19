from __future__ import annotations

import pytest

from app.adapters.models import AutopsyReport, BaselineReport, Finding, TestCounts
from app.scoring.health_score import WEIGHTS, comparison_warning, compute_vitality


def report(**kw) -> AutopsyReport:
    base = dict(
        repository_name="r", frameworks=[], python_files=1, python_lines=100, dependency_files=[],
        dependencies=[], unpinned_dependencies=[], lockfile_present=False, test_files=1,
        test_functions=1, test_config=[], build_files=[], has_dockerfile=False, has_ci=False,
        findings=[], tools=[], known_vulnerabilities=None, vulnerable_packages=None,
        lint_violations=None, audited_packages=None,
    )  # fmt: skip
    return AutopsyReport(**{**base, **kw})


def baseline(passed=0, failed=0, errors=0, status="completed") -> BaselineReport:
    return BaselineReport(
        status=status, counts=TestCounts(passed=passed, failed=failed, errors=errors)
        if status == "completed" else None,
    )  # fmt: skip


def dim(score, name):
    return next(d for d in score.dimensions if d.name == name)


def test_weights_match_the_model_and_sum_to_one():
    assert WEIGHTS == {
        "dependencies": 0.20, "tests": 0.30, "code_quality": 0.20, "security": 0.15,
        "ci_docker": 0.15,
    }  # fmt: skip
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_unknown_stays_unknown_and_is_never_scored_100():
    s = compute_vitality(report(python_files=0), BaselineReport(status="not_run"))
    for name in ("dependencies", "tests", "code_quality", "security"):
        assert dim(s, name).score is None and dim(s, name).basis.startswith("Not measured")
    assert dim(s, "ci_docker").score == 0.0  # absence of CI/Docker is a measured zero
    assert s.unmeasured == ["dependencies", "tests", "code_quality", "security"]
    assert s.measured_weight == 0.15 and s.score == 0.0
    assert "renormalised" in s.note


def test_aggregate_is_renormalised_over_measured_dimensions_only():
    s = compute_vitality(
        report(python_files=0, has_ci=True, has_dockerfile=True), baseline(passed=3, failed=1)
    )
    assert dim(s, "tests").score == 75.0
    assert s.measured == ["tests", "ci_docker"] and s.measured_weight == 0.45
    # (0.30*75 + 0.15*50) / 0.45 = 66.7: unmeasured dimensions do not drag the score down.
    assert s.score == pytest.approx(66.7, abs=0.05)


def test_dimension_formulas():
    findings = [
        Finding(
            id="F-1", category="security", severity="high", title="t", evidence="e", source="ast"
        ),
        Finding(
            id="F-2", category="security", severity="medium", title="t", evidence="e", source="ast"
        ),
        Finding(
            id="F-3",
            category="security",
            severity="critical",
            title="t",
            evidence="e",
            source="detect",
        ),
    ]  # the detect-sourced finding is not an AST security finding and is ignored
    s = compute_vitality(
        report(
            findings=findings, known_vulnerabilities=7, vulnerable_packages=1, audited_packages=4,
            lint_violations=10, python_lines=1000,
        ),
        baseline(passed=8, failed=1, errors=1),
    )  # fmt: skip
    assert dim(s, "dependencies").score == 75.0
    assert dim(s, "tests").score == 80.0
    assert dim(s, "code_quality").score == pytest.approx(50.0)  # 10 per kLOC -> 100/(1+1)
    assert dim(s, "security").score == 65.0  # 100 - 25 - 10
    assert s.measured_weight == 1.0 and "All five" in s.note


def test_zero_tests_is_a_measured_zero_but_not_run_is_unknown():
    assert dim(compute_vitality(report(), baseline(passed=0)), "tests").score == 0.0
    s = compute_vitality(report(), BaselineReport(status="failed_to_execute"))
    assert dim(s, "tests").score is None


def test_audit_with_nothing_pinned_is_unknown_not_clean():
    s = compute_vitality(
        report(known_vulnerabilities=0, vulnerable_packages=0, audited_packages=0), baseline()
    )
    assert dim(s, "dependencies").score is None


def test_ci_docker_unverified_presence_counts_half():
    both = compute_vitality(report(has_ci=True, has_dockerfile=True), baseline(passed=1))
    assert dim(both, "ci_docker").score == 50.0
    verified = compute_vitality(
        report(has_ci=True, has_dockerfile=True), baseline(passed=1),
        ci_validation="passed", docker_build="passed",
    )  # fmt: skip
    assert dim(verified, "ci_docker").score == 100.0
    broken = compute_vitality(
        report(has_ci=True, has_dockerfile=True), baseline(passed=1),
        ci_validation="failed", docker_build="failed",
    )  # fmt: skip
    assert dim(broken, "ci_docker").score == 0.0


def test_comparison_warning_only_when_measurement_sets_differ():
    a = compute_vitality(report(), baseline(passed=1))
    same = compute_vitality(report(), baseline(passed=2))
    different = compute_vitality(report(lint_violations=0), baseline(passed=1))
    assert comparison_warning(a, same) is None
    assert "not directly comparable" in comparison_warning(a, different)
