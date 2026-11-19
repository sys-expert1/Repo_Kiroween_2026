from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

from app.adapters import secrets_scan
from app.adapters.models import BaselineReport
from app.adapters.secrets_scan import scan_secrets
from app.agents.llm_patches import LLMPatcher
from app.agents.patches import generate_patchset
from app.agents.roadmap import build_roadmap
from app.scoring.health_score import compute_vitality
from tests.conftest import OfflineAdapter
from tests.test_llm_patches import FakeClient
from tests.test_scoring import baseline, report


def write(root: Path, rel: str, text: str | bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode())


AWS = "AKIA" + "IOSFODNN7EXAMPLE"
GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SLACK = "xoxb-" + "1234567890-abcdefghij"


def test_known_token_shapes_are_found_without_echoing_them(tmp_path: Path):
    write(tmp_path, "deploy.sh", f"export A={AWS}\nexport B={GH}\n")
    write(tmp_path, "ci.yml", f"slack: {SLACK}\n")
    write(tmp_path, "id.json", '{"k": "-----BEGIN RSA PRIVATE KEY-----"}')
    scan = scan_secrets(tmp_path)
    kinds = {(f.file_path, f.line): f.title.split(": ")[1] for f in scan.findings}
    assert kinds[("deploy.sh", 1)] == "AWS access key id"
    assert kinds[("deploy.sh", 2)] == "GitHub token"
    assert kinds[("ci.yml", 1)] == "Slack token"
    assert kinds[("id.json", 1)] == "private key"
    blob = json.dumps([f.model_dump() for f in scan.findings])
    for secret in (AWS, GH, SLACK):
        assert secret not in blob  # a finding never carries the value
    sev = {f.title.split(": ")[1]: f.severity for f in scan.findings}
    assert sev["private key"] == "critical" and sev["AWS access key id"] == "high"
    assert all(f.source == "secrets" and f.category == "security" for f in scan.findings)


def test_assigned_values_are_found_and_the_value_is_not_shown(tmp_path: Path):
    write(tmp_path, ".env", "DATABASE_PASSWORD=hunter2hunter2\nDEBUG=true\n")
    write(tmp_path, "app.ini", "[db]\napi_key = abcd1234efgh5678\n")
    write(tmp_path, "cfg.json", '{"auth_token": "s3cr3t-value-123"}')
    scan = scan_secrets(tmp_path)
    assert {f.file_path for f in scan.findings} == {".env", "app.ini", "cfg.json"}
    blob = json.dumps([f.model_dump() for f in scan.findings])
    for value in ("hunter2hunter2", "abcd1234efgh5678", "s3cr3t-value-123"):
        assert value not in blob
    assert all(f.severity == "medium" and "value is not shown" in f.evidence for f in scan.findings)


@pytest.mark.parametrize(
    "line",
    [
        "password=changeme",
        "API_KEY=${API_KEY}",
        "token: <your-token-here>",
        "secret={{ vault.secret }}",
        "password=your-password-here",
        "token = process.env.TOKEN",
        "api_key=example-key-123",
        "password=short",  # under 8 characters
        "password=true",
        "name=Alice",
    ],
)
def test_placeholders_and_references_are_not_reported(tmp_path: Path, line: str):
    write(tmp_path, "settings.env", line + "\n")
    assert scan_secrets(tmp_path).findings == []


def test_unrelated_files_lockfiles_binaries_symlinks_and_vendor_dirs_are_skipped(tmp_path: Path):
    secret = f"token={GH}\n"
    write(tmp_path, "notes.txt", secret)  # not a covered type
    write(tmp_path, "package-lock.json", secret)
    write(tmp_path, "app.py", secret)  # Python is covered by the AST scan
    write(tmp_path, "node_modules/x/cfg.json", secret)
    write(tmp_path, "blob.json", b"\x00\x01" + secret.encode())
    outside = tmp_path.parent / "outside-secret.env"
    outside.write_text(secret)
    (tmp_path / "link.env").symlink_to(outside)
    assert scan_secrets(tmp_path).findings == []
    assert scan_secrets(tmp_path).files == 0


def test_limits_stop_the_scan_and_say_so(tmp_path: Path, monkeypatch):
    write(tmp_path, "a.env", "".join(f"API_KEY_{i}=value{i:08d}\n" for i in range(40)))
    scan = scan_secrets(tmp_path)
    assert len(scan.findings) == 25 and scan.truncated
    big = tmp_path / "big"
    big.mkdir()
    write(big, "huge.json", "x" * (secrets_scan.MAX_FILE_BYTES + 1))
    assert all(f.file_path != "big/huge.json" for f in scan_secrets(big).findings)


def test_the_adapter_reports_the_scan_as_a_measured_tool(tmp_path: Path):
    write(tmp_path, "app.py", "x = 1\n")
    write(tmp_path, ".env", "SECRET_KEY=abcdefgh12345678\n")
    rep = OfflineAdapter().autopsy(tmp_path, "r", tmp_path / "s", lambda r: None)
    tool = next(t for t in rep.tools if t.tool == "secret-scan")
    assert tool.measured and "1 possible secret(s) in 1 config" in tool.summary
    assert any(f.source == "secrets" for f in rep.findings)
    assert "abcdefgh12345678" not in rep.model_dump_json()


def test_a_secret_in_a_config_file_becomes_a_task_with_no_automatic_patch(tmp_path: Path):
    write(tmp_path, "app.py", "x = 1\n")
    write(tmp_path, "test_app.py", "def test_a():\n    assert True\n")
    write(tmp_path, ".env", "SECRET_KEY=abcdefgh12345678\n")
    adapter = OfflineAdapter()
    rep = adapter.autopsy(tmp_path, "r", tmp_path / "s", lambda r: None)
    rm = build_roadmap(rep, BaselineReport(status="not_run"))
    task = next(t for t in rm.tasks if ".env" in t.title)
    assert "rotate the credential" in task.proposed_action and task.affected_files == [".env"]
    fixer = partial(adapter.fix_lint, scratch=tmp_path / "s", observe=lambda r: None)
    ps = generate_patchset(rm, rep, tmp_path, fixer)
    assert not any(p.path == ".env" for p in ps.patches)
    assert any(s.task_id == task.id and "person must decide" in s.reason for s in ps.skipped)


def test_config_file_secrets_are_never_sent_to_a_model(tmp_path: Path):
    write(tmp_path, "app.py", "x = 1\n")
    write(tmp_path, "test_app.py", "def test_a():\n    assert True\n")
    write(tmp_path, ".env", "SECRET_KEY=abcdefgh12345678\n")
    adapter = OfflineAdapter()
    rep = adapter.autopsy(tmp_path, "r", tmp_path / "s", lambda r: None)
    rm = build_roadmap(rep, BaselineReport(status="not_run"))
    client = FakeClient()
    fixer = partial(adapter.fix_lint, scratch=tmp_path / "s", observe=lambda r: None)
    ps = generate_patchset(rm, rep, tmp_path, fixer, LLMPatcher(client))
    assert client.calls == [] and any("Only Python findings" in s.reason for s in ps.skipped)


def test_secret_findings_lower_the_security_score():
    from app.adapters.models import Finding

    clean = compute_vitality(report(), baseline(passed=1))
    leaky = compute_vitality(
        report(findings=[Finding(id="F-1", category="security", severity="critical", title="t",
                                 evidence="e", source="secrets")]),
        baseline(passed=1),
    )  # fmt: skip
    pick = lambda s: next(d.score for d in s.dimensions if d.name == "security")  # noqa: E731
    assert pick(clean) == 100.0 and pick(leaky) == 60.0
