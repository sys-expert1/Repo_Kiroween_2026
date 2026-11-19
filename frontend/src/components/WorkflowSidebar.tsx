import { Activity, BadgeCheck, CircleDashed, CircleSlash, CircleX, Microscope, ScanSearch, ShieldCheck, Check, type LucideIcon } from "lucide-react";

import type { StageState } from "../state/runState";
import { STAGES, type Stage } from "../types/revival";
import { TONE_TEXT, type Tone } from "./ui";

const META: Record<Stage, { label: string; icon: LucideIcon; blurb: string }> = {
  AUTOPSY: { label: "Autopsy", icon: ScanSearch, blurb: "Read-only inspection" },
  CHARACTERIZATION: { label: "Characterization", icon: Microscope, blurb: "Baseline behaviour" },
  REANIMATION: { label: "Reanimation", icon: Activity, blurb: "Roadmap, approval, patches" },
  CONSECRATION: { label: "Consecration", icon: ShieldCheck, blurb: "Verify with real tools" },
};
const STATE: Record<StageState, { tone: Tone; text: string; icon: LucideIcon }> = {
  pending: { tone: "muted", text: "Pending", icon: CircleDashed },
  running: { tone: "agent", text: "Running", icon: Activity },
  awaiting_approval: { tone: "warn", text: "Approval required", icon: BadgeCheck },
  completed: { tone: "ok", text: "Completed", icon: Check },
  failed: { tone: "bad", text: "Failed", icon: CircleX },
  cancelled: { tone: "muted", text: "Cancelled", icon: CircleSlash },
  not_run: { tone: "muted", text: "Not run in this mode", icon: CircleDashed },
};

export function WorkflowSidebar({ states }: { states: Record<Stage, StageState> }) {
  return (
    <nav aria-label="Workflow" className="border-edge bg-panel p-4 lg:border-r">
      <h2 className="mb-3 font-heading text-xs font-semibold uppercase tracking-widest text-ink-3">Workflow</h2>
      <ol className="flex gap-3 overflow-x-auto lg:flex-col lg:gap-0">
        {STAGES.map((stage, i) => {
          const m = META[stage];
          const s = STATE[states[stage]];
          const StateIcon = s.icon;
          return (
            <li key={stage} className="relative flex min-w-44 gap-3 lg:min-w-0 lg:pb-6 lg:last:pb-0">
              {i < STAGES.length - 1 && (
                <span className="absolute left-[15px] top-8 hidden h-[calc(100%-2rem)] w-px bg-edge lg:block" aria-hidden="true" />
              )}
              <span className={`z-10 flex size-8 shrink-0 items-center justify-center rounded-full border bg-obsidian ${TONE_TEXT[s.tone]} ${states[stage] === "running" ? "border-haunt motion-safe:animate-pulse" : "border-edge"}`}>
                <m.icon size={15} aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <p className="font-heading text-sm font-semibold text-ink">{i + 1}. {m.label}</p>
                <p className="text-xs text-ink-3">{m.blurb}</p>
                <p className={`mt-0.5 flex items-center gap-1 text-xs font-medium ${TONE_TEXT[s.tone]}`}>
                  <StateIcon size={12} aria-hidden="true" /> {s.text}
                </p>
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
