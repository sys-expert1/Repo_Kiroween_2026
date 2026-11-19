from __future__ import annotations

from pathlib import Path

import pytest

from app.execution import sandbox as sb
from app.execution.runner import CommandResult, SandboxUnavailableError
from app.execution.sandbox import DockerSandboxRunner, SandboxConfig, sandbox_status


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    (root / "r1" / "work").mkdir(parents=True)
    (root / "r1" / "work" / "t.py").write_text("x = 1\n")
    return root


@pytest.fixture
def runner(runs_root: Path, tmp_path: Path) -> DockerSandboxRunner:
    return DockerSandboxRunner(SandboxConfig(), runs_root, tmp_path / "home")


def flag(cmd: list[str], name: str) -> str:
    return cmd[cmd.index(name) + 1]


def test_command_is_restricted(runner: DockerSandboxRunner, runs_root: Path):
    work = runs_root / "r1" / "work"
    cmd = runner.build_argv(["python", "-m", "pytest"], work, "necro-x")
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert flag(cmd, "--network") == "none"
    assert flag(cmd, "--user") == "65534:65534"  # non-root
    assert "--read-only" in cmd
    assert flag(cmd, "--cap-drop") == "ALL"
    assert flag(cmd, "--security-opt") == "no-new-privileges"
    assert flag(cmd, "--pids-limit") == "128"
    assert flag(cmd, "--memory") == "1g" and flag(cmd, "--memory-swap") == "1g"
    assert flag(cmd, "--cpus") == "1.0"
    assert cmd[-4:] == ["necromancer-sandbox:latest", "python", "-m", "pytest"]
    assert "--privileged" not in cmd and not any(a.startswith("--cap-add") for a in cmd)


def test_only_one_directory_is_mounted_and_never_home_or_socket(
    runner: DockerSandboxRunner, runs_root: Path
):
    work = runs_root / "r1" / "work"
    cmd = runner.build_argv(["true"], work, "n")
    mounts = [cmd[i + 1] for i, a in enumerate(cmd) if a in ("--mount", "-v", "--volume")]
    assert mounts == [f"type=bind,source={work},target=/work"]
    joined = " ".join(cmd)
    assert "docker.sock" not in joined and str(Path.home()) + "," not in joined


def test_network_is_only_enabled_for_dependency_install(
    runs_root: Path, tmp_path: Path, monkeypatch
):
    calls: list[list[str]] = []

    def fake_run(argv, cwd, timeout, env, **kw):
        calls.append(list(argv))
        return CommandResult(argv=list(argv), exit_code=0, stdout="", stderr="", duration_seconds=0)

    monkeypatch.setattr(sb, "run_command", fake_run)
    work = runs_root / "r1" / "work"
    off = DockerSandboxRunner(SandboxConfig(), runs_root, tmp_path / "h")
    assert off.install_requirements(work, ["requirements.txt"], 30) is None  # opt-in
    assert calls == []
    on = DockerSandboxRunner(SandboxConfig(install_dependencies=True), runs_root, tmp_path / "h")
    assert on.install_requirements(work, [], 30) is None  # nothing to install
    on.install_requirements(work, ["requirements.txt"], 30)
    cmd = calls[-1]
    assert flag(cmd, "--network") == "bridge"
    assert "--only-binary=:all:" in cmd  # no sdist build, so no setup.py runs with network
    assert "--target" in cmd
    on.run(["python", "-V"], work, 10)
    assert flag(calls[-1], "--network") == "none"  # tests never get the network


def test_deps_dir_is_added_to_pythonpath_when_present(runner: DockerSandboxRunner, runs_root: Path):
    work = runs_root / "r1" / "work"
    (work / ".necro-deps").mkdir()
    cmd = runner.build_argv(["true"], work, "n")
    assert "PYTHONPATH=/work/.necro-deps" in cmd


@pytest.mark.parametrize("bad", ["a,b", "a=b"])
def test_mount_paths_with_option_separators_are_rejected(runs_root: Path, bad: str):
    evil = runs_root / "r1" / bad
    evil.mkdir()
    with pytest.raises(SandboxUnavailableError):
        sb._check_mount(evil, runs_root)


def test_mount_must_be_inside_the_managed_root(runs_root: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(SandboxUnavailableError, match="inside"):
        sb._check_mount(outside, runs_root)
    with pytest.raises(SandboxUnavailableError, match="inside"):
        sb._check_mount(runs_root, runs_root)  # the root itself
    link = runs_root / "r1" / "escape"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SandboxUnavailableError, match="inside"):
        sb._check_mount(link, runs_root)  # symlink resolves outside


def test_home_directory_and_its_ancestors_are_never_mounted(monkeypatch, tmp_path: Path):
    home = tmp_path / "home" / "user"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    inside_home_root = home / "necro" / "runs"
    (inside_home_root / "r").mkdir(parents=True)
    # A managed directory below home is allowed...
    assert sb._check_mount(inside_home_root / "r", inside_home_root)
    # ...but home itself, or a root that is an ancestor of home, is refused.
    with pytest.raises(SandboxUnavailableError, match="home"):
        sb._check_mount(home / "necro", home)
    with pytest.raises(SandboxUnavailableError, match="home"):
        sb._check_mount(home, tmp_path)
    with pytest.raises(SandboxUnavailableError, match="home"):
        sb._check_mount(tmp_path / "home", tmp_path)


def test_forbidden_locations_are_refused(runs_root: Path):
    ssh = runs_root / "r1" / ".ssh"
    ssh.mkdir()
    with pytest.raises(SandboxUnavailableError, match="forbidden"):
        sb._check_mount(ssh, runs_root)


def test_run_opens_permissions_on_the_disposable_copy_only(
    runner: DockerSandboxRunner, runs_root: Path, monkeypatch
):
    monkeypatch.setattr(
        sb, "run_command",
        lambda argv, cwd, t, env, **k: CommandResult(
            argv=list(argv), exit_code=0, stdout="ok", stderr="", duration_seconds=0.1
        ),
    )  # fmt: skip
    work = runs_root / "r1" / "work"
    r = runner.run(["python", "-V"], work, 10)
    assert r.exit_code == 0
    assert (work.stat().st_mode & 0o777) == 0o777
    assert (work / "t.py").stat().st_mode & 0o666 == 0o666


def test_run_refuses_a_directory_outside_the_managed_root(runner: DockerSandboxRunner, tmp_path):
    with pytest.raises(SandboxUnavailableError):
        runner.run(["true"], tmp_path, 10)


def test_timeout_kills_the_container(runner: DockerSandboxRunner, runs_root: Path, monkeypatch):
    seen: list[list[str]] = []

    def fake_run(argv, cwd, timeout, env, **kw):
        seen.append(list(argv))
        if argv[1] == "run":
            return CommandResult(
                argv=list(argv), exit_code=None, stdout="", stderr="", duration_seconds=15,
                timed_out=True, error="Timed out",
            )  # fmt: skip
        return CommandResult(argv=list(argv), exit_code=0, stdout="", stderr="", duration_seconds=0)

    monkeypatch.setattr(sb, "run_command", fake_run)
    r = runner.run(["sleep", "99"], runs_root / "r1" / "work", 10)
    assert r.timed_out
    run_cmd, kill_cmd = seen
    assert kill_cmd[:2] == ["docker", "kill"] and kill_cmd[2] == flag(run_cmd, "--name")


def test_container_names_are_unique(runner: DockerSandboxRunner):
    assert len({runner._new_name() for _ in range(50)}) == 50


# --------------------------------------------------------------------- status
def fake_docker(monkeypatch, info_code: int, image_code: int = 0):
    def fake(argv, cwd, timeout, env, **kw):
        code = info_code if argv[1] == "info" else image_code
        return CommandResult(
            argv=list(argv), exit_code=code, stdout="27.0", duration_seconds=0.0,
            stderr="Cannot connect to the Docker daemon" if code else "",
        )  # fmt: skip

    monkeypatch.setattr(sb, "run_command", fake)
    sb._cache.clear()


def test_status_unavailable_when_daemon_is_down(monkeypatch, tmp_path: Path):
    fake_docker(monkeypatch, info_code=1)
    s = sandbox_status(SandboxConfig(), tmp_path)
    assert not s.available and "Docker is unavailable" in s.reason


def test_status_tells_how_to_build_a_missing_image(monkeypatch, tmp_path: Path):
    fake_docker(monkeypatch, info_code=0, image_code=1)
    s = sandbox_status(SandboxConfig(), tmp_path)
    assert not s.available and "deployment/sandbox.Dockerfile" in s.reason


def test_status_available_and_cached(monkeypatch, tmp_path: Path):
    fake_docker(monkeypatch, info_code=0)
    assert sandbox_status(SandboxConfig(), tmp_path).available
    fake_docker_calls = []
    monkeypatch.setattr(sb, "run_command", lambda *a, **k: fake_docker_calls.append(a))
    assert sandbox_status(SandboxConfig(), tmp_path).available  # served from cache
    assert fake_docker_calls == []


def test_missing_docker_binary_is_unavailable_not_an_exception(tmp_path: Path):
    sb._cache.clear()
    s = sandbox_status(SandboxConfig(docker_bin="definitely-not-docker"), tmp_path)
    assert not s.available and s.reason


def test_status_uses_only_exit_codes_so_docker_and_podman_both_work(monkeypatch, tmp_path: Path):
    """`docker info --format` templates differ between runtimes, so none may be used."""
    seen: list[list[str]] = []

    def record(argv, cwd, timeout, env, **kw):
        seen.append(list(argv))
        return CommandResult(argv=list(argv), exit_code=0, stdout="", stderr="", duration_seconds=0)

    monkeypatch.setattr(sb, "run_command", record)
    sb._cache.clear()
    assert sandbox_status(SandboxConfig(docker_bin="/opt/podman-wrapper"), tmp_path).available
    assert seen == [
        ["/opt/podman-wrapper", "info"],
        ["/opt/podman-wrapper", "image", "inspect", "necromancer-sandbox:latest"],
    ]
    assert not any("--format" in call for call in seen)


def test_the_build_hint_names_the_configured_runtime(monkeypatch, tmp_path: Path):
    fake_docker(monkeypatch, info_code=0, image_code=1)
    s = sandbox_status(SandboxConfig(docker_bin="podman"), tmp_path)
    assert s.reason.startswith(
        "Sandbox image 'necromancer-sandbox:latest' is not built. Run: podman build"
    )


def test_the_container_runtime_binary_is_configurable_from_the_environment(
    monkeypatch, tmp_path: Path
):
    from app.config import load_settings

    monkeypatch.setenv("NECRO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("NECRO_DOCKER_BIN", raising=False)
    assert load_settings().sandbox.docker_bin == "docker"
    monkeypatch.setenv("NECRO_DOCKER_BIN", "/usr/bin/podman")
    assert load_settings().sandbox.docker_bin == "/usr/bin/podman"


def test_every_command_uses_the_configured_runtime_binary(tmp_path: Path):
    root = tmp_path / "runs"
    (root / "r" / "work").mkdir(parents=True)
    runner = DockerSandboxRunner(SandboxConfig(docker_bin="/opt/rt"), root, tmp_path / "h")
    cmd = runner.build_argv(["true"], root / "r" / "work", "n")
    assert cmd[0] == "/opt/rt" and cmd[1] == "run"
