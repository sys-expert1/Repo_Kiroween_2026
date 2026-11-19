import { ListChecks } from "lucide-react";

import type { Roadmap, RoadmapTask } from "../types/revival";
import { Badge, EmptyState, type Tone } from "./ui";

const PRIORITY_TONE: Record<RoadmapTask["priority"], Tone> = { P0: "bad", P1: "bad", P2: "warn", P3: "info" };

interface Props {
  roadmap: Roadmap | null;
  patchedTaskIds: Set<string>;
  skipped: Map<string, string>;
  /** When set, tasks can be excluded. Only used while an approval is pending. */
  editing: { excluded: Set<string>; toggle: (id: string) => void } | null;
}

export function RoadmapView({ roadmap, patchedTaskIds, skipped, editing }: Props) {
  if (!roadmap)
    return (
      <EmptyState icon={ListChecks} title="No Revival Roadmap yet">
        The roadmap is generated after Autopsy and Characterization, in Preview and Revive modes.
      </EmptyState>
    );
  return (
    <div>
      <p className="mb-3 text-sm text-ink-2">
        Revival Roadmap v{roadmap.version}: {roadmap.tasks.filter((t) => t.status === "proposed").length} proposed
        task(s). Every task cites the findings it comes from. Nothing has been changed by the roadmap itself.
      </p>
      <ul className="space-y-3">
        {roadmap.tasks.map((t) => {
          const excluded = t.status === "excluded" || editing?.excluded.has(t.id);
          const why = skipped.get(t.id);
          return (
            <li key={t.id} className={`rounded-lg border border-edge bg-panel p-4 ${excluded ? "opacity-60" : ""}`}>
              <div className="flex flex-wrap items-center gap-2">
                {editing && t.status === "proposed" && (
                  <input
                    type="checkbox" aria-label={`Exclude ${t.id} ${t.title}`} checked={editing.excluded.has(t.id)}
                    onChange={() => editing.toggle(t.id)}
                  />
                )}
                <span className="font-metric text-xs text-ink-3">{t.id}</span>
                <Badge tone={PRIORITY_TONE[t.priority]}>{t.priority}</Badge>
                <h3 className="font-heading text-[15px] font-semibold">{t.title}</h3>
                {excluded && <Badge tone="muted">Excluded</Badge>}
                {!excluded && patchedTaskIds.has(t.id) && <Badge tone="agent">Patch proposed</Badge>}
                {!excluded && why && <Badge tone="muted">No automatic patch</Badge>}
              </div>
              <p className="mt-2 text-sm text-ink-2">{t.proposed_action}</p>
              {why && <p className="mt-1 text-xs text-ink-3">Why no patch: {why}</p>}
              <dl className="mt-3 grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                <Item k="Category" v={t.category} />
                <Item k="Risk / effort" v={`${t.risk} / ${t.estimated_effort}`} />
                <Item k="Evidence" v={t.evidence.join(", ") || "none"} mono />
                <Item k="Affected files" v={t.affected_files.join(", ") || "to be determined"} mono />
                <Item k="Verification" v={t.verification} wide />
              </dl>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Item({ k, v, mono, wide }: { k: string; v: string; mono?: boolean; wide?: boolean }) {
  return (
    <div className={wide ? "sm:col-span-2" : ""}>
      <dt className="inline text-ink-3">{k}: </dt>
      <dd className={`inline break-words text-ink ${mono ? "font-code" : ""}`}>{v}</dd>
    </div>
  );
}
