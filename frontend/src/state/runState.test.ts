import { describe, expect, it } from "vitest";

import { ev } from "../test/fakes";
import { applyEvent, emptyView, replay, stageStates } from "./runState";

const stage = (n: number, s: string, status = "RUNNING") => ev(n, "stage_changed", { stage: s, status });

describe("applyEvent", () => {
  it("ignores duplicate and out-of-order sequences", () => {
    const events = [ev(1, "run_started"), stage(2, "AUTOPSY"), stage(3, "CHARACTERIZATION")];
    let view = replay("run-1", events);
    const before = view;
    view = applyEvent(view, stage(2, "AUTOPSY")); // replayed duplicate
    view = applyEvent(view, ev(1, "run_started"));
    expect(view).toBe(before);
    expect(view.events.map((e) => e.sequence)).toEqual([1, 2, 3]);
    expect(view.lastSequence).toBe(3);
  });

  it("ignores events that belong to another run", () => {
    const view = applyEvent(replay("run-1", [ev(1, "run_started")]), ev(2, "run_completed", {}, "run-2"));
    expect(view.status).toBe("QUEUED");
  });

  it("tracks the pending approval and clears it once a decision is recorded", () => {
    let view = replay("run-1", [stage(1, "REANIMATION"), ev(2, "hitl_required", { stage: "REANIMATION", status: "AWAITING_APPROVAL", roadmap_version: 1 })]);
    expect(view.status).toBe("AWAITING_APPROVAL");
    expect(view.approval).toMatchObject({ roadmap_version: 1 });
    view = applyEvent(view, ev(3, "approval_received", { decision: "approve" }));
    expect(view.approval).toBeNull();
  });

  it("reads before and after scores and the verdict from backend events", () => {
    const score = { score: 36.3, dimensions: [], measured_weight: 1, measured: [], unmeasured: [], note: "" };
    const view = replay("run-1", [
      ev(1, "health_score_updated", { phase: "before", ...score }),
      ev(2, "health_score_updated", { phase: "after", ...score, score: 81.2 }),
      ev(3, "verification_completed", { verdict: "verified" }),
      ev(4, "run_completed", { stage: "CONSECRATION", status: "COMPLETED" }),
    ]);
    expect(view.scoreBefore?.score).toBe(36.3);
    expect(view.scoreAfter?.score).toBe(81.2);
    expect(view.verdict).toBe("verified");
    expect(view.terminal).toBe(true);
  });

  it("marks cancelled and failed runs as terminal", () => {
    expect(replay("r", [ev(1, "run_cancelled", { status: "CANCELLED" }, "r")]).terminal).toBe(true);
    const failed = replay("r", [ev(1, "run_failed", { status: "FAILED", error: "boom" }, "r")]);
    expect(failed.terminal && failed.error).toBe("boom");
  });
});

describe("stageStates", () => {
  it("shows stages that never started in this mode as not run, not completed", () => {
    const view = replay("run-1", [
      stage(1, "AUTOPSY"), stage(2, "CHARACTERIZATION"),
      ev(3, "run_completed", { stage: "CHARACTERIZATION", status: "COMPLETED" }),
    ]);
    expect(stageStates(view, null)).toEqual({
      AUTOPSY: "completed", CHARACTERIZATION: "completed", REANIMATION: "not_run", CONSECRATION: "not_run",
    });
  });

  it("shows pending stages while a run is still going", () => {
    const states = stageStates(replay("run-1", [stage(1, "AUTOPSY")]), null);
    expect(states).toMatchObject({ AUTOPSY: "running", CHARACTERIZATION: "pending", CONSECRATION: "pending" });
  });

  it("shows the approval gate and failures on the right stage", () => {
    const waiting = replay("run-1", [stage(1, "AUTOPSY"), stage(2, "REANIMATION"), ev(3, "hitl_required", { stage: "REANIMATION", status: "AWAITING_APPROVAL" })]);
    expect(stageStates(waiting, null).REANIMATION).toBe("awaiting_approval");
    const failed = replay("run-1", [stage(1, "AUTOPSY"), ev(2, "run_failed", { stage: "AUTOPSY", status: "FAILED" })]);
    expect(stageStates(failed, null).AUTOPSY).toBe("failed");
  });

  it("is empty-safe", () => {
    expect(Object.values(stageStates(emptyView(), null))).toEqual(["pending", "pending", "pending", "pending"]);
  });
});
