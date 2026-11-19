# Repo Necromancer

**Kiroween 2026 | Category: Agentic AI**

> Build something wicked and git commit to the darkness.
>
> Diagnose. Reanimate. Verify.

Repo Necromancer is an Agentic AI-powered legacy software repository analysis and modernization
platform. It accepts a Git URL, a local project path or a demo fixture. It inspects the codebase,
identifies technical debt, establishes baseline behaviour, generates a **Revival Roadmap**, pauses
for human approval, applies approved patches in an isolated working copy and verifies the result
with real tools.

**Product truth:** inspect first, approve before mutation, patch an isolated working copy, verify
with real tools, never auto-push. The original repository is never the patch target.

## Project status

The blueprint targets a seven-day build. The backend now covers Autopsy, Characterization, the
Revival Roadmap and the approval gate. Reanimation, Consecration and the dashboard are not built.

| Area | State |
| --- | --- |
| Specs, steering, architecture | Done (`.kiro/`) |
| Repository intake: inspect, import, list, get, fixtures | Done |
| Local path and Git URL validation, Git clone with commit pinning, managed snapshot | Done |
| Python Autopsy: AST, Ruff, pip-audit, dependency and test discovery, findings | Done |
| LangGraph workflow: typed state, SQLite checkpoints, approval interrupt, cancellation, bounded retries | Done |
| Characterization: baseline pytest run, pre-existing failures recorded | Done for bundled fixtures. Other sources report "not run" until the sandbox exists |
| Revival Roadmap, approve / edit / reject, versioned roadmaps, patch-set digest | Done |
| Revive API with SSE replay, artifacts with checksums | Done |
| Restricted Docker sandbox | Not started |
| Reanimation (patches, diff, rollback) | Not started. An approved REVIVE run ends `FAILED` with an explicit message |
| Consecration and Vitality Score | Not started |
| React dashboard | Theme tokens only (`frontend/src/styles/theme.css`) |
| Docker Compose packaging, LiteLLM-assisted patch generation | Not started |

Nothing marked "Not started" is simulated. The API never reports progress that did not happen.

## What a run does today

| Mode | Stages executed | Ends |
| --- | --- | --- |
| INSPECT | Autopsy, Characterization | `COMPLETED` |
| PREVIEW | Autopsy, Characterization, Revival Roadmap | `COMPLETED` (no patches yet) |
| REVIVE | Autopsy, Characterization, Revival Roadmap, then waits for approval | `AWAITING_APPROVAL`. After approval: `FAILED` with "Reanimation is not implemented yet" |

Baseline tests run only for the bundled demo fixtures, on the host with CPU, memory and file-size
limits, because they are trusted code and the sandbox does not exist yet. Git URL and local-path
repositories are analysed statically and their tests are reported as **not run** (unknown, never
zero). Set `NECRO_ALLOW_HOST_FIXTURE_EXEC=0` to disable host execution entirely.

## Workflow

The four stage names and their order are fixed.

| # | Stage | What it does |
| --- | --- | --- |
| 1 | **Autopsy** | Read-only inspection: languages, frameworks, dependencies, tests, build config, static analysis, dependency audit, evidence-backed findings |
| 2 | **Characterization** | Runs baseline tests in the sandbox, records pass/fail/skip, build outcome and pre-existing failures. Coverage only if a coverage tool ran |
| 3 | **Reanimation** | Generates the Revival Roadmap, **pauses for human approval**, then generates and applies approved patches to an isolated working copy |
| 4 | **Consecration** | Tests, lint, dependency audit, Docker build and CI checks. Compares against the baseline and produces the report and Vitality Score |

If verification fails, changes are never copied to the source. The failed working copy and logs are
kept, the managed copy is rolled back or marked failed, and the rollback is recorded.

## Execution modes

| Mode | Behaviour | Mutation |
| --- | --- | --- |
| **Inspect** (default) | Read-only analysis, tests and findings | None |
| **Preview** | Roadmap and proposed diffs | No patch applied |
| **Revive** | Applies explicitly approved patches and verifies them | Isolated working copy only, approval required |

## Vitality Score

`H = 0.20 D + 0.30 T + 0.20 Q + 0.15 S + 0.15 C`

| Dimension | Weight | Evidence |
| --- | --- | --- |
| Dependencies (D) | 20% | Known vulnerabilities, outdated packages, lockfile consistency |
| Tests (T) | 30% | Discovery, pass/fail ratio, coverage only when measured |
| Code quality (Q) | 20% | Lint findings, static analysis |
| Security (S) | 15% | Dependency audit and configured security checks |
| CI / Docker (C) | 15% | Build result, Docker build, CI workflow checks |

Unknown dimensions stay unknown. They are never scored as 100. If the aggregate uses only measured
dimensions, the weights are renormalised, the effective weight is displayed, and scores with
different measurement sets are not compared without a warning.

## Safety model

| Risk | Mitigation |
| --- | --- |
| Untrusted code execution | Restricted sandbox only |
| Privilege escalation | Non-root container user |
| Resource exhaustion | CPU, memory, process and time limits |
| Host compromise | No home directory or Docker socket mounts, restricted mounts only |
| Exfiltration | Network disabled by default |
| Source modification | Managed working copy, original stays unchanged |
| Unauthorized patching | Mandatory approval gate, recorded with roadmap version and patch-set digest |
| Path traversal | Paths resolved (symlinks included) and checked against allowed roots |
| Credential exposure | No secrets in URLs or logs |
| Runaway execution | Timeouts, cancellation, bounded retries |

Docker is a practical isolation boundary for the MVP, not a guarantee against every container
escape. Treat running untrusted code as high risk.

**Implemented now:** path and URL validation (re-checked when a run starts), snapshots that skip
`.git` and symlinks, hardened `git clone` (hooks off, only https and ssh, no prompts, `--`
separator), detection that never runs git or repository code, a command runner with no shell, a
minimal environment, timeouts that kill the process group and output redaction, approval bound to a
roadmap version, approval audit records, artifact checksums verified on download, cancellation.
**Planned:** the Docker sandbox, credential flow, rollback.

## Technology

| Layer | Choice |
| --- | --- |
| Backend | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy, SQLite |
| Agent orchestration | LangGraph with a persistent checkpointer, LiteLLM as LLM gateway |
| Code intelligence | Python AST, Tree-sitter where useful, Ruff, pip-audit |
| Execution | Restricted Docker sandbox, Docker Compose |
| Streaming | Server-Sent Events |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS, Lucide React, Diff2Html |
| Testing | Pytest, Ruff, frontend type-check and tests |
| Adapters | Python (P0), TypeScript (P1), C++ (stretch) |

Installed now: FastAPI, Pydantic, SQLAlchemy, Uvicorn, LangGraph with the SQLite checkpointer, pip-audit and Ruff. LiteLLM, Tree-sitter and OpenTelemetry arrive with their phases.

## API

Contracts follow the blueprint. See `/docs` on a running server for the live schema.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/repositories/inspect` | Validate a Git URL, local path or fixture (read-only, nothing stored) |
| POST | `/api/repositories/import` | Register a repository |
| GET | `/api/repositories` | List repositories |
| GET | `/api/repositories/{id}` | Repository metadata |
| POST | `/api/revive/start` | Start a run (`INSPECT` default, `PREVIEW` or `REVIVE`) |
| GET | `/api/revive/stream/{run_id}` | SSE stream with `Last-Event-ID` replay and heartbeats |
| POST | `/api/revive/approve/{run_id}` | `approve`, `edit` or `reject`. Valid only while `AWAITING_APPROVAL`, and `roadmap_version` must match |
| POST | `/api/revive/{run_id}/cancel` | Cancel a run |
| GET | `/api/revive/{run_id}/artifacts` | List artifacts with type, size and SHA-256 |
| GET | `/api/revive/{run_id}/artifacts/{id}` | Download one artifact (checksum verified) |
| GET | `/api/fixtures` | List demo fixtures |

Local paths are resolved by the local backend, not the browser, and must fall inside an allowed
root.

**SSE envelope:** `event_id`, `run_id`, `sequence`, `type`, `timestamp`, `payload`. The SSE `id:`
field is `sequence`. Types: `run_started`, `stage_changed`, `roadmap_ready`, `hitl_required`,
`approval_received`, `terminal_log`, `run_completed`, `run_failed`, plus two extensions the
blueprint lacks, `run_cancelled` and `cancel_requested`. `health_score_updated`, `diff_generated`
and `verification_completed` are defined but not emitted until their features exist. Clients should
drop events whose `sequence` they have already seen.

**Artifacts per run:** `AUTOPSY_REPORT.md`, `autopsy.json`, `CHARACTERIZATION_REPORT.md`,
`characterization.json`, `REVIVAL_ROADMAP.v{n}.md`, `roadmap.v{n}.json`.

## Run the backend

Requires Python 3.12+ and Git. Dependency auditing (`pip-audit`) and Git URL imports need network
access; without it the audit is reported as "not measured" and the run continues.

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:create_app --factory --port 8000
```

Open http://localhost:8000/docs, or:

```bash
curl localhost:8000/api/health
curl localhost:8000/api/fixtures
curl -X POST localhost:8000/api/repositories/inspect \
  -H 'content-type: application/json' -d '{"fixture_id": "legacy-python-cli"}'
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
| `NECRO_FIXTURES_DIR` | `fixtures` | Demo fixtures, always allowed |
| `NECRO_ALLOW_HOST_FIXTURE_EXEC` | `1` | Run bundled fixtures' baseline tests on the host. `0` disables it |

`/` and your home directory are refused as roots. Git URLs must be `https://`, `ssh://` or
`git@host:path`.

## Brand

Haunted Cyberpunk: a gothic software-forensics engineering console. Obsidian and slate surfaces,
phosphor emerald signals, restrained spectral violet. No gore, cartoon horror, WebGL or heavy 3D.

| Token | Hex | Use |
| --- | --- | --- |
| Obsidian | `#0B0F17` | Background |
| Dark slate | `#111827` | Surfaces |
| Elevated slate | `#1F2937` | Cards, inputs |
| Border | `#334155` | Outlines |
| Phosphor emerald | `#10B981` | Healthy and verified, only with real evidence |
| Muted emerald | `#064E3B` | Selected surfaces |
| Haunted violet / soft violet | `#8B5CF6` / `#A78BFA` | AI and agent activity, never success |
| Pumpkin amber | `#F59E0B` | Approval required, warnings |
| Crimson | `#EF4444` | Failure, critical findings |
| Ice blue | `#38BDF8` | Information |
| Text | `#E5E7EB` / `#94A3B8` / `#64748B` | Primary, secondary, muted |

Fonts: Space Grotesk (headings), Inter (body), JetBrains Mono (terminal and code), IBM Plex Mono
(metrics). Icons: Lucide outline. Status is never shown by colour alone.

## Dashboard (planned)

Five regions: top header, left four-stage rail, central workspace (Overview, Revival Roadmap,
Diff Inspector, Kiro Trace), right Vitality inspector, bottom Revival Terminal. Target is WCAG AA,
keyboard focus, reduced motion and CPU-only laptops.

## Repository layout

```
.kiro/specs/repo-necromancer/   requirements.md, design.md, tasks.md
.kiro/steering/                 product, stack, rules, security, standards, testing, brand
backend/app/
  api/            thin FastAPI routers
  repositories/   intake: path and URL validation, read-only detection
  adapters/       language adapters (Python today): models, AST, dependencies, Ruff, pip-audit
  agents/         roadmap generation
  graph/          LangGraph state, nodes, workflow, runner
  execution/      command runner and secret redaction
  runs/           run lifecycle, event log, orchestrator
  workspace.py    snapshots, commit pinning, hardened clone
backend/tests/    pytest suite
fixtures/         demo fixtures (legacy-python-cli)
frontend/         theme tokens only for now
```

## Terminology

Autopsy, Characterization, Reanimation and Consecration are the four stages. The **Vitality Score**
is measured repository health. The **Revival Roadmap** is the prioritised remediation plan.
**Surgical Patches** are reviewable code changes. The **Revival Terminal** shows real command output.
The **Authorization ritual** is the human approval gate. **Kiro Trace** is the workflow event
history.

## License

See [LICENSE](LICENSE).
