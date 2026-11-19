"""Python adapter: Autopsy (AST, Ruff, pip-audit) and baseline Characterization (pytest)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from app.adapters.base import CommandObserver
from app.adapters.models import (
    SEVERITY_ORDER,
    AutopsyReport,
    BaselineReport,
    Dependency,
    Finding,
    TestCounts,
    ToolRun,
)
from app.adapters.python_ast import SKIP_DIRS, scan_python
from app.adapters.python_deps import LOCKFILES, read_dependencies
from app.adapters.secrets_scan import scan_secrets
from app.execution.runner import CommandResult, CommandRunner, build_env, run_command

FRAMEWORKS = {
    "flask": "Flask",
    "django": "Django",
    "fastapi": "FastAPI",
    "click": "Click",
    "typer": "Typer",
    "tornado": "Tornado",
    "pyramid": "Pyramid",
    "aiohttp": "aiohttp",
    "bottle": "Bottle",
    "sqlalchemy": "SQLAlchemy",
    "pytest": "pytest",
}
MANIFEST_NAMES = {"pyproject.toml", "setup.py", "setup.cfg", "Pipfile"}
TEST_CONFIG_NAMES = {"pytest.ini", "tox.ini", "conftest.py"}
CI_FILES = {".gitlab-ci.yml", ".travis.yml", "Jenkinsfile", "azure-pipelines.yml"}
TOOL_TIMEOUT = 120
_SUMMARY = re.compile(
    r"(\d+)\s+(passed|failed|skipped|errors?|xfailed|xpassed|deselected|warnings?)"
)
_FAIL_LINE = re.compile(r"^(FAILED|ERROR)\s+(\S+)")


def _walk_files(root: Path) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fname in sorted(filenames):
            found.append((Path(dirpath) / fname).relative_to(root).as_posix())
            if len(found) >= 50_000:
                return found
    return found


def _excerpt(result: CommandResult, lines: int = 25) -> str:
    text = (result.stdout + ("\n" + result.stderr if result.stderr else "")).strip()
    return "\n".join(text.splitlines()[-lines:])


class PythonAdapter:
    language = "Python"

    def __init__(self, python: str | None = None) -> None:
        self._python = python or sys.executable

    def detect(self, root: Path) -> bool:
        files = _walk_files(root)
        return any(f.endswith(".py") for f in files) or any(
            Path(f).name in {"pyproject.toml", "setup.py", "Pipfile"} for f in files
        )

    # ------------------------------------------------------------------ autopsy
    def autopsy(
        self, root: Path, name: str, scratch: Path, observe: CommandObserver
    ) -> AutopsyReport:
        scratch.mkdir(parents=True, exist_ok=True)
        files = _walk_files(root)
        manifests = [
            f
            for f in files
            if Path(f).name in MANIFEST_NAMES
            or (Path(f).name.startswith("requirements") and f.endswith(".txt"))
        ]
        deps, parse_errors = read_dependencies(
            root, [m for m in manifests if m.endswith((".txt", "pyproject.toml"))]
        )
        scan = scan_python(root)
        findings: list[Finding] = list(scan.findings)
        secrets = scan_secrets(root)
        findings.extend(secrets.findings)
        for err in parse_errors:
            findings.append(
                Finding(
                    id="", category="dependencies", severity="medium", source="detect",
                    title="Dependency manifest could not be parsed", evidence=err,
                )
            )  # fmt: skip

        tools: list[ToolRun] = [
            ToolRun(
                tool="secret-scan", command="built-in pattern scan", exit_code=None, measured=True,
                summary=f"{len(secrets.findings)} possible secret(s) in {secrets.files} config and "
                "script file(s)" + ("; stopped at the limit" if secrets.truncated else ""),
                duration_seconds=0.0,
            )
        ]  # fmt: skip
        lint_count, lint_findings, lint_tool = self._ruff(root, scratch, observe)
        tools.append(lint_tool)
        findings.extend(lint_findings)
        vuln_total, vuln_pkgs, audit_findings, audit_tool = self._pip_audit(deps, scratch, observe)
        tools.append(audit_tool)
        findings.extend(audit_findings)

        names = {Path(f).name for f in files}
        has_docker = any(n == "Dockerfile" or n.endswith(".Dockerfile") for n in names)
        has_ci = (
            any(f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml")) for f in files)
            or bool(names & CI_FILES)
            or any(f.startswith(".circleci/") for f in files)
        )
        test_config = sorted(f for f in files if Path(f).name in TEST_CONFIG_NAMES)
        unpinned = sorted({d.name for d in deps if d.pinned_version is None})
        findings.extend(
            self._structure_findings(
                scan.test_files, has_ci, has_docker, bool(manifests), deps, unpinned
            )
        )

        framework_names = {d.name for d in deps} | {m.lower() for m in scan.imports}
        frameworks = sorted({v for k, v in FRAMEWORKS.items() if k in framework_names})
        findings.sort(
            key=lambda f: (SEVERITY_ORDER[f.severity], f.category, f.file_path or "", f.line or 0)
        )
        for i, finding in enumerate(findings, start=1):
            finding.id = f"F-{i:03d}"
        return AutopsyReport(
            repository_name=name,
            frameworks=frameworks,
            python_files=scan.files,
            python_lines=scan.lines,
            dependency_files=sorted(manifests),
            dependencies=sorted(deps, key=lambda d: (d.name, d.source_file)),
            unpinned_dependencies=unpinned,
            lockfile_present=bool(names & LOCKFILES),
            test_files=scan.test_files,
            test_functions=scan.test_functions,
            test_config=test_config,
            build_files=sorted(f for f in files if Path(f).name in MANIFEST_NAMES),
            has_dockerfile=has_docker,
            has_ci=has_ci,
            findings=findings,
            tools=tools,
            known_vulnerabilities=vuln_total,
            vulnerable_packages=vuln_pkgs,
            audited_packages=(
                len({d.name for d in deps if d.pinned_version}) if vuln_total is not None else None
            ),
            lint_violations=lint_count,
        )

    def _structure_findings(
        self,
        test_files: int,
        has_ci: bool,
        has_docker: bool,
        has_manifest: bool,
        deps: list[Dependency],
        unpinned: list[str],
    ) -> list[Finding]:
        out: list[Finding] = []

        def add(category: str, severity: str, title: str, evidence: str) -> None:
            out.append(
                Finding(
                    id="", category=category, severity=severity, title=title,  # type: ignore[arg-type]
                    evidence=evidence, source="detect",
                )
            )  # fmt: skip

        if test_files == 0:
            add("tests", "high", "No tests were found", "No test_*.py or *_test.py files exist.")
        if not has_manifest:
            add("dependencies", "medium", "No dependency manifest found",
                "No requirements*.txt, pyproject.toml, setup.py or Pipfile.")  # fmt: skip
        if unpinned:
            shown = ", ".join(unpinned[:8]) + (" ..." if len(unpinned) > 8 else "")
            add("dependencies", "low", f"{len(unpinned)} dependencies are not pinned exactly",
                f"Not pinned with ==, so they cannot be audited offline: {shown}.")  # fmt: skip
        if not has_ci:
            add("ci_docker", "low", "No CI workflow found",
                "No .github/workflows, .gitlab-ci.yml or equivalent.")  # fmt: skip
        if not has_docker:
            add("ci_docker", "info", "No Dockerfile found", "No Dockerfile in the repository.")
        return out

    def _ruff(
        self, root: Path, scratch: Path, observe: CommandObserver
    ) -> tuple[int | None, list[Finding], ToolRun]:
        argv = [
            self._python, "-m", "ruff", "check", "--isolated", "--no-cache",
            "--output-format", "json", "--select", "E,F,B", "--ignore", "E501", ".",
        ]  # fmt: skip
        result = run_command(argv, root, TOOL_TIMEOUT, build_env(scratch / "home"))
        observe(result)
        tool = ToolRun(
            tool="ruff", command=result.command, exit_code=result.exit_code, measured=False,
            summary="", duration_seconds=result.duration_seconds,
        )  # fmt: skip
        if result.exit_code not in (0, 1):
            tool.summary = f"Not measured: {result.error or _excerpt(result, 3) or 'ruff failed'}"
            tool.output_excerpt = _excerpt(result)
            return None, [], tool
        try:
            items = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            tool.summary = "Not measured: ruff output was not valid JSON"
            return None, [], tool
        groups: dict[str, list[dict]] = {}
        for item in items:
            groups.setdefault(item["code"], []).append(item)
        findings: list[Finding] = []
        for code, hits in sorted(groups.items()):
            first = hits[0]
            rel = Path(first["filename"])
            try:
                rel = rel.relative_to(root)
            except ValueError:
                pass
            sample = "; ".join(
                f"{Path(h['filename']).name}:{h['location']['row']}" for h in hits[:3]
            )
            severe = code.startswith(("F82", "F63", "F7", "F5")) or code == "E999"
            findings.append(
                Finding(
                    id="", category="code_quality", source="ruff",
                    severity="high" if severe else ("medium" if code.startswith("B") else "low"),
                    title=f"{code}: {first['message']} ({len(hits)} occurrence"
                    f"{'s' if len(hits) != 1 else ''})",
                    evidence=f"First hits: {sample}",
                    file_path=rel.as_posix(), line=first["location"]["row"],
                )
            )  # fmt: skip
        tool.measured = True
        tool.summary = f"{len(items)} violations across {len(groups)} rules"
        return len(items), findings, tool

    def _pip_audit(
        self, deps: list[Dependency], scratch: Path, observe: CommandObserver
    ) -> tuple[int | None, int | None, list[Finding], ToolRun]:
        pinned: dict[str, Dependency] = {}
        for dep in deps:
            if dep.pinned_version and dep.name not in pinned:
                pinned[dep.name] = dep
        blank = ToolRun(
            tool="pip-audit", command="pip-audit", exit_code=None, measured=False, summary="",
            duration_seconds=0.0,
        )  # fmt: skip
        if not pinned:
            blank.summary = "Not measured: no exactly pinned dependencies to audit"
            return None, None, [], blank
        req = scratch / "audit-requirements.txt"
        req.write_text("".join(f"{d.name}=={d.pinned_version}\n" for d in pinned.values()))
        argv = [
            self._python, "-m", "pip_audit", "-r", str(req), "--no-deps", "--disable-pip",
            "--format", "json", "--progress-spinner", "off",
        ]  # fmt: skip
        result = run_command(argv, scratch, TOOL_TIMEOUT, build_env(scratch / "home"))
        observe(result)
        tool = ToolRun(
            tool="pip-audit", command=result.command, exit_code=result.exit_code, measured=False,
            summary="", duration_seconds=result.duration_seconds,
        )  # fmt: skip
        try:
            data = json.loads(result.stdout)
            entries = data["dependencies"]
        except (json.JSONDecodeError, KeyError, TypeError):
            tool.summary = "Not measured: " + (
                result.error or _excerpt(result, 3) or "pip-audit produced no JSON"
            )
            tool.output_excerpt = _excerpt(result)
            return None, None, [], tool
        findings: list[Finding] = []
        total = 0
        packages = 0
        for entry in entries:
            vulns = {v["id"]: v for v in entry.get("vulns", [])}
            if not vulns:
                continue
            packages += 1
            total += len(vulns)
            fixes = sorted({fv for v in vulns.values() for fv in v.get("fix_versions", [])})
            ids = sorted(vulns)
            shown = ", ".join(ids[:5]) + (f" and {len(ids) - 5} more" if len(ids) > 5 else "")
            dep = pinned.get(entry["name"])
            findings.append(
                Finding(
                    id="", category="dependencies", source="pip-audit", severity="high",
                    title=f"{entry['name']} {entry['version']} has {len(vulns)} known "
                    f"vulnerabilit{'ies' if len(vulns) != 1 else 'y'}",
                    evidence=f"{shown}. Fixed in: {', '.join(fixes) if fixes else 'no fix listed'}."
                    " pip-audit gives no severity score; known-vulnerable packages are rated high.",
                    file_path=dep.source_file if dep else None,
                    package=entry["name"], installed_version=entry["version"], fix_versions=fixes,
                )
            )  # fmt: skip
        tool.measured = True
        skipped = len(deps) - len(pinned)
        tool.summary = (
            f"{total} known vulnerabilities in {packages} of {len(pinned)} audited packages"
            + (f"; {skipped} dependency entries not pinned and not audited" if skipped else "")
        )
        return total, packages, findings, tool

    def fix_lint(
        self, files: dict[str, str], scratch: Path, observe: CommandObserver
    ) -> dict[str, str]:
        """Apply Ruff's safe fixes to copies of `files`. Returns only the files that changed."""
        work = scratch / "lintfix"
        shutil.rmtree(work, ignore_errors=True)
        for rel, text in files.items():
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        argv = [
            self._python, "-m", "ruff", "check", "--isolated", "--no-cache", "--fix",
            "--select", "E,F,B", "--ignore", "E501", ".",
        ]  # fmt: skip
        observe(run_command(argv, work, TOOL_TIMEOUT, build_env(scratch / "home")))
        changed = {}
        for rel, text in files.items():
            new = (work / rel).read_text(encoding="utf-8")
            if new != text:
                changed[rel] = new
        shutil.rmtree(work, ignore_errors=True)
        return changed

    # ------------------------------------------------------------- characterize
    def characterize(
        self,
        root: Path,
        runner: CommandRunner | None,
        observe: CommandObserver,
        timeout: float,
    ) -> BaselineReport:
        if runner is None:
            return BaselineReport(
                status="not_run",
                reason=(
                    "No sandbox is available for this repository, so its tests were not run. "
                    "The result is unknown, not zero."
                ),
            )
        python = getattr(runner, "python", self._python)
        install_note = None
        install = getattr(runner, "install_requirements", None)
        if install is not None:
            reqs = [r for r in ("requirements.txt",) if (root / r).is_file()]
            installed = install(root, reqs, timeout)
            if installed is not None:
                observe(installed)
                if installed.exit_code != 0:
                    install_note = "Dependency installation failed, so tests ran without them."
        argv = [
            python, "-m", "pytest", "-q", "-rfE", "--tb=no", "--no-header",
            "-p", "no:cacheprovider",
        ]  # fmt: skip
        result = runner.run(argv, root, timeout)
        observe(result)
        report = self.parse_pytest(result)
        output = result.stdout + result.stderr
        if install_note:
            report.reason = install_note
        elif report.status == "completed" and (
            "ModuleNotFoundError" in output or "No module named" in output
        ):
            report.reason = (
                "Some tests could not import a module. The sandbox has no network, so the "
                "repository's dependencies are not installed. These errors come from the "
                "environment, not necessarily the code. Set NECRO_SANDBOX_INSTALL_DEPS=1 to "
                "install wheels first."
            )
        return report

    @staticmethod
    def parse_pytest(result: CommandResult) -> BaselineReport:
        base = {
            "command": result.command,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
            "output_excerpt": _excerpt(result, 40),
        }
        if result.timed_out:
            return BaselineReport(
                status="failed_to_execute", reason=result.error or "Timed out", **base
            )
        if result.exit_code is None:
            return BaselineReport(
                status="failed_to_execute", reason=result.error or "Could not start", **base
            )
        lines = result.stdout.splitlines()
        summary = next((ln for ln in reversed(lines) if _SUMMARY.search(ln) and " in " in ln), None)
        failing = [m.group(2) for ln in lines if (m := _FAIL_LINE.match(ln))]
        if result.exit_code == 5:
            return BaselineReport(
                status="completed",
                reason="pytest collected no tests",
                counts=TestCounts(),
                **base,
            )
        if summary is None or result.exit_code not in (0, 1):
            reasons = {2: "Test run was interrupted", 3: "pytest internal error",
                       4: "pytest usage error"}  # fmt: skip
            reason = reasons.get(result.exit_code, "pytest output could not be interpreted")
            if "No module named pytest" in result.stderr + result.stdout:
                reason = "pytest is not installed in the execution environment"
            return BaselineReport(status="failed_to_execute", reason=reason, **base)
        counts = TestCounts()
        for number, label in _SUMMARY.findall(summary):
            n = int(number)
            if label == "passed":
                counts.passed = n
            elif label == "failed":
                counts.failed = n
            elif label.startswith("error"):
                counts.errors = n
            elif label == "skipped":
                counts.skipped = n
            elif label == "xfailed":
                counts.xfailed = n
            elif label == "xpassed":
                counts.xpassed = n
        return BaselineReport(
            status="completed", counts=counts, failing_tests=sorted(set(failing)), **base
        )
