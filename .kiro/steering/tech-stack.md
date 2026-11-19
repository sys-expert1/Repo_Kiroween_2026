---
inclusion: always
---
# Technology decisions (frozen)

Backend: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy, SQLite.
Agents: LangGraph (typed state, persistent checkpointer, interrupt and resume), LiteLLM.
Code intelligence: Python AST, Tree-sitter where useful, Ruff, pip-audit.
Execution: restricted Docker sandbox, Docker Compose for packaging.
Streaming: Server-Sent Events backed by a persisted event log.
Frontend: React 19, TypeScript, Vite, Tailwind CSS, Lucide React, Diff2Html.
Testing: Pytest, Ruff, frontend type-check and tests.
Observability: OpenTelemetry correlated by `run_id` and stage.
Adapters: Python (P0), TypeScript (P1), C++ (stretch), behind one shared interface.

Python is the primary language for agents, analysis, orchestration, scoring and services. React is
the presentation layer. Do not add services or infrastructure the blueprint does not call for.
