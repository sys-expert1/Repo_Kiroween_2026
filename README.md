# Repo Necromancer | Kiroween 2026

> Build something wicked and git commit to the darkness.
>
> Diagnose. Reanimate. Verify.

Repo Necromancer is a local-first tool that analyses a legacy repository, builds a Revival
Roadmap, requires explicit human approval, applies approved patches in an isolated working copy
and verifies the result. The specs live in `.kiro/specs/repo-necromancer/`.

## Status

| Area | State |
| --- | --- |
| Repository intake API (inspect, import, list, get, fixtures) | Done |
| Local path and Git URL validation | Done |
| Revive API (start, SSE stream with replay, approve, cancel, artifacts) | Done. Runs stay `QUEUED` until the workflow executor exists |
| Python Autopsy, LangGraph workflow, Docker sandbox, patching, scoring | Not started |
| React dashboard | Not started |

Only the backend exists today. There is no frontend or Docker Compose setup yet.

## Requirements

- Python 3.12 or newer
- Git

## Run the backend

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:create_app --factory --port 8000
```

Open http://localhost:8000/docs for the interactive API docs, or try:

```bash
curl localhost:8000/api/health
curl localhost:8000/api/fixtures
curl -X POST localhost:8000/api/repositories/inspect \
  -H 'content-type: application/json' -d '{"fixture_id": "legacy_flask_app"}'
```

## Test and lint

```bash
cd backend
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `NECRO_DATA_DIR` | `backend/.data` | SQLite database and run data |
| `NECRO_ALLOWED_ROOTS` | none | Extra directories (separated by `:`) that local paths may point into |
| `NECRO_FIXTURES_DIR` | `backend/fixtures` | Demo fixtures, always allowed |

Local paths must resolve inside an allowed root. `/` and your home directory are refused as roots.
Git URLs must be `https://`, `ssh://` or `git@host:path`.

## Safety rules

The source repository is never modified, nothing is auto-pushed, and approval cannot be bypassed.
Untrusted repository code will only run in a restricted Docker sandbox (planned, not built yet).
