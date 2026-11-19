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
- [ ] 2.6 Align contracts with the blueprint (see design.md, Known deviations)
  - [ ] Rename SSE fields to `sequence`, `timestamp`, `payload`
  - [ ] Adopt blueprint event types (`run_started`, `stage_changed`, `hitl_required`, ...)
  - [ ] Add `edit` approval decision, roadmap version and patch-set digest to the approval record
  - [ ] Rename fixture to `legacy-python-cli` and move to top-level `fixtures/`
  - [ ] Add duplicate-event protection on the client contract
- [ ] 2.7 LangGraph state definition and frontend theme tokens

## Day 2: Python Autopsy
- [ ] 3. Python adapter and shared adapter interface
  - [ ] 3.1 Git clone into managed workspace, pin commit SHA, snapshot
  - [ ] 3.2 Framework, dependency and test discovery
  - [ ] 3.3 Ruff and pip-audit integration
  - [ ] 3.4 Finding model and AUTOPSY_REPORT.md

## Day 3: Characterization and approval
- [ ] 4. LangGraph workflow with SQLite checkpointer, cancellation, bounded retries
- [ ] 5. Characterization (baseline tests, coverage only if measured) and CHARACTERIZATION_REPORT.md
- [ ] 5.1 Roadmap generation (LiteLLM) and REVIVAL_ROADMAP.md
- [ ] 5.2 Approval interrupt, resume after approval, resume after process restart

## Day 4: Reanimation
- [ ] 6. Docker sandbox runner (non-root, CPU/memory/PID/time limits, no network, restricted mounts)
  - [ ] Verify on a machine with a working Docker daemon
- [ ] 7. Managed working copy, patch generation, apply, focused tests, rollback, PATCHSET.diff

## Day 5: Consecration and integration
- [ ] 8. Consecration checks, baseline comparison, Vitality Score, VERIFICATION_REPORT.md
- [ ] 9. React dashboard (header, rail, workspace, inspector, terminal, all UI states)
- [ ] 10. SSE hook with reconnect and replay, connect UI to the API

## Day 6: Hardening
- [ ] 11. Security tests (traversal, malicious URL, secret leakage, sandbox limits, network off,
      approval bypass, source immutability) and E2E tests
- [ ] 11.1 Docker Compose packaging

## Day 7: Demo
- [ ] 12. Deterministic fixture demo, README, pitch, three-minute video, scope freeze

## Stretch (P1)
- [ ] TypeScript adapter, advanced Kiro Trace, run history, additional scanners
- [ ] C++ adapter, optional MCP integration
