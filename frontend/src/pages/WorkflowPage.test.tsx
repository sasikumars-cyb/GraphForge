import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "jest-axe";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { StageNavigation } from "../components/workflow/StageNavigation";
import { NewWorkflowPage, WorkflowPage } from "./WorkflowPage";
import type { AgentStep, RunDetail, WorkflowDetail } from "../types/agent";
import * as workflowsApi from "../lib/api/workflows";
import * as agentRunsApi from "../lib/api/agentRuns";
import { ApiError } from "../lib/api/client";

// Mock workflows API
vi.mock("../lib/api/workflows", () => ({
  createWorkflow: vi.fn(),
  getWorkflow: vi.fn(),
  continueWorkflow: vi.fn(),
  listWorkflows: vi.fn(),
  approveWorkflow: vi.fn(),
  rejectWorkflow: vi.fn(),
}));

vi.mock("../lib/api/agentRuns", () => ({
  getAgentRun: vi.fn(),
}));

// StageProgressionPanel (rendered once any stage has completed) reads a
// workflow's LLM usage via TanStack Query — resolved empty by default so
// tests that don't care about cost data aren't left with an unhandled
// rejection; the panel already degrades to duration-only when this has no
// stages for the given workflow.
vi.mock("../lib/api/metrics", () => ({
  getWorkflowLLMUsage: vi.fn().mockResolvedValue({ workflow_id: "wf-1", workflow_title: "", stages: [] }),
}));

function renderWithAuth(ui: React.ReactElement, authValue?: Partial<AuthContextValue>) {
  const defaultAuth: AuthContextValue = {
    user: {
      id: "u1",
      email: "test@test.com",
      full_name: "Test User",
      auth_provider: "local",
      role: "user",
      created_at: "2026-01-01T00:00:00Z",
    },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    loginWithToken: vi.fn(),
    logout: vi.fn(),
    ...authValue,
  };

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  return render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={defaultAuth}>
        <MemoryRouter>{ui}</MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

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
    expect(screen.getByText("Describe what you want built")).toBeInTheDocument();
  });

  it("shows Planning Workflow as selected/recommended and Implementation as disabled", () => {
    renderWithAuth(<NewWorkflowPage />);
    const planningOption = screen.getByRole("button", { name: /Planning Workflow/ });
    expect(planningOption).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Recommended")).toBeInTheDocument();

    const executionOption = screen.getByText("Implementation Workflow").closest("[aria-disabled]");
    expect(executionOption).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Coming soon")).toBeInTheDocument();
    // No leaked backend terminology anywhere on this page.
    expect(screen.queryByText(/Auto Execution/)).not.toBeInTheDocument();
  });

  it("creates the workflow with workflow_type 'planning'", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.createWorkflow).mockResolvedValue({
      workflow_id: "wf-new",
      run_id: "run-new",
      stage: "planning",
      status: "completed",
    });
    renderWithAuth(<NewWorkflowPage />);

    await user.type(
      screen.getByLabelText("What's the engineering objective?"),
      "Add rate limiting",
    );
    await user.click(screen.getByRole("button", { name: "Start SDLC workflow" }));

    await waitFor(() =>
      expect(workflowsApi.createWorkflow).toHaveBeenCalledWith("test-token", {
        title: "Add rate limiting",
        workflow_type: "planning",
      }),
    );
  });

  it("renders the text input", () => {
    renderWithAuth(<NewWorkflowPage />);
    expect(screen.getByLabelText("What's the engineering objective?")).toBeInTheDocument();
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
    const textarea = screen.getByLabelText("What's the engineering objective?");
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
      "What's the engineering objective?",
    ) as HTMLTextAreaElement;
    expect(textarea.value).toBe("Implement JWT authentication across all microservices");
  });
});

describe("WorkflowPage", () => {
  function renderWorkflowPage(workflowId = "wf-1") {
    const defaultAuth: AuthContextValue = {
      user: {
        id: "u1",
        email: "test@test.com",
        full_name: "Test User",
        auth_provider: "local",
        role: "user",
        created_at: "2026-01-01T00:00:00Z",
      },
      token: "test-token",
      isLoading: false,
      login: vi.fn(),
      loginWithToken: vi.fn(),
      logout: vi.fn(),
    };
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={defaultAuth}>
          <MemoryRouter initialEntries={[`/workflows/${workflowId}`]}>
            <Routes>
              <Route path="/workflows/:workflowId" element={<WorkflowPage />} />
            </Routes>
          </MemoryRouter>
        </AuthContext.Provider>
      </QueryClientProvider>,
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
      title: null,
      provider: null,
      user: null,
      repository: null,
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
      original_prompt: "Implement JWT auth across the public API",
      workflow_type: "planning",
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
      approved_by: null,
      version: 1,
      parent_workflow_id: null,
      refinement_note: null,
      ...overrides,
    };
  }

  it("renders the header, pipeline, and the selected run's evidence once loaded", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());

    renderWorkflowPage();

    expect(await screen.findByText("Implement JWT auth")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Planning: Complete" })).toBeInTheDocument();
    // Evidence lives in the Summary/Evidence/Log/JSON tab strip on
    // StageResultPanel, inside its own collapsed Evidence Trail card — it's
    // no longer ambiently visible via AgentActivityFeed, so reaching it now
    // takes an explicit tab click plus expanding the trail itself.
    await userEvent.click(await screen.findByRole("tab", { name: "Evidence" }));
    await userEvent.click(await screen.findByRole("button", { name: "Expand" }));
    expect(await screen.findByText("Found 3 repositories.")).toBeInTheDocument();
  });

  it("shows the live progress checklist for a running stage that has one", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(
      makeWorkflow({
        current_stage: "development",
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          {
            stage: "development",
            label: "Development",
            status: "running",
            run_id: "run-2",
            live_progress: {
              iteration: 2,
              max_iterations: 8,
              steps: [
                { label: "Parsing the request", status: "done" },
                { label: "Investigating the architecture", status: "active" },
              ],
            },
          },
          { stage: "testing", label: "Testing", status: "pending", run_id: null },
          { stage: "review", label: "Review", status: "pending", run_id: null },
        ],
      }),
    );
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());

    renderWorkflowPage();

    expect(await screen.findByText("Implement JWT auth")).toBeInTheDocument();
    expect(await screen.findByText("Investigating")).toBeInTheDocument();
    expect(screen.getByText("Parsing the request")).toBeInTheDocument();
    expect(screen.getByText("Investigating the architecture")).toBeInTheDocument();
    expect(screen.getByText("step 2 of 8")).toBeInTheDocument();
  });

  it("shows no live progress checklist when the running stage has none yet", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(
      makeWorkflow({
        current_stage: "development",
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          {
            stage: "development",
            label: "Development",
            status: "running",
            run_id: "run-2",
            live_progress: null,
          },
          { stage: "testing", label: "Testing", status: "pending", run_id: null },
          { stage: "review", label: "Review", status: "pending", run_id: null },
        ],
      }),
    );
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());

    renderWorkflowPage();

    expect(await screen.findByText("Implement JWT auth")).toBeInTheDocument();
    expect(screen.queryByText("Investigating")).not.toBeInTheDocument();
  });

  it("has no detectable accessibility violations once loaded (KAN-38)", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());

    const { container } = renderWorkflowPage();
    await screen.findByText("Implement JWT auth");

    expect(await axe(container)).toHaveNoViolations();
  });

  it("shows the planning stage's artifacts in the Summary tab (active by default)", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());

    renderWorkflowPage();

    // Summary tab is default when no blueprint is present — content is immediately visible.
    expect(await screen.findByRole("tab", { name: /Summary/ })).toBeInTheDocument();
    expect(await screen.findByText("Implementation Steps")).toBeInTheDocument();
  });

  it("does not show a Summary tab for the review stage", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(
      makeWorkflow({
        current_stage: "review",
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          { stage: "review", label: "Review", status: "completed", run_id: "run-4" },
        ],
        runs: [
          {
            run_id: "run-4",
            goal: "review_pr",
            status: "completed",
            workflow_stage: "review",
            confidence_score: 0.8,
            started_at: "2026-01-01T10:00:00Z",
            completed_at: "2026-01-01T10:00:02Z",
            created_at: "2026-01-01T10:00:00Z",
          },
        ],
      }),
    );
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(
      makeRun({ run_id: "run-4", workflow_stage: "review" }),
    );

    renderWorkflowPage();

    await screen.findByText("Implement JWT auth");
    expect(screen.queryByRole("tab", { name: /Summary/ })).not.toBeInTheDocument();
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

  it("shows a confirm prompt (not a silent no-op) when continueWorkflow rejects with context_discovery_partial", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());
    vi.mocked(workflowsApi.continueWorkflow).mockRejectedValue(
      new ApiError(409, "context_discovery_partial", "Context Discovery reached 78% confidence."),
    );

    renderWorkflowPage();
    await screen.findByRole("button", { name: /Approve & Continue/ });
    await user.click(screen.getByRole("button", { name: /Approve & Continue/ }));

    expect(await screen.findByText("Continue with incomplete context?")).toBeInTheDocument();
    expect(screen.getByText(/78% confidence/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue anyway" })).toBeInTheDocument();
  });

  it("retries with acknowledge_partial=true when Continue anyway is clicked", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());
    vi.mocked(workflowsApi.continueWorkflow).mockRejectedValueOnce(
      new ApiError(409, "context_discovery_partial", "Context Discovery reached 78% confidence."),
    );
    vi.mocked(workflowsApi.continueWorkflow).mockResolvedValueOnce({
      workflow_id: "wf-1",
      run_id: "run-2",
      stage: "development",
      status: "completed",
    });

    renderWorkflowPage();
    await screen.findByRole("button", { name: /Approve & Continue/ });
    await user.click(screen.getByRole("button", { name: /Approve & Continue/ }));
    await screen.findByText("Continue with incomplete context?");
    await user.click(screen.getByRole("button", { name: "Continue anyway" }));

    await waitFor(() =>
      expect(workflowsApi.continueWorkflow).toHaveBeenLastCalledWith(
        "test-token",
        "wf-1",
        undefined,
        true,
      ),
    );
    expect(screen.queryByText("Continue with incomplete context?")).not.toBeInTheDocument();
  });

  it("dismisses the partial-confirm prompt when Cancel is clicked, without calling continueWorkflow again", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());
    vi.mocked(workflowsApi.continueWorkflow).mockRejectedValue(
      new ApiError(409, "context_discovery_partial", "Context Discovery reached 78% confidence."),
    );

    renderWorkflowPage();
    await screen.findByRole("button", { name: /Approve & Continue/ });
    await user.click(screen.getByRole("button", { name: /Approve & Continue/ }));
    await screen.findByText("Continue with incomplete context?");
    const callsBeforeCancel = vi.mocked(workflowsApi.continueWorkflow).mock.calls.length;

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByText("Continue with incomplete context?")).not.toBeInTheDocument();
    expect(workflowsApi.continueWorkflow).toHaveBeenCalledTimes(callsBeforeCancel);
  });

  it("shows a visible error banner (not a silent no-op) when continueWorkflow rejects with an unexpected error", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());
    vi.mocked(workflowsApi.continueWorkflow).mockRejectedValue(
      new ApiError(500, "internal_error", "Something went wrong on the server."),
    );

    renderWorkflowPage();
    await screen.findByRole("button", { name: /Approve & Continue/ });
    await user.click(screen.getByRole("button", { name: /Approve & Continue/ }));

    expect(await screen.findByText("Something went wrong on the server.")).toBeInTheDocument();
  });

  it("clears a stale partial-confirm prompt once polling shows the stage is no longer pending", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValueOnce(makeWorkflow());
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());
    vi.mocked(workflowsApi.continueWorkflow).mockRejectedValue(
      new ApiError(409, "context_discovery_partial", "Context Discovery reached 78% confidence."),
    );

    renderWorkflowPage();
    await screen.findByRole("button", { name: /Approve & Continue/ });
    await user.click(screen.getByRole("button", { name: /Approve & Continue/ }));
    await screen.findByText("Continue with incomplete context?");

    // Simulate another actor (another tab, or a direct API call) having
    // already moved the workflow's planning stage off "pending" by the
    // time this tab's next poll lands (POLL_INTERVAL_MS = 2500) — real
    // timers, matching every other test in this file; the extended
    // waitFor timeout is what accommodates the real interval.
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(
      makeWorkflow({
        current_stage: "planning",
        stages: [
          { stage: "planning", label: "Planning", status: "failed", run_id: "run-2" },
          { stage: "development", label: "Development", status: "pending", run_id: null },
        ],
      }),
    );

    await waitFor(
      () => expect(screen.queryByText("Continue with incomplete context?")).not.toBeInTheDocument(),
      { timeout: 4000 },
    );
  }, 6000);

  it("shows the failure banner (not the approval banner) when the current stage failed", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(
      makeWorkflow({
        current_stage: "testing",
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
          { stage: "testing", label: "Testing", status: "failed", run_id: "run-3" },
          { stage: "review", label: "Review", status: "pending", run_id: null },
        ],
        runs: [
          {
            run_id: "run-3",
            goal: "plan_tests",
            status: "failed",
            workflow_stage: "testing",
            confidence_score: null,
            started_at: "2026-01-01T10:00:00Z",
            completed_at: "2026-01-01T10:00:02Z",
            created_at: "2026-01-01T10:00:00Z",
          },
        ],
      }),
    );
    vi.mocked(agentRunsApi.getAgentRun).mockImplementation((_token, runId) =>
      Promise.resolve(
        makeRun({
          run_id: runId,
          workflow_stage: "testing",
          status: "failed",
          error_message: "Neo4j connection refused",
        }),
      ),
    );

    renderWorkflowPage();

    expect(await screen.findByText(/agent hit an error/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry Stage/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve & Continue/ })).not.toBeInTheDocument();
    // The raw error appears in the failure banner and in the Artifacts
    // card's own error disclosure — both must sit behind a "View error
    // details" toggle, never shown as part of the main message.
    const rawErrors = screen.getAllByText("Neo4j connection refused");
    expect(rawErrors.length).toBeGreaterThan(0);
    for (const el of rawErrors) {
      expect(el.closest("details")).not.toBeNull();
    }
    expect(screen.getAllByText("View error details").length).toBe(rawErrors.length);
  });

  it("retries by calling continueWorkflow again when Retry Stage is clicked", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(
      makeWorkflow({
        current_stage: "testing",
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          { stage: "testing", label: "Testing", status: "failed", run_id: "run-3" },
        ],
      }),
    );
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(
      makeRun({ status: "failed", error_message: "boom" }),
    );
    vi.mocked(workflowsApi.continueWorkflow).mockResolvedValue({
      workflow_id: "wf-1",
      run_id: "run-3b",
      stage: "testing",
      status: "completed",
    });

    renderWorkflowPage();
    await screen.findByRole("button", { name: /Retry Stage/ });
    await user.click(screen.getByRole("button", { name: /Retry Stage/ }));

    await waitFor(() =>
      expect(workflowsApi.continueWorkflow).toHaveBeenCalledWith("test-token", "wf-1"),
    );
  });

  it("shows the WorkflowApprovalBanner once Engineering Review completes and calls approveWorkflow on Approve", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(
      makeWorkflow({
        status: "awaiting_approval",
        current_stage: "engineering_review",
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
      }),
    );
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(
      makeRun({ run_id: "run-4", workflow_stage: "engineering_review" }),
    );
    vi.mocked(workflowsApi.approveWorkflow).mockResolvedValue({
      workflow_id: "wf-1",
      status: "approved",
    });

    renderWorkflowPage();

    expect(await screen.findByText(/Engineering Review is complete/)).toBeInTheDocument();
    const approveButton = screen.getByRole("button", { name: /Approve Blueprint/ });
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();

    await user.click(approveButton);

    await waitFor(() =>
      expect(workflowsApi.approveWorkflow).toHaveBeenCalledWith("test-token", "wf-1"),
    );
  });

  it("calls rejectWorkflow when Reject is clicked on the WorkflowApprovalBanner", async () => {
    const user = userEvent.setup();
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(
      makeWorkflow({
        status: "awaiting_approval",
        current_stage: "engineering_review",
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          {
            stage: "engineering_review",
            label: "Engineering Review",
            status: "completed",
            run_id: "run-4",
          },
        ],
      }),
    );
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(
      makeRun({ run_id: "run-4", workflow_stage: "engineering_review" }),
    );
    vi.mocked(workflowsApi.rejectWorkflow).mockResolvedValue({
      workflow_id: "wf-1",
      status: "rejected",
    });

    renderWorkflowPage();

    const rejectButton = await screen.findByRole("button", { name: "Reject" });
    await user.click(rejectButton);

    await waitFor(() =>
      expect(workflowsApi.rejectWorkflow).toHaveBeenCalledWith("test-token", "wf-1"),
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

  it("shows the workflow summary hero, worded for approval, once the blueprint is approved", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(
      makeWorkflow({
        status: "approved",
        current_stage: "engineering_review",
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          {
            stage: "engineering_review",
            label: "Engineering Review",
            status: "completed",
            run_id: "run-4",
          },
        ],
      }),
    );
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(
      makeRun({ run_id: "run-4", workflow_stage: "engineering_review" }),
    );

    renderWorkflowPage();

    expect(await screen.findByText("Blueprint Approved")).toBeInTheDocument();
    expect(screen.queryByText("Workflow Complete")).not.toBeInTheDocument();
  });

  it("does not show the summary hero while still awaiting a blueprint decision", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockResolvedValue(
      makeWorkflow({
        status: "awaiting_approval",
        current_stage: "engineering_review",
        stages: [
          {
            stage: "engineering_review",
            label: "Engineering Review",
            status: "completed",
            run_id: "run-4",
          },
        ],
      }),
    );
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(
      makeRun({ run_id: "run-4", workflow_stage: "engineering_review" }),
    );

    renderWorkflowPage();

    await screen.findByText(/Engineering Review is complete/);
    expect(screen.queryByText("Blueprint Approved")).not.toBeInTheDocument();
    expect(screen.queryByText("Workflow Complete")).not.toBeInTheDocument();
  });

  it("shows an error state when the workflow fails to load", async () => {
    vi.mocked(workflowsApi.getWorkflow).mockRejectedValue(new Error("Workflow not found."));

    renderWorkflowPage();

    expect(await screen.findByText("Workflow not found.")).toBeInTheDocument();
  });
});
