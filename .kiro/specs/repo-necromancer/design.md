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

Implemented (`backend/app/`):
- `config.py`: settings from `NECRO_*` variables. `db.py`: SQLAlchemy tables (`repositories`,
  `runs`, `run_events`, `approvals`, `artifacts`). `schemas.py`: Pydantic v2 contracts.
- `repositories/`: `paths.py`, `urls.py`, `detect.py`, `service.py` (intake).
- `adapters/`: `base.py` (interface), `models.py` (Finding, AutopsyReport, BaselineReport),
  `python_adapter.py` (Autopsy, Ruff, pip-audit, baseline pytest), `python_ast.py`,
  `python_deps.py`. Adapters do not import LangGraph.
- `agents/roadmap.py`: deterministic, evidence-linked Revival Roadmap and versioned edits.
- `execution/`: `runner.py` (no-shell command runner, timeouts, process-group kill, resource
  limits, `HostFixtureRunner`), `redact.py`.
- `workspace.py`: per-run directories, snapshot, commit pinning, hardened clone.
- `graph/`: `state.py` (typed state), `nodes.py`, `workflow.py`, `runner.py` (thread pool,
  SQLite checkpointer, failure handling).
- `runs/`: `service.py` (lifecycle), `events.py` (event log), `orchestrator.py` (API to graph).
- `artifacts.py`, `reports.py`, `fixtures.py`, `api/` (thin routers).

Planned: `scoring/health_score.py`, `execution/sandbox.py` (Docker), `agents/reanimation.py`,
`agents/consecration.py`, TypeScript and C++ adapters, `frontend/` application.

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

## 5. LangGraph state machine (implemented up to the approval gate)

```
START -> validate_and_snapshot -> autopsy -> characterization
  INSPECT -> complete_inspect -> END
  PREVIEW -> roadmap -> complete_preview -> END
  REVIVE  -> roadmap -> request_approval -> await_decision (interrupt)
               approve -> reanimation_unavailable -> END   (task 7 replaces this node)
               edit    -> revise -> request_approval
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
- The roadmap belongs to the REANIMATION stage. Approval does not leave the stage.
- Characterization runs in a disposable copy of the snapshot, so the snapshot stays pristine for
  the working copy that Reanimation will create.

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
| Event types | the 11 listed types | all 11 defined. `health_score_updated`, `diff_generated`, `verification_completed` are not emitted yet. Two extensions added: `run_cancelled`, `cancel_requested` |
| Duplicate-event protection | required | the server guarantees unique, ordered `(run_id, sequence)`. Client-side de-duplication by `sequence` arrives with the React hook |
| Approval actor | actor or local user context | always `local` |
| Roadmap generation | LLM-assisted (LiteLLM) | deterministic rules over findings. LiteLLM is introduced for patch generation (task 7) |
| Baseline execution | Docker sandbox | bundled fixtures run on the host with resource limits. Other sources are not run. Sandbox is task 6 |
| Build and coverage | recorded when run | always "not measured" until a coverage tool or build runs |
| Reanimation, Consecration, Vitality Score | required | not implemented. An approved run ends `FAILED` with an explicit reason |
| Entities | Finding, Roadmap, RoadmapTask, Patch, Verification tables | stored as JSON artifacts for now |
| Observability, Git library | OpenTelemetry, GitPython | not added. `git` runs through the command runner |
| Fixtures | `legacy-python-cli`, `legacy-ts-utils`, `legacy-cpp-parser` | `legacy-python-cli` only |
| Folder layout | blueprint's logical tree | consolidated under `backend/app/` as the blueprint allows |
