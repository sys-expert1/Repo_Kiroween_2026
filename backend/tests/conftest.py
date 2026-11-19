from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Executor, Future
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.models import Dependency, Finding, ToolRun
from app.adapters.python_adapter import PythonAdapter
from app.config import FIXTURES_DIR, Settings
from app.execution.runner import CommandResult
from app.graph.runner import InlineExecutor
from app.main import create_app

PYTEST_OUTPUT = (
    ".F\n=== short test summary info ===\n"
    "FAILED tests/test_inventory.py::test_total_items_known_failure - assert 4 == 3\n"
    "1 failed, 1 passed in 0.01s\n"
)


class OfflineAdapter(PythonAdapter):
    """Python adapter whose dependency audit returns a canned result instead of using the network."""

    def _pip_audit(self, deps: list[Dependency], scratch: Path, observe):  # type: ignore[override]
        finding = Finding(
            id="",
            category="dependencies",
            severity="high",
            source="pip-audit",
            title="requests 2.19.1 has 2 known vulnerabilities",
            evidence="PYSEC-A, PYSEC-B. Fixed in: 0.12.3, 1.0. pip-audit gives no severity score.",
            file_path="requirements.txt",
        )
        tool = ToolRun(
            tool="pip-audit", command="pip-audit (offline test double)", exit_code=1,
            measured=True, summary="2 known vulnerabilities in 1 of 1 audited packages",
            duration_seconds=0.0,
        )  # fmt: skip
        return 2, 1, [finding], tool


class FakeRunner:
    """Returns canned pytest output and records every call. Never starts a process."""

    def __init__(self, stdout: str = PYTEST_OUTPUT, exit_code: int = 1) -> None:
        self.stdout = stdout
        self.exit_code = exit_code
        self.calls: list[tuple[list[str], Path]] = []

    def run(self, argv: Sequence[str], cwd: Path, timeout: float) -> CommandResult:
        self.calls.append((list(argv), cwd))
        return CommandResult(
            argv=list(argv), exit_code=self.exit_code, stdout=self.stdout, stderr="",
            duration_seconds=0.01,
        )  # fmt: skip


class DeferredExecutor(Executor):
    """Collects submitted work and runs it only when asked, to test runs that have not started."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], object]] = []

    def submit(self, fn: Callable, /, *args, **kwargs) -> Future:  # type: ignore[override]
        self.pending.append(lambda: fn(*args, **kwargs))
        return Future()

    def run_all(self) -> None:
        while self.pending:
            self.pending.pop(0)()


@pytest.fixture
def allowed_root(tmp_path: Path) -> Path:
    root = tmp_path / "allowed"
    root.mkdir()
    return root


@pytest.fixture
def settings(tmp_path: Path, allowed_root: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        allowed_roots=(allowed_root.resolve(), FIXTURES_DIR.resolve()),
        fixtures_dir=FIXTURES_DIR.resolve(),
    )


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def client(settings: Settings, fake_runner: FakeRunner) -> TestClient:
    """App whose workflow runs inline, offline, with a fake test runner for fixtures."""
    app = create_app(
        settings,
        executor=InlineExecutor(),
        runner_factory=lambda source_type, home: fake_runner if source_type == "fixture" else None,
        adapter=OfflineAdapter(),
    )
    app.state.sse_poll_seconds = 0.01
    return TestClient(app)


@pytest.fixture
def deferred_client(settings: Settings, fake_runner: FakeRunner):
    """App whose workflow does not run until `executor.run_all()` is called."""
    executor = DeferredExecutor()
    app = create_app(
        settings,
        executor=executor,
        runner_factory=lambda source_type, home: fake_runner if source_type == "fixture" else None,
        adapter=OfflineAdapter(),
    )
    app.state.sse_poll_seconds = 0.01
    return TestClient(app), executor
