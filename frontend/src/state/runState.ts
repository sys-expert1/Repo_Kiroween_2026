import type { EventType, Run, RunEvent, RunStatus, Stage, Vitality } from "../types/revival";
import { STAGES, TERMINAL } from "../types/revival";

/** State derived only from backend events. No value here is invented by the UI. */
export interface RunView {
  runId: string | null;
  events: RunEvent[]; // ordered by sequence, no duplicates
  lastSequence: number;
  status: RunStatus | null;
  stage: Stage | null;
  stagesEntered: Stage[];
  scoreBefore: Vitality | null;
  scoreAfter: Vitality | null;
  approval: Record<string, unknown> | null; // payload of the pending hitl_required event
  verdict: "verified" | "failed" | null;
  error: string | null;
  terminal: boolean;
}

export const emptyView = (runId: string | null = null): RunView => ({
  runId, events: [], lastSequence: 0, status: null, stage: null, stagesEntered: [],
  scoreBefore: null, scoreAfter: null, approval: null, verdict: null, error: null, terminal: false,
});

const statusFor: Partial<Record<EventType, RunStatus>> = {
  run_completed: "COMPLETED", run_failed: "FAILED", run_cancelled: "CANCELLED",
};

/** Apply one event. Events already seen (sequence <= last) are ignored, so replay is safe. */
export function applyEvent(view: RunView, event: RunEvent): RunView {
  if (view.runId !== null && event.run_id !== view.runId) return view;
  if (event.sequence <= view.lastSequence) return view;
  const next: RunView = {
    ...view,
    runId: event.run_id,
    events: [...view.events, event],
    lastSequence: event.sequence,
  };
  const p = event.payload;
  const stage = typeof p.stage === "string" && (STAGES as readonly string[]).includes(p.stage)
    ? (p.stage as Stage) : null;
  if (stage) {
    next.stage = stage;
    if (!next.stagesEntered.includes(stage)) next.stagesEntered = [...next.stagesEntered, stage];
  }
  const explicit = statusFor[event.type];
  if (explicit) next.status = explicit;
  else if (typeof p.status === "string") next.status = p.status as RunStatus;
  else if (event.type === "run_started") next.status = "QUEUED";

  switch (event.type) {
    case "hitl_required": next.approval = p; break;
    case "approval_received": next.approval = null; break;
    case "health_score_updated": {
      const { phase, ...score } = p as unknown as { phase: string } & Vitality;
      if (phase === "before") next.scoreBefore = score as Vitality;
      if (phase === "after") next.scoreAfter = score as Vitality;
      break;
    }
    case "verification_completed": next.verdict = p.verdict as "verified" | "failed"; break;
    case "run_failed": next.error = typeof p.error === "string" ? p.error : null; break;
    default: break;
  }
  next.terminal = next.status !== null && TERMINAL.includes(next.status);
  return next;
}

export function replay(runId: string, events: RunEvent[]): RunView {
  return events.reduce(applyEvent, emptyView(runId));
}

export type StageState =
  | "pending" | "running" | "awaiting_approval" | "completed" | "failed" | "cancelled" | "not_run";

/** Honest per-stage state: a stage that never started in this mode is "not run", not "done". */
export function stageStates(view: RunView, run: Run | null): Record<Stage, StageState> {
  const out = {} as Record<Stage, StageState>;
  const status = view.status ?? run?.status ?? null;
  const current = view.stage ?? run?.current_stage ?? null;
  for (const s of STAGES) {
    const entered = view.stagesEntered.includes(s) || s === current;
    if (!entered) {
      out[s] = status && TERMINAL.includes(status) ? "not_run" : "pending";
    } else if (s !== current) {
      out[s] = "completed";
    } else if (status === "AWAITING_APPROVAL") out[s] = "awaiting_approval";
    else if (status === "FAILED") out[s] = "failed";
    else if (status === "CANCELLED") out[s] = "cancelled";
    else if (status === "COMPLETED") out[s] = "completed";
    else out[s] = "running";
  }
  return out;
}

export const RUN_STATE_LABEL: Record<RunStatus, string> = {
  QUEUED: "Queued", RUNNING: "Running", AWAITING_APPROVAL: "Approval required",
  COMPLETED: "Completed", FAILED: "Failed", CANCELLED: "Cancelled",
};
