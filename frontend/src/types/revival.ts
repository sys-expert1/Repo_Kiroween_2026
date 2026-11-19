// Mirrors the backend contract (backend/app/schemas.py). The UI depends only on this.

export type ExecutionMode = "INSPECT" | "PREVIEW" | "REVIVE";
export type Stage = "AUTOPSY" | "CHARACTERIZATION" | "REANIMATION" | "CONSECRATION";
export const STAGES: readonly Stage[] = ["AUTOPSY", "CHARACTERIZATION", "REANIMATION", "CONSECRATION"];
export type RunStatus = "QUEUED" | "RUNNING" | "AWAITING_APPROVAL" | "COMPLETED" | "FAILED" | "CANCELLED";
export const TERMINAL: readonly RunStatus[] = ["COMPLETED", "FAILED", "CANCELLED"];

export type EventType =
  | "run_started" | "stage_changed" | "health_score_updated" | "roadmap_ready" | "hitl_required"
  | "approval_received" | "diff_generated" | "terminal_log" | "verification_completed"
  | "run_completed" | "run_failed" | "run_cancelled" | "cancel_requested" | "patch_applied"
  | "rollback_completed";

export interface RunEvent {
  event_id: string;
  run_id: string;
  sequence: number;
  type: EventType;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface Run {
  id: string;
  repository_id: string;
  mode: ExecutionMode;
  status: RunStatus;
  current_stage: Stage | null;
  cancel_requested: boolean;
  roadmap_version: number | null;
  patch_set_digest: string | null;
  pinned_commit: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export type SourceType = "git" | "local" | "fixture";
export interface Signals {
  languages: { language: string; files: number; lines: number }[];
  primary_language: string | null;
  dependency_files: string[];
  has_tests: boolean;
  test_files: number;
  has_dockerfile: boolean;
  has_ci: boolean;
  is_git_repo: boolean;
}
export interface Repository {
  id: string;
  name: string;
  source_type: SourceType;
  location: string;
  created_at: string;
  signals: Signals | null;
}
export interface Fixture { id: string; name: string; description: string; language: string }
export interface RepositorySource { git_url?: string; local_path?: string; fixture_id?: string }

export interface Health {
  status: string;
  sandbox: { mode: string; available: boolean; reason: string | null; host_fixture_exec: boolean };
}

export interface Artifact {
  id: string; type: string; name: string; checksum: string; size: number; created_at: string;
}

export interface Finding {
  id: string; category: string; severity: "critical" | "high" | "medium" | "low" | "info";
  title: string; evidence: string; file_path: string | null; line: number | null; source: string;
}
export interface ToolRun { tool: string; measured: boolean; summary: string; exit_code: number | null }
export interface Autopsy {
  repository_name: string; frameworks: string[]; python_files: number; python_lines: number;
  dependency_files: string[]; unpinned_dependencies: string[]; test_files: number;
  test_functions: number; has_dockerfile: boolean; has_ci: boolean; findings: Finding[];
  tools: ToolRun[]; known_vulnerabilities: number | null; lint_violations: number | null;
}
export interface Baseline {
  status: "completed" | "not_run" | "failed_to_execute"; reason: string | null;
  counts: { passed: number; failed: number; errors: number; skipped: number } | null;
  failing_tests: string[]; coverage: string; build: string; exit_code: number | null;
}

export interface RoadmapTask {
  id: string; title: string; priority: "P0" | "P1" | "P2" | "P3"; category: string;
  evidence: string[]; affected_files: string[]; proposed_action: string;
  risk: "low" | "medium" | "high"; estimated_effort: "S" | "M" | "L"; verification: string;
  status: "proposed" | "excluded";
}
export interface Roadmap { version: number; repository_name: string; tasks: RoadmapTask[] }

export interface FilePatch {
  task_id: string; path: string; action: "modify" | "create"; diff: string; rationale: string;
  after_sha256: string;
}
export interface PatchSet {
  roadmap_version: number; patches: FilePatch[];
  skipped: { task_id: string; title: string; reason: string }[];
}

export type CheckStatus = "passed" | "failed" | "not_measured" | "not_configured";
export interface Verification {
  verdict: "verified" | "failed";
  checks: { name: string; status: CheckStatus; detail: string; command: string | null }[];
  regressions: string[]; resolved_findings: string[]; new_findings: string[];
  unmeasured: string[]; score_warning: string | null;
}

export interface Dimension { name: string; weight: number; score: number | null; basis: string }
export interface Vitality {
  dimensions: Dimension[]; score: number | null; measured_weight: number;
  measured: string[]; unmeasured: string[]; note: string;
}
