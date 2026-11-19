import { useEffect, useState } from "react";

import { api, latest } from "../services/api";
import type {
  Artifact, Autopsy, Baseline, PatchSet, Roadmap, Run, Verification, Vitality,
} from "../types/revival";

export interface RunData {
  run: Run | null;
  artifacts: Artifact[];
  autopsy: Autopsy | null;
  baseline: Baseline | null;
  roadmap: Roadmap | null;
  patches: PatchSet | null;
  verification: Verification | null;
  vitality: { before: Vitality; after: Vitality; comparison_warning: string | null } | null;
  error: string | null;
  loading: boolean;
}

const empty: RunData = {
  run: null, artifacts: [], autopsy: null, baseline: null, roadmap: null, patches: null,
  verification: null, vitality: null, error: null, loading: false,
};

async function json<T>(runId: string, artifacts: Artifact[], type: string): Promise<T | null> {
  const found = latest(artifacts, type);
  if (!found) return null;
  return JSON.parse(await api.artifactText(runId, found.id)) as T;
}

/** Loads the run and its artifacts. `refreshKey` changes when the backend produced new data. */
export function useRunData(runId: string | null, refreshKey: string): RunData {
  const [data, setData] = useState<RunData>(empty);

  useEffect(() => {
    if (!runId) {
      setData(empty);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const [run, artifacts] = await Promise.all([api.run(runId), api.artifacts(runId)]);
        const [autopsy, baseline, roadmap, patches, verification, vitality] = await Promise.all([
          json<Autopsy>(runId, artifacts, "autopsy_data"),
          json<Baseline>(runId, artifacts, "characterization_data"),
          json<Roadmap>(runId, artifacts, "roadmap_data"),
          json<PatchSet>(runId, artifacts, "patch_set_data"),
          json<Verification>(runId, artifacts, "verification_data"),
          json<RunData["vitality"]>(runId, artifacts, "vitality_report"),
        ]);
        if (!cancelled)
          setData({ run, artifacts, autopsy, baseline, roadmap, patches, verification, vitality, error: null, loading: false });
      } catch (e) {
        if (!cancelled) setData((d) => ({ ...d, error: e instanceof Error ? e.message : String(e), loading: false }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId, refreshKey]);

  return data;
}
