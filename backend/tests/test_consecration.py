from __future__ import annotations

from pathlib import Path

from app import consecration
from app.adapters.models import AutopsyReport, BaselineReport, Finding, TestCounts
from app.consecration import Check, docker_build_check, validate_ci_workflows, verify
from app.execution.runner import CommandResult
from app.scoring.health_score import compute_vitality
from tests.test_scoring import report

PASSED = Check(name="ci_workflows", status="passed", detail="ok")
DOCKER_OFF = Check(name="docker_build", status="not_configured", detail="none")


def done(failing=(), passed=3) -> BaselineReport:
    return BaselineReport(
        status="completed", counts=TestCounts(passed=passed, failed=len(failing)),
        failing_tests=list(failing), command="pytest", exit_code=1 if failing else 0,
    )  # fmt: skip


def run_verify(
    before: AutopsyReport,
    base: BaselineReport,
    after: AutopsyReport,
    final,
    ci=PASSED,
    dk=DOCKER_OFF,
):
    s0, s1 = compute_vitality(before, base), compute_vitality(after, final)
    return verify(before, base, after, final, ci, dk, s0, s1)


def test_preexisting_failures_are_not_regressions():
    pre = ["t.py::test_old"]
    v = run_verify(report(lint_violations=3), done(pre), report(lint_violations=1), done(pre))
    assert v.verdict == "verified" and v.regressions == []
    tests = next(c for c in v.checks if c.name == "tests")
    assert (
        tests.status == "passed" and "1 failure(s) existed in the baseline; 0 new" in tests.detail
    )


def test_a_new_failing_test_is_a_regression_and_fails_verification():
    v = run_verify(report(), done(["t.py::old"]), report(), done(["t.py::old", "t.py::new"]))
    assert v.verdict == "failed" and v.regressions == ["New failing test: t.py::new"]


def test_tests_that_cannot_run_after_the_patch_fail_when_the_baseline_ran():
    final = BaselineReport(status="failed_to_execute", reason="syntax error")
    v = run_verify(report(), done(), report(), final)
    assert v.verdict == "failed" and any("could not run" in r for r in v.regressions)


def test_unmeasured_tests_do_not_fail_but_are_listed_as_unmeasured():
    nr = BaselineReport(status="not_run", reason="no sandbox")
    v = run_verify(report(lint_violations=1), nr, report(lint_violations=1), nr)
    assert v.verdict == "verified" and "tests" in v.unmeasured
    assert next(c for c in v.checks if c.name == "tests").status == "not_measured"


def test_lint_getting_worse_fails():
    v = run_verify(report(lint_violations=1), done(), report(lint_violations=4), done())
    assert v.verdict == "failed" and "Lint violations rose from 1 to 4." in v.regressions


def test_vulnerabilities_rising_fails_but_remaining_ones_do_not():
    kw = dict(vulnerable_packages=1, audited_packages=2)
    same = run_verify(
        report(known_vulnerabilities=3, **kw), done(), report(known_vulnerabilities=3, **kw), done()
    )
    assert same.verdict == "verified"
    worse = run_verify(
        report(known_vulnerabilities=3, **kw), done(), report(known_vulnerabilities=5, **kw), done()
    )
    assert worse.verdict == "failed"
    unknown = run_verify(report(known_vulnerabilities=3, **kw), done(), report(), done())
    assert "dependency_audit" in unknown.unmeasured and unknown.verdict == "verified"


def test_failed_ci_validation_fails_verification():
    bad = Check(name="ci_workflows", status="failed", detail="ci.yml: no jobs defined")
    v = run_verify(report(), done(), report(), done(), ci=bad)
    assert v.verdict == "failed" and any("ci_workflows failed" in r for r in v.regressions)


def test_resolved_and_new_findings_are_reported_by_identity_not_id():
    def f(i, title):
        return Finding(id=f"F-{i:03d}", category="security", severity="high", title=title,
                       evidence="e", source="ast", file_path="a.py")  # fmt: skip

    before = report(findings=[f(1, "Use of `eval`"), f(2, "Shell command via `os.popen`")])
    after = report(findings=[f(1, "Shell command via `os.popen`"), f(2, "New thing")])
    v = run_verify(before, done(), after, done())
    assert v.resolved_findings == ["Use of `eval`"] and v.new_findings == ["New thing"]


def test_score_warning_when_measurement_sets_differ():
    v = run_verify(report(lint_violations=1), done(), report(), done())
    assert v.score_warning and "not directly comparable" in v.score_warning


# ----------------------------------------------------------------- ci validation
def workflow(root: Path, name: str, text: str) -> None:
    path = root / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_ci_validation(tmp_path: Path):
    assert validate_ci_workflows(tmp_path).status == "not_configured"
    workflow(tmp_path, "ok.yml", "name: x\non: [push]\njobs:\n  t:\n    runs-on: ubuntu-latest\n")
    ok = validate_ci_workflows(tmp_path)
    assert ok.status == "passed" and "not executed" in ok.detail
    workflow(tmp_path, "nojobs.yml", "name: x\non: [push]\n")
    assert "no jobs" in validate_ci_workflows(tmp_path).detail
    workflow(tmp_path, "bad.yml", "a: [unclosed\n")
    check = validate_ci_workflows(tmp_path)
    assert check.status == "failed" and "invalid YAML" in check.detail


# ----------------------------------------------------------------- docker build
def test_docker_build_states(tmp_path: Path, monkeypatch):
    assert docker_build_check(tmp_path, tmp_path / "h", enabled=True)[0].status == "not_configured"
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    off, ran = docker_build_check(tmp_path, tmp_path / "h", enabled=False)
    assert off.status == "not_measured" and "NECRO_ALLOW_DOCKER_BUILD" in off.detail and ran is None

    calls = []

    def fake(code, stderr=""):
        def run(argv, cwd, timeout, env, **kw):
            calls.append(list(argv))
            return CommandResult(argv=list(argv), exit_code=code, stdout="", stderr=stderr,
                                 duration_seconds=1.0)  # fmt: skip

        return run

    monkeypatch.setattr(consecration, "run_command", fake(0))
    ok, _ = docker_build_check(tmp_path, tmp_path / "h", enabled=True)
    assert ok.status == "passed"
    assert [c[1] for c in calls] == ["info", "build", "rmi"]
    calls.clear()

    def build_fails(argv, cwd, timeout, env, **kw):
        calls.append(list(argv))
        code = 0 if argv[1] == "info" else 1
        err = "" if code == 0 else "step 3 failed\nERROR: boom"
        return CommandResult(
            argv=list(argv), exit_code=code, stdout="", stderr=err, duration_seconds=1.0
        )

    monkeypatch.setattr(consecration, "run_command", build_fails)
    bad, _ = docker_build_check(tmp_path, tmp_path / "h", enabled=True)
    assert bad.status == "failed" and bad.detail == "ERROR: boom"

    monkeypatch.setattr(consecration, "run_command", fake(1, "Cannot connect to the Docker daemon"))
    down, ran = docker_build_check(tmp_path, tmp_path / "h", enabled=True)
    assert down.status == "not_measured" and "daemon is unavailable" in down.detail and ran is None
