import { Activity, BadgeCheck, FileDiff, LayoutDashboard, ListChecks, PanelRight } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApprovalDialog, type ApprovalInfo } from "./components/ApprovalDialog";
import { DiffInspector } from "./components/DiffInspector";
import { HealthMeter } from "./components/HealthMeter";
import { IntakePanel } from "./components/IntakePanel";
import { KiroTrace } from "./components/KiroTrace";
import { Overview } from "./components/Overview";
import { RoadmapView } from "./components/RoadmapView";
import { TerminalStream } from "./components/TerminalStream";
import { TopHeader } from "./components/TopHeader";
import { button, Notice, Spinner } from "./components/ui";
import { WorkflowSidebar } from "./components/WorkflowSidebar";
import { useRevivalStream } from "./hooks/useRevivalStream";
import { useRunData } from "./hooks/useRunData";
import { api, ApiError } from "./services/api";
import { stageStates } from "./state/runState";
import type { ExecutionMode, Fixture, Health, Repository, RepositorySource } from "./types/revival";

type View = "overview" | "roadmap" | "diff" | "trace";
const TABS: { id: View; label: string; icon: typeof Activity }[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "roadmap", label: "Revival Roadmap", icon: ListChecks },
  { id: "diff", label: "Diff Inspector", icon: FileDiff },
  { id: "trace", label: "Kiro Trace", icon: Activity },
];
const DATA_EVENTS = new Set([
  "stage_changed", "roadmap_ready", "diff_generated", "verification_completed", "run_completed",
  "run_failed", "run_cancelled", "approval_received",
]);

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [fixtures, setFixtures] = useState<Fixture[]>([]);
  const [repos, setRepos] = useState<Repository[]>([]);
  const [repoId, setRepoId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [mode, setMode] = useState<ExecutionMode>("INSPECT");
  const [tab, setTab] = useState<View>("overview");
  const [adding, setAdding] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [dialog, setDialog] = useState(false);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const { view, connection } = useRevivalStream(runId);
  const refreshKey = `${view.events.filter((e) => DATA_EVENTS.has(e.type)).length}:${view.status}`;
  const data = useRunData(runId, refreshKey);

  useEffect(() => {
    (async () => {
      try {
        const [h, f, r] = await Promise.all([api.health(), api.fixtures(), api.repositories()]);
        setHealth(h); setFixtures(f); setRepos(r);
        if (r[0]) void selectRepository(r[0].id);
      } catch (e) {
        setLoadError(e instanceof ApiError ? e.message : "Could not load the backend state.");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function selectRepository(id: string) {
    setRepoId(id);
    setExcluded(new Set());
    try {
      const [latest] = await api.runs(id);
      setRunId(latest?.id ?? null); // restore the most recent run for this repository
    } catch {
      setRunId(null);
    }
  }

  const importRepo = useCallback(async (source: RepositorySource) => {
    const repo = await api.importRepository(source);
    setRepos(await api.repositories());
    await selectRepository(repo.id);
    return repo;
  }, []);

  async function start() {
    if (!repoId) return;
    setBusy(true); setActionError(null); setExcluded(new Set());
    try {
      const run = await api.startRun(repoId, mode);
      setRunId(run.id);
      setTab("overview");
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : "Could not start the run.");
    } finally {
      setBusy(false);
    }
  }

  async function decide(decision: "approve" | "edit" | "reject") {
    const version = data.run?.roadmap_version;
    if (!runId || !version) return;
    setBusy(true); setActionError(null);
    try {
      await api.decide(runId, decision, version, decision === "edit" ? { excluded_task_ids: [...excluded] } : {});
      setExcluded(new Set());
      setDialog(false);
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : "The decision could not be recorded.");
    } finally {
      setBusy(false);
    }
  }

  const status = view.status ?? data.run?.status ?? null;
  const awaiting = status === "AWAITING_APPROVAL";
  const states = stageStates(view, data.run);
  const patched = useMemo(() => new Set(data.patches?.patches.map((p) => p.task_id) ?? []), [data.patches]);
  const skipped = useMemo(() => new Map(data.patches?.skipped.map((s) => [s.task_id, s.reason]) ?? []), [data.patches]);
  const approval = view.approval as unknown as ApprovalInfo | null;
  const selectedRepo = repos.find((r) => r.id === repoId);
  const sandbox = health?.sandbox;

  if (loading)
    return <div className="flex h-full items-center justify-center"><Spinner label="Connecting to the backend" /></div>;

  return (
    <div className="flex h-full flex-col">
      <TopHeader
        repositories={repos} repositoryId={repoId} onSelectRepository={(id) => void selectRepository(id)}
        onAddRepository={() => setAdding(true)} mode={mode} onMode={setMode}
        status={status} verdict={view.verdict} commit={data.run?.pinned_commit ?? null} busy={busy}
        onStart={() => void start()} onCancel={() => runId && void api.cancel(runId).catch((e: ApiError) => setActionError(e.message))}
      />

      <div className="space-y-2 px-4 pt-3 empty:hidden">
        {loadError && <Notice tone="bad" title="Backend unavailable" role="alert">{loadError}</Notice>}
        {actionError && <Notice tone="bad" title="Action failed" role="alert">{actionError}</Notice>}
        {sandbox && !sandbox.available && (
          <Notice tone="warn" title="Docker sandbox unavailable" role="status">
            {sandbox.reason ?? "The sandbox cannot be used."}{" "}
            {sandbox.host_fixture_exec
              ? "Tests run only for bundled demo fixtures, on the host with resource limits. Other repositories are analysed statically and their tests are reported as not run."
              : "No repository tests will run."}
          </Notice>
        )}
        {(connection === "reconnecting" || connection === "failed") && (
          <Notice tone="warn" title={connection === "failed" ? "Live stream disconnected" : "Reconnecting to the live stream"} role="status">
            {connection === "failed"
              ? "The stream could not be restored. Reload the page to replay the run from the backend log."
              : "Missed events are replayed from the backend log when the connection returns."}
          </Notice>
        )}
        {awaiting && approval && (
          <Notice tone="warn" title="Your approval is required" role="status">
            <button type="button" className={`${button.amber} mt-2`} onClick={() => setDialog(true)}>
              <BadgeCheck size={15} aria-hidden="true" /> Review and decide
            </button>
          </Notice>
        )}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)_290px]">
        <WorkflowSidebar states={states} />

        <main className="min-w-0 overflow-auto p-4">
          {adding || repos.length === 0 ? (
            <div className="mx-auto max-w-2xl space-y-3">
              {repos.length === 0 && (
                <div className="mb-4 text-center">
                  <p className="font-heading text-xl font-semibold">Build something wicked and git commit to the darkness.</p>
                  <p className="mt-1 text-sm text-ink-2">Diagnose. Reanimate. Verify.</p>
                </div>
              )}
              <IntakePanel fixtures={fixtures} onImport={importRepo} onDone={() => setAdding(false)} />
              {repos.length > 0 && <button type="button" className={button.secondary} onClick={() => setAdding(false)}>Back</button>}
            </div>
          ) : (
            <>
              <div role="tablist" aria-label="Workspace" className="mb-4 flex flex-wrap gap-1 border-b border-edge">
                {TABS.map((t) => (
                  <button
                    key={t.id} type="button" role="tab" id={`tab-${t.id}`} aria-selected={tab === t.id} aria-controls="workspace-panel"
                    onClick={() => setTab(t.id)}
                    className={`-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium ${tab === t.id ? "border-haunt text-ink" : "border-transparent text-ink-2 hover:text-ink"}`}
                  >
                    <t.icon size={15} aria-hidden="true" /> {t.label}
                  </button>
                ))}
                <button
                  type="button" className="ml-auto mb-1 flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-xs text-ink-2 xl:hidden"
                  aria-expanded={drawer} onClick={() => setDrawer(!drawer)}
                >
                  <PanelRight size={14} aria-hidden="true" /> Vitality
                </button>
              </div>
              {data.error && <div className="mb-3"><Notice tone="bad" title="Could not load run data" role="alert">{data.error}</Notice></div>}
              {drawer && (
                <div className="mb-4 xl:hidden">
                  <HealthMeter before={view.scoreBefore} after={view.scoreAfter} autopsy={data.autopsy} warning={data.vitality?.comparison_warning ?? null} />
                </div>
              )}
              <div id="workspace-panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
                {tab === "overview" && (
                  <Overview run={data.run} autopsy={data.autopsy} baseline={data.baseline} verification={data.verification} artifacts={data.artifacts} hasRepository={!!selectedRepo} />
                )}
                {tab === "roadmap" && (
                  <RoadmapView
                    roadmap={data.roadmap} patchedTaskIds={patched} skipped={skipped}
                    editing={awaiting ? { excluded, toggle: (id) => setExcluded((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; }) } : null}
                  />
                )}
                {tab === "diff" && <DiffInspector patches={data.patches} />}
                {tab === "trace" && <KiroTrace events={view.events} />}
              </div>
              <p className="mt-8 text-center text-xs text-ink-3">Build something wicked and git commit to the darkness.</p>
            </>
          )}
        </main>

        <div className="hidden overflow-auto border-l border-edge bg-obsidian p-4 xl:block">
          <HealthMeter before={view.scoreBefore} after={view.scoreAfter} autopsy={data.autopsy} warning={data.vitality?.comparison_warning ?? null} />
        </div>
      </div>

      <TerminalStream events={view.events} />

      {dialog && approval && (
        <ApprovalDialog
          info={approval} excludedCount={excluded.size} busy={busy} error={actionError}
          onApprove={() => void decide("approve")} onEdit={() => void decide("edit")}
          onReject={() => void decide("reject")} onClose={() => setDialog(false)}
        />
      )}
    </div>
  );
}
