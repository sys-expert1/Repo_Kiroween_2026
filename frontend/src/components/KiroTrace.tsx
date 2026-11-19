import {
  Activity, BadgeCheck, CheckCircle2, CircleSlash, CircleX, FileDiff, HeartPulse, ListChecks,
  Play, RotateCcw, ShieldCheck, Terminal, type LucideIcon,
} from "lucide-react";
import { useState } from "react";

import type { EventType, RunEvent } from "../types/revival";
import { EmptyState, TONE_TEXT, type Tone } from "./ui";

const META: Record<EventType, { label: string; icon: LucideIcon; tone: Tone }> = {
  run_started: { label: "Run started", icon: Play, tone: "info" },
  stage_changed: { label: "Stage", icon: Activity, tone: "agent" },
  health_score_updated: { label: "Vitality Score", icon: HeartPulse, tone: "info" },
  roadmap_ready: { label: "Roadmap ready", icon: ListChecks, tone: "agent" },
  hitl_required: { label: "Approval required", icon: BadgeCheck, tone: "warn" },
  approval_received: { label: "Decision recorded", icon: BadgeCheck, tone: "warn" },
  diff_generated: { label: "Patches proposed", icon: FileDiff, tone: "agent" },
  terminal_log: { label: "Command", icon: Terminal, tone: "muted" },
  verification_completed: { label: "Verification", icon: ShieldCheck, tone: "info" },
  run_completed: { label: "Run completed", icon: CheckCircle2, tone: "ok" },
  run_failed: { label: "Run failed", icon: CircleX, tone: "bad" },
  run_cancelled: { label: "Run cancelled", icon: CircleSlash, tone: "muted" },
  cancel_requested: { label: "Cancel requested", icon: CircleSlash, tone: "warn" },
  patch_applied: { label: "Patch applied", icon: FileDiff, tone: "agent" },
  rollback_completed: { label: "Rolled back", icon: RotateCcw, tone: "warn" },
};

export function summarize(e: RunEvent): string {
  const p = e.payload;
  switch (e.type) {
    case "stage_changed": return `${p.stage} ${String(p.status ?? "").toLowerCase()}`;
    case "terminal_log": return `${String(p.command ?? "").split("/").pop()} (exit ${p.exit_code ?? "none"})`;
    case "roadmap_ready": return `v${p.roadmap_version}, ${p.task_count} tasks`;
    case "diff_generated": return `${(p.patches as unknown[] | undefined)?.length ?? 0} patches in ${(p.files_changed as unknown[] | undefined)?.length ?? 0} files`;
    case "approval_received": return `${p.decision} (roadmap v${p.roadmap_version})`;
    case "patch_applied": return `${p.task_id}: ${p.path}`;
    case "verification_completed": return `verdict: ${p.verdict}`;
    case "health_score_updated": return `${p.phase}: ${p.score === null ? "not measured" : Number(p.score).toFixed(1)}`;
    case "run_failed": return String(p.error ?? "");
    case "rollback_completed": return String(p.reason ?? "");
    default: return "";
  }
}

export function KiroTrace({ events }: { events: RunEvent[] }) {
  const [hideCommands, setHide] = useState(false);
  const shown = hideCommands ? events.filter((e) => e.type !== "terminal_log") : events;
  if (events.length === 0)
    return <EmptyState icon={Activity} title="No trace yet">Start a run to see its event timeline here.</EmptyState>;
  return (
    <div>
      <label className="mb-3 flex items-center gap-2 text-xs text-ink-2">
        <input type="checkbox" checked={hideCommands} onChange={(e) => setHide(e.target.checked)} />
        Hide command events
      </label>
      <ol className="space-y-1" aria-label="Run event timeline">
        {shown.map((e) => {
          const m = META[e.type];
          return (
            <li key={e.event_id} className="flex items-start gap-3 rounded-md border border-transparent px-2 py-1.5 hover:border-edge hover:bg-raised">
              <span className="w-8 shrink-0 text-right font-metric text-xs text-ink-3">#{e.sequence}</span>
              <m.icon size={15} className={`mt-0.5 shrink-0 ${TONE_TEXT[m.tone]}`} aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm">
                  <span className={`font-medium ${TONE_TEXT[m.tone]}`}>{m.label}</span>{" "}
                  <span className="break-words text-ink-2">{summarize(e)}</span>
                </p>
              </div>
              <time className="shrink-0 font-metric text-xs text-ink-3" dateTime={e.timestamp}>
                {new Date(e.timestamp).toLocaleTimeString([], { hour12: false })}
              </time>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
