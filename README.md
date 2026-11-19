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

The blueprint targets a seven-day build. Only the backend foundation exists so far.

| Area | State |
| --- | --- |
| Specs, steering, architecture | Done (`.kiro/`) |
| Repository intake: inspect, import, list, get, fixtures | Done |
| Local path and Git URL validation | Done |
| Revive API: start, SSE stream with replay, approve, cancel, artifacts | Done. Runs stay `QUEUED` until the workflow executor exists |
| Python Autopsy adapter (AST, Ruff, pip-audit) | Not started |
| LangGraph workflow, checkpointing, approval interrupt | Not started |
| Characterization (baseline tests) | Not started |
| Restricted Docker sandbox | Not started |
| Reanimation (patches, diff, rollback) | Not started |
| Consecration and Vitality Score | Not started |
| React dashboard | Not started |
| Docker Compose packaging | Not started |

Nothing marked "Not started" is simulated. The API never reports progress that did not happen.

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

**Implemented now:** path and URL validation, source immutability for intake, approval-state
enforcement, approval audit records, cancellation. **Planned:** the sandbox, credential flow,
rollback and resource limits.

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

Currently installed: FastAPI, Pydantic, SQLAlchemy and Uvicorn. The rest arrives with its phase.

## API

Contracts follow the blueprint. See `/docs` on a running server for the live schema.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/repositories/inspect` | Validate a Git URL, local path or fixture (read-only, nothing stored) |
| POST | `/api/repositories/import` | Register a repository |
| GET | `/api/repositories` | List repositories |
| GET | `/api/repositories/{id}` | Repository metadata |
| POST | `/api/revive/start` | Start a run (`INSPECT`, `PREVIEW` or `REVIVE`) |
| GET | `/api/revive/stream/{run_id}` | SSE stream with `Last-Event-ID` replay and heartbeats |
| POST | `/api/revive/approve/{run_id}` | Approve or reject (valid only while `AWAITING_APPROVAL`) |
| POST | `/api/revive/{run_id}/cancel` | Cancel a run |
| GET | `/api/revive/{run_id}/artifacts` | List run artifacts |
| GET | `/api/fixtures` | List demo fixtures |

Local paths are resolved by the local backend, not the browser. They must fall inside an allowed
root.

The current event envelope differs from the blueprint. See
[Known deviations](.kiro/specs/repo-necromancer/design.md#known-deviations-from-the-blueprint).

## Run the backend

Requires Python 3.12+ and Git.

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
.kiro/steering/                 product, stack, rules, security, standards, testing
backend/app/                    FastAPI app (api, repositories, runs, db, schemas)
backend/fixtures/               demo fixtures
backend/tests/                  pytest suite
```

## Terminology

Autopsy, Characterization, Reanimation and Consecration are the four stages. The **Vitality Score**
is measured repository health. The **Revival Roadmap** is the prioritised remediation plan.
**Surgical Patches** are reviewable code changes. The **Revival Terminal** shows real command output.
The **Authorization ritual** is the human approval gate. **Kiro Trace** is the workflow event
history.

## License

See [LICENSE](LICENSE).
