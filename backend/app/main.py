from __future__ import annotations

from fastapi import FastAPI

from app.api import repositories, revive
from app.config import Settings, load_settings
from app.db import make_engine, make_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(
        title="Repo Necromancer",
        description="Diagnose. Reanimate. Verify.",
        version="0.1.0",
    )
    app.state.settings = settings
    app.state.session_factory = make_session_factory(make_engine(settings.db_url))
    app.state.sse_poll_seconds = 0.5
    app.include_router(repositories.router)
    app.include_router(revive.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
