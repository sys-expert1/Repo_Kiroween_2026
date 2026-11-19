import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DiffInspector } from "../components/DiffInspector";
import { HealthMeter } from "../components/HealthMeter";
import { Overview } from "../components/Overview";
import { RoadmapView } from "../components/RoadmapView";
import { terminalLines } from "../components/TerminalStream";
import { replay, stageStates } from "../state/runState";
import type {
  Artifact, Autopsy, Baseline, PatchSet, Roadmap, Run, RunEvent, Verification, Vitality,
} from "../types/revival";
import real from "./real-run.json";

/**
 * These tests replay a real run exported from the backend (see backend/tests/export_ui_fixture.py).
 * They fail if the backend's event or artifact shapes drift away from what the UI reads.
 */
type Export = typeof real.verified | typeof real.rolled_back;
const load = (e: Export) => ({
  events: e.events as unknown as RunEvent[],
  run: e.final_run as unknown as Run,
  data: e.data as unknown as {
    autopsy_data: Autopsy; characterization_data: Baseline; roadmap_data: Roadmap;
    patch_set_data: PatchSet; verification_data: Verification;
    vitality_report: { before: Vitality; after: Vitality; comparison_warning: string | null };
  },
  artifacts: e.artifacts as unknown as Artifact[],
});

describe("a verified run from the real backend", () => {
  const { events, run, data, artifacts } = load(real.verified);
  const view = replay(run.id, events);

  it("replays to the same state the backend reports", () => {
    expect(view.events).toHaveLength(events.length); // no event dropped as a duplicate
    expect(view.lastSequence).toBe(events.at(-1)!.sequence);
    expect(view.status).toBe(run.status);
    expect(view.status).toBe("COMPLETED");
    expect(view.verdict).toBe("verified");
    expect(view.terminal).toBe(true);
    expect(view.approval).toBeNull(); // the approval was consumed
  });

  it("derives every stage as completed because every stage really ran", () => {
    expect(stageStates(view, run)).toEqual({
      AUTOPSY: "completed", CHARACTERIZATION: "completed", REANIMATION: "completed", CONSECRATION: "completed",
    });
  });

  it("reads the same before and after scores as the backend report", () => {
    expect(view.scoreBefore?.score).toBe(data.vitality_report.before.score);
    expect(view.scoreAfter?.score).toBe(data.vitality_report.after.score);
    expect(view.scoreAfter!.score!).toBeGreaterThan(view.scoreBefore!.score!);
  });

  it("turns terminal_log events into lines with their real exit codes", () => {
    const lines = terminalLines(events);
    expect(lines.length).toBeGreaterThan(3);
    expect(lines.every((l) => l.command.length > 0 && l.exit !== undefined)).toBe(true);
    expect(lines.some((l) => l.command.includes("pytest") && l.exit === 1)).toBe(true);
  });

  it("renders the real roadmap, patches, vitality and verification without errors", () => {
    const patched = new Set(data.patch_set_data.patches.map((p) => p.task_id));
    const skipped = new Map(data.patch_set_data.skipped.map((s) => [s.task_id, s.reason]));
    const { container } = render(
      <>
        <RoadmapView roadmap={data.roadmap_data} patchedTaskIds={patched} skipped={skipped} editing={null} />
        <DiffInspector patches={data.patch_set_data} />
        <HealthMeter before={view.scoreBefore} after={view.scoreAfter} autopsy={data.autopsy_data} warning={data.vitality_report.comparison_warning} />
        <Overview run={run} autopsy={data.autopsy_data} baseline={data.characterization_data} verification={data.verification_data} artifacts={artifacts} hasRepository />
      </>,
    );
    expect(screen.getByText(/Revival Roadmap v1/)).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText("Pre-existing failures (not regressions):")).toBeInTheDocument();
    expect(container.querySelector(".d2h-wrapper")).not.toBeNull(); // the real diff parsed and rendered
    expect(screen.getByRole("navigation", { name: "Changed files" })).toHaveTextContent("requirements.txt");
    expect(screen.getByText("VERIFICATION_REPORT.md")).toBeInTheDocument();
  });

  it("matches the types the UI declares for each artifact", () => {
    const v = data.verification_data;
    expect(["verified", "failed"]).toContain(v.verdict);
    expect(v.checks.every((c) => typeof c.name === "string" && typeof c.detail === "string")).toBe(true);
    for (const d of data.vitality_report.after.dimensions) {
      expect(typeof d.weight).toBe("number");
      expect(d.score === null || typeof d.score === "number").toBe(true);
    }
    for (const t of data.roadmap_data.tasks) expect(t.evidence.length).toBeGreaterThan(0);
    for (const p of data.patch_set_data.patches) expect(p.diff).toContain("+++ b/");
  });
});

describe("a rolled-back run from the real backend", () => {
  const { events, run, data, artifacts } = load(real.rolled_back);
  const view = replay(run.id, events);

  it("ends failed with the failure on the Consecration stage and a failed verdict", () => {
    expect(view.status).toBe("FAILED");
    expect(view.verdict).toBe("failed");
    expect(view.terminal).toBe(true);
    expect(stageStates(view, run).CONSECRATION).toBe("failed");
    expect(events.map((e) => e.type)).toContain("rollback_completed");
  });

  it("shows the regression and the reason instead of hiding the failure", () => {
    render(
      <Overview run={run} autopsy={data.autopsy_data} baseline={data.characterization_data} verification={data.verification_data} artifacts={artifacts} hasRepository />,
    );
    expect(screen.getByText("This run failed")).toBeInTheDocument();
    expect(screen.getAllByText(/New failing test: t::new/).length).toBeGreaterThan(0);
    expect(screen.getByText("Failed", { selector: "span" })).toBeInTheDocument();
  });
});

describe("a run waiting for approval from the real backend", () => {
  it("exposes the fields the approval dialog needs", () => {
    const waiting = real.verified.waiting_run as unknown as Run;
    const events = real.verified.events as unknown as RunEvent[];
    const hitl = events.find((e) => e.type === "hitl_required")!;
    expect(waiting.status).toBe("AWAITING_APPROVAL");
    expect(hitl.payload.roadmap_version).toBe(waiting.roadmap_version);
    expect(hitl.payload.patch_set_digest).toBe(waiting.patch_set_digest);
    for (const key of ["repository", "task_count", "files_affected", "execution_target", "dependency_changes", "security_fixes"])
      expect(hitl.payload).toHaveProperty(key);
    const midway = replay(waiting.id, events.slice(0, events.indexOf(hitl) + 1));
    expect(midway.status).toBe("AWAITING_APPROVAL");
    expect(midway.approval).not.toBeNull();
    expect(stageStates(midway, waiting).REANIMATION).toBe("awaiting_approval");
    expect(stageStates(midway, waiting).CONSECRATION).toBe("pending");
  });
});
