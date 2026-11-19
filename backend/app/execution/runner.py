"""Command execution with time and output limits.

`run_command` is the only place that starts subprocesses for analysis and tests. It never uses a
shell, passes a minimal environment, kills the whole process group on timeout and redacts output.

`HostFixtureRunner` runs commands for bundled, trusted demo fixtures only. It is not a sandbox.
Untrusted repositories need the Docker sandbox (task 6) and are refused here.
"""

from __future__ import annotations

import os
import resource
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.execution.redact import redact

MAX_OUTPUT_CHARS = 200_000
_PASSTHROUGH_ENV = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
)


class CommandResult(BaseModel):
    argv: list[str]
    exit_code: int | None  # None when the process could not start or was killed on timeout
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    error: str | None = None

    @property
    def command(self) -> str:
        return " ".join(self.argv)


class SandboxUnavailableError(RuntimeError):
    """Raised when a command must run in the sandbox but the sandbox cannot be used."""


class CommandRunner(Protocol):
    python: str  # interpreter to invoke inside this runner's environment

    def run(self, argv: Sequence[str], cwd: Path, timeout: float) -> CommandResult: ...


def _cap(text: str) -> str:
    """Keep the start and the end: summaries and errors are usually at the end."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    head = MAX_OUTPUT_CHARS // 10
    tail = MAX_OUTPUT_CHARS - head
    return f"{text[:head]}\n[... output truncated ...]\n{text[-tail:]}"


def build_env(home: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    for key in _PASSTHROUGH_ENV:
        if key in os.environ:
            env[key] = os.environ[key]
    env.update(extra or {})
    return env


def run_command(
    argv: Sequence[str],
    cwd: Path,
    timeout: float,
    env: Mapping[str, str],
    limits: bool = False,
    cpu_seconds: int = 120,
    memory_bytes: int | None = 2 * 1024**3,
) -> CommandResult:
    """Run argv without a shell. `limits` applies CPU, memory and file-size rlimits."""

    def preexec() -> None:
        os.setsid()
        if limits:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            if memory_bytes is not None:  # V8 reserves huge address ranges, so Node uses a heap cap
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(resource.RLIMIT_FSIZE, (100 * 1024**2, 100 * 1024**2))

    started = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S603  (argv list, no shell)
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            preexec_fn=preexec,  # noqa: PLW1509
        )
    except OSError as exc:
        return CommandResult(
            argv=list(argv),
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error=redact(f"{type(exc).__name__}: {exc}"),
        )
    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        timed_out = True
    return CommandResult(
        argv=list(argv),
        exit_code=None if timed_out else proc.returncode,
        stdout=redact(_cap(out or "")),
        stderr=redact(_cap(err or "")),
        duration_seconds=round(time.monotonic() - started, 3),
        timed_out=timed_out,
        error=f"Timed out after {timeout:g}s" if timed_out else None,
    )


class HostFixtureRunner:
    """Runs commands for trusted bundled fixtures with resource limits. Not a sandbox."""

    python = sys.executable
    # Toolchains are allowed here because bundled fixtures are trusted code.
    node = shutil.which("node")
    cmake = shutil.which("cmake")
    ctest = shutil.which("ctest")

    def __init__(self, home: Path, timeout_cap: float = 300.0) -> None:
        self._home = home
        self._timeout_cap = timeout_cap

    def run(self, argv: Sequence[str], cwd: Path, timeout: float) -> CommandResult:
        self._home.mkdir(parents=True, exist_ok=True)
        is_node = Path(argv[0]).name == "node"  # V8 reserves huge address ranges
        return run_command(
            argv,
            cwd,
            min(timeout, self._timeout_cap),
            build_env(self._home),
            limits=True,
            # Node fails under an address-space cap, so its callers pass --max-old-space-size.
            memory_bytes=None if is_node else 2 * 1024**3,
        )
