from __future__ import annotations

from collections.abc import AsyncIterator
from concurrent.futures import Executor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.adapters.base import LanguageAdapter
from app.adapters.python_adapter import PythonAdapter
from app.api import repositories, revive
from app.config import Settings, load_settings
from app.db import make_engine, make_session_factory
from app.execution.runner import CommandRunner, HostFixtureRunner
from app.graph.runner import WorkflowRunner
from app.graph.state import RunnerFactory, WorkflowContext
from app.runs.orchestrator import RunOrchestrator


def default_runner_factory(settings: Settings) -> RunnerFactory:
    """Only bundled fixtures may run on the host, and only when allowed. Everything else needs
    the Docker sandbox (task 6) and gets no runner, so its tests are reported as not run."""

    def factory(source_type: str, home: Path) -> CommandRunner | None:
        if source_type == "fixture" and settings.allow_host_fixture_exec:
            return HostFixtureRunner(home)
        return None

    return factory


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
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
