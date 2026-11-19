# Design: Repo Necromancer

Derived from the Product Blueprint. See `requirements.md` for the requirements this satisfies.

## 1. Architecture

```
USER -> React + TypeScript UI  --REST/SSE-->  FastAPI (Python)
                                                |
   +--------------------------------------------+------------------------------+
   | Repository Service            LangGraph Workflow            Persistence    |
   |  Git provider                  Autopsy                       SQLite +      |
   |  Local provider                Characterization              SQLAlchemy    |
   |  Demo fixtures                 Approval interrupt            Checkpointer  |
   |                                Reanimation                   Event log     |
   |                                Consecration                                |
   +--------------------------------------------+------------------------------+
                                                |
        Managed workspace: Source snapshot -> Working copy -> Patch artifacts
                                                |
                       Restricted Docker sandbox (non-root, no network by default)
                         tests, lint, security audit, build
                                                |
                                  Vitality Score + reports -> SSE -> UI
```

Rules: handlers hold no business logic; adapters do not import LangGraph; the UI depends only on
the REST/SSE contract; use real backend data only; avoid unnecessary services.

## 2. Module layout

Current (`backend/app/`):
- `config.py`: settings from `NECRO_*` environment variables.
- `db.py`: SQLAlchemy engine, session factory and tables (`repositories`, `runs`, `run_events`,
  `approvals`, `artifacts`).
- `schemas.py`: Pydantic v2 contracts.
- `fixtures.py`: fixture registry (directory plus `fixture.json`).
- `repositories/`: `paths.py` (local path validation), `urls.py` (Git URL validation),
  `detect.py` (read-only detection), `service.py` (inspect, import, list, get).
- `runs/`: `service.py` (lifecycle: start, transition, approve, cancel, artifacts),
  `events.py` (append, replay, SSE formatting).
- `api/`: thin routers (`repositories.py`, `revive.py`, `deps.py`).

Planned, following the blueprint's target structure and consolidated where it avoids premature
complexity:
- `adapters/`: `base.py` interface, `python_adapter.py` (P0), `node_adapter.py` (P1),
  `cpp_adapter.py` (stretch).
- `graph/`: `state.py`, `workflow.py`, `nodes.py`, `approval.py`.
- `agents/`: autopsy, characterization, roadmap, reanimation, consecration.
- `execution/`: `sandbox.py`, `command_runner.py`, `workspace.py`.
- `scoring/health_score.py`.
- `frontend/`: React 19, Vite, Tailwind, Lucide, Diff2Html.

## 3. Intake and path safety

Local paths: reject empty input and NUL bytes, reject any raw `..` segment, resolve with
`Path.resolve(strict=True)` (follows symlinks), require a directory, require the resolved path to
be inside an allowed root. Default allowed root is the fixtures directory. `/` and the home
directory are never accepted as roots.

Git URLs are matched against strict `https://`, `ssh://` and `git@host:path` patterns. Credentials
never appear in URLs or logs.

Detection reads the file tree only. It never runs repository code or git, because a hostile
`.git/config` can execute commands. It does not follow symlinks and caps files scanned.

## 4. Transactional patch lifecycle

```
Original repository (read-only)
  -> Pinned snapshot
  -> Inspect + baseline
  -> Roadmap + proposed patch
  -> HUMAN APPROVAL
  -> Apply to isolated working copy
  -> Verification
       PASS -> report
       FAIL -> preserve evidence, roll back working copy
```
Source mutation is never part of this flow. REVIVE works under
`NECRO_DATA_DIR/runs/<run_id>/work`. Git sources are cloned into the data directory.

## 5. LangGraph state machine (planned)

```
START -> Repository Validation -> AUTOPSY -> CHARACTERIZATION -> REVIVAL ROADMAP
      -> HUMAN APPROVAL INTERRUPT --Reject--> END
                |  Edit -> Roadmap Revision -> Human Approval
                v Approved
            REANIMATION -> CONSECRATION --PASS--> REPORT -> END
                                        --FAIL--> bounded retry / rollback / failure -> END
```
Typed state, SQLite-backed checkpointer, interrupt before patch application, resume via
`/approve`, cancellation checked between nodes, bounded retries, and an audit record per
transition and decision.

## 6. Run lifecycle (implemented)

Statuses: `QUEUED`, `RUNNING`, `AWAITING_APPROVAL`, `COMPLETED`, `FAILED`, `CANCELLED`.
Terminal statuses are final.

- `start`: creates a `QUEUED` run and a first event. The mode defaults to `INSPECT`.
- `approve`: valid only in `AWAITING_APPROVAL`, otherwise 409. Writes an approval row, then
  `RUNNING` on approve or `CANCELLED` on reject.
- `cancel`: waiting runs cancel immediately. A `RUNNING` run records a request that the executor
  honours between steps, so in-flight writes are not killed.
- `transition`: the executor's only way to change status or phase.

## 7. SSE (implemented)

Events are persisted in `run_events` with a unique `(run_id, seq)`. The stream endpoint replays
`seq > cursor`, where the cursor comes from `Last-Event-ID` or `?after=`, then tails new rows.
It sends `retry:` and heartbeat comments, and closes once the run is terminal and drained. The SSE
`id:` field is the per-run sequence number.

## 8. Vitality Score

`H = 0.20 D + 0.30 T + 0.20 Q + 0.15 S + 0.15 C`. When a dimension is unmeasured it is excluded,
`H = sum(w_i s_i) / sum(w_i)` over measured dimensions, and the result carries the effective
weight so the UI can show how much of the model is known. If nothing is measured, `H` is `null`.

## 9. Data model

Implemented: Repository, Run, Event, Approval, Artifact.
Planned per blueprint: WorkflowCheckpoint, Finding, Roadmap, RoadmapTask, Patch, Verification.
Artifacts gain a path and checksum when the first one is produced.

## 10. Security notes

Docker is a practical isolation boundary, not a guarantee. The sandbox runs as non-root with CPU,
memory, PID and time limits, no network by default, restricted mounts, and no home directory or
Docker socket. The runner sits behind an interface so it can be unit tested with a fake runner.

**Environment constraint:** the development sandbox here has no working Docker daemon (podman is
missing `conmon`). Real container execution must be verified on a machine with Docker.

## Known deviations from the blueprint

These are gaps between the implemented code and the blueprint. They are tracked in `tasks.md`.

| Area | Blueprint | Current code |
| --- | --- | --- |
| SSE envelope fields | `sequence`, `timestamp`, `payload` | `seq`, `ts`, `data` |
| SSE event types | `run_started`, `stage_changed`, `roadmap_ready`, `hitl_required`, `approval_received`, ... | `run.created`, `run.status`, `run.cancel_requested`, `approval.recorded` |
| Approval decisions | approve, edit, reject | approve, reject |
| Approval record | adds roadmap version, patch-set digest, actor | decision, note, timestamp, run ID |
| Repository pinning | pinned commit SHA and managed snapshot | not implemented. Git URL import registers only, nothing is cloned |
| Primary fixture | `legacy-python-cli` in top-level `fixtures/` | `legacy_flask_app` in `backend/fixtures/` |
| Entities | Finding, Roadmap, RoadmapTask, Patch, Verification, WorkflowCheckpoint | not yet created |
| Duplicate-event protection | required | unique `(run_id, seq)` only |
| Observability, Git library | OpenTelemetry, GitPython | not yet added |
| Execution | Docker sandbox | not implemented |
