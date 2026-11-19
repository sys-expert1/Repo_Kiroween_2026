# Design: Repo Necromancer

## Architecture
```
frontend (React 19 + Vite + Tailwind)  --HTTP/SSE-->  backend (FastAPI)
                                                         |
              api/ (thin handlers)  ->  services (business logic)
                                          |-- repositories/ (intake, path + URL validation, detection)
                                          |-- autopsy/      (Python adapter: AST, ruff, pip-audit)   [phase 3]
                                          |-- workflow/     (LangGraph, checkpoints, approval)       [phase 4-5]
                                          |-- sandbox/      (Docker runner)                          [phase 6]
                                          |-- patches/      (generation, apply, rollback)            [phase 7]
                                          |-- scoring/      (Vitality Score)                         [phase 8]
                                          `-- db (SQLAlchemy + SQLite)
```
Rules: handlers contain no business logic; adapters do not import LangGraph; the UI depends
only on the HTTP/SSE contract.

## Backend module layout (current)
- `app/config.py`: settings from environment (`NECRO_*`).
- `app/db.py`, `app/orm.py`: SQLAlchemy engine/session and tables.
- `app/schemas.py`: Pydantic v2 request/response contracts.
- `app/repositories/paths.py`: local path validation. `urls.py`: Git URL validation.
- `app/repositories/detect.py`: read-only detection of languages and project signals.
- `app/repositories/service.py`: inspect/import/list/get.
- `app/fixtures.py`: demo fixture registry.
- `app/api/`: routers.

## Path validation
Reject empty/NUL input and any raw `..` segment. Resolve with `Path.resolve(strict=True)`
(follows symlinks), require a directory, require the resolved path to be inside one of
`NECRO_ALLOWED_ROOTS` (default: the bundled fixtures directory). Filesystem roots and the
user's home directory itself are never allowed as a root.

## Isolation model
Source repositories are read-only inputs. REVIVE works on a copy under `NECRO_DATA_DIR/runs/<run_id>/work`.
Git sources are cloned into that data directory, never into the source tree.

## Vitality Score
`score = sum(w_i * s_i) / sum(w_i)` over measured components only, rounded to 1 decimal,
plus `coverage = sum(w_i measured)` so the UI can show how much of the model is known.

## Workflow (phase 4+)
LangGraph `StateGraph` with a typed state, SQLite checkpointer, an `interrupt` before patch
generation for approval, resume via `/approve`, cancellation flag checked between nodes,
bounded retries per node, and an audit table recording every transition and decision.

## SSE
Events are persisted in an `events` table (`run_id`, `seq`, `ts`, `type`, `data`). The stream
endpoint replays rows with `seq > Last-Event-ID` and then tails new ones.

## Known environment constraint
The development sandbox has no working Docker daemon. Sandbox code is written against a
runner interface and is unit tested with a fake runner. Real container execution must be
verified on a machine with Docker.
