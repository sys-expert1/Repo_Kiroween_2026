import { HeartPulse } from "lucide-react";

import type { Autopsy, Vitality } from "../types/revival";
import { Panel, TONE_TEXT, type Tone } from "./ui";

const LABEL: Record<string, string> = {
  dependencies: "Dependencies", tests: "Tests", code_quality: "Code quality",
  security: "Security", ci_docker: "CI / Docker",
};

function band(score: number): { tone: Tone; word: string } {
  if (score >= 75) return { tone: "ok", word: "Healthy" };
  if (score >= 40) return { tone: "warn", word: "Weak" };
  return { tone: "bad", word: "Critical" };
}

export function Gauge({ score, label }: { score: number | null; label: string }) {
  const r = 52, c = 2 * Math.PI * r;
  const known = score !== null;
  const b = known ? band(score) : null;
  return (
    <figure className="relative mx-auto size-[132px]" aria-label={`${label}: ${known ? score.toFixed(1) : "not measured"}`}>
      <svg width="132" height="132" viewBox="0 0 132 132" aria-hidden="true">
        <circle cx="66" cy="66" r={r} fill="none" stroke="#1F2937" strokeWidth="10" />
        {known && (
          <circle
            cx="66" cy="66" r={r} fill="none" strokeWidth="10" strokeLinecap="round"
            stroke={b!.tone === "ok" ? "#10B981" : b!.tone === "warn" ? "#F59E0B" : "#EF4444"}
            strokeDasharray={c} strokeDashoffset={c * (1 - score / 100)} transform="rotate(-90 66 66)"
          />
        )}
      </svg>
      <figcaption className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-metric text-3xl font-semibold text-ink">{known ? score.toFixed(1) : "n/a"}</span>
        <span className={`text-xs font-medium ${b ? TONE_TEXT[b.tone] : "text-ink-3"}`}>
          {b ? b.word : "Not measured"}
        </span>
      </figcaption>
    </figure>
  );
}

interface Props {
  before: Vitality | null;
  after: Vitality | null;
  autopsy: Autopsy | null;
  warning: string | null;
}

export function HealthMeter({ before, after, autopsy, warning }: Props) {
  const current = after ?? before;
  const counts = autopsy?.findings.reduce<Record<string, number>>((acc, f) => {
    acc[f.severity] = (acc[f.severity] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <aside aria-label="Repository health" className="space-y-4">
      <Panel title="Vitality Score" icon={HeartPulse}>
        {!current ? (
          <p className="text-sm text-ink-2">No score yet. It is computed from measured evidence after Characterization.</p>
        ) : (
          <>
            <Gauge score={current.score} label="Vitality Score" />
            <p className="text-center text-xs text-ink-3">
              {after ? "After Consecration" : "Before changes"}
              {before && after && ` (was ${before.score === null ? "n/a" : before.score.toFixed(1)})`}
            </p>
            <ul className="mt-3 space-y-2.5">
              {current.dimensions.map((d, i) => {
                const prior = before?.dimensions[i]?.score;
                return (
                  <li key={d.name} title={d.basis}>
                    <div className="flex justify-between text-xs">
                      <span className="text-ink-2">{LABEL[d.name] ?? d.name} <span className="text-ink-3">{(d.weight * 100).toFixed(0)}%</span></span>
                      <span className="font-metric text-ink">
                        {d.score === null ? <span className="text-ink-3">not measured</span> : d.score.toFixed(1)}
                        {after && prior !== undefined && prior !== null && d.score !== null && prior !== d.score && (
                          <span className="ml-1 text-ink-3">from {prior.toFixed(1)}</span>
                        )}
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 rounded bg-raised">
                      {d.score === null ? (
                        <div className="h-full w-full rounded border border-dashed border-ink-3/60" />
                      ) : (
                        <div className="h-full rounded bg-haunt-soft" style={{ width: `${d.score}%` }} />
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
            <p className="mt-3 text-xs text-ink-3">{current.note}</p>
            {warning && <p className="mt-2 text-xs text-pumpkin">{warning}</p>}
          </>
        )}
      </Panel>

      <Panel title="Repository">
        {!autopsy ? (
          <p className="text-sm text-ink-2">Autopsy has not produced results yet.</p>
        ) : (
          <dl className="space-y-1.5 text-xs">
            <Row k="Language" v={autopsy.language ?? "Python"} />
            <Row k="Frameworks" v={autopsy.frameworks.join(", ") || "none detected"} />
            <Row k="Source files" v={`${autopsy.source_files} (${autopsy.source_lines} lines)`} />
            <Row k="Test files" v={String(autopsy.test_files)} />
            <Row k="Known vulnerabilities" v={autopsy.known_vulnerabilities === null ? "not measured" : String(autopsy.known_vulnerabilities)} />
            <Row k="Lint violations" v={autopsy.lint_violations === null ? "not measured" : String(autopsy.lint_violations)} />
            <Row k="CI / Dockerfile" v={`${autopsy.has_ci ? "yes" : "no"} / ${autopsy.has_dockerfile ? "yes" : "no"}`} />
          </dl>
        )}
      </Panel>

      <Panel title="Findings">
        {!counts ? (
          <p className="text-sm text-ink-2">No findings yet.</p>
        ) : Object.keys(counts).length === 0 ? (
          <p className="text-sm text-ink-2">Autopsy found no issues.</p>
        ) : (
          <ul className="space-y-1 text-xs">
            {(["critical", "high", "medium", "low", "info"] as const).filter((s) => counts[s]).map((s) => (
              <li key={s} className="flex justify-between">
                <span className={s === "critical" || s === "high" ? "text-crimson" : s === "medium" ? "text-pumpkin" : "text-ice"}>{s}</span>
                <span className="font-metric">{counts[s]}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </aside>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-ink-3">{k}</dt>
      <dd className="text-right font-metric text-ink">{v}</dd>
    </div>
  );
}
