"""TypeScript adapter: static Autopsy, `npm audit` from the lockfile, and a baseline test run with
Node's built-in test runner where the runner provides Node.

What it deliberately does NOT do: install dependencies or run the repository's own `npm test`
script. Both execute untrusted code (install scripts, arbitrary test commands). So ESLint and the
TypeScript compiler, which need the repository's dependencies, are reported as not measured.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.adapters.base import CommandObserver
from app.adapters.models import (
    SEVERITY_ORDER,
    AutopsyReport,
    BaselineReport,
    Finding,
    Severity,
    TestCounts,
    ToolRun,
)
from app.adapters.python_adapter import CI_FILES, _excerpt, _walk_files
from app.adapters.secrets_scan import scan_secrets
from app.adapters.ts_deps import FRAMEWORKS, LOCKFILES, lock_versions, read_manifests
from app.adapters.ts_scan import EXTENSIONS, scan_typescript
from app.execution.runner import CommandResult, CommandRunner, build_env, run_command

AUDIT_TIMEOUT = 120
SUPPRESSION_THRESHOLD = 1
ANY_THRESHOLD = 5
_NPM_SEVERITY: dict[str, Severity] = {
    "critical": "critical",
    "high": "high",
    "moderate": "medium",
    "low": "low",
    "info": "info",
}
TEST_CONFIGS = ("jest.config", "vitest.config", ".mocharc", "playwright.config", "ava.config")
NO_NODE = (
    "TypeScript tests need Node inside the sandbox. The sandbox image does not include Node yet, "
    "so the tests were not run. The result is unknown, not zero."
)


class TypeScriptAdapter:
    language = "TypeScript"

    def __init__(self, node: str | None = None, npm: str | None = None) -> None:
        self._node = node or shutil.which("node")
        self._npm = npm or shutil.which("npm")

    # ---------------------------------------------------------------- detection
    def score(self, root: Path) -> int:
        files = _walk_files(root)
        ts = sum(
            1
            for f in files
            if f.endswith((".ts", ".tsx", ".mts", ".cts")) and not f.endswith(".d.ts")
        )
        js = sum(1 for f in files if Path(f).suffix in EXTENSIONS) - ts
        names = {Path(f).name for f in files}
        if ts:
            return ts
        # Plain JavaScript counts only when the project says it is a TypeScript project.
        return js if "tsconfig.json" in names and js else 0

    def detect(self, root: Path) -> bool:
        return self.score(root) > 0

    def _env(self, home: Path) -> dict[str, str]:
        env = build_env(home)
        if self._node:
            env["PATH"] = f"{Path(self._node).parent}:{env['PATH']}"  # npm is a node script
        return env

    # ------------------------------------------------------------------ autopsy
    def autopsy(
        self, root: Path, name: str, scratch: Path, observe: CommandObserver
    ) -> AutopsyReport:
        scratch.mkdir(parents=True, exist_ok=True)
        files = _walk_files(root)
        names = {Path(f).name for f in files}
        manifests = [f for f in files if Path(f).name == "package.json"][:20]
        deps, meta, errors = read_manifests(root, manifests)
        scan = scan_typescript(root)
        secrets = scan_secrets(root)
        findings: list[Finding] = [*scan.findings, *secrets.findings]
        for err in errors:
            findings.append(
                Finding(
                    id="",
                    category="dependencies",
                    severity="medium",
                    source="detect",
                    title="package.json could not be parsed",
                    evidence=err,
                )
            )

        vuln_total, vuln_pkgs, audited, audit_findings, audit_tool = self._audit(
            root, scratch, observe
        )
        findings.extend(audit_findings)
        tools = [
            ToolRun(
                tool="ts-scan",
                command="built-in heuristic scan",
                exit_code=None,
                measured=True,
                summary=f"{len(scan.findings)} finding(s) in {scan.files} file(s). "
                "Heuristic, not a "
                "type checker.",
                duration_seconds=0.0,
            ),
            ToolRun(
                tool="secret-scan",
                command="built-in pattern scan",
                exit_code=None,
                measured=True,
                summary=f"{len(secrets.findings)} possible secret(s) in {secrets.files} config and "
                "script file(s)" + ("; stopped at the limit" if secrets.truncated else ""),
                duration_seconds=0.0,
            ),
            audit_tool,
            _not_measured("tsc", "the TypeScript compiler"),
            _not_measured("eslint", "ESLint"),
        ]

        findings.extend(self._quality_findings(scan.suppressions, scan.any_uses))
        has_lock = bool(names & LOCKFILES)
        has_docker = any(n == "Dockerfile" or n.endswith(".Dockerfile") for n in names)
        has_ci = (
            any(f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml")) for f in files)
            or bool(names & CI_FILES)
            or any(f.startswith(".circleci/") for f in files)
        )
        findings.extend(
            self._structure_findings(scan.test_files, has_ci, has_docker, bool(manifests), has_lock)
        )

        dep_names = {d.name for d in deps}
        frameworks = sorted({v for k, v in FRAMEWORKS.items() if k in dep_names} - {"TypeScript"})
        findings.sort(
            key=lambda f: (SEVERITY_ORDER[f.severity], f.category, f.file_path or "", f.line or 0)
        )
        for i, finding in enumerate(findings, start=1):
            finding.id = f"F-{i:03d}"
        return AutopsyReport(
            repository_name=name,
            language="TypeScript",
            frameworks=frameworks,
            source_files=scan.files,
            source_lines=scan.lines,
            dependency_files=sorted(manifests),
            dependencies=sorted(deps, key=lambda d: d.name),
            unpinned_dependencies=[]
            if has_lock
            else sorted({d.name for d in deps if not d.pinned_version}),
            lockfile_present=has_lock,
            test_files=scan.test_files,
            test_functions=scan.test_functions,
            test_config=sorted(f for f in files if Path(f).name.startswith(TEST_CONFIGS)),
            build_files=sorted(
                f for f in files if Path(f).name in ("package.json", "tsconfig.json")
            ),
            has_dockerfile=has_docker,
            has_ci=has_ci,
            findings=findings,
            tools=tools,
            known_vulnerabilities=vuln_total,
            vulnerable_packages=vuln_pkgs,
            audited_packages=audited,
            lint_violations=None,  # ESLint is not run, so this stays unknown
        )

    def _quality_findings(self, suppressions: int, any_uses: int) -> list[Finding]:
        out: list[Finding] = []
        if suppressions >= SUPPRESSION_THRESHOLD:
            out.append(
                Finding(
                    id="",
                    category="code_quality",
                    severity="low",
                    source="ts-scan",
                    title=f"{suppressions} TypeScript error suppression(s)",
                    evidence="`@ts-ignore`, `@ts-nocheck` and `@ts-expect-error` hide compiler "
                    "errors.",
                )
            )
        if any_uses >= ANY_THRESHOLD:
            out.append(
                Finding(
                    id="",
                    category="code_quality",
                    severity="low",
                    source="ts-scan",
                    title=f"{any_uses} uses of the `any` type",
                    evidence="`any` switches off type checking for the values it touches.",
                )
            )
        return out

    def _structure_findings(
        self, test_files: int, has_ci: bool, has_docker: bool, has_manifest: bool, has_lock: bool
    ) -> list[Finding]:
        out: list[Finding] = []

        def add(category: str, severity: str, title: str, evidence: str) -> None:
            out.append(
                Finding(
                    id="",
                    category=category,
                    severity=severity,
                    title=title,  # type: ignore[arg-type]
                    evidence=evidence,
                    source="detect",
                )
            )

        if test_files == 0:
            add("tests", "high", "No tests were found", "No *.test.* or *.spec.* files exist.")
        if not has_manifest:
            add("dependencies", "medium", "No dependency manifest found", "No package.json.")
        elif not has_lock:
            add(
                "dependencies",
                "medium",
                "No lockfile found",
                "Without package-lock.json, installs are not reproducible and cannot be audited.",
            )
        if not has_ci:
            add(
                "ci_docker",
                "low",
                "No CI workflow found",
                "No .github/workflows, .gitlab-ci.yml or equivalent.",
            )
        if not has_docker:
            add("ci_docker", "info", "No Dockerfile found", "No Dockerfile in the repository.")
        return out

    def _audit(
        self, root: Path, scratch: Path, observe: CommandObserver
    ) -> tuple[int | None, int | None, int | None, list[Finding], ToolRun]:
        tool = ToolRun(
            tool="npm-audit",
            command="npm audit --package-lock-only",
            exit_code=None,
            measured=False,
            summary="",
            duration_seconds=0.0,
        )
        lock = root / "package-lock.json"
        manifest = root / "package.json"
        if (
            not (lock.is_file() and manifest.is_file())
            or lock.is_symlink()
            or manifest.is_symlink()
        ):
            tool.summary = "Not measured: npm audit needs a package-lock.json next to package.json"
            return None, None, None, [], tool
        if not self._npm:
            tool.summary = "Not measured: npm is not installed"
            return None, None, None, [], tool
        work = scratch / "npm-audit"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        # Only the two manifests are copied, so nothing from the repository is touched or run.
        (work / "package.json").write_bytes(manifest.read_bytes())
        (work / "package-lock.json").write_bytes(lock.read_bytes())
        argv = [self._npm, "audit", "--package-lock-only", "--json", "--ignore-scripts"]
        result = run_command(argv, work, AUDIT_TIMEOUT, self._env(scratch / "home"))
        observe(result)
        tool.command, tool.exit_code = result.command, result.exit_code
        tool.duration_seconds = result.duration_seconds
        try:
            data = json.loads(result.stdout)
            vulns = data["vulnerabilities"]
            meta = data["metadata"]
        except (json.JSONDecodeError, KeyError, TypeError):
            reason = result.error or _excerpt(result, 3) or "npm audit produced no JSON"
            tool.summary = f"Not measured: {reason}"
            tool.output_excerpt = _excerpt(result)
            return None, None, None, [], tool
        versions = lock_versions(lock.read_text(encoding="utf-8", errors="replace"))
        findings = [_npm_finding(n, v, versions.get(n)) for n, v in sorted(vulns.items())]
        total = int(meta["vulnerabilities"]["total"])
        audited = int(meta["dependencies"]["total"])
        tool.measured = True
        tool.summary = f"{total} vulnerable package(s) of {audited} in the lockfile"
        return total, total, audited, findings, tool

    # ----------------------------------------------------------- characterize
    def characterize(
        self, root: Path, runner: CommandRunner | None, observe: CommandObserver, timeout: float
    ) -> BaselineReport:
        if runner is None:
            return BaselineReport(
                status="not_run",
                reason="No sandbox is available for this repository, so its tests were not run. "
                "The result is unknown, not zero.",
            )
        node = getattr(runner, "node", None)
        if not node:
            return BaselineReport(status="not_run", reason=NO_NODE)
        argv = [
            node,
            "--max-old-space-size=1024",
            "--experimental-strip-types",
            "--test",
            "--test-reporter=tap",
        ]
        result = runner.run(argv, root, timeout)
        observe(result)
        return self.parse_tap(result, root)

    @staticmethod
    def parse_tap(result: CommandResult, root: Path) -> BaselineReport:
        base = {
            "command": result.command,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
            "output_excerpt": _excerpt(result, 40),
        }
        if result.timed_out or result.exit_code is None:
            return BaselineReport(
                status="failed_to_execute", reason=result.error or "Could not start", **base
            )
        totals: dict[str, int] = {}
        failing: list[str] = []
        prefix = root.as_posix().rstrip("/") + "/"
        for line in result.stdout.splitlines():
            if line.startswith("# ") and line[2:].split(" ")[0] in (
                "tests",
                "pass",
                "fail",
                "cancelled",
                "skipped",
                "todo",
            ):
                key, _, value = line[2:].partition(" ")
                if value.strip().isdigit():
                    totals[key] = int(value)
            elif line.startswith("not ok "):
                title = line.split(" - ", 1)[-1].split(" # ")[0].strip()
                failing.append(title.removeprefix(prefix))
        if "tests" not in totals:
            output = result.stdout + result.stderr
            reason = "The Node test runner produced no summary"
            if "bad option" in output:
                reason = (
                    "This Node version does not support --experimental-strip-types (needs 22.6+)"
                )
            return BaselineReport(status="failed_to_execute", reason=reason, **base)
        counts = TestCounts(
            passed=totals.get("pass", 0),
            failed=totals.get("fail", 0),
            errors=totals.get("cancelled", 0),
            skipped=totals.get("skipped", 0) + totals.get("todo", 0),
        )
        note = None
        output = result.stdout + result.stderr
        if totals["tests"] == 0:
            note = "The Node test runner found no tests"
        elif "ERR_MODULE_NOT_FOUND" in output or "Cannot find package" in output:
            note = (
                "Some tests could not import a package. Dependencies are not installed (installing "
                "would run untrusted install scripts), so these errors come from the environment."
            )
        return BaselineReport(
            status="completed",
            reason=note,
            counts=counts,
            failing_tests=sorted(set(failing)),
            **base,
        )

    # -------------------------------------------------------------------- fixes
    def fix_lint(
        self, files: dict[str, str], scratch: Path, observe: CommandObserver
    ) -> dict[str, str]:
        return {}  # no safe automatic TypeScript fixer is available without the repo's toolchain


def _not_measured(tool: str, label: str) -> ToolRun:
    return ToolRun(
        tool=tool,
        command="",
        exit_code=None,
        measured=False,
        duration_seconds=0.0,
        summary=f"Not measured: {label} needs the repository's dependencies installed, which is "
        "not done because install scripts are untrusted code",
    )


def _npm_finding(name: str, info: dict, installed: str | None) -> Finding:
    titles: list[str] = []
    for via in info.get("via", []):
        if isinstance(via, dict) and via.get("title") and via["title"] not in titles:
            titles.append(str(via["title"]))
    fix = info.get("fixAvailable")
    fix_versions: list[str] = []
    fix_note = "No fix is available."
    if isinstance(fix, dict) and fix.get("version"):
        fix_versions = [str(fix["version"])]
        major = " (a breaking major upgrade)" if fix.get("isSemVerMajor") else ""
        fix_note = f"Fix available: {fix.get('name', name)}@{fix['version']}{major}."
    elif fix is True:
        fix_note = "A fix is available through `npm audit fix`."
    shown = "; ".join(titles[:4]) + (f"; and {len(titles) - 4} more" if len(titles) > 4 else "")
    where = f"{name} {installed}" if installed else name
    return Finding(
        id="",
        category="dependencies",
        source="npm-audit",
        severity=_NPM_SEVERITY.get(str(info.get("severity")), "medium"),
        title=f"{where} has known vulnerabilities",
        evidence=f"{shown or 'See the npm advisory database'}. {fix_note}",
        file_path="package.json",
        package=name,
        installed_version=installed,
        fix_versions=fix_versions,
    )
