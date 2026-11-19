import { FolderGit2, Play, Square } from "lucide-react";

import type { ExecutionMode, Repository, RunStatus } from "../types/revival";
import { Logo } from "./Logo";
import { StatusChip } from "./StatusChip";
import { button, field } from "./ui";

const MODES: { id: ExecutionMode; label: string; help: string }[] = [
  { id: "INSPECT", label: "Inspect", help: "Read-only. Findings and baseline. Nothing is changed." },
  { id: "PREVIEW", label: "Preview", help: "Roadmap and proposed diffs. No patch is applied." },
  { id: "REVIVE", label: "Revive", help: "Applies only patches you approve, in an isolated copy." },
];

interface Props {
  repositories: Repository[];
  repositoryId: string | null;
  onSelectRepository: (id: string) => void;
  onAddRepository: () => void;
  mode: ExecutionMode;
  onMode: (m: ExecutionMode) => void;
  status: RunStatus | null;
  verdict: "verified" | "failed" | null;
  commit: string | null;
  busy: boolean;
  onStart: () => void;
  onCancel: () => void;
}

export function TopHeader(p: Props) {
  const active = p.status === "QUEUED" || p.status === "RUNNING" || p.status === "AWAITING_APPROVAL";
  const mode = MODES.find((m) => m.id === p.mode)!;
  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-edge bg-panel px-4 py-2.5">
      <div className="flex items-center gap-3">
        <Logo />
        <div className="leading-tight">
          <h1 className="font-heading text-lg font-bold tracking-wide text-ink">REPO NECROMANCER</h1>
          <p className="text-[11px] text-ink-2">Diagnose. Reanimate. Verify.</p>
        </div>
      </div>

      <div className="flex min-w-0 items-center gap-2">
        <FolderGit2 size={16} className="shrink-0 text-ink-2" aria-hidden="true" />
        <label className="sr-only" htmlFor="repo-select">Repository</label>
        <select
          id="repo-select"
          className={`${field} max-w-56`}
          value={p.repositoryId ?? ""}
          onChange={(e) => e.target.value && p.onSelectRepository(e.target.value)}
        >
          <option value="">{p.repositories.length ? "Select a repository" : "No repositories yet"}</option>
          {p.repositories.map((r) => (
            <option key={r.id} value={r.id}>{r.name} ({r.source_type})</option>
          ))}
        </select>
        <button type="button" className={button.secondary} onClick={p.onAddRepository}>Add</button>
        {p.commit && (
          <span className="font-metric text-xs text-ink-2" title="Pinned commit">
            {p.commit.slice(0, 7)}
          </span>
        )}
      </div>

      <fieldset className="flex items-center gap-1" disabled={active}>
        <legend className="sr-only">Execution mode</legend>
        {MODES.map((m) => (
          <label
            key={m.id}
            title={m.help}
            className={`cursor-pointer rounded-md border px-3 py-1.5 text-xs font-semibold has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ice ${
              p.mode === m.id ? "border-haunt bg-haunt/15 text-haunt-soft" : "border-edge text-ink-2 hover:text-ink"
            } ${active ? "opacity-50" : ""}`}
          >
            <input
              type="radio" name="mode" className="sr-only" checked={p.mode === m.id}
              onChange={() => p.onMode(m.id)}
            />
            {m.label.toUpperCase()}
          </label>
        ))}
      </fieldset>

      <div className="flex items-center gap-2">
        <button
          type="button" className={button.primary}
          disabled={!p.repositoryId || active || p.busy} onClick={p.onStart}
        >
          <Play size={15} aria-hidden="true" /> Start {mode.label}
        </button>
        {active && (
          <button type="button" className={button.danger} onClick={p.onCancel}>
            <Square size={14} aria-hidden="true" /> Cancel
          </button>
        )}
      </div>

      <div className="ml-auto flex items-center gap-2" aria-live="polite">
        <span className="text-xs text-ink-3">{mode.help}</span>
        <StatusChip status={p.status} verdict={p.verdict} />
      </div>
    </header>
  );
}
