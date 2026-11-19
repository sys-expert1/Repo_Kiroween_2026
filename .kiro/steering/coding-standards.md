---
inclusion: always
---
# Coding standards

- Typed interfaces everywhere: Pydantic v2 for contracts and state, TypeScript on the frontend.
- Keep modules small and independently testable. No unnecessary abstractions.
- Business logic lives in services, not API handlers.
- Language adapters do not import LangGraph. The UI does not depend on agent internals.
- Follow existing conventions. Ruff is the linter and formatter (line length 100).
- Implement incrementally. Do not generate large amounts of unverified code in one step.
- Document API contracts. Preserve existing working code and interactions.
- After each phase report: files changed, features completed, commands run, actual results,
  security implications, remaining work.
