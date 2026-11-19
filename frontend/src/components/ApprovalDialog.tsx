import { BadgeCheck, Pencil, XCircle } from "lucide-react";
import { useEffect, useRef } from "react";

import { button, Spinner } from "./ui";

export interface ApprovalInfo {
  repository?: string; source_commit?: string | null; roadmap_version: number;
  patch_set_digest: string; task_count: number; files_affected: number;
  dependency_changes?: string[]; security_fixes?: number; execution_target?: string;
}

interface Props {
  info: ApprovalInfo;
  excludedCount: number;
  busy: boolean;
  error: string | null;
  onApprove: () => void;
  onEdit: () => void;
  onReject: () => void;
  onClose: () => void;
}

/** The authorization ritual: nothing is applied until Approve is pressed for this exact digest. */
export function ApprovalDialog({ info, excludedCount, busy, error, onApprove, onEdit, onReject, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const rows: [string, string][] = [
    ["Repository", info.repository ?? "unknown"],
    ["Source commit", info.source_commit ?? "not available"],
    ["Roadmap version", `v${info.roadmap_version}`],
    ["Proposed tasks", String(info.task_count)],
    ["Files affected", String(info.files_affected)],
    ["Security fixes", String(info.security_fixes ?? 0)],
    ["Execution target", info.execution_target ?? "isolated working copy"],
  ];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-obsidian/80 p-4">
      <div
        ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="approval-title"
        className="max-h-full w-full max-w-xl overflow-auto rounded-lg border border-pumpkin/60 bg-panel p-5 shadow-xl"
      >
        <h2 id="approval-title" className="flex items-center gap-2 font-heading text-lg font-semibold text-pumpkin">
          <BadgeCheck size={20} aria-hidden="true" /> Authorization required
        </h2>
        <p className="mt-1 text-sm text-ink-2">
          Review the patches in the Diff Inspector first. Approving authorizes exactly this patch set, and only in an
          isolated working copy. Your original repository is never changed.
        </p>
        <dl className="mt-4 divide-y divide-edge rounded-md border border-edge text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-4 px-3 py-1.5">
              <dt className="text-ink-3">{k}</dt>
              <dd className="text-right font-metric text-ink">{v}</dd>
            </div>
          ))}
          <div className="px-3 py-1.5">
            <dt className="text-ink-3">Patch-set digest</dt>
            <dd className="break-all font-code text-xs text-ink">{info.patch_set_digest}</dd>
          </div>
          {!!info.dependency_changes?.length && (
            <div className="px-3 py-1.5">
              <dt className="text-ink-3">Dependency changes</dt>
              <dd className="space-y-0.5 text-xs text-ink">
                {info.dependency_changes.map((d) => <p key={d}>{d.split(".")[0]}.</p>)}
              </dd>
            </div>
          )}
        </dl>
        {error && <p role="alert" className="mt-3 text-sm text-crimson">{error}</p>}
        <div className="mt-5 flex flex-wrap items-center gap-2">
          <button type="button" className={button.amber} disabled={busy || excludedCount > 0} onClick={onApprove}
            title={excludedCount > 0 ? "Submit your edit first. Approval is required again after it." : undefined}>
            <BadgeCheck size={15} aria-hidden="true" /> Approve and Revive
          </button>
          <button type="button" className={button.secondary} disabled={busy || excludedCount === 0} onClick={onEdit}>
            <Pencil size={15} aria-hidden="true" /> Submit edit ({excludedCount} excluded)
          </button>
          <button type="button" className={button.danger} disabled={busy} onClick={onReject}>
            <XCircle size={15} aria-hidden="true" /> Reject
          </button>
          <button type="button" className={`${button.secondary} ml-auto`} onClick={onClose}>Close</button>
        </div>
        {busy && <div className="mt-3"><Spinner label="Recording your decision" /></div>}
        <p className="mt-3 text-xs text-ink-3">
          To edit, close this dialog, tick the tasks to exclude in the Revival Roadmap, then submit the edit.
          A new roadmap version needs a new approval.
        </p>
      </div>
    </div>
  );
}
