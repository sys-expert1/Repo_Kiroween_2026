---
inclusion: always
---
# Product context

Repo Necromancer is a local-first, Python-first, agentic legacy repository analysis and
modernization platform for Kiroween 2026 (category: Agentic AI). Spec source of truth:
`.kiro/specs/repo-necromancer/`, derived from the Product Blueprint.

Stages, in this exact order: AUTOPSY, CHARACTERIZATION, REANIMATION, CONSECRATION.
Modes: INSPECT (default, read-only), PREVIEW (roadmap and diffs only), REVIVE (approved patches in
an isolated working copy).

Product truth: inspect first; approve before mutation; patch an isolated working copy; verify with
real tools; never auto-push; the original repository is never the patch target.

Scope: finish one verifiable Python flow before TypeScript (P1) or C++ (stretch). Primary demo is a
legacy Python repository. Cloud is optional future work and cannot reach local paths.

Changing the product name, tagline, stage names, primary stack, approval gate or original-repo
protection needs an explicit team decision and a spec update.
