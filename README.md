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

## Quick start

Requires Python 3.12+, Node 20+ and Git.

```bash
scripts/run_local.sh          # builds the dashboard, starts API + UI on http://127.0.0.1:8000
```

Open http://127.0.0.1:8000, choose **Demo fixture**, then **Legacy Python CLI**, pick a mode and
start. `DEMO.md` walks through the three-minute demo. `scripts/reset_demo.sh` forgets all runs.

Or run the pieces yourself:

```bash
# backend
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:create_app --factory --port 8000
# frontend, in another terminal (proxies /api to :8000)
cd frontend && npm ci && npm run dev
```

Or with Docker Compose (see [Packaging](#packaging-and-the-docker-sandbox) for what that changes):

```bash
docker compose -f deployment/docker-compose.yml up --build
```

## What works

| Area | State |
| --- | --- |
| Intake: Git URL, local path, demo fixture, with path and URL validation | Done |
| Autopsy: AST, Ruff, pip-audit, dependency and test discovery, evidence-backed findings | Done (Python) |
| Characterization: baseline tests, pre-existing failures recorded, coverage and build "not measured" | Done |
| Revival Roadmap: versioned, evidence-linked, editable | Done (rule-based) |
| Patches: bound to file hashes and a digest, shown as diffs | Done for 6 kinds of task |
| Approval: approve / edit / reject, bound to roadmap version and patch-set digest | Done |
| Reanimation: apply to an isolated copy, rollback with evidence kept | Done |
| Consecration: tests, lint, dependency audit, CI validation, optional Docker build | Done |
| Vitality Score: before and after, unknown stays unknown | Done |
| Live events: SSE with replay, `Last-Event-ID`, heartbeats | Done |
| Dashboard: header, stepper, workspace, Vitality inspector, terminal, all four views | Done |
| Restricted Docker sandbox | Built and unit tested. **Never run against a real Docker daemon** (see below) |
| AI-assisted patches (LiteLLM) | Built, **off by default**. Tested with a scripted fake client only. Never called a real model (see below) |
| TypeScript and C++ adapters, OpenTelemetry, MCP | Not built (P1 and stretch) |

### What was verified, and what was not

- **Backend:** 254 tests pass and 1 is skipped. `ruff check` is clean.
- **Frontend:** `tsc --noEmit` is clean, 53 tests pass and the production build succeeds. The tests
  cover the reducer, the stream hook (replay, reconnect, de-duplication), diff escaping, the
  approval flow, and a **contract test that replays a real run exported from the backend**.
- **Real tools:** a full REVIVE run on the demo fixture was executed with real Ruff, real pytest and
  real pip-audit. It took known vulnerabilities from 13 to 0 and Ruff violations from 1 to 0, kept
  the pre-existing test failure as a baseline failure, and moved the score from 36.3 to 81.2.
- **Not verified here:** the Docker sandbox and Compose image (no Docker daemon was available), and
  the dashboard in a real browser against a live server. The one real-container test is skipped and
  reported as skipped. The Dockerfile steps were replayed locally (clean `npm ci` and build, clean
  `pip install`, static serving, a run on the thread-pool executor). `scripts/run_local.sh` and
  `scripts/reset_demo.sh` were syntax-checked only, because they start a server or delete data.

## Workflow

The four stage names and their order are fixed.

| # | Stage | What it does |
| --- | --- | --- |
| 1 | **Autopsy** | Read-only inspection: languages, frameworks, dependencies, tests, static analysis, dependency audit, findings. Never runs repository code or git on the source |
| 2 | **Characterization** | Runs baseline tests, records pass/fail/skip and pre-existing failures. Coverage and build are reported "not measured" |
| 3 | **Reanimation** | Builds the Revival Roadmap and patch proposals, **waits for approval**, then applies exactly the approved patch set to an isolated copy |
| 4 | **Consecration** | Re-runs analysis and tests on the patched copy, compares with the baseline, scores it, and rolls back on a regression |

### Modes

| Mode | Stages run | Mutation |
| --- | --- | --- |
| **Inspect** (default) | Autopsy, Characterization | None |
| **Preview** | plus roadmap and diffs | No patch is applied anywhere |
| **Revive** | plus approval, Reanimation, Consecration | Only the approved patches, only in an isolated copy |

### Patches

A patch is a whole-file change bound to the SHA-256 of the file it was made against, and the
patch-set digest covers every (task, path, before, after). Approving a digest approves exactly those
changes. At apply time each file must still match its recorded hash, and the approval on record
must match the digest, otherwise nothing is written. Patches are generated for:

- dependency upgrades to the highest fix version pip-audit lists for the pinned package
- hardcoded secrets, replaced by an environment lookup (the value is never shown or logged)
- `os.popen("cmd " + x).read()`, rewritten as an argument-list `subprocess.run` with no shell
- Ruff's safe automatic fixes
- a CI workflow and a non-root Dockerfile, when missing

Everything else gets a stated reason instead of a guess, for example "tests are never edited
automatically" or "no automatic fix exists for this construct". Dependency upgrades can break
compatibility. Only the repository's tests (and the Docker build, if enabled) check that.

### AI-assisted patches (opt-in)

Set `NECRO_LLM_MODEL` (any LiteLLM model string, for example `openai/gpt-4o`) and the provider's
usual API key variable. With no model set, nothing leaves your machine and nothing changes.

When enabled, a security finding that has **no rule-based fix** (for example `eval`, `os.system`,
`subprocess` with `shell=True`, `pickle`, `yaml.load`) is sent to the model, together with only the
source of the enclosing function. The dashboard says so when it is on. The model is treated as an
untrusted text generator, and the repository code is treated as untrusted data (prompt injection):

- Code that looks like it holds a secret is never sent.
- The reply must be JSON. The new function must keep its name, parameters and decorators, and parse.
- The same detectors re-scan the file. The finding must be gone and no new security finding may
  appear, which rejects injected shell calls and similar.
- Calls use temperature 0, a timeout, a token cap and a per-run budget (`NECRO_LLM_MAX_TASKS`,
  default 5). A failed call is never retried.
- The result is a normal patch: labelled **AI-proposed** in the diff list and the approval dialog,
  covered by the approved digest and the file-hash checks, and recorded in the audit trail.

These checks reduce risk but do not prove a patch is correct. Read AI-proposed diffs closely.
Only the scripted tests exercise this path. It has **not** been run against a real model.

### Verification and rollback

Consecration fails a run, and rolls the working copy back to the snapshot, if tests regress against
the baseline (a failure that was already in the baseline is not a regression), tests cannot run
after the patch although they ran before, lint gets worse, known vulnerabilities rise, or a CI file
is invalid. The failed copy is kept as `work-failed` for diagnosis, and the rollback is an event in
the audit trail. Checks that could not be measured are reported as not measured, not as passed.

## Vitality Score

`H = 0.20 D + 0.30 T + 0.20 Q + 0.15 S + 0.15 C`

| Dimension | Weight | How it is measured |
| --- | --- | --- |
| Dependencies (D) | 20% | Share of audited, exactly pinned packages with no known vulnerabilities. Unpinned packages are not audited |
| Tests (T) | 30% | Passed / executed. Coverage is not measured |
| Code quality (Q) | 20% | `100 / (1 + density / 10)`, with density in Ruff violations per 1000 lines |
| Security (S) | 15% | 100 minus penalties for static AST findings. A static scan only, not a security assessment |
| CI / Docker (C) | 15% | CI and Dockerfile validated count 1, present but unverified count half, absent or failing 0 |

A dimension that was not measured is `null`. It is excluded and the remaining weights are
renormalised, the effective weight is shown, and a warning appears if the before and after scores
used different measured dimensions. Nothing is scored 100 because it is missing.

## Safety model

| Risk | Mitigation |
| --- | --- |
| Untrusted code execution | Tests run only in the restricted Docker sandbox. Bundled fixtures may fall back to the host with resource limits. Repositories with no runner get static analysis only |
| Privilege escalation | Sandbox runs as `65534:65534`, read-only root, all capabilities dropped, `no-new-privileges` |
| Resource exhaustion | CPU, memory, PID and time limits per command. Whole process group killed on timeout |
| Exfiltration | Sandbox network is off. Dependency install is opt-in and wheels-only |
| Host compromise | One mount only, inside the managed run directory. Home directory, its ancestors and the Docker socket are refused |
| Source modification | Work happens on a snapshot that skips `.git` and symlinks. The source is never written |
| Unauthorized patching | Approval is required, bound to roadmap version and patch-set digest, and recorded |
| Path traversal | Paths are resolved (symlinks included) and checked against allowed roots, again when a run starts |
| Hostile Git input | https/ssh only, `--` separator, hooks off, no prompts, no credential helper |
| Credential exposure | Secrets are redacted from output, events, errors and reports. Environment passed to commands is minimal |
| Runaway execution | Bounded retries (two attempts, transient I/O only), cancellation checked between steps |
| Tampered artifacts | SHA-256 recorded and verified on every download |

Docker is a practical isolation boundary, not a guarantee against every container escape.
Building a repository's own Dockerfile runs its `RUN` steps with network access, so it is off unless
`NECRO_ALLOW_DOCKER_BUILD=1`.

### Packaging and the Docker sandbox

The per-command sandbox needs the backend to talk to a Docker daemon. Build its image once:

```bash
docker build -f deployment/sandbox.Dockerfile -t necromancer-sandbox:latest deployment
```

`GET /api/health` reports whether the sandbox is usable, and the dashboard shows a notice when it is
not. **Docker Compose does not mount the Docker socket**, because that would give the app container
root-equivalent control of the host. The Compose container is itself the boundary (non-root,
read-only root filesystem, no capabilities, memory/CPU/PID limits), runs with the sandbox off, and
only runs bundled fixtures' tests. For the full sandbox, run the backend on a host with Docker.

## API

See `/docs` on a running server for the live schema.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/repositories/inspect` | Validate a Git URL, local path or fixture. Read-only, nothing stored |
| POST | `/api/repositories/import` | Register a repository |
| GET | `/api/repositories`, `/api/repositories/{id}` | List and read repositories |
| POST | `/api/revive/start` | Start a run (`INSPECT` default, `PREVIEW` or `REVIVE`) |
| GET | `/api/revive/stream/{run_id}` | SSE with `Last-Event-ID` replay and heartbeats |
| POST | `/api/revive/approve/{run_id}` | `approve`, `edit` or `reject`. Valid only while `AWAITING_APPROVAL`, and `roadmap_version` must match |
| POST | `/api/revive/{run_id}/cancel` | Cancel a run |
| GET | `/api/revive/{run_id}/artifacts`, `.../{id}` | List and download artifacts (checksum verified) |
| GET | `/api/fixtures` | List demo fixtures |
| GET | `/api/revive`, `/api/revive/{run_id}` | **Additive:** list runs, read one run (used to restore the UI) |
| GET | `/api/health` | **Additive:** status and sandbox availability |

**SSE envelope:** `event_id`, `run_id`, `sequence`, `type`, `timestamp`, `payload`. Types are the
blueprint's eleven plus four documented extensions: `run_cancelled`, `cancel_requested`,
`patch_applied` and `rollback_completed`. Clients drop events whose `sequence` they have seen.

**Artifacts per run:** `AUTOPSY_REPORT.md`, `CHARACTERIZATION_REPORT.md`, `REVIVAL_ROADMAP.v{n}.md`,
`PATCHSET.v{n}.diff` and `.md`, `VERIFICATION_REPORT.md`, `VITALITY_REPORT.json`, `RUN_AUDIT.json`,
plus the JSON the dashboard reads.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `NECRO_DATA_DIR` | `backend/.data` | SQLite databases, run directories, artifacts |
| `NECRO_ALLOWED_ROOTS` | none | Extra directories (separated by `:`) that local paths may point into |
| `NECRO_FIXTURES_DIR` | `fixtures` | Demo fixtures, always allowed |
| `NECRO_SANDBOX` | `auto` | `off` never uses Docker |
| `NECRO_SANDBOX_IMAGE` | `necromancer-sandbox:latest` | Image for sandboxed commands |
| `NECRO_SANDBOX_INSTALL_DEPS` | `0` | `1` installs a repository's wheels (network on) before its tests |
| `NECRO_ALLOW_HOST_FIXTURE_EXEC` | `1` | Let bundled fixtures run tests on the host when there is no sandbox |
| `NECRO_ALLOW_DOCKER_BUILD` | `0` | `1` builds the repository's Dockerfile during Consecration |
| `NECRO_STATIC_DIR` | `frontend/dist` | Built dashboard served at `/` |
| `NECRO_LLM_MODEL` | empty (off) | LiteLLM model for AI-assisted patches. Sends function source to that provider |
| `NECRO_LLM_TIMEOUT` / `NECRO_LLM_MAX_TASKS` | `60` / `5` | Per-call timeout in seconds, and AI patch requests per run |

`/` and your home directory are refused as allowed roots. Git URLs must be `https://`, `ssh://` or
`git@host:path`.

## Develop

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/python -m pytest -q
cd frontend && npm run typecheck && npm test && npm run build
cd backend && .venv/bin/python -m tests.export_ui_fixture   # regenerate the UI contract fixture
```

Run stream tests under a hard `timeout`: a run that is waiting for approval keeps its stream open
by design.

## Brand

Haunted Cyberpunk: a gothic software-forensics engineering console. No gore, cartoon horror, WebGL
or heavy 3D. Tokens live in `frontend/src/styles/theme.css` and `.kiro/steering/brand.md`.

| Token | Hex | Meaning |
| --- | --- | --- |
| Obsidian / dark slate / elevated slate | `#0B0F17` `#111827` `#1F2937` | Background, panels, cards |
| Border | `#334155` | Outlines |
| Phosphor emerald | `#10B981` | Healthy or verified, only with real evidence |
| Haunted violet / soft violet | `#8B5CF6` `#A78BFA` | Agent activity, never success |
| Pumpkin amber | `#F59E0B` | Approval required, warnings |
| Crimson | `#EF4444` | Failure, critical findings |
| Ice blue | `#38BDF8` | Information |

Fonts (self-hosted): Space Grotesk, Inter, JetBrains Mono, IBM Plex Mono. Icons: Lucide outline.
Status is never colour alone. The UI honours `prefers-reduced-motion`.

## Layout

```
.kiro/specs/repo-necromancer/   requirements.md, design.md, tasks.md
.kiro/steering/                 product, stack, rules, security, standards, testing, brand
backend/app/                    api, repositories, adapters, agents, graph, execution, runs, scoring
backend/tests/                  pytest suite and UI fixture exporter
frontend/src/                   components, hooks, state reducer, API client, tests
fixtures/legacy-python-cli/     deterministic demo repository
deployment/                     app Dockerfile, sandbox Dockerfile, docker-compose.yml
scripts/                        run_local.sh, reset_demo.sh
```

## Terminology

Autopsy, Characterization, Reanimation and Consecration are the four stages. The **Vitality Score**
is measured repository health. The **Revival Roadmap** is the prioritised remediation plan.
**Surgical Patches** are reviewable code changes. The **Revival Terminal** shows real command output.
The **Authorization ritual** is the human approval gate. **Kiro Trace** is the workflow event
history.

## License

See [LICENSE](LICENSE).
