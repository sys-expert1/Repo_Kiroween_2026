import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { api } from "./services/api";
import { ev, FakeEventSource } from "./test/fakes";
import type { Artifact, Health, Repository, Run } from "./types/revival";

vi.mock("./services/api", async (orig) => {
  const real = await orig<typeof import("./services/api")>();
  return {
    ...real,
    api: {
      health: vi.fn(), fixtures: vi.fn(), repositories: vi.fn(), importRepository: vi.fn(),
      startRun: vi.fn(), runs: vi.fn(), run: vi.fn(), cancel: vi.fn(), decide: vi.fn(),
      artifacts: vi.fn(), artifactText: vi.fn(), streamUrl: real.api.streamUrl,
    },
  };
});

const mocked = vi.mocked(api);
const health = (available: boolean): Health => ({
  status: "ok",
  sandbox: { mode: "auto", available, reason: available ? null : "Docker is unavailable: no daemon", host_fixture_exec: true },
});
const repo: Repository = { id: "repo-1", name: "legacy-python-cli", source_type: "fixture", location: "/f", created_at: "", signals: null };
const run = (over: Partial<Run> = {}): Run => ({
  id: "run-1", repository_id: "repo-1", mode: "REVIVE", status: "AWAITING_APPROVAL", current_stage: "REANIMATION",
  cancel_requested: false, roadmap_version: 1, patch_set_digest: "ab".repeat(32), pinned_commit: null, error: null,
  created_at: "", updated_at: "", completed_at: null, ...over,
});
const roadmap = {
  version: 1, repository_name: "legacy",
  tasks: [
    { id: "RT-001", title: "Upgrade requests", priority: "P1", category: "dependencies", evidence: ["F-001"], affected_files: ["requirements.txt"], proposed_action: "Raise the pin", risk: "medium", estimated_effort: "S", verification: "pip-audit clean", status: "proposed" },
    { id: "RT-002", title: "Add a Dockerfile", priority: "P3", category: "ci_docker", evidence: ["F-009"], affected_files: ["Dockerfile"], proposed_action: "Add one", risk: "low", estimated_effort: "S", verification: "build", status: "proposed" },
  ],
};
const arts: Artifact[] = [{ id: "a1", type: "roadmap_data", name: "roadmap.v1.json", checksum: "c", size: 10, created_at: "" }];

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
  Object.values(mocked).forEach((m) => typeof m === "function" && "mockReset" in m && m.mockReset());
  mocked.fixtures.mockResolvedValue([{ id: "legacy-python-cli", name: "Legacy Python CLI", description: "A demo", language: "python" }]);
  mocked.health.mockResolvedValue(health(true));
  mocked.artifacts.mockResolvedValue(arts);
  mocked.artifactText.mockResolvedValue(JSON.stringify(roadmap));
});
afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("starts empty with the tagline and the three intake paths", async () => {
    mocked.repositories.mockResolvedValue([]);
    render(<App />);
    expect(await screen.findByText("Build something wicked and git commit to the darkness.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Demo fixture/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Git URL/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Local path/ })).toBeInTheDocument();
    expect(screen.getByText("Legacy Python CLI")).toBeInTheDocument();
  });

  it("shows validation errors from the backend when an import is rejected", async () => {
    const { ApiError } = await import("./services/api");
    mocked.repositories.mockResolvedValue([]);
    mocked.importRepository.mockRejectedValue(new ApiError(422, "Path is outside the allowed roots."));
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: /Local path/ }));
    expect(screen.getByText(/resolves this path, not the browser/)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Path on the machine/), "/etc");
    await user.click(screen.getByRole("button", { name: /Validate and import/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Path is outside the allowed roots.");
  });

  it("warns that the Docker sandbox is unavailable and says what that means", async () => {
    mocked.health.mockResolvedValue(health(false));
    mocked.repositories.mockResolvedValue([]);
    render(<App />);
    expect(await screen.findByText("Docker sandbox unavailable")).toBeInTheDocument();
    expect(screen.getByText(/only for bundled demo fixtures/)).toBeInTheDocument();
  });

  it("discloses when code can be sent to a model provider, and stays quiet otherwise", async () => {
    mocked.repositories.mockResolvedValue([]);
    mocked.health.mockResolvedValue({ ...health(true), llm: { enabled: true, model: "openai/gpt-x" } });
    const { unmount } = render(<App />);
    expect(await screen.findByText("AI-assisted patches are on (openai/gpt-x)")).toBeInTheDocument();
    expect(screen.getByText(/never sent/)).toBeInTheDocument();
    unmount();
    mocked.health.mockResolvedValue({ ...health(true), llm: { enabled: false, model: null } });
    render(<App />);
    await screen.findByText(/Build something wicked/);
    expect(screen.queryByText(/AI-assisted patches are on/)).toBeNull();
  });

  it("reports an unreachable backend", async () => {
    mocked.health.mockRejectedValue(new Error("down"));
    mocked.repositories.mockRejectedValue(new Error("down"));
    render(<App />);
    expect(await screen.findByText("Backend unavailable")).toBeInTheDocument();
  });

  it("starts an Inspect run by default and never auto-selects Revive", async () => {
    mocked.repositories.mockResolvedValue([repo]);
    mocked.runs.mockResolvedValue([]);
    mocked.startRun.mockResolvedValue(run({ mode: "INSPECT", status: "QUEUED", roadmap_version: null, patch_set_digest: null }));
    mocked.run.mockResolvedValue(run({ mode: "INSPECT", status: "QUEUED", roadmap_version: null }));
    render(<App />);
    const start = await screen.findByRole("button", { name: /Start Inspect/ });
    expect(screen.getByLabelText("INSPECT")).toBeChecked();
    await userEvent.setup().click(start);
    expect(mocked.startRun).toHaveBeenCalledWith("repo-1", "INSPECT");
  });

  it("walks the approval gate: the run waits, the user reviews, and approve sends the awaited version", async () => {
    mocked.repositories.mockResolvedValue([repo]);
    mocked.runs.mockResolvedValue([run()]);
    mocked.run.mockResolvedValue(run());
    mocked.decide.mockResolvedValue(run({ status: "RUNNING" }));
    render(<App />);
    const user = userEvent.setup();
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    act(() => {
      FakeEventSource.last.emit(ev(1, "run_started"));
      FakeEventSource.last.emit(ev(2, "hitl_required", {
        stage: "REANIMATION", status: "AWAITING_APPROVAL", repository: "legacy-python-cli", source_commit: null,
        roadmap_version: 1, patch_set_digest: "ab".repeat(32), task_count: 2, files_affected: 2, security_fixes: 0,
      }));
    });
    expect(await screen.findByText("Your approval is required")).toBeInTheDocument();
    expect(screen.getAllByText("Approval required").length).toBeGreaterThan(0); // header chip and stepper
    expect(mocked.decide).not.toHaveBeenCalled(); // waiting does not approve anything

    await user.click(screen.getByRole("button", { name: /Review and decide/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("ab".repeat(32))).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /Approve and Revive/ }));
    expect(mocked.decide).toHaveBeenCalledWith("run-1", "approve", 1, {});
  });

  it("lets the user exclude tasks and submit an edit instead of approving", async () => {
    mocked.repositories.mockResolvedValue([repo]);
    mocked.runs.mockResolvedValue([run()]);
    mocked.run.mockResolvedValue(run());
    mocked.decide.mockResolvedValue(run({ roadmap_version: 2 }));
    render(<App />);
    const user = userEvent.setup();
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    act(() => {
      FakeEventSource.last.emit(ev(1, "hitl_required", {
        stage: "REANIMATION", status: "AWAITING_APPROVAL", roadmap_version: 1, patch_set_digest: "ab".repeat(32),
        task_count: 2, files_affected: 2,
      }));
    });
    await user.click(await screen.findByRole("tab", { name: /Revival Roadmap/ }));
    await user.click(await screen.findByLabelText(/Exclude RT-002/));
    await user.click(screen.getByRole("button", { name: /Review and decide/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("button", { name: /Approve and Revive/ })).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: /Submit edit \(1 excluded\)/ }));
    expect(mocked.decide).toHaveBeenCalledWith("run-1", "edit", 1, { excluded_task_ids: ["RT-002"] });
  });

  it("opens a past run from the history tab", async () => {
    const older = run({ id: "run-0", status: "COMPLETED", mode: "INSPECT", roadmap_version: null, created_at: "2026-11-18T09:00:00Z", completed_at: "2026-11-18T09:00:03Z" });
    mocked.repositories.mockResolvedValue([repo]);
    mocked.runs.mockResolvedValue([run({ status: "COMPLETED" }), older]);
    mocked.run.mockImplementation(async (id: string) => (id === "run-0" ? older : run({ status: "COMPLETED" })));
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: /History/ }));
    const buttons = await screen.findAllByRole("button", { name: /Open run/ });
    expect(buttons).toHaveLength(2);
    await user.click(buttons[1]!);
    await waitFor(() => expect(mocked.run).toHaveBeenCalledWith("run-0"));
    expect(FakeEventSource.last.url).toContain("run-0"); // its events are replayed from the backend log
    expect(screen.getByRole("tab", { name: /Overview/ })).toHaveAttribute("aria-selected", "true");
  });

  it("shows a failed run's reason and keeps the failed stage visible", async () => {
    mocked.repositories.mockResolvedValue([repo]);
    mocked.runs.mockResolvedValue([run({ status: "FAILED", current_stage: "CONSECRATION", error: "Verification failed and the working copy was rolled back" })]);
    mocked.run.mockResolvedValue(run({ status: "FAILED", current_stage: "CONSECRATION", error: "Verification failed and the working copy was rolled back" }));
    render(<App />);
    expect(await screen.findByText("This run failed")).toBeInTheDocument();
    expect(screen.getByText(/rolled back/)).toBeInTheDocument();
  });
});
