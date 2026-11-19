from __future__ import annotations

import sys
from pathlib import Path

from app.execution.redact import redact
from app.execution.runner import HostFixtureRunner, build_env, run_command


def test_redacts_key_value_secrets():
    text = 'SECRET_KEY = "hunter2"\npassword: abc123\napi_key=sk-live-999 token=T0K3N'
    out = redact(text)
    for leaked in ("hunter2", "abc123", "sk-live-999", "T0K3N"):
        assert leaked not in out
    assert "SECRET_KEY" in out and "***" in out


def test_redacts_url_credentials_and_bearer():
    out = redact("clone https://user:pa55@github.com/o/r and Authorization: Bearer abcdef123456789")
    assert "pa55" not in out and "user" not in out and "abcdef123456789" not in out
    assert "github.com/o/r" in out


def test_redact_leaves_ordinary_text():
    assert redact("1 failed, 1 passed in 0.01s") == "1 failed, 1 passed in 0.01s"


def test_run_command_captures_output_and_exit_code(tmp_path: Path):
    r = run_command(
        [sys.executable, "-c", "print('out'); import sys; sys.stderr.write('err'); sys.exit(3)"],
        tmp_path, 20, build_env(tmp_path),
    )  # fmt: skip
    assert (r.exit_code, r.stdout.strip(), r.stderr.strip(), r.timed_out) == (
        3,
        "out",
        "err",
        False,
    )


def test_run_command_does_not_use_a_shell(tmp_path: Path):
    marker = tmp_path / "pwned"
    r = run_command(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", f"; touch {marker}"],
        tmp_path, 20, build_env(tmp_path),
    )  # fmt: skip
    assert r.exit_code == 0 and not marker.exists()


def test_run_command_kills_whole_process_group_on_timeout(tmp_path: Path):
    marker = tmp_path / "child-survived"
    code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', \"import time; time.sleep(3); "
        f"open(r'{marker}', 'w').write('x')\"])\n"
        "time.sleep(30)\n"
    )
    r = run_command([sys.executable, "-c", code], tmp_path, 1, build_env(tmp_path))
    assert r.timed_out and r.exit_code is None and "Timed out" in (r.error or "")
    import time

    time.sleep(3.5)
    assert not marker.exists()  # the grandchild was killed too


def test_run_command_missing_binary_is_reported_not_raised(tmp_path: Path):
    r = run_command(["definitely-not-a-binary"], tmp_path, 5, build_env(tmp_path))
    assert r.exit_code is None and r.error


def test_env_is_minimal_and_excludes_host_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
    monkeypatch.setenv("GITHUB_TOKEN", "leak-me-too")
    env = build_env(tmp_path)
    assert "AWS_SECRET_ACCESS_KEY" not in env and "GITHUB_TOKEN" not in env
    r = run_command([sys.executable, "-c", "import os; print(dict(os.environ))"], tmp_path, 20, env)
    assert "leak-me" not in r.stdout


def test_run_command_output_is_redacted(tmp_path: Path):
    r = run_command(
        [sys.executable, "-c", "print('password=swordfish')"], tmp_path, 20, build_env(tmp_path)
    )
    assert "swordfish" not in r.stdout


def test_host_fixture_runner_applies_memory_limit(tmp_path: Path):
    runner = HostFixtureRunner(tmp_path / "home")
    r = runner.run([sys.executable, "-c", "print(1+1)"], tmp_path, 20)
    assert r.exit_code == 0 and r.stdout.strip() == "2"
    big = runner.run([sys.executable, "-c", "x = bytearray(4 * 1024**3)"], tmp_path, 20)
    assert big.exit_code != 0  # RLIMIT_AS (2 GiB) stops the allocation
