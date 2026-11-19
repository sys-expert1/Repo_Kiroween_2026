import { History } from "lucide-react";

import { RUN_STATE_LABEL } from "../state/runState";
import type { Run } from "../types/revival";
import { Badge, EmptyState, Spinner, type Tone } from "./ui";

const TONE: Record<Run["status"], Tone> = {
  QUEUED: "info", RUNNING: "agent", AWAITING_APPROVAL: "warn", COMPLETED: "info", FAILED: "bad", CANCELLED: "muted",
};

export function formatDuration(run: Run): string {
  if (!run.completed_at) return "in progress";
  const seconds = Math.max(0, (Date.parse(run.completed_at) - Date.parse(run.created_at)) / 1000);
  if (Number.isNaN(seconds)) return "unknown";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

interface Props {
  runs: Run[] | null; // null while loading
  error: string | null;
  currentId: string | null;
  onOpen: (id: string) => void;
}

/** Past runs for the selected repository. Opening one replays its events from the backend log. */
export function RunHistory({ runs, error, currentId, onOpen }: Props) {
  if (error) return <p role="alert" className="text-sm text-crimson">{error}</p>;
  if (runs === null) return <Spinner label="Loading run history" />;
  if (runs.length === 0)
    return (
      <EmptyState icon={History} title="No runs yet">
        Runs for this repository appear here, newest first, and can be reopened at any time.
      </EmptyState>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Runs for this repository, newest first</caption>
        <thead className="text-xs text-ink-3">
          <tr><th className="py-1 pr-3">Started</th><th className="pr-3">Mode</th><th className="pr-3">Status</th>
            <th className="pr-3">Stage</th><th className="pr-3">Duration</th><th className="pr-3">Commit</th><th /></tr>
        </thead>
        <tbody className="align-top">
          {runs.map((r) => (
            <tr key={r.id} className={`border-t border-edge ${r.id === currentId ? "bg-haunt/10" : ""}`} aria-current={r.id === currentId || undefined}>
              <td className="py-2 pr-3 font-metric text-xs">{new Date(r.created_at).toLocaleString([], { hour12: false })}</td>
              <td className="pr-3 font-metric text-xs">{r.mode}</td>
              <td className="pr-3"><Badge tone={TONE[r.status]}>{RUN_STATE_LABEL[r.status]}</Badge></td>
              <td className="pr-3 text-xs text-ink-2">{r.current_stage ?? "none"}</td>
              <td className="pr-3 font-metric text-xs">{formatDuration(r)}</td>
              <td className="pr-3 font-metric text-xs">{r.pinned_commit ? r.pinned_commit.slice(0, 7) : "n/a"}</td>
              <td className="py-1 text-right">
                <button
                  type="button" disabled={r.id === currentId} onClick={() => onOpen(r.id)}
                  aria-label={`Open run from ${r.created_at}`}
                  className="rounded-md border border-edge px-2.5 py-1 text-xs text-ink hover:border-ink-3 disabled:opacity-40"
                >
                  {r.id === currentId ? "Current" : "Open"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {runs.some((r) => r.error) && (
        <ul className="mt-3 space-y-1 text-xs text-ink-2">
          {runs.filter((r) => r.error).map((r) => (
            <li key={r.id}><span className="font-metric text-ink-3">{r.id.slice(0, 8)}</span> {r.error!.slice(0, 160)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
