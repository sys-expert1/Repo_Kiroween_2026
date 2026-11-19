"""Tests that run the sandbox against a REAL container runtime (Docker, or Podman through
NECRO_DOCKER_BIN). They are skipped when no runtime or sandbox image is usable.

A skip means the sandbox was NOT verified. `scripts/verify_sandbox.sh` builds the image, runs this
file and fails if anything was skipped.

Status: these tests have never run. They were written where no container runtime could start
containers (see the README), so treat the first run on a real machine as their own test: a failure
may be a bug in the sandbox or in a test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.adapters.python_adapter import PythonAdapter
from app.config import FIXTURES_DIR
from app.execution import sandbox as sb
from app.execution.runner import CommandResult
from app.execution.sandbox import DockerSandboxRunner, SandboxConfig, sandbox_status

DOCKER_BIN = os.environ.get("NECRO_DOCKER_BIN", "docker")
IMAGE = os.environ.get("NECRO_SANDBOX_IMAGE", "necromancer-sandbox:latest")


def _runtime_ready() -> bool:
    if shutil.which(DOCKER_BIN) is None:
        return False
    sb._cache.clear()
    config = SandboxConfig(image=IMAGE, docker_bin=DOCKER_BIN)
    return sandbox_status(config, Path(tempfile.mkdtemp()), use_cache=False).available


needs_docker = pytest.mark.skipif(
    not _runtime_ready(),
    reason=f"No usable container runtime ('{DOCKER_BIN}') or sandbox image '{IMAGE}' here. "
    "This test did NOT run, so the sandbox is NOT verified",
)


@dataclass
class Box:
    root: Path
    work: Path
    home: Path

    def run(self, argv: list[str], timeout: float = 60, **config) -> CommandResult:
        cfg = SandboxConfig(image=IMAGE, docker_bin=DOCKER_BIN, **config)
        return DockerSandboxRunner(cfg, self.root, self.home).run(argv, self.work, timeout)


@pytest.fixture
def box(tmp_path: Path) -> Box:
    root = tmp_path / "runs"
    work = root / "r1" / "work"
    work.mkdir(parents=True)
    return Box(root=root, work=work, home=tmp_path / "home")


def container_ids() -> set[str]:
    out = subprocess.run(
        [DOCKER_BIN, "ps", "-a", "-q"], capture_output=True, text=True, timeout=30, check=True
    )
    return set(out.stdout.split())


def status_lines(text: str) -> dict[str, str]:
    return {
        k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in text.splitlines()) if v
    }


@needs_docker
def test_the_process_is_non_root_with_no_capabilities_and_no_new_privileges(box: Box):
    r = box.run(["sh", "-c", "id -u; grep -E '^(CapEff|CapBnd|NoNewPrivs):' /proc/self/status"])
    assert r.exit_code == 0, r.stderr
    uid, *rest = r.stdout.splitlines()
    fields = status_lines("\n".join(rest))
    assert uid == "65534"
    assert int(fields["CapEff"], 16) == 0 and int(fields["CapBnd"], 16) == 0  # --cap-drop ALL
    assert fields["NoNewPrivs"] == "1"


@needs_docker
def test_the_network_is_disabled(box: Box):
    r = box.run(["python", "-c", "import socket; socket.create_connection(('1.1.1.1', 53), 3)"])
    assert r.exit_code != 0 and "Network is unreachable" in r.stderr
    interfaces = box.run(["sh", "-c", "cut -d: -f1 /proc/net/dev | tail -n +3 | tr -d ' '"])
    assert interfaces.stdout.split() == ["lo"]  # only loopback exists


@needs_docker
def test_the_root_filesystem_is_read_only_but_the_work_directory_and_tmp_are_writable(box: Box):
    r = box.run(["sh", "-c", "touch /etc/pwned"])
    assert r.exit_code != 0 and "Read-only file system" in r.stderr
    ok = box.run(["sh", "-c", "echo hello > /work/out.txt && echo scratch > /tmp/scratch.txt"])
    assert ok.exit_code == 0, ok.stderr  # the unprivileged user can write to the mounted copy
    assert (box.work / "out.txt").read_text() == "hello\n"  # and the host sees it


@needs_docker
def test_only_the_work_directory_is_mounted_and_never_home_or_the_runtime_socket(
    box: Box, tmp_path: Path
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "canary.txt").write_text("host secret")
    r = box.run(
        [
            "sh", "-c",
            f"test -e {outside}/canary.txt && echo VISIBLE || echo HIDDEN; "
            "test -S /var/run/docker.sock && echo SOCKET || echo NOSOCKET; "
            "cut -d' ' -f2 /proc/mounts",
        ]
    )  # fmt: skip
    assert r.exit_code == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[:2] == ["HIDDEN", "NOSOCKET"]
    targets = lines[2:]
    assert "/work" in targets
    home = str(Path.home())
    assert not [t for t in targets if t == home or t.startswith(home + "/")]
    assert not [t for t in targets if t.endswith((".sock", "docker.sock", "podman.sock"))]


@needs_docker
def test_the_memory_limit_is_enforced(box: Box):
    allocate = "b = b'x' * ({mb} * 1024 * 1024); print('ALLOCATED')"
    # Control: the container runs, and an allocation inside the limit works. Without this, a
    # runtime that cannot start containers at all would also make the check below pass.
    fits = box.run(["python", "-c", allocate.format(mb=32)], memory="128m")
    assert fits.exit_code == 0 and "ALLOCATED" in fits.stdout, fits.stderr
    over = box.run(["python", "-c", allocate.format(mb=512)], memory="128m")
    assert "ALLOCATED" not in over.stdout
    # Killed by the out-of-memory killer (137, or -9 as a signal) or refused with MemoryError.
    assert over.exit_code in (137, -9) or "MemoryError" in over.stderr, (
        over.exit_code,
        over.stderr,
    )


@needs_docker
def test_the_process_limit_is_enforced(box: Box):
    code = (
        "import subprocess\n"
        "kids = []\n"
        "try:\n"
        "    for _ in range(150):\n"
        "        kids.append(subprocess.Popen(['sleep', '30']))\n"
        "    print('NOT BLOCKED', len(kids))\n"
        "except OSError:\n"
        "    print('BLOCKED', len(kids))\n"
        "for k in kids:\n"
        "    k.kill()\n"
    )
    r = box.run(["python", "-c", code], pids_limit=32)
    assert r.stdout.startswith("BLOCKED"), r.stdout + r.stderr
    assert int(r.stdout.split()[1]) < 40  # a fork bomb stops at the limit


@needs_docker
def test_a_timeout_kills_the_container_and_leaves_none_behind(box: Box):
    before = container_ids()
    started = time.monotonic()
    r = box.run(["sleep", "120"], timeout=3)
    assert r.timed_out and time.monotonic() - started < 40
    deadline = time.monotonic() + 20
    while container_ids() - before and time.monotonic() < deadline:
        time.sleep(1)
    assert container_ids() - before == set()  # --rm removed it once it was killed


@needs_docker
def test_the_python_baseline_runs_inside_the_sandbox_and_keeps_the_known_failure(box: Box):
    shutil.copytree(
        FIXTURES_DIR / "legacy-python-cli", box.work, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )  # fmt: skip
    runner = DockerSandboxRunner(
        SandboxConfig(image=IMAGE, docker_bin=DOCKER_BIN), box.root, box.home
    )
    report = PythonAdapter().characterize(box.work, runner, lambda r: None, 120)
    assert report.status == "completed", report.reason
    assert (report.counts.passed, report.counts.failed) == (1, 1)
    assert report.failing_tests == ["tests/test_inventory.py::test_total_items_known_failure"]
