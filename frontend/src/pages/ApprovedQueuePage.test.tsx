import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { ApprovedQueuePage } from "./ApprovedQueuePage";
import * as workflowsApi from "../lib/api/workflows";
import * as agentRunsApi from "../lib/api/agentRuns";
import type {
  DevelopmentPlanResult,
  RunDetail,
  WorkflowListItem,
  WorkflowListResponse,
} from "../types/agent";

vi.mock("../lib/api/workflows", () => ({
  listWorkflows: vi.fn(),
}));

vi.mock("../lib/api/agentRuns", () => ({
  getAgentRun: vi.fn(),
}));

function renderWithAuth() {
  const authValue: AuthContextValue = {
    user: { id: "u1", email: "test@test.com", full_name: "Test User", auth_provider: "local", role: "user", created_at: "2026-01-01T00:00:00Z" },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    loginWithToken: vi.fn(),
    logout: vi.fn(),
  };

  return render(
    <AuthContext.Provider value={authValue}>
      <MemoryRouter>
        <ApprovedQueuePage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

function makeWorkflowItem(overrides: Partial<WorkflowListItem> = {}): WorkflowListItem {
  return {
    workflow_id: "wf-1",
    title: "Add rate limiting",
    workflow_type: "planning",
    current_stage: "engineering_review",
    status: "approved",
    stages: [
      { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
      { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
      { stage: "testing", label: "Testing", status: "completed", run_id: "run-3" },
      {
        stage: "engineering_review",
        label: "Engineering Review",
        status: "completed",
        run_id: "run-4",
      },
    ],
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-02T10:00:00Z",
    approved_by: "Jane Doe",
    version: 1,
    parent_workflow_id: null,
    ...overrides,
  };
}

function makeDevelopmentResult(
  overrides: Partial<DevelopmentPlanResult> = {},
): DevelopmentPlanResult {
  return {
    goal: "develop_change_plan",
    executive_summary: "Adds a rate limiter.",
    repositories: [{ name: "payment-service", owner: "acme", reason: "Hosts the API." }],
    components: [],
    dependencies: [],
    reusable_implementations: [],
    implementation_phases: [
      {
        order: 1,
        title: "Add limiter",
        description: "x",
        affected_components: [],
        estimated_complexity: "medium",
        depends_on_phases: [],
      },
    ],
    risks: [],
    recommendations: [],
    graph_context_used: true,
    ...overrides,
  };
}

function makeRun(result: DevelopmentPlanResult | null): RunDetail {
  return {
    run_id: "run-2",
    goal: "develop_change_plan",
    status: "completed",
    subject: { subject_id: "freetext:x", subject_type: "freetext", display_name: "x" },
    title: null,
    provider: null,
    user: null,
    repository: null,
    model: null,
    error_message: null,
    started_at: "2026-01-01T10:00:00Z",
    completed_at: "2026-01-01T10:01:00Z",
    created_at: "2026-01-01T10:00:00Z",
    steps: result
      ? [
          {
            step_id: "s1",
            agent_id: "development",
            status: "completed",
            confidence: { score: 0.9, reasoning: "" },
            evidence: [],
            result: result as unknown as Record<string, unknown>,
            prompt_version: "1.0",
            output_ref: null,
            error_message: null,
            latency_ms: 1000,
            created_at: null,
            completed_at: null,
          },
        ]
      : [],
    workflow_id: "wf-1",
    workflow_stage: "development",
    previous_run_id: null,
  };
}

function listResponse(items: WorkflowListItem[], total = items.length): WorkflowListResponse {
  return { items, page: 1, page_size: 20, total, has_more: false };
}

describe("ApprovedQueuePage", () => {
  it("renders a row with repository, scope, approver, and approval date derived from real data", async () => {
    vi.mocked(workflowsApi.listWorkflows).mockResolvedValue(listResponse([makeWorkflowItem()]));
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun(makeDevelopmentResult()));

    renderWithAuth();

    expect(await screen.findByText("Add rate limiting")).toBeInTheDocument();
    expect(await screen.findByText("payment-service")).toBeInTheDocument();
    expect(screen.getByText("1 phase · medium")).toBeInTheDocument();
    expect(screen.getByText("Jane Doe")).toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();
  });

  it("shows a mixed complexity label when phases disagree", async () => {
    vi.mocked(workflowsApi.listWorkflows).mockResolvedValue(listResponse([makeWorkflowItem()]));
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(
      makeRun(
        makeDevelopmentResult({
          implementation_phases: [
            {
              order: 1,
              title: "A",
              description: "",
              affected_components: [],
              estimated_complexity: "low",
              depends_on_phases: [],
            },
            {
              order: 2,
              title: "B",
              description: "",
              affected_components: [],
              estimated_complexity: "high",
              depends_on_phases: [],
            },
          ],
        }),
      ),
    );

    renderWithAuth();

    expect(await screen.findByText("2 phases · mixed")).toBeInTheDocument();
  });

  it("falls back to '—' when the Development stage has no result yet", async () => {
    vi.mocked(workflowsApi.listWorkflows).mockResolvedValue(
      listResponse([makeWorkflowItem({ approved_by: null })]),
    );
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun(null));

    renderWithAuth();

    await screen.findByText("Add rate limiting");
    const dashes = await screen.findAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(3); // repository, scope, approved_by
  });

  it("shows the Start Implementation control as a disabled, non-interactive placeholder", async () => {
    vi.mocked(workflowsApi.listWorkflows).mockResolvedValue(listResponse([makeWorkflowItem()]));
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun(makeDevelopmentResult()));

    renderWithAuth();

    const placeholder = await screen.findByText("Start Implementation");
    expect(placeholder.closest("[aria-disabled]")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Coming soon")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Start Implementation/ })).not.toBeInTheDocument();
  });

  it("shows an empty state when there are no approved blueprints", async () => {
    vi.mocked(workflowsApi.listWorkflows).mockResolvedValue(listResponse([], 0));

    renderWithAuth();

    expect(await screen.findByText("No approved blueprints yet.")).toBeInTheDocument();
    expect(await screen.findByText("0 approved blueprints")).toBeInTheDocument();
  });

  it("paginates by calling listWorkflows again with the next page", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.listWorkflows).mockResolvedValue({
      ...listResponse([makeWorkflowItem()], 25),
      has_more: true,
    });
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun(makeDevelopmentResult()));

    renderWithAuth();

    await screen.findByText("Add rate limiting");
    const nextButton = screen.getByRole("button", { name: "Next page" });
    await user.click(nextButton);

    await waitFor(() =>
      expect(workflowsApi.listWorkflows).toHaveBeenCalledWith(
        "test-token",
        expect.objectContaining({ page: 2 }),
        expect.anything(),
      ),
    );
  });
});
