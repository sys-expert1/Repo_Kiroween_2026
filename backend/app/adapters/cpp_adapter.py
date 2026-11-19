"""C++ adapter: static Autopsy (heuristic scan, clang-tidy, secrets, CMake parsing) and a baseline
that configures, builds and runs CTest, but only through a runner that may execute code.

Why the split: reading a project is safe, building it is not. `cmake` runs the project's scripts
(`execute_process`, custom commands, downloads) and compilers run for as long as the project wants,
so configuring, building and testing happen only in the Docker sandbox or for trusted bundled
fixtures. clang-tidy parses source without a compilation database (which would need CMake to run),
so the analysis is real but partial: it cannot see headers the project fetches or generates.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.adapters.base import CommandObserver
from app.adapters.cpp_deps import (
    BUILD_FILES,
    FRAMEWORKS,
    LOCKFILES,
    read_build_files,
)
from app.adapters.cpp_scan import _TEST_FILE, EXTENSIONS, SKIP_DIRS, SOURCE_EXT, scan_cpp
from app.adapters.models import (
    SEVERITY_ORDER,
    AutopsyReport,
    BaselineReport,
    Finding,
    Severity,
    TestCounts,
    ToolRun,
)
from app.adapters.python_adapter import CI_FILES, _excerpt
from app.adapters.secrets_scan import scan_secrets
from app.execution.runner import CommandResult, CommandRunner, build_env, run_command

TIDY_TIMEOUT = 180
MAX_TIDY_FILES = 40
CONFIGURE_TIMEOUT = 180
BUILD_DIR = "necro-build"  # never "build": a stale directory in the repository must not be reused
TIDY_CONFIG = (
    "{Checks: 'clang-analyzer-*,bugprone-*,cert-*,-bugprone-easily-swappable-parameters,"
    "-cert-err58-cpp,-bugprone-reserved-identifier', HeaderFilterRegex: ''}"
)
NO_TOOLCHAIN = (
    "C++ tests need CMake and a compiler inside the sandbox. The sandbox image does not include a "
    "C++ toolchain yet, so the tests were not run. The result is unknown, not zero."
)
_DIAGNOSTIC = re.compile(
    r"^(?P<file>/.+?):(?P<line>\d+):(?P<col>\d+): (?P<level>warning|error): (?P<msg>.*?) "
    r"\[(?P<check>[\w.,-]+)\]$"
)
_CTEST_LINE = re.compile(
    r"^\s*(?:\d+/\d+\s+)?Test\s+#\d+:\s+(?P<name>.+?)\s+\.{2,}\s*"
    r"(?P<status>\*{3}[^\d\n]+?|Passed)\s+(?P<secs>[\d.]+)\s+sec"
)
_CTEST_SUMMARY = re.compile(r"(\d+)% tests passed, (\d+) tests failed out of (\d+)")
_ERROR_LINE = re.compile(r"(?i)(?:\b(?:fatal )?error:|^\s*CMake Error)")


def _unpinned_fetches(deps) -> set[str]:  # noqa: ANN001
    """Dependencies that are downloaded at configure time from a branch or without a hash.
    System packages found with find_package() are not fetched, so they are not counted."""
    return {
        d.name
        for d in deps
        if d.pinned_version is None
        and d.specifier
        and not re.fullmatch(r"\d+(?:\.\d+)*", d.specifier)
    }


def _tidy_severity(check: str) -> tuple[str, Severity]:
    """(category, severity) for a clang-tidy check name. Aliases arrive comma-joined, such as
    `bugprone-command-processor,cert-env33-c`, and any CERT alias makes it a security check."""
    parts = check.split(",")
    if any(p.startswith("clang-analyzer-security") for p in parts) or check == (
        "clang-diagnostic-fortify-source"
    ):
        return "security", "high"
    if any(p.startswith("cert-") for p in parts):
        return "security", "medium"
    if check.startswith("clang-analyzer-"):
        return "code_quality", "medium"
    if check.startswith("bugprone-"):
        return "code_quality", "medium"
    return "code_quality", "low"


class CppAdapter:
    language = "C++"
    runner_tool = "cmake"  # a runner without this attribute cannot build C++ projects

    def __init__(self, clang_tidy: str | None = None) -> None:
        self._tidy = clang_tidy or shutil.which("clang-tidy")

    # ---------------------------------------------------------------- detection
    @staticmethod
    def _walk(root: Path) -> list[str]:
        import os

        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames if d not in SKIP_DIRS and not d.startswith("cmake-build")
            )
            for name in sorted(filenames):
                found.append((Path(dirpath) / name).relative_to(root).as_posix())
                if len(found) >= 50_000:
                    return found
        return found

    def score(self, root: Path) -> int:
        return sum(1 for f in self._walk(root) if Path(f).suffix.lower() in EXTENSIONS)

    def detect(self, root: Path) -> bool:
        return self.score(root) > 0

    # ------------------------------------------------------------------- autopsy
    def autopsy(
        self, root: Path, name: str, scratch: Path, observe: CommandObserver
    ) -> AutopsyReport:
        scratch.mkdir(parents=True, exist_ok=True)
        files = self._walk(root)
        names = {Path(f).name for f in files}
        manifest_files = [
            f
            for f in files
            if Path(f).name in ("CMakeLists.txt", "conanfile.txt", "vcpkg.json")
            or f.endswith(".cmake")
        ]
        cmake, other_deps, errors = read_build_files(root, manifest_files)
        deps = [*cmake.dependencies, *other_deps]
        scan = scan_cpp(root)
        secrets = scan_secrets(root)
        findings: list[Finding] = [*scan.findings, *secrets.findings]
        for err in errors:
            findings.append(
                Finding(
                    id="",
                    category="dependencies",
                    severity="medium",
                    source="detect",
                    title="A dependency manifest could not be parsed",
                    evidence=err,
                )
            )

        covered = {(f.file_path, f.line) for f in scan.findings}
        tidy_count, tidy_findings, tidy_tool = self._clang_tidy(
            root, files, scratch, observe, covered
        )
        findings.extend(tidy_findings)
        tools = [
            ToolRun(
                tool="cpp-scan",
                command="built-in heuristic scan",
                exit_code=None,
                measured=True,
                summary=f"{len(scan.findings)} finding(s) in {scan.files} file(s). Heuristic, not "
                "a compiler.",
                duration_seconds=0.0,
            ),
            ToolRun(
                tool="secret-scan",
                command="built-in pattern scan",
                exit_code=None,
                measured=True,
                summary=f"{len(secrets.findings)} possible secret(s) in {secrets.files} config and "
                "script file(s)",
                duration_seconds=0.0,
            ),
            tidy_tool,
            ToolRun(
                tool="dependency-audit",
                command="",
                exit_code=None,
                measured=False,
                duration_seconds=0.0,
                summary="Not measured: there is no vulnerability database lookup for C and C++ "
                "dependencies, so known vulnerabilities stay unknown",
            ),
        ]

        build_systems = sorted(
            {BUILD_FILES[Path(f).name] for f in files if Path(f).name in BUILD_FILES}
        )
        has_docker = any(n == "Dockerfile" or n.endswith(".Dockerfile") for n in names)
        has_ci = (
            any(f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml")) for f in files)
            or bool(names & CI_FILES)
            or any(f.startswith(".circleci/") for f in files)
        )
        findings.extend(
            self._structure_findings(scan, cmake, deps, build_systems, has_ci, has_docker)
        )

        dep_names = {d.name.lower() for d in deps} | {
            inc.split("/")[0].lower().removesuffix(".h").removesuffix(".hpp")
            for inc in scan.includes
        }
        frameworks = sorted({v for k, v in FRAMEWORKS.items() if k in dep_names})
        findings.sort(
            key=lambda f: (SEVERITY_ORDER[f.severity], f.category, f.file_path or "", f.line or 0)
        )
        for i, finding in enumerate(findings, start=1):
            finding.id = f"F-{i:03d}"
        test_registered = cmake.ctest_tests > 0 or cmake.test_discovery
        return AutopsyReport(
            repository_name=name,
            language="C++",
            frameworks=frameworks,
            source_files=scan.files,
            source_lines=scan.lines,
            dependency_files=sorted(manifest_files),
            dependencies=sorted(deps, key=lambda d: (d.name, d.source_file)),
            unpinned_dependencies=sorted(_unpinned_fetches(deps)),
            lockfile_present=bool(names & LOCKFILES),
            test_files=scan.test_files,
            test_functions=scan.test_functions or cmake.ctest_tests,
            test_config=["CTest"] if test_registered or cmake.ctest_enabled else [],
            build_files=sorted(f for f in files if Path(f).name in BUILD_FILES),
            has_dockerfile=has_docker,
            has_ci=has_ci,
            findings=findings,
            tools=tools,
            known_vulnerabilities=None,
            vulnerable_packages=None,
            audited_packages=None,
            lint_violations=tidy_count,
        )

    def _structure_findings(
        self,
        scan,
        cmake,
        deps,
        build_systems,
        has_ci,
        has_docker,  # noqa: ANN001
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

        has_tests = scan.test_files > 0 or cmake.ctest_tests > 0 or cmake.test_discovery
        if not has_tests:
            add(
                "tests",
                "high",
                "No tests were found",
                "No test sources, test macros or add_test() calls were found.",
            )
        elif scan.test_files and not (cmake.ctest_tests or cmake.test_discovery):
            add(
                "tests",
                "medium",
                "Test sources are not registered with CTest",
                "Tests exist but no add_test() or gtest_discover_tests() call registers them, so "
                "`ctest` would run nothing.",
            )
        if not build_systems:
            add(
                "structure",
                "medium",
                "No build system found",
                "No CMakeLists.txt, Makefile, meson.build or similar.",
            )
        elif "CMake" not in build_systems:
            add(
                "structure",
                "info",
                "Not a CMake project",
                f"Found {', '.join(build_systems)}. Only CMake projects are built and tested "
                "automatically.",
            )
        unpinned = sorted(_unpinned_fetches(deps))
        if unpinned:
            add(
                "dependencies",
                "low",
                f"{len(unpinned)} fetched dependencies are not pinned",
                "Fetched from a branch or without a hash, so builds are not reproducible: "
                + ", ".join(unpinned[:8])
                + ".",
            )
        if cmake.runs_commands:
            add(
                "security",
                "low",
                "The build runs commands or uses the network",
                "; ".join(sorted(cmake.runs_commands)) + ". Building this project executes its "
                "scripts, so it is only built inside the sandbox or for trusted fixtures.",
            )
        if scan.headers_using_std:
            add(
                "code_quality",
                "low",
                f"{scan.headers_using_std} header(s) use `using namespace std`",
                "A using-directive in a header leaks into every file that includes it.",
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

    # ---------------------------------------------------------------- clang-tidy
    def _clang_tidy(
        self,
        root: Path,
        files: list[str],
        scratch: Path,
        observe: CommandObserver,
        covered: set[tuple[str | None, int | None]],
    ) -> tuple[int | None, list[Finding], ToolRun]:
        """`covered` holds (file, line) pairs the built-in scan already reported. clang-tidy hits on
        those lines are counted but not listed again, so one problem is one finding."""
        tool = ToolRun(
            tool="clang-tidy",
            command="clang-tidy",
            exit_code=None,
            measured=False,
            summary="",
            duration_seconds=0.0,
        )
        if not self._tidy:
            tool.summary = "Not measured: clang-tidy is not installed"
            return None, [], tool
        sources = [
            f for f in files if Path(f).suffix.lower() in SOURCE_EXT and not _TEST_FILE.search(f)
        ][:MAX_TIDY_FILES]
        if not sources:
            tool.summary = "Not measured: no non-test C or C++ source files to analyse"
            return None, [], tool
        includes = [f"-I{root}"] + [
            f"-I{root / d}" for d in ("include", "src", "inc", "lib") if (root / d).is_dir()
        ]
        hits: list[tuple[str, int, str, str, str]] = []
        unparsable: set[str] = set()
        commands: list[str] = []
        total_time = 0.0
        last: CommandResult | None = None
        for group, flags in (
            ([f for f in sources if not f.endswith(".c")], ["-std=c++17"]),
            ([f for f in sources if f.endswith(".c")], ["-std=c11"]),
        ):
            if not group:
                continue
            argv = [
                self._tidy,
                "--quiet",
                f"--config={TIDY_CONFIG}",
                *[str(root / f) for f in group],
                "--",
                *flags,
                *includes,
            ]
            result = run_command(
                argv,
                root,
                TIDY_TIMEOUT,
                build_env(scratch / "home"),
                limits=True,
                memory_bytes=None,
            )
            observe(result)
            last = result
            commands.append(result.command)
            total_time += result.duration_seconds
            if result.timed_out or result.exit_code is None:
                tool.summary = f"Not measured: {result.error or 'clang-tidy could not run'}"
                return None, [], tool
            for line in result.stdout.splitlines():
                m = _DIAGNOSTIC.match(line)
                if not m:
                    continue
                rel = m.group("file")
                try:
                    rel = Path(rel).relative_to(root).as_posix()
                except ValueError:
                    continue  # a diagnostic in a system or foreign header
                if m.group("level") == "error":
                    unparsable.add(rel)
                else:
                    hits.append(
                        (
                            rel,
                            int(m.group("line")),
                            m.group("check"),
                            m.group("msg"),
                            m.group("level"),
                        )
                    )
        tool.command = " ; ".join(commands)[:300]
        tool.exit_code = last.exit_code if last else None
        tool.duration_seconds = total_time
        if len(unparsable) >= len(sources):
            tool.summary = (
                f"Not measured: all {len(sources)} file(s) failed to parse (missing headers or "
                "dependencies that are fetched or generated at build time)"
            )
            return None, [], tool
        groups: dict[str, list[tuple[str, int, str, str, str]]] = {}
        duplicates = 0
        for hit in hits:
            if (hit[0], hit[1]) in covered:
                duplicates += 1
                continue
            groups.setdefault(hit[2], []).append(hit)
        findings: list[Finding] = []
        for check, items in sorted(groups.items()):
            first = items[0]
            plural = "s" if len(items) != 1 else ""
            category, severity = _tidy_severity(check)
            sample = "; ".join(f"{Path(h[0]).name}:{h[1]}" for h in items[:3])
            findings.append(
                Finding(
                    id="",
                    category=category,
                    severity=severity,
                    source="clang-tidy",  # type: ignore[arg-type]
                    title=f"{check}: {first[3]} ({len(items)} occurrence{plural})",
                    evidence=f"First hits: {sample}",
                    file_path=first[0],
                    line=first[1],
                )
            )
        partial = ""
        if unparsable:
            partial = (
                f"; {len(unparsable)} of {len(sources)} file(s) could not be fully parsed, so the "
                "result is partial"
            )
            findings.append(
                Finding(
                    id="",
                    category="structure",
                    severity="info",
                    source="detect",
                    title=f"{len(unparsable)} file(s) could not be fully analysed",
                    evidence="clang-tidy hit missing headers: "
                    + ", ".join(sorted(unparsable)[:5])
                    + ".",
                )
            )
        tool.measured = True
        capped = f" (first {MAX_TIDY_FILES} files)" if len(sources) == MAX_TIDY_FILES else ""
        tool.summary = (
            f"{len(hits)} warning(s) in {len(sources)} file(s){capped}"
            + (f"; {duplicates} already reported by the built-in scan" if duplicates else "")
            + f"{partial}"
        )
        return len(hits), findings, tool

    # ------------------------------------------------------------- characterize
    def characterize(
        self, root: Path, runner: CommandRunner | None, observe: CommandObserver, timeout: float
    ) -> BaselineReport:
        if runner is None:
            return BaselineReport(
                status="not_run",
                reason="No sandbox is available for this repository, so its tests were not run. "
                "The result is unknown, not zero.",
            )
        cmake = getattr(runner, "cmake", None)
        if not cmake:
            return BaselineReport(status="not_run", reason=NO_TOOLCHAIN)
        if not (root / "CMakeLists.txt").is_file():
            return BaselineReport(
                status="not_run",
                reason="There is no top-level CMakeLists.txt. Only CMake projects are built and "
                "tested automatically. The result is unknown, not zero.",
            )
        shutil.rmtree(root / BUILD_DIR, ignore_errors=True)
        configure = runner.run(
            [cmake, "-S", ".", "-B", BUILD_DIR, "-DCMAKE_BUILD_TYPE=Debug"],
            root,
            min(timeout, CONFIGURE_TIMEOUT),
        )
        observe(configure)
        if configure.exit_code != 0:
            return self._failed(
                configure,
                "CMake could not configure the project here (a missing dependency, or "
                "one that needs the network). Build and tests are unknown",
                "not_measured",
            )
        build = runner.run([cmake, "--build", BUILD_DIR, "--parallel", "2"], root, timeout)
        observe(build)
        if build.exit_code != 0:
            return self._failed(
                build,
                "The build failed (this can also come from the environment, such as a "
                "missing system library). Tests were not run",
                "failed",
            )
        ctest = getattr(runner, "ctest", None) or str(Path(cmake).with_name("ctest"))
        result = runner.run([ctest, "--test-dir", BUILD_DIR, "--timeout", "60"], root, timeout)
        observe(result)
        return self.parse_ctest(result)

    @staticmethod
    def _failed(result: CommandResult, why: str, build: str) -> BaselineReport:
        detail = ""
        if result.timed_out:
            detail = f" {result.error}."
        else:
            lines = (result.stdout + "\n" + result.stderr).splitlines()
            for i, line in enumerate(lines):
                if _ERROR_LINE.search(line):
                    # The first error is the cause; later ones cascade. A CMake error puts its
                    # message on the following line.
                    text = line.strip()
                    if text.startswith("CMake Error"):
                        follow = next((x.strip() for x in lines[i + 1 :] if x.strip()), "")
                        text = f"{text} {follow}".strip()
                    detail = f" First error: {text[:240]}"
                    break
        return BaselineReport(
            status="failed_to_execute",
            reason=f"{why}.{detail}",
            build=build,  # type: ignore[arg-type]
            command=result.command,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
            output_excerpt=_excerpt(result, 40),
        )

    @staticmethod
    def parse_ctest(result: CommandResult) -> BaselineReport:
        base = {
            "command": result.command,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
            "output_excerpt": _excerpt(result, 40),
            "build": "passed",
        }
        if result.timed_out or result.exit_code is None:
            return BaselineReport(
                status="failed_to_execute", reason=result.error or "ctest could not start", **base
            )  # type: ignore[arg-type]
        counts = TestCounts()
        failing: list[str] = []
        seen = 0
        for line in result.stdout.splitlines():
            m = _CTEST_LINE.match(line)
            if not m:
                continue
            seen += 1
            status = m.group("status").strip("* ").lower()
            if status == "passed":
                counts.passed += 1
            elif status.startswith("skipped"):
                counts.skipped += 1
            elif status.startswith(("not run", "disabled")):
                counts.errors += 1
                failing.append(m.group("name"))
            else:  # Failed, Exception: SegFault, Timeout, ...
                counts.failed += 1
                failing.append(m.group("name"))
        summary = _CTEST_SUMMARY.search(result.stdout)
        if "No tests were found" in result.stdout:
            return BaselineReport(
                status="completed",
                reason="CTest found no tests (the project registers none)",
                counts=TestCounts(),
                **base,
            )  # type: ignore[arg-type]
        if summary is None or seen == 0 or seen != int(summary.group(3)):
            return BaselineReport(
                status="failed_to_execute", reason="CTest output could not be interpreted", **base
            )  # type: ignore[arg-type]
        return BaselineReport(
            status="completed", counts=counts, failing_tests=sorted(set(failing)), **base
        )  # type: ignore[arg-type]

    # --------------------------------------------------------------------- fixes
    def fix_lint(
        self, files: dict[str, str], scratch: Path, observe: CommandObserver
    ) -> dict[str, str]:
        return {}  # clang-tidy --fix rewrites code in ways that need review; none is automatic
