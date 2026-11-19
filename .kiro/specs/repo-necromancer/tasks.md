# Tasks: Repo Necromancer

Order follows the blueprint's implementation order and seven-day plan. Complete one verifiable
Python flow before any other language.

## Day 1: Architecture and foundation
- [x] 1. Repository setup, specs, steering, layout
- [x] 2. FastAPI and Pydantic contracts
  - [x] 2.1 Config, DB, ORM, schemas
  - [x] 2.2 Local path and Git URL validation
  - [x] 2.3 Read-only repository detection
  - [x] 2.4 Repositories and fixtures endpoints
  - [x] 2.5 Revive endpoints (start, stream, approve, cancel, artifacts)
- [x] 2.6 Align contracts with the blueprint
  - [x] SSE fields `sequence`, `timestamp`, `payload`; blueprint event types
  - [x] `edit` approval decision, roadmap version and patch-set digest in the approval record
  - [x] Fixture `legacy-python-cli` (Click) in top-level `fixtures/`
  - [x] Unique ordered event sequence (client de-duplicates by `sequence`)
- [x] 2.7 LangGraph state definition and frontend theme tokens

## Day 2: Python Autopsy
- [x] 3. Python adapter and shared adapter interface
  - [x] 3.1 Git clone into managed workspace, pin commit SHA, snapshot
  - [x] 3.2 Framework, dependency and test discovery
  - [x] 3.3 Ruff and pip-audit integration (unmeasured results stay unknown)
  - [x] 3.4 Finding model and AUTOPSY_REPORT.md

## Day 3: Characterization and approval
- [x] 4. LangGraph workflow with SQLite checkpointer, cancellation, bounded retries
- [x] 5. Characterization (baseline tests, coverage only if measured) and CHARACTERIZATION_REPORT.md
  - [x] Baseline runs for bundled fixtures; other sources report "not run" until task 6
- [x] 5.1 Roadmap generation and REVIVAL_ROADMAP.md (deterministic; LiteLLM not used yet)
- [x] 5.2 Approval interrupt, resume after approval, resume after process restart

## Day 4: Reanimation
- [x] 6. Docker sandbox runner (non-root, CPU/memory/PID/time limits, no network, one restricted mount)
  - [x] Command construction, mount validation, status check, timeout kill: unit tested
  - [ ] **Run against a real Docker daemon.** Not done: none was available in the build environment.
        The real-container test exists and is skipped when Docker is missing.
- [x] 7. Managed working copy, patch generation, digest-bound apply, rollback, PATCHSET.diff
  - [x] Patches for dependency pins, hardcoded secrets, os.popen, Ruff safe fixes, CI, Dockerfile
  - [x] LiteLLM-assisted patch generation: opt-in, validated, labelled. Tested with a fake client;
        not run against a real model

## Day 5: Consecration and integration
- [x] 8. Consecration checks, baseline comparison, Vitality Score, VERIFICATION_REPORT.md, RUN_AUDIT.json
- [x] 9. React dashboard (header, rail, workspace, inspector, terminal, all four views and states)
- [x] 10. SSE hook with reconnect, replay and de-duplication; UI connected to the API

## Day 6: Hardening
- [x] 11. Security tests (traversal, malicious URL, secret redaction, mount rules, approval bypass,
      source immutability, rollback) and workflow tests. Frontend contract test on a real exported run
  - [ ] Browser E2E against a live server (not possible in the build environment)
- [x] 11.1 Docker Compose packaging (no Docker socket mounted)
  - [ ] Build the image with Docker (not possible in the build environment)

## Day 7: Demo
- [x] 12. DEMO.md storyboard, README, scripts, deterministic fixture
  - [ ] Record the three-minute video

## Stretch (P1)
- [ ] TypeScript adapter, run history view, additional scanners, OpenTelemetry
- [ ] C++ adapter, optional MCP integration
