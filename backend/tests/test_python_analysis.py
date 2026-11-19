from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from app.adapters import python_adapter
from app.adapters.python_adapter import PythonAdapter
from app.adapters.python_ast import scan_python
from app.adapters.python_deps import parse_pyproject, parse_requirement_line, parse_requirements
from app.config import FIXTURES_DIR
from app.execution.runner import CommandResult, HostFixtureRunner


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


# ------------------------------------------------------------------ dependencies
def test_requirement_parsing_pins_and_ranges():
    flask = parse_requirement_line("Flask==0.12.2  # old", "r.txt")
    assert (flask.name, flask.pinned_version) == ("flask", "0.12.2")
    rng = parse_requirement_line("requests>=2.0,<3", "r.txt")
    assert rng.pinned_version is None and rng.specifier.startswith(">=2.0")
    extra = parse_requirement_line("uvicorn[standard]==0.30.0", "r.txt")
    assert (extra.name, extra.pinned_version) == ("uvicorn", "0.30.0")
    assert parse_requirement_line("Foo_Bar.baz==1", "r.txt").name == "foo-bar-baz"


@pytest.mark.parametrize(
    "line",
    ["", "# comment", "-r other.txt", "-e .", "git+https://x/y.git#egg=z", "pkg @ https://x/y.whl"],
)
def test_requirement_parsing_skips_non_requirements(line: str):
    assert parse_requirement_line(line, "r.txt") is None


def test_wildcard_is_not_pinned():
    assert parse_requirement_line("django==3.*", "r.txt").pinned_version is None


def test_pyproject_pep621_and_poetry():
    deps = parse_pyproject(
        textwrap.dedent(
            """
            [project]
            dependencies = ["click==8.0.0", "rich>=13"]
            [project.optional-dependencies]
            dev = ["pytest==7.0.0"]
            [tool.poetry.dependencies]
            python = "^3.8"
            flask = "1.0.2"
            requests = {version = "^2.0"}
            """
        ),
        "pyproject.toml",
    )
    pins = {d.name: d.pinned_version for d in deps}
    assert pins == {
        "click": "8.0.0", "rich": None, "pytest": "7.0.0", "flask": "1.0.2", "requests": None,
    }  # fmt: skip


def test_pyproject_invalid_toml_raises_value_error():
    with pytest.raises(ValueError):
        parse_pyproject("[project\nbroken", "pyproject.toml")


def test_parse_requirements_ignores_blank_and_comments():
    assert [d.name for d in parse_requirements("a==1\n\n# x\nb>=2\n", "r.txt")] == ["a", "b"]


# ------------------------------------------------------------------------- AST
def test_ast_flags_unsafe_calls_with_dynamic_vs_constant_severity(tmp_path: Path):
    write(
        tmp_path, "app.py",
        """
        import os, subprocess, pickle, yaml
        def f(cmd, data):
            os.system("ls")
            os.popen("report " + cmd)
            subprocess.run(cmd, shell=True)
            subprocess.run(["ls"], shell=False)
            eval(data)
            pickle.loads(data)
            yaml.load(data)
            yaml.load(data, Loader=yaml.SafeLoader)
        """,
    )  # fmt: skip
    found = {(f.title, f.severity) for f in scan_python(tmp_path).findings}
    assert ("Shell command via `os.system`", "medium") in found
    assert ("Shell command via `os.popen`", "high") in found
    assert ("`subprocess.run` with shell=True", "high") in found
    assert ("Use of `eval`", "high") in found
    assert ("Unsafe deserialization via `pickle.loads`", "medium") in found
    assert ("`yaml.load` without an explicit Loader", "medium") in found
    assert len(found) == 6  # shell=False and yaml.load(Loader=...) are not flagged


def test_ast_secret_detection_never_echoes_the_value(tmp_path: Path):
    write(tmp_path, "cfg.py", 'SECRET_KEY = "hunter2"\nAPI_TOKEN: str = "abc123"\nNAME = "ok"\n')
    write(tmp_path, "tests/test_x.py", 'PASSWORD = "fixture-only"\ndef test_a():\n    pass\n')
    result = scan_python(tmp_path)
    secrets = [f for f in result.findings if "secret-like" in f.title]
    assert {f.file_path for f in secrets} == {"cfg.py"}  # test files are not reported
    assert len(secrets) == 2
    blob = json.dumps([f.model_dump() for f in result.findings])
    assert "hunter2" not in blob and "abc123" not in blob and "fixture-only" not in blob


def test_ast_environment_lookup_is_not_a_secret(tmp_path: Path):
    write(tmp_path, "cfg.py", 'import os\nSECRET_KEY = os.environ["SECRET_KEY"]\nTOKEN = ""\n')
    assert scan_python(tmp_path).findings == []


def test_ast_counts_tests_and_collects_imports(tmp_path: Path):
    write(tmp_path, "pkg/mod.py", "import flask\nfrom click import command\n")
    write(
        tmp_path, "tests/test_mod.py",
        "class TestA:\n    def test_one(self): ...\n    def helper(self): ...\ndef test_two(): ...\n",
    )  # fmt: skip
    r = scan_python(tmp_path)
    assert (r.files, r.test_files, r.test_functions) == (2, 1, 2)
    assert {"flask", "click"} <= r.imports


def test_ast_reports_python2_syntax_without_crashing(tmp_path: Path):
    write(tmp_path, "old.py", 'print "hello"\n')
    write(tmp_path, "new.py", "x = 1\n")
    r = scan_python(tmp_path)
    assert r.files == 2
    [f] = r.findings
    assert f.file_path == "old.py" and f.category == "code_quality"


def test_ast_skips_symlinks_and_vendor_dirs(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    write(outside, "evil.py", "import os\nos.system(input())\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    write(repo, "ok.py", "x = 1\n")
    (repo / "link.py").symlink_to(outside / "evil.py")
    (repo / "linkdir").symlink_to(outside, target_is_directory=True)
    write(repo, "node_modules/a.py", "import os\nos.system(input())\n")
    r = scan_python(repo)
    assert r.files == 1 and r.findings == []


def test_ast_never_executes_scanned_code(tmp_path: Path):
    marker = tmp_path / "executed"
    write(tmp_path, "boom.py", f"open({str(marker)!r}, 'w').write('x')\n")
    scan_python(tmp_path)
    assert not marker.exists()


# --------------------------------------------------------------------- adapter
def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES_DIR / "legacy-python-cli", repo)
    return repo


def fake_pip_audit(monkeypatch, payload: dict | None, exit_code: int = 1, stderr: str = ""):
    real = python_adapter.run_command

    def fake(argv, cwd, timeout, env, **kw):
        if "pip_audit" in argv:
            return CommandResult(
                argv=list(argv), exit_code=exit_code, stderr=stderr, duration_seconds=0.1,
                stdout=json.dumps(payload) if payload is not None else "",
            )  # fmt: skip
        return real(argv, cwd, timeout, env, **kw)

    monkeypatch.setattr(python_adapter, "run_command", fake)


AUDIT = {
    "dependencies": [
        {
            "name": "requests", "version": "2.19.1",
            "vulns": [
                {"id": "PYSEC-1", "fix_versions": ["0.12.3", "1.0"]},
                {"id": "PYSEC-2", "fix_versions": ["1.0"]},
                {"id": "PYSEC-2", "fix_versions": ["1.0"]},
            ],
        },
        {"name": "pyyaml", "version": "3.13", "vulns": []},
    ]
}  # fmt: skip


def test_autopsy_on_fixture_reports_real_evidence(tmp_path: Path, monkeypatch):
    fake_pip_audit(monkeypatch, AUDIT)
    seen: list[CommandResult] = []
    report = PythonAdapter().autopsy(
        make_repo(tmp_path), "legacy", tmp_path / "scratch", seen.append
    )
    titles = [f.title for f in report.findings]
    assert report.frameworks == ["Click"]
    assert report.test_files == 1 and report.test_functions == 2
    assert report.known_vulnerabilities == 2 and report.vulnerable_packages == 1  # deduplicated
    assert any("requests 2.19.1 has 2 known vulnerabilities" in t for t in titles)
    assert any("API_TOKEN" in t for t in titles) and any("os.popen" in t for t in titles)
    assert any(t.startswith("F401") for t in titles)
    assert [t.tool for t in report.tools] == ["secret-scan", "ruff", "pip-audit"]
    assert all(t.measured for t in report.tools) and len(seen) == 2
    ids = [f.id for f in report.findings]
    assert ids == [f"F-{i:03d}" for i in range(1, len(ids) + 1)]
    sev = [f.severity for f in report.findings]
    assert sev == sorted(sev, key=["critical", "high", "medium", "low", "info"].index)


def test_autopsy_does_not_modify_the_repository(tmp_path: Path, monkeypatch):
    fake_pip_audit(monkeypatch, AUDIT)
    repo = make_repo(tmp_path)
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    PythonAdapter().autopsy(repo, "legacy", tmp_path / "scratch", lambda r: None)
    after = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.parametrize(
    ("payload", "exit_code", "stderr"),
    [(None, 1, "Connection error: network unreachable"), ({"unexpected": 1}, 1, "")],
)
def test_failed_audit_is_not_measured_not_zero(tmp_path, monkeypatch, payload, exit_code, stderr):
    fake_pip_audit(monkeypatch, payload, exit_code, stderr)
    report = PythonAdapter().autopsy(make_repo(tmp_path), "legacy", tmp_path / "s", lambda r: None)
    audit = next(t for t in report.tools if t.tool == "pip-audit")
    assert audit.measured is False and audit.summary.startswith("Not measured")
    assert report.known_vulnerabilities is None and report.vulnerable_packages is None
    assert not any(f.source == "pip-audit" for f in report.findings)


def test_no_pinned_dependencies_means_audit_not_measured(tmp_path: Path):
    write(tmp_path, "requirements.txt", "requests>=2\nflask\n")
    write(tmp_path, "app.py", "x = 1\n")
    report = PythonAdapter().autopsy(tmp_path, "r", tmp_path / "s", lambda r: None)
    assert report.known_vulnerabilities is None
    assert report.unpinned_dependencies == ["flask", "requests"]
    assert any("not pinned exactly" in f.title for f in report.findings)


def test_structure_findings_for_bare_repo(tmp_path: Path):
    write(tmp_path, "main.py", "x = 1\n")
    titles = {
        f.title
        for f in PythonAdapter().autopsy(tmp_path, "r", tmp_path / "s", lambda r: None).findings
    }
    assert {"No tests were found", "No dependency manifest found", "No CI workflow found"} <= titles


def test_ci_and_docker_are_detected(tmp_path: Path):
    write(tmp_path, "a.py", "x = 1\n")
    write(tmp_path, ".github/workflows/ci.yml", "name: ci\n")
    write(tmp_path, "Dockerfile", "FROM python:3.12\n")
    report = PythonAdapter().autopsy(tmp_path, "r", tmp_path / "s", lambda r: None)
    assert report.has_ci and report.has_dockerfile


def test_detect(tmp_path: Path):
    assert PythonAdapter().detect(make_repo(tmp_path))
    empty = tmp_path / "empty"
    empty.mkdir()
    write(empty, "main.go", "package main\n")
    assert not PythonAdapter().detect(empty)


# -------------------------------------------------------------- characterization
def result(
    stdout: str, code: int | None, stderr: str = "", timed_out: bool = False
) -> CommandResult:
    return CommandResult(
        argv=["pytest"], exit_code=code, stdout=stdout, stderr=stderr, duration_seconds=0.2,
        timed_out=timed_out, error="Timed out after 5s" if timed_out else None,
    )  # fmt: skip


def test_parse_pytest_failures_and_counts():
    out = (
        "FAILED t/test_a.py::test_x - assert 1 == 2\nERROR t/test_b.py::test_y - boom\n"
        "1 failed, 3 passed, 2 skipped, 1 error, 1 xfailed in 0.50s\n"
    )
    r = PythonAdapter.parse_pytest(result(out, 1))
    assert r.status == "completed"
    c = r.counts
    assert (c.passed, c.failed, c.skipped, c.errors, c.xfailed, c.total) == (3, 1, 2, 1, 1, 8)
    assert r.failing_tests == ["t/test_a.py::test_x", "t/test_b.py::test_y"]
    assert r.coverage == "not_measured" and r.build == "not_measured"


def test_parse_pytest_all_pass_and_no_tests():
    ok = PythonAdapter.parse_pytest(result("12 passed in 1.00s\n", 0))
    assert ok.counts.passed == 12 and ok.failing_tests == []
    none = PythonAdapter.parse_pytest(result("no tests ran in 0.01s\n", 5))
    assert none.status == "completed" and none.counts.total == 0 and "no tests" in none.reason


@pytest.mark.parametrize(
    ("res", "reason"),
    [
        (result("", None, timed_out=True), "Timed out"),
        (result("", 2, "KeyboardInterrupt"), "interrupted"),
        (result("", 4, "usage: pytest"), "usage error"),
        (result("", 1, "/usr/bin/python: No module named pytest"), "not installed"),
        (result("garbage", 0), "could not be interpreted"),
    ],
)
def test_parse_pytest_execution_failures_are_not_reported_as_results(res, reason):
    r = PythonAdapter.parse_pytest(res)
    assert r.status == "failed_to_execute" and reason in r.reason
    assert r.counts is None  # unknown, never zero


def test_characterize_without_sandbox_is_not_run(tmp_path: Path):
    r = PythonAdapter().characterize(tmp_path, None, lambda x: None, 10)
    assert r.status == "not_run" and r.counts is None and "unknown, not zero" in r.reason


def test_characterize_real_pytest_run_on_fixture_copy(tmp_path: Path):
    repo = make_repo(tmp_path)
    runner = HostFixtureRunner(tmp_path / "home")
    r = PythonAdapter().characterize(repo, runner, lambda x: None, 120)
    assert r.status == "completed" and r.exit_code == 1
    assert (r.counts.passed, r.counts.failed) == (1, 1)
    assert r.failing_tests == ["tests/test_inventory.py::test_total_items_known_failure"]
    assert not (repo / ".pytest_cache").exists()  # cache provider is disabled
