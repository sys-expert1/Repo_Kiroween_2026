---
inclusion: always
---
# Necromancy rules (non-negotiable)

1. Never modify the original repository. Work on a managed snapshot or copy.
2. Never auto-push.
3. Approval is mandatory before any patch is applied. Record run ID, decision, timestamp, roadmap
   version and patch-set digest.
4. Never present fabricated progress, logs, findings or scores. Only real backend evidence.
5. Unknown measurements stay unknown. Never award 100 for a missing measurement. Coverage counts
   only if a coverage tool ran. Show the effective weight when the score is partial.
6. Never rewrite tests to make a patch look successful. Separate baseline failures from
   regressions.
7. On verification failure: do not copy changes to the source, preserve evidence, roll back or
   mark the working copy failed, and audit it.
8. Retries are bounded. Cancellation is honoured between steps.
9. Keep the exact tagline and the four stage names and order.
10. Inspect, Preview and Revive stay distinct. No single ambiguous "Run" action.
