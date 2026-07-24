import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { RunHistoryPage } from "./RunHistoryPage";
import * as agentRunsApi from "../lib/api/agentRuns";
import * as workflowsApi from "../lib/api/workflows";
import type { RunListItem, WorkflowDetail } from "../types/agent";

vi.mock("../lib/api/agentRuns", () => ({
  listAgentRuns: vi.fn().mockResolvedValue({
    items: [],
    page: 1,
    page_size: 25,
    total: 0,
    has_more: false,
  }),
}));

vi.mock("../lib/api/workflows", () => ({
  getWorkflow: vi.fn(),
}));

function renderWithAuth() {
  const authValue: AuthContextValue = {
    user: { id: "u1", email: "test@test.com", full_name: "Test User", is_active: true },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };

  return render(
    <AuthContext.Provider value={authValue}>
      <MemoryRouter>
        <RunHistoryPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("RunHistoryPage", () => {
  it("renders the run history heading", () => {
    renderWithAuth();
    expect(screen.getByText("Run History")).toBeInTheDocument();
  });

  it("shows empty state message", async () => {
    renderWithAuth();
    expect(await screen.findByText("No agent runs yet.")).toBeInTheDocument();
  });

  it("has a refresh button", () => {
    renderWithAuth();
    expect(screen.getByRole("button", { name: "Refresh run history" })).toBeInTheDocument();
  });
});

function makeRun(overrides: Partial<RunListItem> = {}): RunListItem {
  return {
    run_id: "run-1",
    goal: "plan_freeform",
    status: "completed",
    subject: { subject_id: "freetext:abc", subject_type: "freetext", display_name: "Task" },
    started_at: "2026-01-01T10:00:00Z",
    completed_at: "2026-01-01T10:00:02Z",
    created_at: "2026-01-01T10:00:00Z",
    confidence_score: 0.9,
    workflow_id: null,
    workflow_stage: null,
    ...overrides,
  };
}

function makeWorkflow(overrides: Partial<WorkflowDetail> = {}): WorkflowDetail {
  return {
    workflow_id: "wf-1",
    title: "Add rate limiting",
    workflow_type: "planning",
    current_stage: "development",
    status: "in_progress",
    stages: [
      { stage: "planning", label: "Planning", status: "completed", run_id: "run-a" },
      { stage: "development", label: "Development", status: "pending", run_id: null },
      { stage: "testing", label: "Testing", status: "pending", run_id: null },
      { stage: "review", label: "Review", status: "pending", run_id: null },
    ],
    runs: [],
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-01T10:00:02Z",
    approved_by: null,
    ...overrides,
  };
}

describe("RunHistoryPage grouping", () => {
  it("groups a workflow's runs into one collapsed top-level row, real title from getWorkflow", async () => {
    vi.mocked(agentRunsApi.listAgentRuns).mockResolvedValue({
      items: [
        makeRun({
          run_id: "run-a",
          workflow_id: "wf-1",
          workflow_stage: "planning",
          subject: {
            subject_id: "freetext:x",
            subject_type: "freetext",
            display_name: "raw title",
          },
        }),
        makeRun({ run_id: "run-b", workflow_id: null }),
      ],
      page: 1,
      page_size: 25,
      total: 2,
      has_more: false,
    });
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());

    renderWithAuth();

    // The real workflow title (from getWorkflow), not the raw subject text.
    expect(await screen.findByText("Add rate limiting")).toBeInTheDocument();
    expect(screen.queryByText("raw title")).not.toBeInTheDocument();

    // The standalone run still appears as its own row.
    expect(await screen.findByText("Standalone Runs")).toBeInTheDocument();
    expect(screen.getByText("Task")).toBeInTheDocument();
  });

  it("expanding a workflow group reveals its stage runs", async () => {
    const user = userEvent.setup();
    vi.mocked(agentRunsApi.listAgentRuns).mockResolvedValue({
      items: [makeRun({ run_id: "run-a", workflow_id: "wf-1", workflow_stage: "planning" })],
      page: 1,
      page_size: 25,
      total: 1,
      has_more: false,
    });
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());

    renderWithAuth();
    const summary = await screen.findByText("Add rate limiting");
    const details = () => screen.getByText("Planning").closest("details");
    expect(details()).toHaveProperty("open", false);

    await user.click(summary);
    expect(details()).toHaveProperty("open", true);
  });

  it("shows a loading placeholder for a workflow group before getWorkflow resolves", async () => {
    vi.mocked(agentRunsApi.listAgentRuns).mockResolvedValue({
      items: [makeRun({ run_id: "run-a", workflow_id: "wf-1", workflow_stage: "planning" })],
      page: 1,
      page_size: 25,
      total: 1,
      has_more: false,
    });
    // Never resolves within this test — asserts the placeholder, not a stale title.
    vi.mocked(workflowsApi.getWorkflow).mockReturnValue(new Promise(() => {}));

    renderWithAuth();
    expect(await screen.findByText("Loading workflow…")).toBeInTheDocument();
  });
});
