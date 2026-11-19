import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ev } from "../test/fakes";
import type { PatchSet, Run, Vitality } from "../types/revival";
import { ApprovalDialog } from "./ApprovalDialog";
import { DiffInspector, renderDiff } from "./DiffInspector";
import { HealthMeter } from "./HealthMeter";
import { KiroTrace } from "./KiroTrace";
import { formatDuration, RunHistory } from "./RunHistory";
import { StatusChip } from "./StatusChip";
import { terminalLines, TerminalStream } from "./TerminalStream";

const dim = (name: string, score: number | null, weight: number) => ({ name, weight, score, basis: "" });
const score = (dimensions: Vitality["dimensions"], total: number | null): Vitality => ({
  dimensions, score: total, measured_weight: 0.45, measured: [], unmeasured: [], note: "Computed from 2 of 5 dimensions.",
});

describe("HealthMeter", () => {
  it("shows unmeasured dimensions as not measured, never as 0 or 100", () => {
    render(
      <HealthMeter
        before={score([dim("dependencies", null, 0.2), dim("tests", 75, 0.3), dim("code_quality", null, 0.2), dim("security", null, 0.15), dim("ci_docker", 50, 0.15)], 66.7)}
        after={null} autopsy={null} warning={null}
      />,
    );
    expect(screen.getAllByText("not measured")).toHaveLength(3);
    expect(screen.getByText("Computed from 2 of 5 dimensions.")).toBeInTheDocument();
    expect(screen.getByLabelText("Vitality Score: 66.7")).toBeInTheDocument();
  });

  it("shows n/a when there is no score and an honest empty state without data", () => {
    const { rerender } = render(<HealthMeter before={null} after={null} autopsy={null} warning={null} />);
    expect(screen.getByText(/No score yet/)).toBeInTheDocument();
    rerender(<HealthMeter before={score([dim("tests", null, 0.3)], null)} after={null} autopsy={null} warning={null} />);
    expect(screen.getByLabelText("Vitality Score: not measured")).toBeInTheDocument();
  });

  it("prefers the after score and shows the comparison warning", () => {
    render(
      <HealthMeter
        before={score([dim("tests", 50, 0.3)], 36.3)} after={score([dim("tests", 50, 0.3)], 81.2)} autopsy={null}
        warning="different measured dimensions"
      />,
    );
    expect(screen.getByLabelText("Vitality Score: 81.2")).toBeInTheDocument();
    expect(screen.getByText(/was 36\.3/)).toBeInTheDocument();
    expect(screen.getByText("different measured dimensions")).toBeInTheDocument();
  });
});

describe("DiffInspector", () => {
  const patches = (diff: string): PatchSet => ({
    roadmap_version: 1, skipped: [],
    patches: [{ task_id: "RT-001", path: "a.py", action: "modify", rationale: "why", after_sha256: "x", diff }],
  });

  it("escapes file content, so a hostile repository cannot inject markup", () => {
    const hostile = '--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = "<img src=x onerror=alert(1)><script>alert(2)</script>"\n';
    const html = renderDiff(hostile);
    expect(html).not.toMatch(/<img[^>]*onerror/i);
    expect(html).not.toContain("<script>");
    const { container } = render(<DiffInspector patches={patches(hostile)} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("lists changed files with the task reference and a rationale", () => {
    render(<DiffInspector patches={patches("--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n")} />);
    expect(screen.getByRole("navigation", { name: "Changed files" })).toHaveTextContent("a.py");
    expect(screen.getByText("RT-001", { selector: "span.font-metric" })).toBeInTheDocument();
  });

  it("has distinct empty states for no patches yet and nothing patchable", () => {
    const { rerender } = render(<DiffInspector patches={null} />);
    expect(screen.getByText("No patches yet")).toBeInTheDocument();
    rerender(<DiffInspector patches={{ roadmap_version: 1, patches: [], skipped: [] }} />);
    expect(screen.getByText("No automatic patches")).toBeInTheDocument();
  });
});

describe("ApprovalDialog", () => {
  const info = { repository: "legacy", source_commit: null, roadmap_version: 2, patch_set_digest: "ab".repeat(32), task_count: 8, files_affected: 4, security_fixes: 2 };
  const handlers = () => ({ onApprove: vi.fn(), onEdit: vi.fn(), onReject: vi.fn(), onClose: vi.fn() });

  it("shows the exact scope being authorized", () => {
    render(<ApprovalDialog info={info} excludedCount={0} busy={false} error={null} {...handlers()} />);
    const dialog = screen.getByRole("dialog", { name: /Authorization required/ });
    expect(within(dialog).getByText("v2")).toBeInTheDocument();
    expect(within(dialog).getByText("ab".repeat(32))).toBeInTheDocument();
    expect(within(dialog).getByText("not available")).toBeInTheDocument();
    expect(within(dialog).getByText("isolated working copy")).toBeInTheDocument();
  });

  it("calls the right handler and blocks approval while an edit is pending", async () => {
    const h = handlers();
    const user = userEvent.setup();
    const { rerender } = render(<ApprovalDialog info={info} excludedCount={0} busy={false} error={null} {...h} />);
    expect(screen.getByRole("button", { name: /Submit edit/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Approve and Revive/ }));
    await user.click(screen.getByRole("button", { name: /Reject/ }));
    expect(h.onApprove).toHaveBeenCalledOnce();
    expect(h.onReject).toHaveBeenCalledOnce();
    rerender(<ApprovalDialog info={info} excludedCount={2} busy={false} error={null} {...h} />);
    expect(screen.getByRole("button", { name: /Approve and Revive/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Submit edit \(2 excluded\)/ }));
    expect(h.onEdit).toHaveBeenCalledOnce();
  });

  it("closes on Escape and surfaces errors accessibly", async () => {
    const h = handlers();
    render(<ApprovalDialog info={info} excludedCount={0} busy={false} error="roadmap_version must equal 2" {...h} />);
    expect(screen.getByRole("alert")).toHaveTextContent("roadmap_version must equal 2");
    await userEvent.setup().keyboard("{Escape}");
    expect(h.onClose).toHaveBeenCalled();
  });
});

describe("StatusChip", () => {
  it("uses emerald verified only for a verified completion, and always pairs colour with text", () => {
    const { rerender } = render(<StatusChip status="COMPLETED" verdict="verified" />);
    expect(screen.getByText("Verified")).toBeInTheDocument();
    rerender(<StatusChip status="COMPLETED" verdict={null} />);
    expect(screen.getByText("Completed")).toBeInTheDocument(); // inspect/preview completion is not "verified"
    rerender(<StatusChip status="AWAITING_APPROVAL" verdict={null} />);
    expect(screen.getByText("Approval required")).toBeInTheDocument();
    rerender(<StatusChip status="FAILED" verdict={null} />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    rerender(<StatusChip status={null} verdict={null} />);
    expect(screen.getByText("No run")).toBeInTheDocument();
  });
});

describe("Revival Terminal and Kiro Trace", () => {
  const log = ev(3, "terminal_log", { stage: "AUTOPSY", command: "ruff check .", exit_code: 1, duration_seconds: 0.03, output: "F401 unused" });

  it("shows real command output with its true exit code, and an honest empty state", () => {
    const { rerender } = render(<TerminalStream events={[]} />);
    expect(screen.getByText(/No commands have run yet/)).toBeInTheDocument();
    rerender(<TerminalStream events={[log]} />);
    expect(screen.getByText(/\$ ruff check \./)).toBeInTheDocument();
    expect(screen.getByText("exit 1 in 0.03s")).toBeInTheDocument();
    expect(terminalLines([log])[0]).toMatchObject({ exit: 1, command: "ruff check ." });
  });

  it("collapses the terminal", async () => {
    render(<TerminalStream events={[log]} />);
    await userEvent.setup().click(screen.getByRole("button", { name: /Revival Terminal/ }));
    expect(screen.queryByRole("log")).toBeNull();
  });

  it("lists the trace in order and can hide command noise", async () => {
    const events = [ev(1, "run_started"), ev(2, "stage_changed", { stage: "AUTOPSY", status: "RUNNING" }), log];
    render(<KiroTrace events={events} />);
    expect(within(screen.getByRole("list", { name: "Run event timeline" })).getAllByRole("listitem")).toHaveLength(3);
    await userEvent.setup().click(screen.getByLabelText("Hide command events"));
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("shows an empty trace state", () => {
    render(<KiroTrace events={[]} />);
    expect(screen.getByText("No trace yet")).toBeInTheDocument();
  });
});

describe("AI-proposed patches", () => {
  const ai: PatchSet = {
    roadmap_version: 1, skipped: [],
    patches: [
      { task_id: "RT-004", path: "app.py", action: "modify", rationale: "AI-proposed (m): x", after_sha256: "x", origin: "llm", model: "openai/gpt-x", diff: "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-a\n+b\n" },
      { task_id: "RT-001", path: "requirements.txt", action: "modify", rationale: "bump", after_sha256: "y", origin: "rule", diff: "--- a/requirements.txt\n+++ b/requirements.txt\n@@ -1 +1 @@\n-a\n+b\n" },
    ],
  };

  it("labels only the AI patch and warns before it is reviewed", async () => {
    render(<DiffInspector patches={ai} />);
    expect(screen.getAllByText("AI-proposed")).toHaveLength(1);
    expect(screen.getByText(/Proposed by an AI model \(openai\/gpt-x\)/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /requirements\.txt/ }));
    expect(screen.queryByText(/Proposed by an AI model/)).toBeNull();
  });

  it("treats patches from older runs, with no origin field, as rule-based", () => {
    const legacy: PatchSet = { roadmap_version: 1, skipped: [], patches: [{ ...ai.patches[1]!, origin: undefined }] };
    render(<DiffInspector patches={legacy} />);
    expect(screen.queryByText("AI-proposed")).toBeNull();
  });

  it("tells the approver how many patches an AI wrote", () => {
    const info = { roadmap_version: 1, patch_set_digest: "ab".repeat(32), task_count: 3, files_affected: 2, ai_patch_count: 2, ai_model: "openai/gpt-x" };
    render(<ApprovalDialog info={info} excludedCount={0} busy={false} error={null} onApprove={() => {}} onEdit={() => {}} onReject={() => {}} onClose={() => {}} />);
    expect(screen.getByText("AI-proposed patches")).toBeInTheDocument();
    expect(screen.getByText(/2 patch\(es\) were written by openai\/gpt-x/)).toBeInTheDocument();
  });

  it("shows no AI section when none were proposed", () => {
    const info = { roadmap_version: 1, patch_set_digest: "ab".repeat(32), task_count: 3, files_affected: 2, ai_patch_count: 0 };
    render(<ApprovalDialog info={info} excludedCount={0} busy={false} error={null} onApprove={() => {}} onEdit={() => {}} onReject={() => {}} onClose={() => {}} />);
    expect(screen.queryByText("AI-proposed patches")).toBeNull();
  });
});

describe("RunHistory", () => {
  const mk = (over: Partial<Run>): Run => ({
    id: "11111111-aaaa", repository_id: "r", mode: "REVIVE", status: "COMPLETED", current_stage: "CONSECRATION",
    cancel_requested: false, roadmap_version: 1, patch_set_digest: null, pinned_commit: "abcdef1234567", error: null,
    created_at: "2026-11-19T10:00:00Z", updated_at: "", completed_at: "2026-11-19T10:01:05Z", ...over,
  });

  it("shows loading, error and empty states distinctly", () => {
    const { rerender } = render(<RunHistory runs={null} error={null} currentId={null} onOpen={() => {}} />);
    expect(screen.getByText("Loading run history")).toBeInTheDocument();
    rerender(<RunHistory runs={null} error="Could not load run history." currentId={null} onOpen={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load");
    rerender(<RunHistory runs={[]} error={null} currentId={null} onOpen={() => {}} />);
    expect(screen.getByText("No runs yet")).toBeInTheDocument();
  });

  it("lists runs with status text, short commit and duration, and marks the open one", async () => {
    const open = vi.fn();
    const runs = [mk({ id: "22222222-bbbb", status: "FAILED", error: "Verification failed and the working copy was rolled back", completed_at: "2026-11-19T10:00:07Z", pinned_commit: null }), mk({})];
    render(<RunHistory runs={runs} error={null} currentId="11111111-aaaa" onOpen={open} />);
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("Failed")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("7.0s")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("n/a")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("1m 5s")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("abcdef1")).toBeInTheDocument();
    expect(rows[1]).toHaveAttribute("aria-current", "true");
    expect(within(rows[1]!).getByRole("button", { name: /Open run/ })).toBeDisabled();
    expect(screen.getByText(/rolled back/)).toBeInTheDocument();
    await userEvent.setup().click(within(rows[0]!).getByRole("button", { name: /Open run/ }));
    expect(open).toHaveBeenCalledWith("22222222-bbbb");
  });

  it("formats unfinished and malformed durations without inventing numbers", () => {
    expect(formatDuration(mk({ completed_at: null, status: "RUNNING" }))).toBe("in progress");
    expect(formatDuration(mk({ completed_at: "not a date" }))).toBe("unknown");
  });
});
