"""Typed results produced by language adapters. No LangGraph or HTTP dependencies."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["dependencies", "tests", "code_quality", "security", "ci_docker", "structure"]
Severity = Literal["critical", "high", "medium", "low", "info"]
Source = Literal["ast", "ruff", "pip-audit", "detect"]
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class Finding(BaseModel):
    id: str
    category: Category
    severity: Severity
    title: str
    evidence: str
    file_path: str | None = None
    line: int | None = None
    source: Source
    status: Literal["open"] = "open"


class Dependency(BaseModel):
    name: str
    specifier: str
    pinned_version: str | None
    source_file: str


class ToolRun(BaseModel):
    """One analysis tool run. `measured` is false if the tool produced no usable result."""

    tool: str
    command: str
    exit_code: int | None
    measured: bool
    summary: str
    duration_seconds: float
    output_excerpt: str = ""


class AutopsyReport(BaseModel):
    repository_name: str
    language: Literal["Python"] = "Python"
    frameworks: list[str]
    python_files: int
    python_lines: int
    dependency_files: list[str]
    dependencies: list[Dependency]
    unpinned_dependencies: list[str]
    lockfile_present: bool
    test_files: int
    test_functions: int
    test_config: list[str]
    build_files: list[str]
    has_dockerfile: bool
    has_ci: bool
    findings: list[Finding]
    tools: list[ToolRun]
    # None means "not measured". Never substitute zero for a missing measurement.
    known_vulnerabilities: int | None
    vulnerable_packages: int | None
    lint_violations: int | None


class TestCounts(BaseModel):
    __test__ = False  # not a pytest class

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    xfailed: int = 0
    xpassed: int = 0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped + self.xfailed + self.xpassed


class BaselineReport(BaseModel):
    """Baseline behaviour before any change. `status` is `not_run` when no sandbox could run it."""

    status: Literal["completed", "not_run", "failed_to_execute"]
    reason: str | None = None
    command: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    counts: TestCounts | None = None
    failing_tests: list[str] = Field(default_factory=list)
    # Always "not_measured" unless a coverage tool ran. None of the adapters run one yet.
    coverage: Literal["not_measured"] = "not_measured"
    build: Literal["not_measured"] = "not_measured"
    duration_seconds: float | None = None
    output_excerpt: str = ""
