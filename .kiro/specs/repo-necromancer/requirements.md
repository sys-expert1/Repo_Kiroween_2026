# Requirements: Repo Necromancer (Kiroween 2026)

Tagline: "Build something wicked and git commit to the darkness."
Supporting tagline: "Diagnose. Reanimate. Verify."

Repo Necromancer is a local-first web application that analyses a legacy repository,
builds a Revival Roadmap, requires explicit human approval, applies approved patches in an
isolated working copy and verifies the result.

## R1. Repository intake
1. The user can supply a Git URL, a local path, or choose a demo fixture.
2. The backend resolves and validates local paths. The browser never accesses the filesystem.
3. Local paths containing `..` segments, symlink escapes, or paths outside the configured
   allowed roots are rejected.
4. Git URLs must use `https://` or `ssh://`/`git@host:` form. `file://`, `ext::` and values
   beginning with `-` are rejected.
5. `POST /api/repositories/inspect` is read-only and does not persist anything.
6. `POST /api/repositories/import` registers a repository. It never modifies the source.

## R2. Workflow
Canonical phases: AUTOPSY, CHARACTERIZATION, REANIMATION, CONSECRATION.
1. Autopsy detects languages, frameworks, dependencies, tests, configuration, security findings, debt.
2. Characterization runs baseline tests in the sandbox, records existing failures and coverage.
3. Reanimation generates a Revival Roadmap, waits for approval, generates patches, applies
   approved patches in an isolated working copy.
4. Consecration runs tests, lint, dependency audit, Docker build and configured CI checks.

## R3. Execution modes
- INSPECT: read-only.
- PREVIEW: roadmap and diffs, no patches applied.
- REVIVE: approved patches applied in an isolated working copy.
The original source repository is never modified. Nothing is auto-pushed. Approval cannot be bypassed.

## R4. Safety
1. Untrusted repository code runs only in a restricted Docker sandbox.
2. Non-root user, CPU/memory/time limits, network disabled by default.
3. No mount of the user's home directory or the Docker socket.
4. Uncommitted source changes are preserved.
5. Approval decisions and patch history are recorded.
6. Rollback and cancellation are supported.

## R5. API contract
```
POST /api/repositories/inspect
POST /api/repositories/import
GET  /api/repositories
GET  /api/repositories/{id}
POST /api/revive/start
GET  /api/revive/stream/{run_id}
POST /api/revive/approve/{run_id}
POST /api/revive/{run_id}/cancel
GET  /api/revive/{run_id}/artifacts
GET  /api/fixtures
```
SSE events carry an event ID, run ID, sequence number and timestamp. Reconnect with
`Last-Event-ID` replays missed events.

## R6. Vitality Score
Weights: Dependencies 20%, Tests 30%, Code Quality 20%, Security 15%, CI/Docker 15%.
A component that was not measured is `null` (unknown). Unknown components are excluded and
reported; no value is fabricated. The overall score is `null` if nothing was measured.

## R7. Dashboard
Header, repository selector, execution mode selector, left workflow stepper, central workspace
(Overview, Revival Roadmap, Diff Inspector, Kiro Trace), right Vitality Score inspector,
bottom Revival Terminal. Visual system: Haunted Cyberpunk (see steering/brand.md).

## R8. MVP acceptance
A user can select a legacy Python repository, run Autopsy, establish a test baseline, receive a
Revival Roadmap, approve it, inspect patches, apply them in an isolated copy, run Consecration,
review verification results, and view the final Vitality Score and report.
