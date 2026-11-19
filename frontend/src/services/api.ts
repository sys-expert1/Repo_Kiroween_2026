import type {
  Artifact, ExecutionMode, Fixture, Health, Repository, RepositorySource, Run,
} from "../types/revival";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the Repo Necromancer backend. Is it running?");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/api/health"),
  fixtures: () => request<Fixture[]>("/api/fixtures"),
  repositories: () => request<Repository[]>("/api/repositories"),
  importRepository: (source: RepositorySource) => post<Repository>("/api/repositories/import", source),
  startRun: (repository_id: string, mode: ExecutionMode) =>
    post<Run>("/api/revive/start", { repository_id, mode }),
  runs: (repositoryId?: string) =>
    request<Run[]>(`/api/revive${repositoryId ? `?repository_id=${encodeURIComponent(repositoryId)}` : ""}`),
  run: (id: string) => request<Run>(`/api/revive/${encodeURIComponent(id)}`),
  cancel: (id: string) => post<Run>(`/api/revive/${encodeURIComponent(id)}/cancel`),
  decide: (
    id: string,
    decision: "approve" | "edit" | "reject",
    roadmapVersion: number,
    extra: { note?: string; excluded_task_ids?: string[] } = {},
  ) =>
    post<Run>(`/api/revive/approve/${encodeURIComponent(id)}`, {
      decision,
      roadmap_version: roadmapVersion,
      ...extra,
    }),
  artifacts: (id: string) => request<Artifact[]>(`/api/revive/${encodeURIComponent(id)}/artifacts`),
  artifactText: async (runId: string, artifactId: string): Promise<string> => {
    const res = await fetch(`/api/revive/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`);
    if (!res.ok) throw new ApiError(res.status, `Artifact download failed (${res.status})`);
    return res.text();
  },
  streamUrl: (id: string, after = 0) =>
    `/api/revive/stream/${encodeURIComponent(id)}${after > 0 ? `?after=${after}` : ""}`,
};

/** Latest artifact of a given type, by creation order. */
export function latest(artifacts: Artifact[], type: string): Artifact | undefined {
  return artifacts.filter((a) => a.type === type).at(-1);
}
