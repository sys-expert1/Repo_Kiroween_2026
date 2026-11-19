"""Markdown renderers for run reports. Pure functions over typed models."""

from __future__ import annotations

from app.adapters.models import AutopsyReport, BaselineReport
from app.agents.patches import PatchSet
from app.agents.roadmap import Roadmap
from app.consecration import Verification
from app.scoring.health_score import VitalityScore


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def autopsy_md(r: AutopsyReport, commit: str | None) -> str:
    lines = [
        f"# Autopsy Report: {r.repository_name}",
        "",
        f"- Language: {r.language}",
        f"- Frameworks: {', '.join(r.frameworks) or 'none detected'}",
        f"- Pinned commit: {commit or 'not available (not a git source or HEAD unreadable)'}",
        f"- Python files: {r.python_files} ({r.python_lines} lines)",
        f"- Dependency files: {', '.join(r.dependency_files) or 'none'}",
        f"- Dependencies: {len(r.dependencies)} ({len(r.unpinned_dependencies)} not pinned)",
        f"- Lockfile: {'present' if r.lockfile_present else 'not found'}",
        f"- Test files: {r.test_files}, test functions: {r.test_functions}",
        f"- Dockerfile: {'yes' if r.has_dockerfile else 'no'}; CI: {'yes' if r.has_ci else 'no'}",
        f"- Known vulnerabilities: {_val(r.known_vulnerabilities)}"
        f" in {_val(r.vulnerable_packages)} package(s)",
        f"- Lint violations: {_val(r.lint_violations)}",
        "",
        "## Tools",
        "",
        "| Tool | Measured | Exit | Result |",
        "| --- | --- | --- | --- |",
    ]
    for t in r.tools:
        lines.append(
            f"| {t.tool} | {'yes' if t.measured else 'no'} | {t.exit_code} | {_cell(t.summary)} |"
        )
    lines += ["", f"## Findings ({len(r.findings)})", ""]
    if not r.findings:
        lines.append("No findings.")
    else:
        lines += [
            "| ID | Severity | Category | Title | Location | Source |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for f in r.findings:
            loc = f"{f.file_path}:{f.line}" if f.file_path and f.line else (f.file_path or "")
            lines.append(
                f"| {f.id} | {f.severity} | {f.category} | {_cell(f.title)} | {_cell(loc)} "
                f"| {f.source} |"
            )
        lines += ["", "### Evidence", ""]
        lines += [f"- **{f.id}**: {f.evidence}" for f in r.findings]
    return "\n".join(lines) + "\n"


def _val(value: int | None) -> str:
    return "not measured" if value is None else str(value)


def characterization_md(b: BaselineReport) -> str:
    lines = ["# Characterization Report", "", f"- Status: {b.status}"]
    if b.reason:
        lines.append(f"- Note: {b.reason}")
    if b.command:
        lines.append(f"- Command: `{b.command}`")
        lines.append(f"- Exit code: {b.exit_code}")
    if b.counts is not None:
        c = b.counts
        lines += [
            f"- Tests discovered: {c.total}",
            f"- Passed: {c.passed}, failed: {c.failed}, errors: {c.errors}, skipped: {c.skipped}",
        ]
    else:
        lines.append("- Tests discovered: not measured")
    lines += [
        f"- Coverage: {b.coverage.replace('_', ' ')}",
        f"- Build: {b.build.replace('_', ' ')}",
    ]
    if b.failing_tests:
        lines += ["", "## Pre-existing failures (baseline)", ""]
        lines += [f"- `{t}`" for t in b.failing_tests]
        lines += ["", "These failures existed before any change and are not regressions."]
    if b.output_excerpt:
        lines += ["", "## Output (tail)", "", "```", b.output_excerpt, "```"]
    return "\n".join(lines) + "\n"


def roadmap_md(r: Roadmap) -> str:
    lines = [
        f"# Revival Roadmap v{r.version}: {r.repository_name}",
        "",
        f"- Tasks proposed: {len(r.included)} (excluded: {len(r.tasks) - len(r.included)})",
        f"- Patch-set digest: `{r.digest}`",
        "- Status: proposed. Nothing has been changed. Approval is required before any patch.",
        "",
    ]
    for t in r.tasks:
        lines += [
            f"## {t.id} [{t.priority}] {t.title}"
            + (" (excluded)" if t.status == "excluded" else ""),
            "",
            f"- Category: {t.category}; risk: {t.risk}; effort: {t.estimated_effort}",
            f"- Evidence: {', '.join(t.evidence) or 'none'}",
            f"- Affected files: {', '.join(t.affected_files) or 'to be determined'}",
            f"- Proposed action: {t.proposed_action}",
            f"- Verification: {t.verification}",
            "",
        ]
    return "\n".join(lines)


def patchset_md(ps: PatchSet) -> str:
    lines = [
        f"# Surgical Patches (roadmap v{ps.roadmap_version})",
        "",
        f"- Patch-set digest: `{ps.digest}`",
        f"- Patches: {len(ps.patches)} across {len(ps.files_changed)} file(s)",
        f"- Tasks with no automatic patch: {len(ps.skipped)}",
        "",
    ]
    if ps.llm:
        lines[5:5] = [
            f"- AI assistance: model `{ps.llm.model}`, {ps.llm.requests} request(s), "
            f"{ps.llm.accepted} accepted, {ps.llm.rejected} rejected",
        ]
    for p in ps.patches:
        tag = f" (AI-proposed, `{p.model}`)" if p.origin == "llm" else ""
        lines += [f"## {p.task_id}: {p.action} `{p.path}`{tag}", "", p.rationale, ""]
    if ps.skipped:
        lines += ["## No automatic patch", ""]
        lines += [f"- **{s.task_id}** {s.title}: {s.reason}" for s in ps.skipped]
    return "\n".join(lines) + "\n"


def verification_md(
    v: Verification, before: VitalityScore, after: VitalityScore, applied: list[str]
) -> str:
    lines = [
        "# Verification Report",
        "",
        f"- Verdict: **{v.verdict}**",
        f"- Patches applied to the isolated working copy: {len(applied)}",
        "- The original repository was not modified.",
        "",
        "## Checks",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for c in v.checks:
        lines.append(f"| {c.name} | {c.status} | {_cell(c.detail)} |")
    commands = [c for c in v.checks if c.command]
    if commands:
        lines += ["", "## Commands", ""]
        lines += [f"- `{c.command}` exit {c.exit_code}" for c in commands]
    if v.regressions:
        lines += ["", "## Regressions", ""] + [f"- {r}" for r in v.regressions]
    lines += ["", "## Findings", ""]
    lines += [f"- Resolved: {t}" for t in v.resolved_findings] or ["- Resolved: none"]
    lines += [f"- New: {t}" for t in v.new_findings] or ["- New: none"]
    if v.unmeasured:
        lines += ["", f"Not measured: {', '.join(v.unmeasured)}."]
    lines += [
        "",
        "## Vitality Score",
        "",
        "| Dimension | Weight | Before | After |",
        "| --- | --- | --- | --- |",
    ]
    for b, a in zip(before.dimensions, after.dimensions, strict=True):
        lines.append(f"| {b.name} | {b.weight:.0%} | {_score(b.score)} | {_score(a.score)} |")
    lines += [
        f"| **Total** | | **{_score(before.score)}** | **{_score(after.score)}** |",
        "",
        f"- Before: {before.note}",
        f"- After: {after.note}",
    ]
    if v.score_warning:
        lines.append(f"- Warning: {v.score_warning}")
    return "\n".join(lines) + "\n"


def _score(value: float | None) -> str:
    return "not measured" if value is None else f"{value:.1f}"
