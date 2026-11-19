from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import cpp_adapter
from app.adapters.cpp_adapter import BUILD_DIR, NO_TOOLCHAIN, CppAdapter
from app.adapters.cpp_deps import parse_cmake, parse_conan, parse_vcpkg, read_build_files
from app.adapters.cpp_scan import blank, scan_cpp, scan_source
from app.adapters.models import AutopsyReport, BaselineReport, Finding
from app.adapters.registry import choose_adapter
from app.adapters.secrets_scan import scan_secrets
from app.agents.roadmap import build_roadmap
from app.config import FIXTURES_DIR, Settings
from app.consecration import verify
from app.execution import runner as runner_mod
from app.execution.runner import CommandResult, HostFixtureRunner
from app.graph.runner import InlineExecutor
from app.main import create_app
from app.scoring.health_score import compute_vitality
from tests.conftest import OfflineAdapter
from tests.helpers import parse_sse

FIXTURE = FIXTURES_DIR / "legacy-cpp-parser"
HAVE_TOOLCHAIN = bool(
    shutil.which("cmake") and shutil.which("ctest") and (shutil.which("g++") or shutil.which("c++"))
)
needs_toolchain = pytest.mark.skipif(
    not HAVE_TOOLCHAIN, reason="cmake, ctest or a C++ compiler is missing; this test did NOT run"
)
needs_tidy = pytest.mark.skipif(
    shutil.which("clang-tidy") is None, reason="clang-tidy is not installed; this test did NOT run"
)


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file() and not p.is_symlink():
            h.update(p.read_bytes())
    return h.hexdigest()


# --------------------------------------------------------------------- scanner
def test_blank_ignores_comments_strings_raw_strings_and_digit_separators():
    src = (
        '// strcpy(a, b);\n/* system("x"); */\nconst char* s = "gets(x)";\n'
        "auto raw = R\"d(system(cmd); \" )d\" )d\";\nint big = 1'000'000; char q = '\\'';\n"
    )
    out = blank(src)
    assert len(out) == len(src) and out.count("\n") == src.count("\n")
    for word in ("strcpy", "system", "gets"):
        assert word not in out
    assert "1'000'000" in out  # a digit separator is not the start of a char literal


def test_unbounded_calls_are_found_with_the_right_severity():
    src = (
        "void f(char* in, const std::string& cmd) {\n"
        '  char buf[8];\n  strcpy(buf, in);\n  std::strcat(buf, in);\n  sprintf(buf, "%s", in);\n'
        '  gets(buf);\n  scanf("%s", buf);\n  scanf("%9s", buf);\n  char* t = tmpnam(nullptr);\n}\n'
    )
    found = {(f.title, f.severity, f.line) for f in scan_source(src, "a.cpp").findings}
    assert found == {
        ("Unbounded `strcpy`", "medium", 3), ("Unbounded `strcat`", "medium", 4),
        ("Unbounded `sprintf`", "medium", 5), ("`gets` reads without a length limit", "high", 6),
        ("`scanf` with an unbounded `%s`", "high", 7), ("Insecure temporary file name", "medium", 9),
    }  # fmt: skip


@pytest.mark.parametrize(
    ("call", "severity"),
    [
        ('system("ls -la")', "medium"),
        ('std::system("a" "b")', "medium"),
        ("system(cmd.c_str())", "high"),
        ('::system(("report " + cmd).c_str())', "high"),
        ('popen(cmd.c_str(), "r")', "high"),
    ],
)
def test_shell_calls_are_rated_by_whether_the_argument_is_constant(call: str, severity: str):
    [f] = scan_source(f"void f() {{\n  {call};\n}}\n", "a.cpp").findings
    assert f.severity == severity and f.line == 2 and f.source == "cpp-scan"


def test_member_calls_and_other_namespaces_are_not_confused_with_the_c_functions():
    src = 'void f() {\n  obj.system(a);\n  obj->strcpy(a, b);\n  foo::system(c);\n  printf("%s", x);\n}\n'
    assert scan_source(src, "a.cpp").findings == []


def test_a_format_string_taken_from_a_variable_is_flagged():
    [f] = scan_source("void f(char* in) {\n  printf(in);\n}\n", "a.cpp").findings
    assert f.title == "Format string taken from a variable" and "`in`" in f.evidence


def test_test_files_are_counted_not_scanned_and_headers_using_std_are_counted():
    gtest = "#include <gtest/gtest.h>\nTEST(A, b) { system(x); }\nTEST_F(C, d) {}\n"
    t = scan_source(gtest, "src/foo.cpp")
    assert (t.test_files, t.test_functions, t.findings) == (1, 2, [])
    by_path = scan_source("int main() { return 0; }\n", "tests/check.cpp")
    assert by_path.test_files == 1 and by_path.test_functions == 0
    assert scan_source("using namespace std;\n", "a.hpp").headers_using_std == 1
    assert scan_source("using namespace std;\n", "a.cpp").headers_using_std == 0


def test_scan_skips_build_dirs_vendored_code_symlinks_and_huge_files(tmp_path: Path):
    write(tmp_path, "src/a.cpp", "void f(char* i){ gets(i); }\n")
    for skipped in ("build", "third_party/lib", "_deps/x", "cmake-build-debug", "vendor/y"):
        write(tmp_path, f"{skipped}/b.cpp", "void g(char* i){ gets(i); }\n")
    outside = tmp_path.parent / "outside.cpp"
    outside.write_text("void h(char* i){ gets(i); }\n")
    (tmp_path / "src" / "link.cpp").symlink_to(outside)
    write(tmp_path, "src/huge.cpp", "x" * 1_000_001)
    scan = scan_cpp(tmp_path)
    assert scan.files == 1 and [f.file_path for f in scan.findings] == ["src/a.cpp"]


def test_the_scanner_never_compiles_or_runs_what_it_reads(tmp_path: Path):
    marker = tmp_path / "ran"
    write(
        tmp_path,
        "src/boom.cpp",
        f'#include "{marker}"\n#error nope\nint main() {{ system("touch {marker}"); }}\n',
    )
    scan_cpp(tmp_path)
    assert not marker.exists()


# ---------------------------------------------------------------------- CMake
CMAKE = """
cmake_minimum_required(VERSION 3.16)
project(x CXX)  # comment
find_package(Boost 1.74 REQUIRED COMPONENTS system)
find_package(GTest EXACT 1.12.1)
find_package(fmt)
include(FetchContent)
FetchContent_Declare(json GIT_REPOSITORY https://github.com/nlohmann/json.git GIT_TAG v3.11.2)
FetchContent_Declare(rapid GIT_REPOSITORY https://x/y.git GIT_TAG main)
FetchContent_Declare(sha GIT_REPOSITORY https://x/z.git GIT_TAG 0123456789abcdef0123456789abcdef01234567)
FetchContent_Declare(tarball URL https://x/a.tgz URL_HASH SHA256=abc)
FetchContent_Declare(loose URL https://x/b.tgz)
enable_testing()
add_executable(a a.cpp)
add_library(l l.cpp)
add_test(NAME t1 COMMAND a)
add_test(t2 a)
gtest_discover_tests(a)
execute_process(COMMAND ls)
add_custom_command(OUTPUT x COMMAND touch x)
file(DOWNLOAD https://x/y out)
"""


def test_cmake_parsing_reads_dependencies_tests_and_dangerous_commands():
    info = parse_cmake(CMAKE, "CMakeLists.txt")
    deps = {d.name: (d.specifier, d.pinned_version) for d in info.dependencies}
    assert deps["Boost"] == ("1.74", None) and deps["GTest"] == ("1.12.1", "1.12.1")
    assert deps["json"] == ("v3.11.2", "v3.11.2") and deps["rapid"] == ("main", None)
    assert deps["sha"][1] == "0123456789abcdef0123456789abcdef01234567"
    assert deps["tarball"] == ("URL", "hash-verified") and deps["loose"] == ("URL", None)
    assert (info.ctest_enabled, info.ctest_tests, info.test_discovery) == (True, 2, True)
    assert (info.executables, info.libraries, info.min_version) == (1, 1, "3.16")
    assert len(info.runs_commands) == 4 and any("execute_process" in c for c in info.runs_commands)


def test_conan_and_vcpkg_manifests():
    conan = parse_conan(
        "[requires]\nzlib/1.2.13\nboost/[>=1.70]\nfmt/9.1.0@user/channel\n[generators]\ncmake\n",
        "c",
    )
    assert [(d.name, d.pinned_version) for d in conan] == [
        ("zlib", "1.2.13"),
        ("boost", None),
        ("fmt", "9.1.0"),
    ]
    vcpkg = parse_vcpkg(json.dumps({"dependencies": ["fmt", {"name": "zlib"}],
                                    "overrides": [{"name": "fmt", "version": "9.1.0"}]}), "v")  # fmt: skip
    assert [(d.name, d.pinned_version) for d in vcpkg] == [("fmt", "9.1.0"), ("zlib", None)]
    assert parse_vcpkg("not json", "v") == []


def test_reading_build_files_skips_symlinks_and_never_runs_cmake(tmp_path: Path, monkeypatch):
    marker = tmp_path / "ran"
    write(
        tmp_path,
        "CMakeLists.txt",
        f"execute_process(COMMAND touch {marker})\nadd_test(NAME a COMMAND a)\n",
    )
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("add_test(NAME evil COMMAND x)\nadd_test(NAME evil2 COMMAND y)\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "CMakeLists.txt").symlink_to(outside)
    info, _, _ = read_build_files(tmp_path, ["CMakeLists.txt", "sub/CMakeLists.txt"])
    assert info.ctest_tests == 1 and not marker.exists()


# ------------------------------------------------------------------ clang-tidy
def fake_tidy(monkeypatch, root: Path, lines: list[str], code: int = 0, **kw):
    calls: list[list[str]] = []

    def fake(argv, cwd, timeout, env, **options):
        calls.append(list(argv))
        shown = []
        for line in (ln.replace("{root}", str(root)) for ln in lines):
            path = line.split(":")[0]
            # Like the real tool: report only on files it was given. Foreign paths (system headers)
            # appear in the first invocation only.
            if path in argv or (not path.startswith(str(root)) and len(calls) == 1):
                shown.append(line)
        out = "\n".join(shown)
        return CommandResult(
            argv=list(argv), exit_code=code, stdout=out, stderr="", duration_seconds=0.5, **kw
        )

    monkeypatch.setattr(cpp_adapter, "run_command", fake)
    return calls


def adapter() -> CppAdapter:
    return CppAdapter(clang_tidy="/usr/bin/clang-tidy")


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    write(root, "CMakeLists.txt", "project(p CXX)\nenable_testing()\nadd_test(NAME t COMMAND t)\n")
    write(root, "src/a.cpp", "int a() { return 1; }\n")
    write(root, "src/b.cpp", "int b() { return 2; }\n")
    write(root, "src/c.c", "int c(void) { return 3; }\n")
    write(root, "tests/t.cpp", "int main() { return 0; }\n")
    return root


def autopsy(root: Path, tmp_path: Path):
    return adapter().autopsy(root, "proj", tmp_path / "scratch", lambda r: None)


def test_clang_tidy_hits_are_grouped_classified_and_never_echo_paths_outside_the_repo(
    proj, tmp_path, monkeypatch
):
    calls = fake_tidy(monkeypatch, proj, [
        "{root}/src/a.cpp:3:5: warning: use of a moved object [bugprone-use-after-move]",
        "{root}/src/b.cpp:9:1: warning: use of a moved object [bugprone-use-after-move]",
        "{root}/src/a.cpp:7:2: warning: 'strcpy' is insecure [clang-analyzer-security.insecureAPI.strcpy]",
        "{root}/src/b.cpp:2:2: warning: dead store [clang-analyzer-deadcode.DeadStores]",
        "/usr/include/x.h:1:1: warning: from a system header [bugprone-foo]",
        "{root}/src/a.cpp:4:4: warning: unused [clang-diagnostic-unused-variable]",
    ])  # fmt: skip
    rep = autopsy(proj, tmp_path)
    tidy = {f.title.split(":")[0]: f for f in rep.findings if f.source == "clang-tidy"}
    assert tidy["bugprone-use-after-move"].title.endswith("(2 occurrences)")
    assert (
        tidy["bugprone-use-after-move"].file_path == "src/a.cpp"
        and tidy["bugprone-use-after-move"].line == 3
    )
    assert (
        tidy["clang-analyzer-security.insecureAPI.strcpy"].category,
        tidy["clang-analyzer-security.insecureAPI.strcpy"].severity,
    ) == ("security", "high")
    assert tidy["clang-analyzer-deadcode.DeadStores"].severity == "medium"
    assert tidy["clang-diagnostic-unused-variable"].severity == "low"
    assert "bugprone-foo" not in tidy  # a diagnostic outside the repository is ignored
    assert rep.lint_violations == 5
    assert len(calls) == 2  # one C++ group and one C group, with matching language flags
    cpp_call, c_call = calls
    assert (
        "-std=c++17" in cpp_call
        and str(proj / "src" / "a.cpp") in cpp_call
        and str(proj / "src" / "c.c") not in cpp_call
    )
    assert "-std=c11" in c_call and str(proj / "src" / "c.c") in c_call
    assert not any("tests/t.cpp" in a for a in cpp_call)  # test sources are not analysed
    assert (
        next(a for a in cpp_call if a.startswith("--config=")).count("HeaderFilterRegex: ''") == 1
    )


def test_clang_tidy_ignores_the_repositorys_own_clang_tidy_config(proj, tmp_path, monkeypatch):
    write(proj, ".clang-tidy", "Checks: '-*'\nCustomChecks: evil\n")
    calls = fake_tidy(monkeypatch, proj, [])
    autopsy(proj, tmp_path)
    assert all(any(a.startswith("--config=") for a in call) for call in calls)  # inline config wins


def test_alias_check_names_with_a_cert_part_are_security_findings(proj, tmp_path, monkeypatch):
    fake_tidy(
        monkeypatch,
        proj,
        [
            "{root}/src/a.cpp:3:5: warning: uses a command processor [bugprone-command-processor,cert-env33-c]"
        ],
    )
    [f] = [f for f in autopsy(proj, tmp_path).findings if f.source == "clang-tidy"]
    assert (f.category, f.severity) == ("security", "medium")


def test_findings_the_builtin_scan_already_reported_are_not_listed_twice(tmp_path, monkeypatch):
    root = tmp_path / "p"
    write(root, "CMakeLists.txt", "project(p)\n")
    write(root, "src/a.cpp", "void f(char* i, char* o) {\n  strcpy(o, i);\n}\n")
    write(root, "tests/t.cpp", "int main() { return 0; }\n")
    fake_tidy(
        monkeypatch,
        root,
        [
            "{root}/src/a.cpp:2:3: warning: strcpy is insecure [clang-analyzer-security.insecureAPI.strcpy]"
        ],
    )
    rep = autopsy(root, tmp_path)
    assert [f.source for f in rep.findings if "strcpy" in f.title.lower()] == ["cpp-scan"]
    assert rep.lint_violations == 1  # still counted as a linter finding
    tool = next(t for t in rep.tools if t.tool == "clang-tidy")
    assert "1 already reported by the built-in scan" in tool.summary


def test_missing_headers_make_the_result_partial_and_total_failure_means_not_measured(
    proj, tmp_path, monkeypatch
):
    fake_tidy(monkeypatch, proj, [
        "{root}/src/a.cpp:1:10: error: 'dep.h' file not found [clang-diagnostic-error]",
        "{root}/src/b.cpp:2:2: warning: dead store [clang-analyzer-deadcode.DeadStores]",
    ])  # fmt: skip
    rep = autopsy(proj, tmp_path)
    tool = next(t for t in rep.tools if t.tool == "clang-tidy")
    assert (
        tool.measured and "could not be fully parsed" in tool.summary and "partial" in tool.summary
    )
    assert any(f.title == "1 file(s) could not be fully analysed" for f in rep.findings)
    assert rep.lint_violations == 1
    fake_tidy(
        monkeypatch,
        proj,
        [
            f"{{root}}/src/{n}:1:1: error: nope [clang-diagnostic-error]"
            for n in ("a.cpp", "b.cpp", "c.c")
        ],
    )
    rep = autopsy(proj, tmp_path)
    tool = next(t for t in rep.tools if t.tool == "clang-tidy")
    assert not tool.measured and rep.lint_violations is None and "failed to parse" in tool.summary


def test_clang_tidy_unavailable_or_timing_out_is_not_measured_not_zero(proj, tmp_path, monkeypatch):
    missing = CppAdapter(clang_tidy=None)
    missing._tidy = None
    rep = missing.autopsy(proj, "p", tmp_path / "s", lambda r: None)
    assert rep.lint_violations is None
    assert (
        next(t for t in rep.tools if t.tool == "clang-tidy").summary
        == "Not measured: clang-tidy is not installed"
    )
    fake_tidy(monkeypatch, proj, [], code=None, timed_out=True, error="Timed out after 180s")  # type: ignore[arg-type]
    rep = autopsy(proj, tmp_path)
    assert (
        rep.lint_violations is None
        and "Timed out" in next(t for t in rep.tools if t.tool == "clang-tidy").summary
    )


def test_autopsy_reports_structure_secrets_and_unknowns(tmp_path, monkeypatch):
    root = tmp_path / "p"
    write(
        root,
        "CMakeLists.txt",
        "project(p)\nFetchContent_Declare(r GIT_REPOSITORY u GIT_TAG main)\nexecute_process(COMMAND ls)\n",
    )
    write(root, "src/a.cpp", 'static const char* API_KEY = "abcdefgh12345678";\n')
    write(root, "include/h.hpp", "using namespace std;\n")
    write(root, "test_a.cpp", "int main() { return 0; }\n")
    fake_tidy(monkeypatch, root, [])
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    rep = autopsy(root, tmp_path)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    titles = {f.title for f in rep.findings}
    assert any(t.startswith("Possible secret in src/a.cpp") for t in titles)
    assert "1 fetched dependencies are not pinned" in titles
    assert "The build runs commands or uses the network" in titles
    assert "1 header(s) use `using namespace std`" in titles
    assert (
        "Test sources are not registered with CTest" in titles and "No CI workflow found" in titles
    )
    assert "abcdefgh12345678" not in rep.model_dump_json()
    assert rep.known_vulnerabilities is None and rep.audited_packages is None
    audit = next(t for t in rep.tools if t.tool == "dependency-audit")
    assert not audit.measured and "no vulnerability database lookup" in audit.summary
    assert rep.unpinned_dependencies == ["r"] and rep.language == "C++"


def test_no_tests_and_no_build_system_are_reported(tmp_path, monkeypatch):
    write(tmp_path / "p", "a.cpp", "int main() { return 0; }\n")
    fake_tidy(monkeypatch, tmp_path / "p", [])
    rep = autopsy(tmp_path / "p", tmp_path)
    titles = {f.title for f in rep.findings}
    assert {"No tests were found", "No build system found"} <= titles
    make = tmp_path / "m"
    write(make, "Makefile", "all:\n")
    write(make, "a.cpp", "int main() { return 0; }\n")
    fake_tidy(monkeypatch, make, [])
    assert "Not a CMake project" in {f.title for f in autopsy(make, tmp_path).findings}


# ----------------------------------------------------------------------- CTest
def ctest_result(stdout: str, code: int = 8, **kw) -> CommandResult:
    return CommandResult(
        argv=["ctest"], exit_code=code, stdout=stdout, stderr="", duration_seconds=0.2, **kw
    )


CTEST = """Test project /tmp/p
    Start 1: ok
1/4 Test #1: ok ........................   Passed    0.01 sec
    Start 2: known failure with spaces
2/4 Test #2: known failure with spaces ...***Failed    0.02 sec
    Start 3: crash
3/4 Test #3: crash .......................***Exception: SegFault  0.30 sec
    Start 4: slow
4/4 Test #4: slow ........................***Timeout  60.00 sec

25% tests passed, 3 tests failed out of 4
"""


def test_ctest_output_is_parsed_into_counts_and_failing_names():
    r = CppAdapter.parse_ctest(ctest_result(CTEST))
    assert r.status == "completed" and r.build == "passed"
    assert (r.counts.passed, r.counts.failed, r.counts.errors, r.counts.total) == (1, 3, 0, 4)
    assert r.failing_tests == ["crash", "known failure with spaces", "slow"]
    assert r.coverage == "not_measured"


def test_ctest_skipped_and_not_run_tests_are_not_failures_or_passes():
    out = "1/3 Test #1: a ....   Passed 0.01 sec\n2/3 Test #2: b ...***Skipped   0.00 sec\n3/3 Test #3: c ...***Not Run (Disabled)   0.00 sec\n\n67% tests passed, 0 tests failed out of 3\n"
    r = CppAdapter.parse_ctest(ctest_result(out, 0))
    assert (r.counts.passed, r.counts.skipped, r.counts.errors, r.counts.failed) == (1, 1, 1, 0)
    assert r.failing_tests == ["c"]


def test_ctest_with_no_tests_and_unreadable_output_are_distinguished():
    none = CppAdapter.parse_ctest(ctest_result("Test project x\nNo tests were found!!!\n", 0))
    assert none.status == "completed" and none.counts.total == 0 and "registers none" in none.reason
    for out in ("", "garbage", "1/2 Test #1: a ... Passed 0.1 sec\n"):  # the last has no summary
        bad = CppAdapter.parse_ctest(ctest_result(out, 1))
        assert bad.status == "failed_to_execute" and bad.counts is None
    mismatch = CppAdapter.parse_ctest(ctest_result(CTEST.replace("out of 4", "out of 9"), 8))
    assert mismatch.status == "failed_to_execute"
    timed = CppAdapter.parse_ctest(
        ctest_result("", None, timed_out=True, error="Timed out after 5s")
    )  # type: ignore[arg-type]
    assert timed.status == "failed_to_execute" and timed.timed_out


class CMakeRunner:
    """A scripted runner with the attributes of a toolchain-capable one."""

    python = "python"
    cmake = "/usr/bin/cmake"
    ctest = "/usr/bin/ctest"

    def __init__(
        self, configure: int = 0, build: int = 0, tests: str = CTEST, test_code: int = 8
    ) -> None:
        self.calls: list[tuple[list[str], Path]] = []
        self._codes = [configure, build]
        self._tests, self._test_code = tests, test_code

    def run(self, argv, cwd, timeout) -> CommandResult:
        self.calls.append((list(argv), Path(cwd)))
        step = len(self.calls) - 1
        if step < 2:
            code = self._codes[step]
            err = (
                "CMake Error at CMakeLists.txt:5 (find_package):\n  Could not find Boost"
                if (step == 0 and code)
                else ""
            )
            err = err or (
                "src/a.cpp:3:1: error: expected ';'\nmake: *** [all] Error 2" if code else ""
            )
            return CommandResult(
                argv=list(argv), exit_code=code, stdout="", stderr=err, duration_seconds=0.1
            )
        return CommandResult(
            argv=list(argv),
            exit_code=self._test_code,
            stdout=self._tests,
            stderr="",
            duration_seconds=0.1,
        )


def test_characterize_configures_builds_and_tests_in_a_fresh_build_dir(proj: Path):
    (proj / BUILD_DIR).mkdir()
    (proj / BUILD_DIR / "CMakeCache.txt").write_text("stale cache from another machine")
    runner = CMakeRunner()
    r = CppAdapter().characterize(proj, runner, lambda x: None, 60)
    configure, build, test = (c[0] for c in runner.calls)
    assert (
        configure[:5] == ["/usr/bin/cmake", "-S", ".", "-B", BUILD_DIR]
        and "-DCMAKE_BUILD_TYPE=Debug" in configure
    )
    assert build[:3] == ["/usr/bin/cmake", "--build", BUILD_DIR] and test[:3] == [
        "/usr/bin/ctest",
        "--test-dir",
        BUILD_DIR,
    ]
    assert all(cwd == proj for _, cwd in runner.calls)
    assert not (proj / BUILD_DIR / "CMakeCache.txt").exists()  # a stale directory is never reused
    assert r.status == "completed" and r.build == "passed" and r.counts.passed == 1


def test_a_configure_failure_leaves_build_and_tests_unknown(proj: Path):
    r = CppAdapter().characterize(proj, CMakeRunner(configure=1), lambda x: None, 60)
    assert r.status == "failed_to_execute" and r.build == "not_measured" and r.counts is None
    assert "could not configure" in r.reason and "Could not find Boost" in r.reason


def test_a_compile_failure_is_a_failed_build_with_the_compiler_error(proj: Path):
    runner = CMakeRunner(build=2)
    r = CppAdapter().characterize(proj, runner, lambda x: None, 60)
    assert r.status == "failed_to_execute" and r.build == "failed" and r.counts is None
    assert (
        "expected ';'" in r.reason and len(runner.calls) == 2
    )  # tests are not run after a failed build


def test_characterize_needs_a_runner_a_toolchain_and_a_cmake_project(proj: Path, tmp_path: Path):
    from tests.conftest import FakeRunner

    a = CppAdapter()
    assert a.characterize(proj, None, lambda r: None, 10).status == "not_run"
    no_tools = a.characterize(proj, FakeRunner(), lambda r: None, 10)
    assert (
        no_tools.status == "not_run" and no_tools.reason == NO_TOOLCHAIN and no_tools.counts is None
    )
    (proj / "CMakeLists.txt").unlink()
    runner = CMakeRunner()
    other = a.characterize(proj, runner, lambda r: None, 10)
    assert (
        other.status == "not_run"
        and "top-level CMakeLists.txt" in other.reason
        and runner.calls == []
    )


# ----------------------------------------------------------------- real tools
@needs_toolchain
def test_real_cmake_builds_and_ctest_runs_the_fixture(tmp_path: Path):
    work = tmp_path / "fx"
    shutil.copytree(FIXTURE, work)
    r = CppAdapter().characterize(work, HostFixtureRunner(tmp_path / "home"), lambda x: None, 180)
    assert r.status == "completed" and r.build == "passed" and r.exit_code == 8
    assert (r.counts.passed, r.counts.failed) == (1, 1)
    assert r.failing_tests == ["known_failure_trims_spaces"]
    assert not (FIXTURE / BUILD_DIR).exists() and not (FIXTURE / "build").exists()


@needs_toolchain
@needs_tidy
def test_real_clang_tidy_and_scan_on_the_fixture_without_modifying_it(tmp_path: Path):
    before = tree_hash(FIXTURE)
    rep = CppAdapter().autopsy(FIXTURE, "legacy-cpp-parser", tmp_path / "s", lambda r: None)
    assert tree_hash(FIXTURE) == before
    tools = {t.tool: t for t in rep.tools}
    assert (
        tools["clang-tidy"].measured
        and rep.lint_violations is not None
        and rep.lint_violations >= 2
    )
    titles = {f.title for f in rep.findings}
    assert {"Shell command via `system`", "Unbounded `strcpy`", "Unbounded `sprintf`"} <= titles
    assert any("API_TOKEN" in t for t in titles) and "No CI workflow found" in titles
    assert "legacy-token-do-not-commit" not in rep.model_dump_json()
    assert (rep.test_files, rep.test_functions, rep.test_config) == (1, 2, ["CTest"])


@needs_toolchain
def test_real_compiler_errors_become_a_failed_build(tmp_path: Path):
    work = tmp_path / "broken"
    shutil.copytree(FIXTURE, work)
    (work / "src" / "parser.cpp").write_text("this is not C++;\n")
    r = CppAdapter().characterize(work, HostFixtureRunner(tmp_path / "home"), lambda x: None, 180)
    assert r.status == "failed_to_execute" and r.build == "failed" and r.counts is None


def test_host_runner_exposes_the_toolchain_and_caps_memory_for_compilers(
    monkeypatch, tmp_path: Path
):
    host = HostFixtureRunner(tmp_path)
    assert host.cmake == shutil.which("cmake") and host.ctest == shutil.which("ctest")
    seen = []
    monkeypatch.setattr(
        runner_mod, "run_command", lambda *a, **k: seen.append((a[0][0], k["memory_bytes"]))
    )
    host.run(["/usr/bin/cmake", "--version"], tmp_path, 5)
    assert seen == [("/usr/bin/cmake", 2 * 1024**3)]  # compilers keep the address-space cap


def test_long_output_keeps_its_end_where_summaries_and_errors_are():
    body = "x" * (runner_mod.MAX_OUTPUT_CHARS * 2)
    capped = runner_mod._cap("HEAD" + body + "SUMMARY: 1 test failed")
    assert capped.startswith("HEAD") and capped.endswith("SUMMARY: 1 test failed")
    assert (
        "[... output truncated ...]" in capped and len(capped) < runner_mod.MAX_OUTPUT_CHARS + 100
    )


def test_secret_scan_reads_c_and_cpp_sources(tmp_path: Path):
    write(tmp_path, "src/k.cpp", 'static const char* PASSWORD = "hunter2hunter2";\n')
    write(tmp_path, "src/k.h", '#define API_TOKEN "abcdefgh12345678"\n')
    scan = scan_secrets(tmp_path)
    assert {f.file_path for f in scan.findings} >= {"src/k.cpp"}
    assert "hunter2hunter2" not in json.dumps([f.model_dump() for f in scan.findings])


# -------------------------------------------------------------------- selection
def test_adapter_selection_includes_cpp(tmp_path: Path):
    from app.adapters.ts_adapter import TypeScriptAdapter

    py, ts, cpp = OfflineAdapter(), TypeScriptAdapter(), CppAdapter()
    assert cpp.score(FIXTURE) == 4 and py.score(FIXTURE) == 0 and ts.score(FIXTURE) == 0
    assert choose_adapter([py, ts, cpp], FIXTURE) is cpp
    assert choose_adapter([py, ts, cpp], FIXTURES_DIR / "legacy-python-cli") is py
    assert choose_adapter([py, ts, cpp], FIXTURES_DIR / "legacy-ts-utils") is ts
    mixed = tmp_path / "mixed"
    write(mixed, "a.py", "x = 1\n")
    write(mixed, "b.cpp", "int a;\n")
    write(mixed, "c.cpp", "int b;\n")
    assert choose_adapter([py, ts, cpp], mixed) is cpp  # more C++ files than Python files
    write(mixed, "d.py", "y = 1\n")
    assert choose_adapter([py, ts, cpp], mixed) is py  # a tie goes to the earlier adapter
    write(mixed, "build/gen.cpp", "int c;\n")
    assert cpp.score(mixed) == 2  # build output is not counted


# ----------------------------------------------------------------------- planning
def report(findings: list[Finding], **kw) -> AutopsyReport:
    base = dict(
        repository_name="r", language="C++", frameworks=[], source_files=4, source_lines=100,
        dependency_files=[], dependencies=[], unpinned_dependencies=[], lockfile_present=False,
        test_files=1, test_functions=2, test_config=["CTest"], build_files=[], has_dockerfile=False,
        has_ci=False, findings=findings, tools=[], known_vulnerabilities=None, vulnerable_packages=None,
        lint_violations=10, audited_packages=None,
    )  # fmt: skip
    return AutopsyReport(**{**base, **kw})


def f(i: int, **kw) -> Finding:
    base = dict(
        id=f"F-{i:03d}",
        category="security",
        severity="high",
        title="t",
        evidence="e",
        source="cpp-scan",
    )
    return Finding(**{**base, **kw})


def test_roadmap_gives_cpp_specific_advice_and_a_task_for_non_security_tidy_findings():
    rep = report([
        f(1, title="Shell command via `system`", file_path="a.cpp", line=3),
        f(2, category="code_quality", severity="medium", source="clang-tidy", title="bugprone-use-after-move: x (2 occurrences)", file_path="a.cpp"),
    ])  # fmt: skip
    tasks = {t.title: t for t in build_roadmap(rep, BaselineReport(status="not_run")).tasks}
    assert "snprintf" in tasks["Shell command via `system`"].proposed_action
    tidy = tasks["Resolve clang-tidy findings"]
    assert (
        tidy.category == "code_quality"
        and "suppress a check only with a written reason" in tidy.proposed_action
    )


def test_vitality_treats_cpp_like_the_others_and_leaves_dependencies_unknown():
    rep = report([f(1, severity="critical")], lint_violations=20)
    s = compute_vitality(rep, BaselineReport(status="not_run"))
    dims = {d.name: d.score for d in s.dimensions}
    assert dims["dependencies"] is None and dims["tests"] is None
    assert dims["security"] == 60.0 and dims["code_quality"] is not None
    assert "linter findings" in next(d.basis for d in s.dimensions if d.name == "code_quality")


def test_consecration_flags_a_build_that_stopped_working_and_only_when_measured():
    def run(base_build: str, final_build: str):
        def mk(build: str) -> BaselineReport:
            return BaselineReport(
                status="completed",
                counts=__import__("app.adapters.models", fromlist=["TestCounts"]).TestCounts(
                    passed=1
                ),
                build=build,
            )  # type: ignore[arg-type]

        b, fin, rep = mk(base_build), mk(final_build), report([])
        s = compute_vitality(rep, b)
        from app.consecration import Check

        ok = Check(name="ci_workflows", status="not_configured", detail="")
        return verify(
            rep,
            b,
            rep,
            fin,
            ok,
            Check(name="docker_build", status="not_configured", detail=""),
            s,
            s,
        )

    broke = run("passed", "failed")
    assert broke.verdict == "failed" and "no longer builds" in broke.regressions[0]
    assert next(c for c in broke.checks if c.name == "build").status == "failed"
    assert run("passed", "passed").verdict == "verified"
    assert next(c for c in run("failed", "failed").checks if c.name == "build").detail.endswith(
        "as it did in the baseline."
    )
    assert "build" not in {c.name for c in run("not_measured", "not_measured").checks}


# ------------------------------------------------------------------ end to end
def make_client(settings: Settings, *, runner=None, tidy: bool = True, llm=None) -> TestClient:
    cpp = CppAdapter()
    if not tidy:
        cpp._tidy = None
    app = create_app(
        settings, executor=InlineExecutor(), adapter=OfflineAdapter(), cpp_adapter=cpp, llm_client=llm,
        **({"runner_factory": (lambda st, home: runner if st == "fixture" else None)} if runner else {}),
    )  # fmt: skip
    app.state.sse_poll_seconds = 0.01
    return TestClient(app)


def start(client: TestClient, source: dict, mode: str) -> dict:
    repo = client.post("/api/repositories/import", json=source).json()["id"]
    return client.post("/api/revive/start", json={"repository_id": repo, "mode": mode}).json()


def artifact(client: TestClient, run_id: str, name: str) -> str:
    arts = client.get(f"/api/revive/{run_id}/artifacts").json()
    art = next(a for a in arts if a["name"] == name)
    return client.get(f"/api/revive/{run_id}/artifacts/{art['id']}").text


@needs_toolchain
def test_inspect_on_the_cpp_fixture_uses_real_tools_through_the_whole_workflow(settings: Settings):
    before = tree_hash(FIXTURE)
    client = make_client(settings, tidy=False)
    run = start(client, {"fixture_id": "legacy-cpp-parser"}, "INSPECT")
    assert run["status"] == "COMPLETED", run["error"]
    assert json.loads(artifact(client, run["id"], "autopsy.json"))["language"] == "C++"
    base = json.loads(artifact(client, run["id"], "characterization.json"))
    assert (
        base["build"] == "passed"
        and base["counts"]["passed"] == 1
        and base["failing_tests"] == ["known_failure_trims_spaces"]
    )
    assert "Build: passed" in artifact(client, run["id"], "CHARACTERIZATION_REPORT.md")
    assert tree_hash(FIXTURE) == before  # builds happen in disposable copies, never in the fixture


@needs_toolchain
def test_revive_on_the_cpp_fixture_patches_ci_only_then_verifies_with_a_real_build(
    settings: Settings,
):
    client = make_client(settings, tidy=False)
    run = start(client, {"fixture_id": "legacy-cpp-parser"}, "REVIVE")
    assert run["status"] == "AWAITING_APPROVAL"
    ps = json.loads(artifact(client, run["id"], "patches.v1.json"))
    assert [p["path"] for p in ps["patches"]] == [".github/workflows/ci.yml"]
    ci = ps["patches"][0]["after_content"]
    assert (
        "ctest --test-dir build" in ci and "cmake -S . -B build" in ci and "pip install" not in ci
    )
    reasons = [s["reason"] for s in ps["skipped"]]
    assert sum("person must decide" in r for r in reasons) == 4  # system, strcpy, sprintf, secret
    assert any("Dockerfile template for C++" in r for r in reasons)
    final = client.post(
        f"/api/revive/approve/{run['id']}", json={"decision": "approve", "roadmap_version": 1}
    ).json()
    assert final["status"] == "COMPLETED", final["error"]
    checks = {
        c["name"]: c for c in json.loads(artifact(client, run["id"], "verification.json"))["checks"]
    }
    assert checks["build"]["status"] == "passed" and checks["tests"]["status"] == "passed"
    assert "1 failure(s) existed in the baseline; 0 new" in checks["tests"]["detail"]
    work = settings.data_dir / "runs" / run["id"] / "work"
    assert (work / ".github" / "workflows" / "ci.yml").exists() and not (work / BUILD_DIR).exists()
    assert not (FIXTURE / ".github").exists()


def test_a_patch_that_breaks_the_build_is_rolled_back(settings: Settings):
    class BreakingRunner(CMakeRunner):
        """Baseline builds; after the patch the build fails."""

        def __init__(self) -> None:
            super().__init__()
            self.builds = 0

        def run(self, argv, cwd, timeout):
            if "--build" in argv:
                self.builds += 1
                if self.builds == 2:
                    self._codes = [0, 2]
                    self.calls.append((list(argv), Path(cwd)))
                    return CommandResult(
                        argv=list(argv),
                        exit_code=2,
                        stdout="",
                        stderr="src/a.cpp:3:1: error: expected ';'",
                        duration_seconds=0.1,
                    )
            return super().run(argv, cwd, timeout)

    client = make_client(settings, runner=BreakingRunner(), tidy=False)
    run = start(client, {"fixture_id": "legacy-cpp-parser"}, "REVIVE")
    final = client.post(
        f"/api/revive/approve/{run['id']}", json={"decision": "approve", "roadmap_version": 1}
    ).json()
    assert final["status"] == "FAILED" and "rolled back" in final["error"]
    assert "no longer builds" in final["error"] or "could not run after the patch" in final["error"]
    events = parse_sse(client.get(f"/api/revive/stream/{run['id']}").text)
    assert (
        "rollback_completed" in [e["type"] for e in events] and events[-1]["type"] == "run_failed"
    )
    work = settings.data_dir / "runs" / run["id"] / "work"
    assert not (work / ".github").exists()  # restored from the snapshot
    assert (settings.data_dir / "runs" / run["id"] / "work-failed" / ".github").exists()


def test_a_toolchain_less_sandbox_still_runs_bundled_fixtures_on_the_host(settings: Settings):
    class DockerLike:
        """Has no cmake attribute, like the Docker sandbox runner."""

        python = "python"

        def run(self, *a, **k):  # pragma: no cover - must never be used for C++
            raise AssertionError("the toolchain-less runner must not be used")

    if not HAVE_TOOLCHAIN:
        pytest.skip("cmake, ctest or a C++ compiler is missing; this test did NOT run")
    client = make_client(settings, runner=DockerLike(), tidy=False)
    run = start(client, {"fixture_id": "legacy-cpp-parser"}, "INSPECT")
    base = json.loads(artifact(client, run["id"], "characterization.json"))
    assert (
        run["status"] == "COMPLETED"
        and base["status"] == "completed"
        and base["counts"]["passed"] == 1
    )


def test_untrusted_cpp_is_never_built_and_the_reason_is_the_toolchain_not_the_sandbox(
    settings: Settings, allowed_root: Path
):
    repo = allowed_root / "proj"
    write(
        repo,
        "CMakeLists.txt",
        "project(p CXX)\nexecute_process(COMMAND touch /tmp/necro-should-not-exist)\n",
    )
    write(repo, "a.cpp", "int main() { return 0; }\n")
    write(repo, "tests/t.cpp", "int main() { return 0; }\n")
    from tests.conftest import FakeRunner

    class SandboxWithoutToolchain(FakeRunner):
        pass

    sandbox = SandboxWithoutToolchain()
    client = make_client(settings, runner=sandbox, tidy=False)
    run = start(client, {"local_path": str(repo)}, "INSPECT")
    base = json.loads(artifact(client, run["id"], "characterization.json"))
    assert run["status"] == "COMPLETED" and base["status"] == "not_run" and base["counts"] is None
    assert sandbox.calls == [] and not Path("/tmp/necro-should-not-exist").exists()
    assert (repo / "CMakeLists.txt").read_text().count("execute_process") == 1


def test_cpp_findings_are_never_sent_to_a_model(settings: Settings):
    from tests.test_llm_patches import FakeClient

    llm = FakeClient()
    client = make_client(settings, runner=CMakeRunner(), tidy=False, llm=llm)
    run = start(client, {"fixture_id": "legacy-cpp-parser"}, "PREVIEW")
    ps = json.loads(artifact(client, run["id"], "patches.v1.json"))
    assert llm.calls == [] and ps["llm"]["requests"] == 0
    assert any("Only Python findings" in s["reason"] for s in ps["skipped"])
