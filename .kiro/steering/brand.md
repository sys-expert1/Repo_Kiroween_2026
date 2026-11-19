---
inclusion: always
---
# Repo Necromancer brand and engineering rules

Product: Repo Necromancer. Event: Kiroween 2026.
Tagline (exact): "Build something wicked and git commit to the darkness."
Supporting tagline (exact): "Diagnose. Reanimate. Verify."
Visual direction: Haunted Cyberpunk, gothic software-forensics engineering console.

Colors: background #0B0F17, surface #111827, elevated #1F2937, border #334155,
emerald #10B981, muted emerald #064E3B, violet #8B5CF6, soft violet #A78BFA, amber #F59E0B,
crimson #EF4444, ice blue #38BDF8, text #E5E7EB, secondary text #94A3B8.
Fonts: Space Grotesk, Inter, JetBrains Mono, IBM Plex Mono.
Logo: geometric circuit-board skull, emerald eyes, restrained violet accents.

Engineering rules: typed interfaces, small modules, no business logic in API handlers,
adapters independent of LangGraph, UI independent of agent internals, real backend data only,
unknown measurements stay unknown, never modify the source repo, never auto-push, never
bypass approval.
