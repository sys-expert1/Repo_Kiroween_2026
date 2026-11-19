---
inclusion: always
---
# Testing strategy

Layers: unit (validators, detection, scoring, adapters, state transitions), integration
(intake, database, checkpointing, approval resume, event persistence), security (traversal,
malicious URLs, secret leakage, sandbox limits, network restriction), E2E (intake to report),
regression (baseline vs patch-induced failures).

Must-test behaviours: approval bypass attempts, resume after process restart, SSE reconnect and
replay via `Last-Event-ID`, cancellation and timeout, bounded retries, rollback, source unchanged
after success and failure, uncommitted changes preserved, credential redaction.

Run with `cd backend && .venv/bin/python -m pytest -q` and `.venv/bin/ruff check .`.
Never claim a test passed unless it ran. Report the actual command, exit code and result. Use a
hard timeout when running tests that open streams. Sandbox tests that need Docker must say so
when Docker is unavailable and never be reported as passed.
