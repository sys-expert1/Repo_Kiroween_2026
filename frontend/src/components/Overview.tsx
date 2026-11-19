import { Download, FileText, FlaskConical, ScanSearch, ShieldCheck } from "lucide-react";

import type { Artifact, Autopsy, Baseline, Run, Verification } from "../types/revival";
import { Badge, EmptyState, Notice, Panel, type Tone } from "./ui";

const SEV: Record<string, Tone> = { critical: "bad", high: "bad", medium: "warn", low: "info", info: "muted" };
const CHECK: Record<string, Tone> = { passed: "ok", failed: "bad", not_measured: "warn", not_configured: "muted" };

interface Props {
  run: Run | null;
  autopsy: Autopsy | null;
  baseline: Baseline | null;
  verification: Verification | null;
  artifacts: Artifact[];
  hasRepository: boolean;
}

export function Overview({ run, autopsy, baseline, verification, artifacts, hasRepository }: Props) {
  if (!run)
    return (
      <EmptyState icon={ScanSearch} title={hasRepository ? "Ready to inspect" : "Choose a repository"}>
        {hasRepository
          ? "Pick a mode and start. Inspect is read-only and is the safe default."
          : "Add a demo fixture, a Git URL or a local path to begin."}
      </EmptyState>
    );
  return (
    <div className="space-y-4">
      {run.status === "FAILED" && (
        <Notice tone="bad" title="This run failed" role="alert">{run.error ?? "No reason was recorded."}</Notice>
      )}
      {run.status === "CANCELLED" && <Notice tone="muted" title="This run was cancelled">The source repository was not modified.</Notice>}
      {verification && (
        <Panel title="Verification" icon={ShieldCheck}>
          <p className="mb-3 text-sm">
            Verdict:{" "}
            <Badge tone={verification.verdict === "verified" ? "ok" : "bad"}>
              {verification.verdict === "verified" ? "Verified" : "Failed"}
            </Badge>
          </p>
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-ink-3"><tr><th className="py-1">Check</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody className="align-top">
              {verification.checks.map((c) => (
                <tr key={c.name} className="border-t border-edge">
                  <td className="py-1.5 pr-3 font-code text-xs">{c.name}</td>
                  <td className="pr-3"><Badge tone={CHECK[c.status] ?? "muted"}>{c.status.replace("_", " ")}</Badge></td>
                  <td className="py-1.5 text-ink-2">{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {verification.regressions.length > 0 && (
            <ul className="mt-3 list-disc pl-5 text-sm text-crimson">{verification.regressions.map((r) => <li key={r}>{r}</li>)}</ul>
          )}
          <p className="mt-3 text-xs text-ink-3">
            Resolved findings: {verification.resolved_findings.length}. New findings: {verification.new_findings.length}.
            {verification.unmeasured.length > 0 && ` Not measured: ${verification.unmeasured.join(", ")}.`}
          </p>
        </Panel>
      )}

      <Panel title="Characterization (baseline)" icon={FlaskConical}>
        {!baseline ? (
          <p className="text-sm text-ink-2">The baseline has not been recorded yet.</p>
        ) : (
          <div className="space-y-2 text-sm">
            <p>
              Tests:{" "}
              {baseline.counts ? (
                <span className="font-metric">
                  {baseline.counts.passed} passed, {baseline.counts.failed} failed, {baseline.counts.errors} errors, {baseline.counts.skipped} skipped
                </span>
              ) : (
                <Badge tone="warn">not measured ({baseline.status.replace(/_/g, " ")})</Badge>
              )}
            </p>
            {baseline.reason && <p className="text-ink-2">{baseline.reason}</p>}
            {baseline.failing_tests.length > 0 && (
              <div>
                <p className="text-ink-3">Pre-existing failures (not regressions):</p>
                <ul className="list-disc pl-5 font-code text-xs">{baseline.failing_tests.map((t) => <li key={t}>{t}</li>)}</ul>
              </div>
            )}
            <p className="text-xs text-ink-3">Coverage: {baseline.coverage.replace("_", " ")}. Build:{" "}
              {baseline.build === "not_measured" ? "not measured" : baseline.build}.</p>
          </div>
        )}
      </Panel>

      <Panel title="Autopsy findings" icon={ScanSearch}>
        {!autopsy ? (
          <p className="text-sm text-ink-2">Autopsy is still running or has not started.</p>
        ) : autopsy.findings.length === 0 ? (
          <p className="text-sm text-ink-2">No findings. The scan completed with nothing to report.</p>
        ) : (
          <ul className="space-y-2">
            {autopsy.findings.map((f) => (
              <li key={f.id} className="rounded-md border border-edge bg-raised p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-metric text-xs text-ink-3">{f.id}</span>
                  <Badge tone={SEV[f.severity] ?? "muted"}>{f.severity}</Badge>
                  <span className="text-sm font-medium">{f.title}</span>
                </div>
                <p className="mt-1 text-xs text-ink-2">{f.evidence}</p>
                {f.file_path && (
                  <p className="mt-1 font-code text-xs text-ice">{f.file_path}{f.line ? `:${f.line}` : ""}</p>
                )}
              </li>
            ))}
          </ul>
        )}
        {autopsy && autopsy.tools.some((t) => !t.measured) && (
          <p className="mt-3 text-xs text-pumpkin">
            Not measured: {autopsy.tools.filter((t) => !t.measured).map((t) => `${t.tool} (${t.summary})`).join("; ")}
          </p>
        )}
      </Panel>

      {artifacts.length > 0 && (
        <Panel title="Reports and artifacts" icon={FileText}>
          <ul className="grid gap-2 sm:grid-cols-2">
            {artifacts.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-2 rounded-md border border-edge px-3 py-1.5 text-xs">
                <span className="min-w-0 truncate font-code" title={`sha256 ${a.checksum}`}>{a.name}</span>
                <a
                  className="inline-flex items-center gap-1 text-ice hover:underline"
                  href={`/api/revive/${run.id}/artifacts/${a.id}`} download={a.name}
                >
                  <Download size={12} aria-hidden="true" /> {(a.size / 1024).toFixed(1)} KB
                </a>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}
