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
| Autopsy: AST, Ruff, pip-audit, secret-pattern scan of config and script files, dependency and test discovery, evidence-backed findings | Done for Python, TypeScript and C++ |
| Characterization: baseline tests, pre-existing failures recorded, coverage and build "not measured" | Done |
| Revival Roadmap: versioned, evidence-linked, editable | Done (rule-based) |
| Patches: bound to file hashes and a digest, shown as diffs | Done for 6 kinds of task |
| Approval: approve / edit / reject, bound to roadmap version and patch-set digest | Done |
| Reanimation: apply to an isolated copy, rollback with evidence kept | Done |
| Consecration: tests, lint, dependency audit, CI validation, optional Docker build | Done |
| Vitality Score: before and after, unknown stays unknown | Done |
| Live events: SSE with replay, `Last-Event-ID`, heartbeats | Done |
| Dashboard: header, stepper, workspace, Vitality inspector, terminal, all four views, run history | Done |
| OpenTelemetry traces correlated by run and stage | Built, **off by default** (`NECRO_OTEL`). Tested with an in-memory exporter. Never sent to a real collector |
| Restricted Docker sandbox | Built and unit tested. **Never run against a real container runtime**: attempted, blocked by the build environment. Run `scripts/verify_sandbox.sh` on a machine with Docker (see [Verifying the sandbox](#verifying-the-sandbox)) |
| AI-assisted patches (LiteLLM) | Built, **off by default**. Tested with a scripted fake client only. Never called a real model (see below) |
| TypeScript adapter | Built with limits (see below). Python stays the complete flow |
| C++ adapter | Built with limits (see below). Real CMake, compiler, CTest and clang-tidy verified here |
| MCP server for AI agents | Built. Read-only plus Inspect and Preview. **No approval or Revive tool exists** (see below) |

### What was verified, and what was not

- **Backend:** 406 tests pass and 8 are skipped: the real-container sandbox tests, which need a
  container runtime and so did **not** run. `ruff check` is clean.
- **Frontend:** `tsc --noEmit` is clean, 67 tests pass and the production build succeeds. The tests
  cover the reducer, the stream hook (replay, reconnect, de-duplication), diff escaping, the
  approval flow, and a **contract test that replays a real run exported from the backend**.
- **Real tools:** a full REVIVE run on the demo fixture was executed with real Ruff, real pytest and
  real pip-audit. It took known vulnerabilities from 13 to 0 and Ruff violations from 1 to 0, kept
  the pre-existing test failure as a baseline failure, and moved the score from 36.3 to 81.2.
  The TypeScript fixture ran with the real `npm audit` and Node test runner. The C++ fixture ran
  with the real CMake, g++, CTest and clang-tidy (one pass, one baseline failure, build passed).
- **Not verified here:** a real MCP client application (the server was tested through the protocol
  and a real stdio launch), the AI-assisted patches against a real model, OpenTelemetry against a
  real collector, the Docker sandbox and Compose image (no Docker daemon was available), and
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

### TypeScript

The repository's dominant language picks the adapter (the one with more source files; a tie goes to
Python). The demo fixture is `fixtures/legacy-ts-utils`. The adapter never installs dependencies and
never runs the repository's own `npm test` script, because install scripts and arbitrary test
commands are untrusted code. That decides what it can measure:

| Measured | How |
| --- | --- |
| Dangerous calls (`eval`, `new Function`, `child_process.exec`, `innerHTML`, `document.write`, `dangerouslySetInnerHTML`) | Built-in heuristic scan that ignores comments and strings. Not a type checker |
| Secrets | The same secret-pattern scan as for Python |
| Known vulnerabilities | `npm audit --package-lock-only` on a copy of `package.json` and `package-lock.json`, with `--ignore-scripts`. Needs a lockfile and network |
| Baseline tests | Node's built-in test runner (`node --test`, running `.ts` files directly) when the runner provides Node. Dependencies are not installed, so tests that import a package fail and are labelled as environment errors |
| **Not measured** | ESLint and the TypeScript compiler (they need installed dependencies), so code quality stays `null` and out of the score |

Patches for TypeScript are limited: a Node CI workflow is generated, but dependency upgrades, `eval`,
`exec` and secrets are listed with a reason and left to a person. npm upgrades must change
`package.json` and `package-lock.json` together, and no Dockerfile template exists yet. Findings from
TypeScript are never sent to a model. Node has no memory cap through `RLIMIT_AS` (V8 fails under it),
so Node runs use a V8 heap cap (`--max-old-space-size=1024`) plus the CPU and file-size limits. The
Docker sandbox image contains no Node yet, so TypeScript tests only run for bundled fixtures on the
host and are reported as not run elsewhere.

### C++

The demo fixture is `fixtures/legacy-cpp-parser`. Reading a C++ project is safe; **building it is
not**, because `cmake` runs the project's own scripts (`execute_process`, custom commands,
downloads) and compilers run for as long as the project wants. So the adapter splits the work:

| Measured | How |
| --- | --- |
| Unbounded `strcpy`, `strcat`, `sprintf`, `gets`, `scanf("%s")`, `system` / `popen` (rated by whether the argument is constant), format strings from variables, insecure temp names | Built-in heuristic scan that ignores comments, strings and raw strings. Not a compiler |
| Bugprone, CERT and static-analyzer findings | `clang-tidy` run **without a compilation database** (that would need CMake to run), with its own inline config so a repository's `.clang-tidy` is ignored. Real, but partial: it cannot see headers a project fetches or generates, and it says so |
| Dependencies | `find_package`, `FetchContent` (pinned only by tag, hash or SHA, never a branch), `ExternalProject`, Conan and vcpkg, read as text. CMake is never run to learn them |
| Secrets | The same secret-pattern scan, now also over C and C++ sources |
| Build and tests | `cmake` configure, build and `ctest` in a fresh `necro-build` directory of a disposable copy, **only** through a runner allowed to execute code (the Docker sandbox, or bundled fixtures on the host) |
| **Not measured** | Known vulnerabilities: there is no vulnerability lookup for C/C++ dependencies, so it stays `null` and out of the score |

Build status is now `passed`, `failed` or *not measured*. A project that cannot configure here
(missing system dependency, or it needs the network) is reported as *not measured*, not as a broken
build. A compile error is `failed`, and Consecration treats "built before, no longer builds" as a
regression and rolls back. The Docker sandbox image has no C++ toolchain yet, so a sandbox runner
reports the tests as not run; bundled fixtures fall back to the host instead. Patches for C++ are
limited to a CMake CI workflow. Unsafe calls are listed with a reason for a person to fix, and C++
findings are never sent to a model. A scan finding and a clang-tidy finding on the same line are
listed once.

### MCP server (optional)

`python -m app.mcp_server` lets an AI agent (for example Kiro) use Repo Necromancer over MCP. It
talks to a running backend, so start that first. Install with `pip install -r requirements-mcp.txt`.

```json
{
  "mcpServers": {
    "repo-necromancer": {
      "command": "backend/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "backend",
      "env": { "NECRO_API_URL": "http://127.0.0.1:8000" }
    }
  }
}
```

**What an agent can do:** list and import bundled fixtures, start an `INSPECT` or `PREVIEW` run, wait
for it, cancel it, and read findings, baseline, roadmap, patch list and diffs, Vitality Score and
verification. **What it cannot do, by design:** there is no tool to approve, reject or start a
Revive run. Approval is a human decision made in the dashboard, so a prompt-injected agent cannot
authorize a patch. `start_run` refuses `REVIVE` before making any request.

- Importing Git URLs and local paths is off unless `NECRO_MCP_ALLOW_IMPORT=1` (then an
  `import_repository` tool appears, and the backend's own path and URL validation still applies).
  Local paths are never shown to the agent.
- Everything that came from a scanned repository (finding titles, file paths, test names, diffs) is
  untrusted text. It is stripped of control and invisible characters (bidirectional overrides,
  zero-width characters), length-capped, and returned with a notice saying not to follow
  instructions found in it. That is a mitigation, not a guarantee: the real protection is that the
  tools cannot do anything dangerous.
- Ids are validated before they are put into a URL, errors are short messages without tracebacks,
  and the backend must be on this machine unless `NECRO_MCP_ALLOW_REMOTE=1`, because it has no
  authentication.
- Unknown values stay `null` through the interface (a test count of `null` means the tests did not
  run, not zero).

Tested through the MCP protocol against the real backend, including a real stdio launch of the entry
point. Not tested with a real MCP client application such as Kiro.

### Secret-pattern scan

Besides the Python AST scan, Autopsy scans `.env*`, ini, yaml, json, toml, properties, shell and
JavaScript files for private key blocks, AWS, GitHub and Slack tokens, and literal values assigned
to names like `password` or `api_key`. Placeholders such as `changeme` or `${VAR}` are ignored.
A finding names the kind and the line, **never the value**. These become roadmap tasks that need a
person (rotate the credential, move it out of the repository), are never patched automatically and
never sent to a model. Matches are "possible" secrets, so expect some false positives.

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
only runs bundled fixtures' tests. The image has no Node, CMake or compiler, so the TypeScript and
C++ fixtures' tests are reported as not run there. For the full sandbox, or to run those tests, run
the backend on a host with Docker or the toolchains.

### Verifying the sandbox

The sandbox's command line is unit tested, but it has **never started a real container**, so its
core promises (non-root, no network, read-only root, one mount, memory and process limits, timeout
kill) are unverified. On a machine with Docker:

```bash
scripts/verify_sandbox.sh          # builds the image, runs backend/tests/test_sandbox_real.py
```

It exits 0 only if every real-container test ran and passed. A skipped test is not a pass: exit 3
means something was skipped, 2 means no usable runtime, 1 means a test failed. `NECRO_DOCKER_BIN`
selects another Docker-compatible runtime, for example `podman`. The 8 tests have **never run**
either, so a first failure may be a sandbox bug or a test bug.

What an attempt in the build environment found (it could not get further):

- The `docker` command there is Podman 5.2.3, and it has no `conmon`. I fetched the official
  static `conmon` and checked its SHA-256 against the release digest, and Podman then ran.
- `/var`, `/run` and `/usr/local` are read-only, so Podman's storage and runtime directories had to
  be redirected with flags. That worked.
- This Podman build supports only the netavark network helper. The official prebuilt netavark needs
  a newer glibc than the host has, so it cannot run there.
- `/sys/fs/cgroup` is read-only. A runtime cannot create the cgroup that `--memory`, `--cpus` and
  `--pids-limit` need, so the resource limits **cannot be applied or verified in that environment**
  at all. Running with cgroups disabled would test a different configuration and was not done.

Known gaps to check on a real host: on SELinux systems a bind mount may need relabelling (`:z`),
which the runner does not add; and rootless Podman may not enforce all of the limits.

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
| `NECRO_DOCKER_BIN` | `docker` | Container runtime command. Only exit statuses are used, so Docker-compatible runtimes such as Podman can work |
| `NECRO_SANDBOX_INSTALL_DEPS` | `0` | `1` installs a repository's wheels (network on) before its tests |
| `NECRO_ALLOW_HOST_FIXTURE_EXEC` | `1` | Let bundled fixtures run tests on the host when there is no sandbox |
| `NECRO_ALLOW_DOCKER_BUILD` | `0` | `1` builds the repository's Dockerfile during Consecration |
| `NECRO_STATIC_DIR` | `frontend/dist` | Built dashboard served at `/` |
| `NECRO_OTEL` | empty (off) | `console` prints spans to stderr. `otlp` needs `opentelemetry-exporter-otlp` and the standard `OTEL_EXPORTER_OTLP_*` variables |
| `NECRO_MCP_ALLOW_IMPORT` / `NECRO_MCP_ALLOW_REMOTE` | `0` / `0` | MCP server only: add the `import_repository` tool, or allow a backend that is not on this machine |
| `NECRO_LLM_MODEL` | empty (off) | LiteLLM model for AI-assisted patches. Sends function source to that provider |
| `NECRO_LLM_TIMEOUT` / `NECRO_LLM_MAX_TASKS` | `60` / `5` | Per-call timeout in seconds, and AI patch requests per run |

`/` and your home directory are refused as allowed roots. Git URLs must be `https://`, `ssh://` or
`git@host:path`.

## Develop

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/python -m pytest -q
cd frontend && npm run typecheck && npm test && npm run build
cd backend && .venv/bin/python -m tests.export_ui_fixture   # regenerate the UI contract fixture
                                                            # (needs npm, cmake, ctest on PATH)
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
backend/app/                    api, repositories, adapters (python, ts, cpp), agents, graph,
                                execution, runs, scoring, mcp_api + mcp_server
backend/tests/                  pytest suite and UI fixture exporter
frontend/src/                   components, hooks, state reducer, API client, tests
fixtures/                       legacy-python-cli, legacy-ts-utils, legacy-cpp-parser (demo repositories)
deployment/                     app Dockerfile, sandbox Dockerfile, docker-compose.yml
scripts/                        run_local.sh, reset_demo.sh, verify_sandbox.sh
```

## Terminology

Autopsy, Characterization, Reanimation and Consecration are the four stages. The **Vitality Score**
is measured repository health. The **Revival Roadmap** is the prioritised remediation plan.
**Surgical Patches** are reviewable code changes. The **Revival Terminal** shows real command output.
The **Authorization ritual** is the human approval gate. **Kiro Trace** is the workflow event
history.

## License

See [LICENSE](LICENSE).
