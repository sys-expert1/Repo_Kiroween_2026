"""Markdown renderers for run reports. Pure functions over typed models."""

from __future__ import annotations

from app.adapters.models import AutopsyReport, BaselineReport
from app.agents.roadmap import Roadmap


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
