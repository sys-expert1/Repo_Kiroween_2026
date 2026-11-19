"""Restricted Docker sandbox for running untrusted repository commands.

Docker is a practical isolation boundary, not a guarantee against every container escape.

Every container is started with: no network, a non-root user, a read-only root filesystem, all
capabilities dropped, no-new-privileges, and CPU, memory, process and time limits. Only one
directory is mounted (a disposable copy under the managed run directory). The user's home
directory and the Docker socket are never mounted.

This module has been unit tested at the command-construction level only. It has not been run
against a real Docker daemon in the development environment (none was available).
"""

from __future__ import annotations

import os
import re
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.execution.runner import (
    CommandResult,
    SandboxUnavailableError,
    build_env,
    run_command,
)

CONTAINER_WORKDIR = "/work"
DEPS_DIR = ".necro-deps"
_UNSAFE_MOUNT = re.compile(r"[,\n\r\x00=]")
_FORBIDDEN_MOUNT_NAMES = {"docker.sock", ".docker", ".ssh", ".aws", ".gnupg"}
STATUS_CACHE_SECONDS = 30.0


@dataclass(frozen=True)
class SandboxConfig:
    image: str = "necromancer-sandbox:latest"
    cpus: float = 1.0
    memory: str = "1g"
    pids_limit: int = 128
    docker_bin: str = "docker"
    install_dependencies: bool = False  # opt-in: this is the only step that uses the network


def _is_home_or_ancestor(path: Path) -> bool:
    home = Path.home().resolve()
    return path == home or path in home.parents


def _check_mount(path: Path, mount_root: Path) -> Path:
    """Return the resolved mount source or raise. The source must be strictly inside
    `mount_root`, and neither may be the filesystem root, the home directory or an ancestor of
    it. A directory below the home directory is allowed."""
    resolved = path.resolve(strict=True)
    root = mount_root.resolve(strict=True)
    if _UNSAFE_MOUNT.search(str(resolved)):
        raise SandboxUnavailableError("Mount path contains characters that are not allowed.")
    if resolved == root or root not in resolved.parents:
        raise SandboxUnavailableError("Mount source must be inside the managed run directory.")
    if _is_home_or_ancestor(root) or _is_home_or_ancestor(resolved):
        raise SandboxUnavailableError("Refusing to mount the filesystem root or home directory.")
    if any(part in _FORBIDDEN_MOUNT_NAMES for part in resolved.parts):
        raise SandboxUnavailableError("Mount path includes a forbidden location.")
    return resolved


def _open_permissions(root: Path) -> None:
    """Let the unprivileged container user write into a disposable copy."""
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        os.chmod(dirpath, 0o777)
        for name in filenames:
            p = Path(dirpath) / name
            if not p.is_symlink():
                os.chmod(p, p.stat().st_mode | 0o666)


class DockerSandboxRunner:
    """Runs a command in a fresh, restricted container. Implements `CommandRunner`."""

    python = "python"

    def __init__(self, config: SandboxConfig, mount_root: Path, home: Path) -> None:
        self._config = config
        self._mount_root = mount_root
        self._home = home

    # ------------------------------------------------------------ construction
    def build_argv(
        self,
        argv: Sequence[str],
        source: Path,
        name: str,
        *,
        network: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> list[str]:
        cfg = self._config
        env = {"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        if (source / DEPS_DIR).is_dir():
            env["PYTHONPATH"] = f"{CONTAINER_WORKDIR}/{DEPS_DIR}"
        env.update(extra_env or {})
        cmd = [
            cfg.docker_bin, "run", "--rm", "--name", name,
            "--network", "bridge" if network else "none",
            "--user", "65534:65534",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(cfg.pids_limit),
            "--memory", cfg.memory, "--memory-swap", cfg.memory,
            "--cpus", str(cfg.cpus),
            "--tmpfs", "/tmp:rw,nosuid,size=256m",
            "--mount", f"type=bind,source={source},target={CONTAINER_WORKDIR}",
            "--workdir", CONTAINER_WORKDIR,
        ]  # fmt: skip
        for key, value in sorted(env.items()):
            cmd += ["-e", f"{key}={value}"]
        return [*cmd, cfg.image, *argv]

    @staticmethod
    def _new_name() -> str:
        return f"necro-{secrets.token_hex(8)}"

    # --------------------------------------------------------------- execution
    def _execute(self, argv: Sequence[str], cwd: Path, timeout: float, *, network: bool,
                 extra_env: dict[str, str] | None = None) -> CommandResult:  # fmt: skip
        source = _check_mount(cwd, self._mount_root)
        _open_permissions(source)
        name = self._new_name()
        full = self.build_argv(argv, source, name, network=network, extra_env=extra_env)
        self._home.mkdir(parents=True, exist_ok=True)
        # Grace period so a hung container is killed by us, not left to the CLI.
        result = run_command(full, self._home, timeout + 5, build_env(self._home))
        if result.timed_out:
            run_command(
                [self._config.docker_bin, "kill", name], self._home, 15, build_env(self._home)
            )
        return result

    def run(self, argv: Sequence[str], cwd: Path, timeout: float) -> CommandResult:
        """Run with the network disabled."""
        return self._execute(argv, cwd, timeout, network=False)

    def install_requirements(
        self, cwd: Path, requirements: Sequence[str], timeout: float
    ) -> CommandResult | None:
        """Install wheels into `<cwd>/.necro-deps` with the network enabled (opt-in).

        `--only-binary=:all:` means no source distribution is built, so no repository or
        third-party setup code runs while the network is reachable.
        """
        if not self._config.install_dependencies or not requirements:
            return None
        argv = ["python", "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
                "--only-binary=:all:", "--target", f"{CONTAINER_WORKDIR}/{DEPS_DIR}"]  # fmt: skip
        for req in requirements:
            argv += ["-r", req]
        return self._execute(argv, cwd, timeout, network=True, extra_env={"PYTHONPATH": ""})


@dataclass(frozen=True)
class SandboxStatus:
    available: bool
    reason: str | None = None


_cache: dict[str, tuple[float, SandboxStatus]] = {}


def sandbox_status(config: SandboxConfig, home: Path, *, use_cache: bool = True) -> SandboxStatus:
    """Report whether the container runtime and the sandbox image are usable. Never raises.

    Only exit statuses are used, not Go templates: `docker info` and `podman info` name their
    fields differently, so a template that works for one fails for the other.
    """
    key = f"{config.docker_bin}|{config.image}"
    now = time.monotonic()
    if use_cache and key in _cache and now - _cache[key][0] < STATUS_CACHE_SECONDS:
        return _cache[key][1]
    home.mkdir(parents=True, exist_ok=True)
    env = build_env(home)
    info = run_command([config.docker_bin, "info"], home, 10, env)
    if info.exit_code != 0:
        why = (info.error or info.stderr or "docker info failed").strip().splitlines()
        status = SandboxStatus(False, f"Docker is unavailable: {why[-1][:200]}")
    else:
        image = run_command([config.docker_bin, "image", "inspect", config.image], home, 10, env)
        if image.exit_code != 0:
            status = SandboxStatus(
                False,
                f"Sandbox image '{config.image}' is not built. "
                f"Run: {config.docker_bin} build -f deployment/sandbox.Dockerfile -t "
                f"{config.image} deployment",
            )
        else:
            status = SandboxStatus(True)
    _cache[key] = (now, status)
    return status
