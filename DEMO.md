# Three-minute demo: Repo Necromancer

> Build something wicked and git commit to the darkness.
> Diagnose. Reanimate. Verify.

Rules for the recording: use the same repository, branch and stage names throughout. Show only what
the backend really produced. Never narrate a result the screen does not show.

## Before you record

```bash
scripts/reset_demo.sh      # forget old runs (demo fixtures are never touched)
scripts/run_local.sh       # http://127.0.0.1:8000
```

- The demo repository is `fixtures/legacy-python-cli`, a small Click tool with old pinned
  dependencies, a hardcoded token, an `os.popen` call with user input, an unused import, one test
  that fails before any change, and no CI or Dockerfile.
- `pip-audit` needs network access. Without it the audit is shown as "not measured", the run
  continues, and the Vitality Score uses fewer dimensions. Record with a network connection.
- Check the notice under the header. If Docker is available and the sandbox image is built, tests
  run in the sandbox. Otherwise the notice says tests run on the host with resource limits, which
  is allowed only for this bundled fixture. Say which one you are showing.

## Storyboard

| Time | Scene | On screen | What to say |
| --- | --- | --- | --- |
| 0:00 to 0:25 | Intake | Empty state, **Demo fixture** tab, click **Use** on Legacy Python CLI. Header shows mode **INSPECT** | "Legacy code is difficult to understand, risky to modify and expensive to maintain. Repo Necromancer uses Agentic AI to examine, repair and verify it." |
| 0:25 to 0:55 | Autopsy and baseline | Switch mode to **REVIVE** and press **Start Revive**. Stepper moves through Autopsy and Characterization. Open **Overview** for findings, the Revival Terminal for the real Ruff, pip-audit and pytest commands with exit codes, and the Vitality inspector (about 36) | "It examines the repository, finds the dependencies and issues, and records the existing test baseline. One test already fails, and it is kept as a baseline failure, not hidden." |
| 0:55 to 1:20 | Approval | Run pauses with the amber **Your approval is required** banner. Open **Revival Roadmap** and **Diff Inspector**, then **Review and decide** | "The roadmap is evidence-linked. Nothing changes until I approve this exact patch set." Point at the digest. Optionally exclude the Dockerfile task and submit an edit to show a new version |
| 1:20 to 2:20 | Reanimation | Press **Approve and Revive**. Stepper reaches Reanimation, then Consecration. **Kiro Trace** shows `patch_applied` events | "Approved patches are applied in an isolated working copy. Every change is reviewable, and the original repository is untouched." |
| 2:20 to 3:00 | Consecration | **Overview** shows Verified, the checks, resolved findings, and the report links. Vitality inspector shows before and after (about 36 to about 81) | "Tests, lint, dependency audit and CI checks ran against the patched copy. The old failing test is still a baseline failure, not a regression. Diagnose. Reanimate. Verify." |

Expected real results with network access: known vulnerabilities 13 to 0, Ruff violations 1 to 0,
one pre-existing test failure unchanged, `docker_build` shown as **not measured** (it is opt-in).
Your exact numbers can differ if new advisories are published; read them off the screen.

## Optional beats

- **C++:** pick **Legacy C++ Parser**. Autopsy finds the `system()` call with user input, the
  unbounded `strcpy` and `sprintf`, and the hardcoded token. Characterization really configures,
  builds and runs CTest (one pass, one pre-existing failure) and shows `Build: passed`. Dependencies
  and known vulnerabilities are **not measured**, and the demo should say so.
- **MCP:** with the dashboard running, connect an MCP client to `python -m app.mcp_server` and ask it
  to inspect the fixture. Show that it can read the findings but has no tool to approve a patch.

- **TypeScript:** pick **Legacy TypeScript Utils** instead. Autopsy runs `npm audit` (network needed)
  and finds a vulnerable `lodash`, an `eval`, a shell `exec` and a hardcoded token. The Node baseline
  shows one pass and one pre-existing failure. Revive proposes only a CI workflow and lists why the
  rest needs a person. Say plainly that code quality is **not measured** for TypeScript.

- **Rollback:** to show failure handling, make a patched run regress (for example, add a second
  failing test to a copy of the fixture under an allowed root). Consecration then fails the run,
  the copy is restored from the snapshot, and the failed copy is kept as evidence.
- **Cancel:** press **Cancel** while the run is waiting for approval. The run ends `CANCELLED` and
  no working copy is ever created.
- **Reject:** choose **Reject** in the dialog. Same result, and the decision is in the audit trail.

## Checks before publishing the video

- Every number shown was visible on screen. No terminal output was typed by hand.
- The header showed the mode the whole time, and it was never labelled with a generic "Run".
- The final `RUN_AUDIT.json` lists the approval with the same digest that was on screen.
- The original `fixtures/legacy-python-cli` is unchanged (`git status` shows no changes there).
