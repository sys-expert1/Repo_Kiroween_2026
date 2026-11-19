# Requirements: Repo Necromancer (Kiroween 2026)

Source of truth: the Repo Necromancer Complete Product Blueprint (consolidated final baseline).
Tagline (exact): "Build something wicked and git commit to the darkness."
Supporting line (exact): "Diagnose. Reanimate. Verify."

Change control: the product name, tagline, four stage names, primary technology decisions,
approval gate and original-repository protection change only by explicit team decision and an
update to the blueprint.

## R1. Product
Repo Necromancer is a local-first, Python-first, agentic legacy repository analysis and
modernization platform. It inspects a repository, identifies technical debt, establishes baseline
behaviour, generates a Revival Roadmap, pauses for human approval, applies approved patches in an
isolated working copy and verifies the result.

Target users: developers maintaining legacy applications, tech leads and architects, engineering
managers, DevOps and platform engineers, open-source maintainers.

## R2. Repository intake
1. Supported intake: Git URL (P0), local path (P0), demo fixture (P0).
2. **Git URL:** accept a public HTTPS repository (private access through a secure local credential
   flow, never embedded in the URL). Validate URL and permitted host. Clone into a managed
   workspace. Resolve and pin the inspected commit SHA. Record repository metadata and create a
   managed snapshot.
3. **Local path:** resolved by the local backend, never by the browser. Resolve symlinks, enforce
   allowed roots, reject traversal. Preserve uncommitted changes and never reset or clean the
   source. Create a managed snapshot, worktree or copy.
4. **Fixtures:** `legacy-python-cli` is the deterministic P0 demo. `legacy-ts-utils` (P1) and
   `legacy-cpp-parser` (stretch) follow. Fixtures reset only through an explicit action.
5. Git URLs must be `https://`, `ssh://` or `git@host:path`. `file://`, `ext::` and values
   starting with `-` are rejected.
6. `inspect` is read-only and persists nothing. `import` never modifies the source.

## R3. Execution modes
| Mode | Behaviour | Mutation |
| --- | --- | --- |
| INSPECT (default) | Read-only analysis, tests, findings | None |
| PREVIEW | Roadmap and proposed patches/diffs | No patch applied |
| REVIVE | Apply explicitly approved patches and verify | Isolated working copy, approval required |

The mode stays visible in the header and run details. There is no single ambiguous "Run" action.
The original repository is never the patch target. Nothing is auto-pushed.

## R4. Workflow
Stage names and order are canonical: AUTOPSY, CHARACTERIZATION, REANIMATION, CONSECRATION.

1. **Autopsy** (no source modification): validate reference and pinned commit; detect languages and
   frameworks; inventory files; discover dependency manifests and lockfiles, tests and build
   configuration; run static analysis and a configured dependency audit; produce evidence-backed
   findings (category, severity, title, evidence, file path, status).
2. **Characterization:** discover tests; run baseline tests in the sandbox; record pass/fail/skip;
   capture coverage only if a coverage tool ran; record build outcome and pre-existing failures;
   persist baseline evidence. Never rewrite tests to make patches look successful. Distinguish
   baseline failures from regressions.
3. **Revival Roadmap:** each task has ID, title, priority, category, evidence, affected files,
   proposed action, risk, estimated effort, verification criteria and status.
4. **Approval interrupt:** pause after the roadmap, persist state, wait for approve, reject or
   roadmap revision, then resume the same run.
5. **Reanimation:** create an isolated working copy from the pinned snapshot; generate patches
   linked to roadmap tasks; show diff and affected files; require approval before applying; apply
   only approved patches to the managed copy; run focused tests; keep patch and command audit.
6. **Consecration:** unit tests, integration tests when configured, lint and static checks,
   dependency security audit, Docker build if configured, CI workflow validation, baseline vs
   final comparison, final report and Vitality Score.
7. Workflow capabilities: typed state, persistent checkpointing, approval interrupt and resume,
   bounded retries (never indefinite), cancellation, error handling, rollback path, run audit.

## R5. Approval and patch review
1. Approval panel shows repository, source commit, roadmap version, task count, files affected,
   dependency changes, security fixes and execution target.
2. Decisions: **Approve and Revive**, **Edit Roadmap** (approval is required again if patch scope
   changes), **Reject** (stops mutation, source preserved).
3. The approval record stores run ID, decision, timestamp, actor or local user context where
   available, roadmap version and approved patch-set digest. Approval is mandatory before mutation.
4. Diff Inspector: changed-file navigation, side-by-side diff, file path, line numbers, patch
   summary, roadmap task reference, syntax highlighting, collapsible unchanged sections,
   copy/download patch, revert in the managed workspace. Added lines emerald, removed crimson.
5. **Rollback:** if verification fails, do not copy changes to the source. Preserve the failed
   working copy and logs, restore the managed copy to its pre-patch snapshot or mark it failed,
   report which verification failed. Rollback is auditable.

## R6. Vitality Score
`H = 0.20 D + 0.30 T + 0.20 Q + 0.15 S + 0.15 C`
(Dependencies 20%, Tests 30%, Code Quality 20%, Security 15%, CI/Docker 15%.)
1. Unknown dimensions stay unknown (`null`). Never award 100 for a missing measurement.
2. If the aggregate uses only measured dimensions, document and display the effective
   denominator and weight normalisation.
3. Do not compare scores with different measurement sets without a warning.
4. Coverage counts only when a coverage tool actually ran.

## R7. Reports and artifacts
Report contents: repository identity and pinned commit, run ID and timestamps, mode, baseline
results, findings and evidence, approved roadmap and patch inventory, files and dependencies
changed, verification commands and exit codes, passed/failed/skipped/not configured/not measured
outcomes, Vitality Score breakdown, known limitations, recommended next actions.

Artifacts: `REVIVAL_ROADMAP.md`, `AUTOPSY_REPORT.md`, `CHARACTERIZATION_REPORT.md`,
`PATCHSET.diff`, `VERIFICATION_REPORT.md`, `VITALITY_REPORT.json`, `RUN_AUDIT.json`. Stored in a
managed directory scoped to the local application, with checksums and run association.

## R8. Safety
| Risk | Required mitigation |
| --- | --- |
| Untrusted code execution | Restricted sandbox only |
| Privilege escalation | Non-root container user |
| Resource exhaustion | CPU, memory, process and time limits |
| Filesystem access | Explicit restricted mounts |
| Exfiltration | Network disabled by default |
| Host compromise | Never mount the home directory or Docker socket |
| Accidental source modification | Managed copy, original unchanged |
| Unauthorized patching | Human approval interrupt |
| Credential exposure | Secure credential flow, no secrets in URLs or logs |
| Path traversal | Resolve paths and symlinks, enforce allowed roots |
| Infinite execution | Timeout, cancellation, bounded retries |
| Failed patches | Preserve evidence, roll back the managed copy |

Docker is a practical MVP boundary, not a guarantee against every container escape. Do not
overstate sandbox security. Cloud deployment is optional future work and cannot reach local paths.

## R9. API contract
```
POST /api/repositories/inspect        GET  /api/repositories/{id}
POST /api/repositories/import         POST /api/revive/start
GET  /api/repositories                GET  /api/revive/stream/{run_id}
GET  /api/fixtures                    POST /api/revive/approve/{run_id}
                                      POST /api/revive/{run_id}/cancel
                                      GET  /api/revive/{run_id}/artifacts
```

## R10. SSE
1. Envelope: `event_id`, `run_id`, `sequence`, `type`, `timestamp`, `payload`.
2. Types: `run_started`, `stage_changed`, `health_score_updated`, `roadmap_ready`, `hitl_required`,
   `approval_received`, `diff_generated`, `terminal_log`, `verification_completed`,
   `run_completed`, `run_failed`.
3. Requirements: heartbeats, monotonically increasing per-run sequence numbers, `Last-Event-ID`
   reconnect, replay from a persisted log, duplicate-event protection, concurrent runs,
   cancellation, terminal event and stream closure. No in-memory-only queues.
4. Documented extensions to the type list: `run_cancelled` (terminal event for a cancelled run) and
   `cancel_requested` (acknowledges a cancel request on a running run).

## R11. Dashboard and UI
1. Five regions: top header (logo, repository, branch and commit, mode, run state, settings), left
   four-stage rail, central workspace (Overview, Revival Roadmap, Diff Inspector, Kiro Trace),
   right Vitality inspector, bottom Revival Terminal showing real commands, output and exit codes.
2. Visual system: Haunted Cyberpunk (see `steering/brand.md`). No gore, cartoon horror, horror
   fonts, WebGL or heavy 3D.
3. Colour meaning: emerald means verified only with real evidence; violet means AI activity, not
   success; amber means approval or warning; crimson means failure; ice blue means information.
   Status is never colour alone, and WCAG AA is the minimum.
4. Responsive: desktop three-column; laptop collapses the inspector; tablet stacks; mobile
   prioritises status, approvals and findings.
5. States to design: no repository, invalid URL or path, importing, inspecting, no findings,
   findings, roadmap ready, approval required, approved or rejected, patch generation,
   verification running, completed, failed, cancelled, Docker unavailable, unsupported language,
   SSE disconnected or reconnecting, empty artifacts.
6. Keyboard focus and reduced motion supported. UI shows real backend data only.

## R12. Quality and observability
Pytest and Ruff for the backend; TypeScript type-check and frontend tests. Test layers: unit,
integration, security, E2E, regression. Required tests include approval resume after restart,
SSE reconnect and replay, cancellation and timeout, and source unchanged after success and
failure. Correlate logs with `run_id` and stage, redact credentials, keep audit events for
approvals and patches. Record the actual command, exit code and output for every verification.

## R13. Acceptance
**Functional:** Git import, local path registration, fixture selection, Python structure
detection, dependency and test discovery, baseline recorded, evidence-backed findings, roadmap,
pause for approval, approve/edit/reject, reviewable patches, patches applied only to an isolated
copy, persisted verification, final report and measured Vitality Score.

**Safety:** original unchanged; untrusted code never runs on the host; sandbox is non-root and
limited; network off by default; no home or Docker socket mounts; credentials not exposed;
traversal rejected; approval before mutation; cancellation and rollback exist.

**Demo:** the deterministic fixture works end to end, at least one meaningful patch, approval is
visible, real verification results, no fabricated progress or metrics, final report opens.

**MVP complete** when a user can select a legacy Python repository, run Autopsy, record a
baseline, receive a roadmap, approve it, inspect a patch, apply it in an isolated copy, run
Consecration, and review the final report and Vitality Score.

## R14. Scope
| P0 | P1 | Deferred |
| --- | --- | --- |
| Python end to end, Git/local/fixture intake, Autopsy and baseline, roadmap and approval, isolated patching, diff, terminal, verification, report | TypeScript adapter, advanced Kiro Trace, more scanners, C++ basic adapter, optional MCP, run history | Multi-user SaaS, RBAC and SSO, distributed execution, auto production deploy, auto GitHub PRs |

Complete one verifiable Python flow before adding other languages.
