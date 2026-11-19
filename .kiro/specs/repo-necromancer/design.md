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

`backend/app/`:
- `config.py`, `db.py`, `schemas.py`, `fixtures.py`, `artifacts.py`, `reports.py`, `workspace.py`.
- `repositories/`: path and URL validation, read-only detection, intake service.
- `adapters/`: `base.py` interface, `models.py`, `python_adapter.py` (Autopsy, Ruff, pip-audit,
  baseline pytest, safe lint fixes), `python_ast.py`, `python_deps.py`, `python_edit.py`
  (conservative source edits). Adapters do not import LangGraph.
- `agents/`: `roadmap.py` (deterministic, versioned), `patches.py` (proposal, digest, apply).
- `execution/`: `runner.py` (no shell, minimal env, process-group kill, rlimits), `sandbox.py`
  (Docker runner and status), `redact.py`.
- `scoring/health_score.py`, `consecration.py` (verification and CI/Docker checks).
- `graph/`: `state.py`, `nodes.py`, `workflow.py`, `runner.py`.
- `runs/`: `service.py`, `events.py`, `orchestrator.py`.
- `api/`: thin routers. `main.py` builds the app and serves the built dashboard.

`frontend/src/`: `types/revival.ts` (contract mirror), `services/api.ts`, `state/runState.ts`
(pure reducer, de-duplicates by sequence), `hooks/useRevivalStream.ts`, `hooks/useRunData.ts`,
`components/` (TopHeader, WorkflowSidebar, IntakePanel, Overview, RoadmapView, DiffInspector,
KiroTrace, TerminalStream, HealthMeter, ApprovalDialog), `styles/theme.css`.

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

## 5. LangGraph state machine

```
START -> validate_and_snapshot -> autopsy -> characterization
  INSPECT -> complete_inspect -> END
  otherwise roadmap -> generate_patches
    PREVIEW -> complete_preview -> END
    REVIVE, nothing patchable -> complete_no_patches -> END
    REVIVE -> request_approval -> await_decision (interrupt)
        approve -> reanimation -> consecration -> END
        edit    -> revise -> generate_patches -> request_approval
        reject  -> END
```
- Typed `WorkflowState`, `SqliteSaver` checkpointer in `NECRO_DATA_DIR/checkpoints.db`, thread id
  equals run id. A run waiting for approval resumes in a new process (tested).
- `request_approval` and `await_decision` are separate nodes because LangGraph re-runs an
  interrupted node on resume, so side effects must not sit beside `interrupt()`.
- Every node is wrapped by a guard that ends the run if cancellation was requested.
- Retries: two attempts, only for `OSError` and `TimeoutError`. `WorkflowError` and other
  deterministic failures are never retried. Any uncaught error ends the run `FAILED` with a
  redacted message.
- Characterization and the final test run use disposable copies, so the snapshot and the working
  copy stay free of test artifacts.
- `generate_patches` works against the snapshot and writes nothing to any working copy. The
  working copy is created only after approval.
- `reanimation` refuses to run unless the latest approval is an `approve` whose digest equals the
  patch set's digest. `apply_patchset` checks each file's SHA-256 before writing, writes atomically,
  and refuses symlinks and paths that escape the copy.
- `consecration` re-runs the Python adapter's Autopsy on the working copy, runs tests in a disposable
  copy, validates CI workflows, optionally builds the Dockerfile, then calls `consecration.verify`.
  A failed verdict triggers `_rollback`: the failed copy is kept as `work-failed` and a fresh copy
  is restored from the snapshot.

## 6. Run lifecycle

Statuses: `QUEUED`, `RUNNING`, `AWAITING_APPROVAL`, `COMPLETED`, `FAILED`, `CANCELLED`. Terminal
statuses are final. Status changes go through `runs.service.transition`.

- `approve` is valid only in `AWAITING_APPROVAL`, otherwise 409. The request carries
  `roadmap_version`, which must equal the awaited version, so a stale approval cannot authorize a
  different roadmap. It writes an approval row (decision, note, roadmap version, patch-set digest,
  actor `local`) and an `approval_received` event.
- `edit` excludes tasks, creates roadmap version n+1 with a new digest, and returns to
  `AWAITING_APPROVAL`. It is validated before anything is recorded: unknown task ids, or an edit
  that would leave no tasks, return 422.
- `reject` cancels the run. `cancel` on a waiting run cancels at once and ends the paused graph.
  `cancel` on a `RUNNING` run records `cancel_requested`, and the next node guard finishes it.
- The patch-set digest is the SHA-256 of the included tasks' id, action and files. It becomes the
  digest of real patches in task 7.

## 7. SSE

Events are persisted in `run_events` with a unique `(run_id, sequence)`. Envelope: `event_id`,
`run_id`, `sequence`, `type`, `timestamp`, `payload`; the SSE `id:` is `sequence`. The stream
endpoint replays `sequence > cursor` (from `Last-Event-ID` or `?after=`), tails new rows, sends
`retry:` and heartbeat comments, and closes once the run is terminal and drained. Types follow the
blueprint plus `run_cancelled` and `cancel_requested`. `terminal_log` payloads carry the real
command, exit code, duration and output tail, with secrets redacted.

## 8. Vitality Score

`H = 0.20 D + 0.30 T + 0.20 Q + 0.15 S + 0.15 C`. When a dimension is unmeasured it is excluded,
`H = sum(w_i s_i) / sum(w_i)` over measured dimensions, and the result carries the effective
weight so the UI can show how much of the model is known. If nothing is measured, `H` is `null`.

## 9. Data model

Implemented: Repository, Run (with `current_stage`, `roadmap_version`, `patch_set_digest`,
`pinned_commit`, `error`, `completed_at`), Event, Approval, Artifact (type, name, path, SHA-256,
size). Findings, roadmap tasks and baselines are stored as JSON artifacts, not tables.
Planned per blueprint: WorkflowCheckpoint (LangGraph owns it today), Finding, RoadmapTask, Patch and
Verification tables when queries over them are needed.

## 10. Security notes

Docker is a practical isolation boundary, not a guarantee. The sandbox runs as non-root with CPU,
memory, PID and time limits, no network by default, restricted mounts, and no home directory or
Docker socket. The runner sits behind an interface so it can be unit tested with a fake runner.

**Environment constraint:** the development sandbox here has no working Docker daemon (podman is
missing `conmon`). Real container execution must be verified on a machine with Docker.

## Known deviations from the blueprint

| Area | Blueprint | Current code |
| --- | --- | --- |
| Event types | the 11 listed types | all 11 are emitted. Four extensions: `run_cancelled`, `cancel_requested`, `patch_applied`, `rollback_completed` |
| Extra endpoints | the 10 listed | adds `GET /api/revive`, `GET /api/revive/{id}`, `GET /api/revive/{id}/artifacts/{artifact}` and `GET /api/health`. None changes a listed contract |
| Duplicate-event protection | required | the server guarantees unique, ordered `(run_id, sequence)`. The React reducer drops sequences it has seen |
| Approval actor | actor or local user context | always `local` |
| Roadmap and patch generation | LLM-assisted (LiteLLM) | deterministic rules. LiteLLM is not integrated |
| Sandbox | Docker sandbox | implemented and unit tested, never run against a real daemon. Bundled fixtures fall back to the host with rlimits |
| Compose | Docker Compose packaging | single service with no Docker socket, sandbox off, the container is the boundary |
| Build and coverage | recorded when run | coverage is never measured. Docker build is opt-in (`NECRO_ALLOW_DOCKER_BUILD`) |
| Revert in Diff Inspector | revert in managed workspace | not implemented. Rollback is automatic on a failed verification |
| Entities | Finding, Roadmap, RoadmapTask, Patch, Verification tables | stored as JSON artifacts |
| Observability, Git library | OpenTelemetry, GitPython | not added. `git` runs through the command runner |
| Fixtures | `legacy-python-cli`, `legacy-ts-utils`, `legacy-cpp-parser` | `legacy-python-cli` only |
| Languages | Python P0, TypeScript P1, C++ stretch | Python only |
| Folder layout | blueprint's logical tree | consolidated under `backend/app/` as the blueprint allows |
