import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { WorkflowTimeline } from "../components/workflow/WorkflowTimeline";
import { StageNavigation } from "../components/workflow/StageNavigation";
import { NewWorkflowPage, WorkflowPage } from "./WorkflowPage";
import type { AgentStep, RunDetail, WorkflowDetail, WorkflowStageInfo } from "../types/agent";
import * as workflowsApi from "../lib/api/workflows";
import * as agentRunsApi from "../lib/api/agentRuns";

// Mock workflows API
vi.mock("../lib/api/workflows", () => ({
  createWorkflow: vi.fn(),
  getWorkflow: vi.fn(),
  continueWorkflow: vi.fn(),
  listWorkflows: vi.fn(),
}));

vi.mock("../lib/api/agentRuns", () => ({
  getAgentRun: vi.fn(),
}));

function renderWithAuth(ui: React.ReactElement, authValue?: Partial<AuthContextValue>) {
  const defaultAuth: AuthContextValue = {
    user: { id: "u1", email: "test@test.com", full_name: "Test User", is_active: true },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    ...authValue,
  };

  return render(
    <AuthContext.Provider value={defaultAuth}>
      <MemoryRouter>{ui}</MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("WorkflowTimeline", () => {
  const stages: WorkflowStageInfo[] = [
    { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
    { stage: "development", label: "Development", status: "running", run_id: "run-2" },
    { stage: "testing", label: "Testing", status: "pending", run_id: null },
    { stage: "review", label: "Review", status: "pending", run_id: null },
  ];

  it("renders all stage labels", () => {
    renderWithAuth(<WorkflowTimeline stages={stages} currentStage="development" />);
    expect(screen.getByText("Planning")).toBeInTheDocument();
    expect(screen.getByText("Development")).toBeInTheDocument();
    expect(screen.getByText("Testing")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
  });

  it("renders completed stage as a link", () => {
    renderWithAuth(<WorkflowTimeline stages={stages} currentStage="development" />);
    const link = screen.getByRole("link", { name: "Planning: completed" });
    expect(link).toHaveAttribute("href", "/runs/run-1");
  });

  it("renders pending stage without a link", () => {
    renderWithAuth(<WorkflowTimeline stages={stages} currentStage="development" />);
    expect(screen.queryByRole("link", { name: "Testing: pending" })).not.toBeInTheDocument();
  });
});

describe("StageNavigation", () => {
  it("shows continue button for development stage", () => {
    const onContinue = vi.fn();
    renderWithAuth(
      <StageNavigation nextStage="development" isSubmitting={false} onContinue={onContinue} />,
    );
    expect(screen.getByRole("button", { name: "Continue to Development" })).toBeInTheDocument();
  });

  it("shows testing button text", () => {
    renderWithAuth(
      <StageNavigation nextStage="testing" isSubmitting={false} onContinue={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Generate Test Plan" })).toBeInTheDocument();
  });

  it("shows review button text", () => {
    renderWithAuth(
      <StageNavigation nextStage="review" isSubmitting={false} onContinue={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Start Review" })).toBeInTheDocument();
  });

  it("shows completed message when workflow is done", () => {
    renderWithAuth(
      <StageNavigation nextStage="completed" isSubmitting={false} onContinue={vi.fn()} />,
    );
    expect(screen.getByText(/All SDLC stages complete/)).toBeInTheDocument();
  });

  it("disables button when submitting", () => {
    renderWithAuth(
      <StageNavigation nextStage="development" isSubmitting={true} onContinue={vi.fn()} />,
    );
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("calls onContinue when clicked", async () => {
    const user = userEvent.setup();
    const onContinue = vi.fn();
    renderWithAuth(
      <StageNavigation nextStage="development" isSubmitting={false} onContinue={onContinue} />,
    );
    await user.click(screen.getByRole("button"));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });
});

describe("NewWorkflowPage", () => {
  it("renders the page heading", () => {
    renderWithAuth(<NewWorkflowPage />);
    expect(screen.getByText("New SDLC Workflow")).toBeInTheDocument();
  });

  it("renders the text input", () => {
    renderWithAuth(<NewWorkflowPage />);
    expect(
      screen.getByLabelText("What engineering task do you want to deliver?"),
    ).toBeInTheDocument();
  });

  it("renders example buttons", () => {
    renderWithAuth(<NewWorkflowPage />);
    expect(
      screen.getByText("Implement JWT authentication across all microservices"),
    ).toBeInTheDocument();
  });

  it("disables start button when input is empty", () => {
    renderWithAuth(<NewWorkflowPage />);
    const button = screen.getByRole("button", { name: "Start SDLC workflow" });
    expect(button).toBeDisabled();
  });

  it("enables start button when input has text", async () => {
    const user = userEvent.setup();
    renderWithAuth(<NewWorkflowPage />);
    const textarea = screen.getByLabelText("What engineering task do you want to deliver?");
    await user.type(textarea, "Test task");
    const button = screen.getByRole("button", { name: "Start SDLC workflow" });
    expect(button).toBeEnabled();
  });

  it("fills textarea when example is clicked", async () => {
    const user = userEvent.setup();
    renderWithAuth(<NewWorkflowPage />);
    const example = screen.getByText("Implement JWT authentication across all microservices");
    await user.click(example);
    const textarea = screen.getByLabelText(
      "What engineering task do you want to deliver?",
    ) as HTMLTextAreaElement;
    expect(textarea.value).toBe("Implement JWT authentication across all microservices");
  });
});

describe("WorkflowPage", () => {
  function renderWorkflowPage(workflowId = "wf-1") {
    const defaultAuth: AuthContextValue = {
      user: { id: "u1", email: "test@test.com", full_name: "Test User", is_active: true },
      token: "test-token",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    };
    return render(
      <AuthContext.Provider value={defaultAuth}>
        <MemoryRouter initialEntries={[`/workflows/${workflowId}`]}>
          <Routes>
            <Route path="/workflows/:workflowId" element={<WorkflowPage />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>,
    );
  }

  function makeRun(overrides: Partial<RunDetail> = {}): RunDetail {
    const step: AgentStep = {
      step_id: "step-1",
      agent_id: "planning",
      status: "completed",
      confidence: { score: 0.85, reasoning: "Grounded in graph data." },
      evidence: [{ kind: "tool_call", reference: "get_repos", summary: "Found 3 repositories." }],
      result: {
        executive_summary: "A plan.",
        implementation_steps: [{ order: 1, description: "x" }],
      },
      prompt_version: "1.0",
      output_ref: null,
      error_message: null,
      latency_ms: 2000,
      created_at: "2026-01-01T10:00:00Z",
      completed_at: "2026-01-01T10:00:02Z",
    };
    return {
      run_id: "run-1",
      goal: "plan_freeform",
      status: "completed",
      subject: {
        subject_id: "freetext:abc",
        subject_type: "freetext",
        display_name: "Implement JWT auth",
      },
      model: null,
      error_message: null,
      started_at: "2026-01-01T10:00:00Z",
      completed_at: "2026-01-01T10:00:02Z",
      created_at: "2026-01-01T10:00:00Z",
      steps: [step],
      workflow_id: "wf-1",
      workflow_stage: "planning",
      previous_run_id: null,
      ...overrides,
    };
  }

  function makeWorkflow(overrides: Partial<WorkflowDetail> = {}): WorkflowDetail {
    return {
      workflow_id: "wf-1",
      title: "Implement JWT auth",
      current_stage: "development",
      status: "in_progress",
      stages: [
        { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
        { stage: "development", label: "Development", status: "pending", run_id: null },
        { stage: "testing", label: "Testing", status: "pending", run_id: null },
        { stage: "review", label: "Review", status: "pending", run_id: null },
      ],
      runs: [
        {
          run_id: "run-1",
          goal: "plan_freeform",
          status: "completed",
          workflow_stage: "planning",
          confidence_score: 0.85,
          started_at: "2026-01-01T10:00:00Z",
          completed_at: "2026-01-01T10:00:02Z",
          created_at: "2026-01-01T10:00:00Z",
        },
      ],
      created_at: "2026-01-01T10:00:00Z",
      updated_at: "2026-01-01T10:00:02Z",
      ...overrides,
    };
  }

  it("renders the header, pipeline, and activity feed once loaded", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());

    renderWorkflowPage();

    expect(await screen.findByText("Implement JWT auth")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Planning: Complete" })).toBeInTheDocument();
    // The same real evidence line legitimately appears in both the
    // Activity Feed and the Evidence Trail panel.
    expect(screen.getAllByText("Found 3 repositories.").length).toBeGreaterThan(0);
  });

  it("reveals the Workflow Replay panel only after the toggle is clicked", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());

    renderWorkflowPage();

    const toggle = await screen.findByRole("button", { name: "Replay Execution" });
    expect(screen.queryByRole("slider", { name: "Replay position" })).not.toBeInTheDocument();

    await user.click(toggle);
    expect(screen.getByRole("slider", { name: "Replay position" })).toBeInTheDocument();
  });

  it("shows the approval gate once a stage completes and the workflow is still in progress", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());

    renderWorkflowPage();

    expect(await screen.findByText(/is complete\. Review its output above/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve & Continue/ })).toBeInTheDocument();
  });

  it("calls continueWorkflow when Approve & Continue is clicked", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());
    vi.mocked(workflowsApi.continueWorkflow).mockResolvedValue({
      workflow_id: "wf-1",
      run_id: "run-2",
      stage: "development",
      status: "completed",
    });

    renderWorkflowPage();
    await screen.findByRole("button", { name: /Approve & Continue/ });
    await user.click(screen.getByRole("button", { name: /Approve & Continue/ }));

    await waitFor(() =>
      expect(workflowsApi.continueWorkflow).toHaveBeenCalledWith("test-token", "wf-1"),
    );
  });

  it("shows the workflow summary hero once the workflow is completed", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(
      makeWorkflow({
        status: "completed",
        current_stage: "completed",
        stages: [{ stage: "planning", label: "Planning", status: "completed", run_id: "run-1" }],
      }),
    );
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());

    renderWorkflowPage();

    expect(await screen.findByText("Workflow Complete")).toBeInTheDocument();
    // The approval gate must not appear once the workflow has finished.
    expect(screen.queryByRole("button", { name: /Approve & Continue/ })).not.toBeInTheDocument();
  });

  it("shows an error state when the workflow fails to load", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockRejectedValue(new Error("Workflow not found."));

    renderWorkflowPage();

    expect(await screen.findByText("Workflow not found.")).toBeInTheDocument();
  });
});
