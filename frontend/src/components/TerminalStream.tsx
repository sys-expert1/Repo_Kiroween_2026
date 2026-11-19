import { ChevronDown, ChevronUp, Terminal } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { RunEvent } from "../types/revival";

export interface TerminalLine {
  sequence: number; time: string; stage: string; command: string; exit: number | null;
  seconds: number; timedOut: boolean; output: string;
}

export function terminalLines(events: RunEvent[]): TerminalLine[] {
  return events.filter((e) => e.type === "terminal_log").map((e) => ({
    sequence: e.sequence,
    time: new Date(e.timestamp).toLocaleTimeString([], { hour12: false }),
    stage: String(e.payload.stage ?? ""),
    command: String(e.payload.command ?? ""),
    exit: typeof e.payload.exit_code === "number" ? e.payload.exit_code : null,
    seconds: Number(e.payload.duration_seconds ?? 0),
    timedOut: e.payload.timed_out === true,
    output: String(e.payload.output ?? ""),
  }));
}

/** Real commands only: every line comes from a terminal_log event with its true exit code. */
export function TerminalStream({ events }: { events: RunEvent[] }) {
  const [open, setOpen] = useState(true);
  const lines = terminalLines(events);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) end.current?.scrollIntoView?.({ block: "end" }); }, [lines.length, open]);
  return (
    <section aria-label="Revival Terminal" className="border-t border-edge bg-obsidian">
      <button
        type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-4 py-1.5 text-left text-xs font-semibold uppercase tracking-widest text-ink-2 hover:text-ink"
      >
        <Terminal size={14} aria-hidden="true" /> Revival Terminal
        <span className="font-metric normal-case tracking-normal text-ink-3">({lines.length} commands)</span>
        {open ? <ChevronDown size={14} className="ml-auto" aria-hidden="true" /> : <ChevronUp size={14} className="ml-auto" aria-hidden="true" />}
      </button>
      {open && (
        <div className="scan-lines h-48 overflow-auto px-4 pb-3 font-code text-xs" role="log" aria-live="off" tabIndex={0}>
          {lines.length === 0 ? (
            <p className="text-ink-3">No commands have run yet. Output appears here as the backend executes real tools.</p>
          ) : (
            lines.map((l) => (
              <div key={l.sequence} className="mb-2">
                <p className="text-ink-3">
                  {l.time} <span className="text-haunt-soft">[{l.stage}]</span>{" "}
                  <span className="text-ink">$ {l.command}</span>
                </p>
                {l.output && <pre className="whitespace-pre-wrap pl-4 text-ink-2">{l.output}</pre>}
                <p className={`pl-4 ${l.exit === 0 ? "text-phosphor" : "text-crimson"}`}>
                  {l.timedOut ? "timed out" : `exit ${l.exit ?? "none"}`} in {l.seconds.toFixed(2)}s
                </p>
              </div>
            ))
          )}
          <div ref={end} />
        </div>
      )}
    </section>
  );
}
