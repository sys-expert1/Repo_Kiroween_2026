from __future__ import annotations

import pytest

from app.adapters.models import AutopsyReport, BaselineReport, Finding, TestCounts
from app.agents.roadmap import RoadmapEditError, build_roadmap, revise_roadmap
from app.reports import autopsy_md, characterization_md, roadmap_md


def finding(i: int, **kw) -> Finding:
    base = dict(
        id=f"F-{i:03d}", category="security", severity="high", title="t", evidence="e",
        source="ast",
    )  # fmt: skip
    return Finding(**{**base, **kw})


def report(findings: list[Finding]) -> AutopsyReport:
    return AutopsyReport(
        repository_name="r", frameworks=[], source_files=1, source_lines=1, dependency_files=[],
        dependencies=[], unpinned_dependencies=[], lockfile_present=False, test_files=0,
        test_functions=0, test_config=[], build_files=[], has_dockerfile=False, has_ci=False,
        findings=findings, tools=[], known_vulnerabilities=None, vulnerable_packages=None,
        lint_violations=None,
    )  # fmt: skip


NOT_RUN = BaselineReport(status="not_run", reason="no sandbox")


def test_every_task_is_linked_to_evidence_and_fully_described():
    findings = [
        finding(1, category="dependencies", source="pip-audit", title="requests 2.19.1 has 2 known vulnerabilities",
                evidence="PYSEC-1. Fixed in: 0.12.3, 1.0. pip-audit gives no severity score.",
                file_path="requirements.txt"),
        finding(2, title="Hardcoded secret-like value assigned to `KEY`", file_path="a.py", line=3),
        finding(3, title="Shell command via `os.popen`", file_path="b.py", line=9),
        finding(4, category="ci_docker", severity="low", source="detect", title="No CI workflow found"),
    ]  # fmt: skip
    rm = build_roadmap(report(findings), NOT_RUN)
    assert rm.version == 1
    assert [t.id for t in rm.tasks] == [f"RT-{i:03d}" for i in range(1, len(rm.tasks) + 1)]
    for t in rm.tasks:
        assert t.evidence and t.proposed_action and t.verification and t.status == "proposed"
    dep = next(t for t in rm.tasks if t.category == "dependencies")
    assert "0.12.3, 1.0" in dep.proposed_action  # version list is intact
    secret = next(t for t in rm.tasks if "secret" in t.title.lower())
    assert "Rotate" in secret.proposed_action
    priorities = [t.priority for t in rm.tasks]
    assert priorities == sorted(priorities)


def test_baseline_failures_become_an_investigation_not_a_test_rewrite():
    baseline = BaselineReport(
        status="completed", counts=TestCounts(passed=1, failed=1),
        failing_tests=["tests/test_a.py::test_x"],
    )  # fmt: skip
    rm = build_roadmap(report([]), baseline)
    [task] = rm.tasks
    assert (
        task.category == "tests"
        and "Do not edit a test only to make it pass" in task.proposed_action
    )
    assert "Baseline failure: tests/test_a.py::test_x" in task.evidence


def test_no_failure_task_when_tests_were_not_run():
    assert build_roadmap(report([]), NOT_RUN).tasks == []


def test_digest_is_stable_and_changes_with_scope():
    findings = [finding(1), finding(2, title="Use of `eval`")]
    a = build_roadmap(report(findings), NOT_RUN)
    b = build_roadmap(report(findings), NOT_RUN)
    assert a.digest == b.digest and len(a.digest) == 64
    c = revise_roadmap(a, [a.tasks[0].id])
    assert c.digest != a.digest and c.version == 2


def test_revise_excludes_tasks_and_does_not_mutate_the_original():
    rm = build_roadmap(report([finding(1), finding(2, title="Use of `eval`")]), NOT_RUN)
    revised = revise_roadmap(rm, ["RT-001"])
    assert [t.status for t in revised.tasks] == ["excluded", "proposed"]
    assert all(t.status == "proposed" for t in rm.tasks) and rm.version == 1
    assert [t.id for t in revised.included] == ["RT-002"]


def test_revise_validation():
    rm = build_roadmap(report([finding(1)]), NOT_RUN)
    with pytest.raises(RoadmapEditError, match="Unknown"):
        revise_roadmap(rm, ["RT-404"])
    with pytest.raises(RoadmapEditError, match="at least one"):
        revise_roadmap(rm, ["RT-001"])


def test_reports_state_unknowns_instead_of_zeroes():
    md = autopsy_md(report([finding(1)]), None)
    assert "Known vulnerabilities: not measured" in md and "Lint violations: not measured" in md
    assert "not available" in md  # no pinned commit
    char = characterization_md(NOT_RUN)
    assert "Status: not_run" in char and "Tests discovered: not measured" in char
    assert "Coverage: not measured" in char
    assert "Nothing has been changed" in roadmap_md(build_roadmap(report([finding(1)]), NOT_RUN))
