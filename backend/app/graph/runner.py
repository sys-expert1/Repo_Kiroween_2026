"""Runs the workflow graph on worker threads and resumes it after human approval.

Checkpoints are stored in SQLite under the data directory, so a run waiting for approval can be
resumed by a new process.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.execution.redact import redact
from app.graph.state import WorkflowContext
from app.graph.workflow import build_workflow
from app.runs import service
from app.schemas import TERMINAL_STATUSES, RunStatus

log = logging.getLogger(__name__)
MAX_ERROR_CHARS = 500


class InlineExecutor(Executor):
    """Runs submitted work immediately on the caller's thread. For deterministic tests."""

    def submit(self, fn: Callable, /, *args, **kwargs) -> Future:  # type: ignore[override]
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future


class WorkflowRunner:
    def __init__(self, ctx: WorkflowContext, executor: Executor | None = None) -> None:
        self._ctx = ctx
        self._executor = executor or ThreadPoolExecutor(max_workers=4, thread_name_prefix="necro")
        ctx.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(ctx.settings.data_dir / "checkpoints.db"), check_same_thread=False
        )
        self._graph = build_workflow(ctx, SqliteSaver(self._conn))

    @staticmethod
    def _config(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

    def start(self, run_id: str, mode: str) -> None:
        self._executor.submit(self._invoke, run_id, {"run_id": run_id, "mode": mode})

    def resume(self, run_id: str, decision: dict) -> None:
        self._executor.submit(self._invoke, run_id, Command(resume=decision))

    def is_waiting(self, run_id: str) -> bool:
        return bool(self._graph.get_state(self._config(run_id)).next)

    def _invoke(self, run_id: str, payload: object) -> None:
        try:
            self._graph.invoke(payload, self._config(run_id))
        except Exception as exc:  # noqa: BLE001  (every failure must end the run explicitly)
            log.exception("workflow failed for run %s", run_id)
            self._fail(run_id, exc)

    def _fail(self, run_id: str, exc: Exception) -> None:
        message = redact(f"{type(exc).__name__}: {exc}")[:MAX_ERROR_CHARS]
        with self._ctx.session_factory() as db:
            run = service.get_run(db, run_id)
            if run.status in TERMINAL_STATUSES:
                return
            service.transition(db, run_id, RunStatus.FAILED, error=message)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._conn.close()
