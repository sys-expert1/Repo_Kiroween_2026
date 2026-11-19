"""Settings read from NECRO_* environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.execution.sandbox import SandboxConfig

BACKEND_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = BACKEND_DIR.parent / "fixtures"
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    allowed_roots: tuple[Path, ...]
    fixtures_dir: Path
    # Bundled demo fixtures are trusted code. When true, their baseline tests may run on the host
    # (resource-limited) because the Docker sandbox does not exist yet. Never applies to Git URLs
    # or local paths.
    allow_host_fixture_exec: bool = True
    # Built frontend served at "/" when the directory exists.
    static_dir: Path | None = None
    # "auto" uses Docker when the daemon and image are usable. "off" never uses Docker.
    # Building a repository's Dockerfile runs its RUN steps with network access: opt-in only.
    allow_docker_build: bool = False
    # AI-assisted patches. Empty means disabled. Code leaves the machine only when this is set.
    llm_model: str = ""
    llm_timeout: float = 60.0
    llm_max_tasks: int = 5
    sandbox_mode: str = "auto"
    sandbox: SandboxConfig = SandboxConfig()

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'necromancer.db'}"


def _forbidden_root(path: Path) -> bool:
    return path == Path(path.anchor) or path == Path.home().resolve()


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("NECRO_DATA_DIR", BACKEND_DIR / ".data")).resolve()
    fixtures_dir = Path(os.environ.get("NECRO_FIXTURES_DIR", FIXTURES_DIR)).resolve()
    raw = os.environ.get("NECRO_ALLOWED_ROOTS", "")
    roots = [Path(p).resolve() for p in raw.split(os.pathsep) if p.strip()]
    roots.append(fixtures_dir)
    for root in roots:
        if _forbidden_root(root):
            raise ValueError(f"Refusing to allow filesystem root or home directory: {root}")
    return Settings(
        data_dir=data_dir,
        allowed_roots=tuple(dict.fromkeys(roots)),
        fixtures_dir=fixtures_dir,
        allow_host_fixture_exec=os.environ.get("NECRO_ALLOW_HOST_FIXTURE_EXEC", "1") != "0",
        static_dir=Path(os.environ.get("NECRO_STATIC_DIR", FRONTEND_DIST)).resolve(),
        allow_docker_build=os.environ.get("NECRO_ALLOW_DOCKER_BUILD", "0") == "1",
        llm_model=os.environ.get("NECRO_LLM_MODEL", "").strip(),
        llm_timeout=float(os.environ.get("NECRO_LLM_TIMEOUT", "60")),
        llm_max_tasks=max(0, int(os.environ.get("NECRO_LLM_MAX_TASKS", "5"))),
        sandbox_mode="off" if os.environ.get("NECRO_SANDBOX", "auto") == "off" else "auto",
        sandbox=SandboxConfig(
            image=os.environ.get("NECRO_SANDBOX_IMAGE", SandboxConfig.image),
            install_dependencies=os.environ.get("NECRO_SANDBOX_INSTALL_DEPS", "0") == "1",
        ),
    )
