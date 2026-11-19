import { Bot, ClipboardCopy, Download, FileDiff } from "lucide-react";
import { html as diffHtml } from "diff2html";
import { ColorSchemeType } from "diff2html/lib/types";
import { useMemo, useState } from "react";

import type { PatchSet } from "../types/revival";
import { Badge, button, EmptyState } from "./ui";

/** Render a unified diff. diff2html escapes file content; the test suite verifies that. */
export function renderDiff(diff: string): string {
  return diffHtml(diff, { drawFileList: false, outputFormat: "side-by-side", matching: "lines", colorScheme: ColorSchemeType.DARK });
}

export function DiffInspector({ patches }: { patches: PatchSet | null }) {
  const [selected, setSelected] = useState(0);
  const list = patches?.patches ?? [];
  const current = list[Math.min(selected, Math.max(list.length - 1, 0))];
  const rendered = useMemo(() => (current ? renderDiff(current.diff) : ""), [current]);

  if (!patches)
    return (
      <EmptyState icon={FileDiff} title="No patches yet">
        Patches are proposed after the roadmap, in Preview and Revive modes.
      </EmptyState>
    );
  if (list.length === 0)
    return (
      <EmptyState icon={FileDiff} title="No automatic patches">
        None of the roadmap tasks could be patched automatically. See each task for the reason.
      </EmptyState>
    );

  const all = list.map((p) => `# ${p.task_id}: ${p.rationale}\n${p.diff}`).join("");
  const download = () => {
    const url = URL.createObjectURL(new Blob([all], { type: "text/x-diff" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `PATCHSET.v${patches.roadmap_version}.diff` });
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[240px_1fr]">
      <nav aria-label="Changed files">
        <ul className="space-y-1">
          {list.map((p, i) => (
            <li key={`${p.task_id}-${p.path}`}>
              <button
                type="button" onClick={() => setSelected(i)} aria-current={i === selected}
                className={`w-full rounded-md border px-3 py-2 text-left ${i === selected ? "border-haunt bg-haunt/10" : "border-edge hover:border-ink-3"}`}
              >
                <span className="block break-all font-code text-xs text-ink">{p.path}</span>
                <span className="mt-1 flex items-center gap-2 text-xs text-ink-3">
                  <Badge tone={p.action === "create" ? "ok" : "agent"}>{p.action === "create" ? "new file" : "modified"}</Badge>
                  {p.origin === "llm" && <Badge tone="warn" icon={Bot}>AI-proposed</Badge>}
                  {p.task_id}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </nav>
      <div className="min-w-0">
        {current && (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <p className="min-w-0 flex-1 text-sm text-ink-2">
                <span className="font-metric text-ink">{current.task_id}</span> {current.rationale}
              </p>
              <button type="button" className={button.secondary} onClick={() => navigator.clipboard?.writeText(all)}>
                <ClipboardCopy size={14} aria-hidden="true" /> Copy patch set
              </button>
              <button type="button" className={button.secondary} onClick={download}>
                <Download size={14} aria-hidden="true" /> Download
              </button>
            </div>
            {current.origin === "llm" && (
              <p className="mb-3 rounded-md border border-pumpkin/60 bg-pumpkin/10 px-3 py-2 text-xs text-pumpkin">
                Proposed by an AI model ({current.model ?? "unknown"}). It passed automatic checks (it parses, keeps the
                function signature, removes the finding and adds none), but review every line before approving.
              </p>
            )}
            <div className="overflow-x-auto" dangerouslySetInnerHTML={{ __html: rendered }} />
          </>
        )}
      </div>
    </div>
  );
}
