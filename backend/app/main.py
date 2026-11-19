from __future__ import annotations

from collections.abc import AsyncIterator
from concurrent.futures import Executor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.adapters.base import LanguageAdapter
from app.adapters.python_adapter import PythonAdapter
from app.api import repositories, revive
from app.config import Settings, load_settings
from app.db import make_engine, make_session_factory
from app.execution.runner import CommandRunner, HostFixtureRunner
from app.execution.sandbox import DockerSandboxRunner, sandbox_status
from app.graph.runner import WorkflowRunner
from app.graph.state import RunnerFactory, WorkflowContext
from app.runs.orchestrator import RunOrchestrator


def default_runner_factory(settings: Settings) -> RunnerFactory:
    """Prefer the Docker sandbox. Bundled fixtures may fall back to the host (resource limited)
    when allowed. Everything else gets no runner, so its tests are reported as not run."""

    def factory(source_type: str, home: Path) -> CommandRunner | None:
        if settings.sandbox_mode != "off":
            if sandbox_status(settings.sandbox, home).available:
                return DockerSandboxRunner(settings.sandbox, settings.data_dir / "runs", home)
        if source_type == "fixture" and settings.allow_host_fixture_exec:
            return HostFixtureRunner(home)
        return None

    return factory


def no_runner_reason(settings: Settings):  # noqa: ANN201
    def reason(source_type: str) -> str:
        if settings.sandbox_mode == "off":
            why = "the sandbox is disabled (NECRO_SANDBOX=off)"
        else:
            why = sandbox_status(settings.sandbox, settings.data_dir).reason or "unavailable"
        return (
            f"Tests were not run: {why}. Untrusted repositories only run inside the Docker "
            "sandbox. The result is unknown, not zero."
        )

    return reason


def create_app(
    settings: Settings | None = None,
    *,
    executor: Executor | None = None,
    runner_factory: RunnerFactory | None = None,
    adapter: LanguageAdapter | None = None,
) -> FastAPI:
    """`executor`, `runner_factory` and `adapter` let tests run the workflow inline and offline."""
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    session_factory = make_session_factory(make_engine(settings.db_url))
    ctx = WorkflowContext(
        settings=settings,
        session_factory=session_factory,
        adapter=adapter or PythonAdapter(),
        runner_for=runner_factory or default_runner_factory(settings),
        no_runner_reason=no_runner_reason(settings),
    )
    workflow = WorkflowRunner(ctx, executor)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        workflow.close()

    app = FastAPI(
        title="Repo Necromancer",
        description="Diagnose. Reanimate. Verify.",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.orchestrator = RunOrchestrator(workflow)
    app.state.workflow = workflow
    app.state.sse_poll_seconds = 0.5
    app.include_router(repositories.router)
    app.include_router(revive.router)

    @app.get("/api/health")
    def health() -> dict:
        status = (
            None
            if settings.sandbox_mode == "off"
            else sandbox_status(settings.sandbox, settings.data_dir)
        )
        return {
            "status": "ok",
            "sandbox": {
                "mode": settings.sandbox_mode,
                "available": bool(status and status.available),
                "reason": "disabled" if status is None else status.reason,
                "host_fixture_exec": settings.allow_host_fixture_exec,
            },
        }

    if settings.static_dir and (settings.static_dir / "index.html").is_file():
        # Registered last so every /api route wins over the static catch-all.
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")

    return app
