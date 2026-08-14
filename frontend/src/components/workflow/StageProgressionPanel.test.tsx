import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthContext, type AuthContextValue } from "../../app/auth-context";
import { StageProgressionPanel } from "./StageProgressionPanel";
import * as metricsApi from "../../lib/api/metrics";
import type { AgentStep, WorkflowStageInfo } from "../../types/agent";

// ---------------------------------------------------------------------------
// Regression tests for the Context Discovery confidence-semantics audit:
// this panel is the one place every stage's score sits side by side, which
// is exactly where a bare "Confidence" label — reused across five
// completely different stages, and once shared with Context Discovery's
// evidence-completeness score too — read as all of them being the same
// kind of measurement. Every stage now gets its own name for what it
// actually assessed, so a non-technical reader never has to guess.
// ---------------------------------------------------------------------------

function renderWithProviders(ui: React.ReactElement) {
  const auth: AuthContextValue = {
    user: {
      id: "u1",
      email: "t@t.com",
      full_name: "T",
      auth_provider: "local",
      role: "user",
      created_at: "2026-01-01T00:00:00Z",
    },
    token: "tok",
    isLoading: false,
    login: vi.fn(),
    loginWithToken: vi.fn(),
    logout: vi.fn(),
  };
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={auth}>{ui}</AuthContext.Provider>
    </QueryClientProvider>,
  );
}

function makeStep(score: number): AgentStep {
  return {
    step_id: "s1",
    agent_id: "x",
    status: "completed",
    confidence: { score, reasoning: "" },
    evidence: [],
    result: {},
    prompt_version: "1.0",
    output_ref: null,
    error_message: null,
    created_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:05:00Z",
  };
}

function mockNoUsage() {
  vi.spyOn(metricsApi, "getWorkflowLLMUsage").mockResolvedValue({
    workflow_id: "w1",
    total_cost_usd: 0,
    stages: [],
  });
}

describe("StageProgressionPanel", () => {
  it("gives every stage its own, distinctly-worded metric label — never a bare 'Confidence' reused across stages", () => {
    mockNoUsage();

    const stages: WorkflowStageInfo[] = [
      { stage: "context_discovery", label: "Context Discovery", status: "completed", run_id: "r1" },
      { stage: "planning", label: "Planning", status: "completed", run_id: "r2" },
      { stage: "development", label: "Development", status: "completed", run_id: "r3" },
      { stage: "testing", label: "Testing", status: "completed", run_id: "r4" },
      {
        stage: "documentation_planning",
        label: "Documentation Planning",
        status: "completed",
        run_id: "r5",
      },
      {
        stage: "engineering_review",
        label: "Engineering Review",
        status: "completed",
        run_id: "r6",
      },
    ] as unknown as WorkflowStageInfo[];
    const stepsByRunId = new Map<string, AgentStep>([
      ["r1", makeStep(0.83)],
      ["r2", makeStep(0.9)],
      ["r3", makeStep(0.9)],
      ["r4", makeStep(0.93)],
      ["r5", makeStep(0.8)],
      ["r6", makeStep(0.2)],
    ]);

    renderWithProviders(
      <StageProgressionPanel workflowId="w1" stages={stages} stepsByRunId={stepsByRunId} />,
    );

    // Every stage gets its own wording — no two stages share a label, and
    // none of the five execution stages falls back to bare "Confidence."
    expect(screen.getByText("Context completeness")).toBeInTheDocument();
    expect(screen.getByText("Plan confidence")).toBeInTheDocument();
    expect(screen.getByText("Implementation confidence")).toBeInTheDocument();
    expect(screen.getByText("Test confidence")).toBeInTheDocument();
    expect(screen.getByText("Documentation confidence")).toBeInTheDocument();
    expect(screen.getByText("Engineering confidence")).toBeInTheDocument();
    expect(screen.queryByText("Confidence", { selector: "span" })).not.toBeInTheDocument();

    // The section explains up front that these aren't all the same metric.
    expect(screen.getByText(/how much relevant information was gathered/)).toBeInTheDocument();
  });

  it("falls back to plain 'Confidence' only for a stage outside the known pipeline (e.g. the standalone PR review agent)", () => {
    mockNoUsage();
    const stages: WorkflowStageInfo[] = [
      { stage: "review", label: "Review", status: "completed", run_id: "r1" },
    ] as unknown as WorkflowStageInfo[];
    const stepsByRunId = new Map<string, AgentStep>([["r1", makeStep(0.7)]]);

    renderWithProviders(
      <StageProgressionPanel workflowId="w1" stages={stages} stepsByRunId={stepsByRunId} />,
    );

    expect(screen.getByText("Confidence", { selector: "span" })).toBeInTheDocument();
    expect(screen.queryByText("Context completeness")).not.toBeInTheDocument();
    expect(screen.queryByText("Engineering confidence")).not.toBeInTheDocument();
  });

  it("titles the section generically, not 'Confidence by stage', since the tiles are no longer all confidence", () => {
    mockNoUsage();
    const stages: WorkflowStageInfo[] = [
      { stage: "context_discovery", label: "Context Discovery", status: "completed", run_id: "r1" },
    ] as unknown as WorkflowStageInfo[];
    const stepsByRunId = new Map<string, AgentStep>([["r1", makeStep(0.83)]]);

    renderWithProviders(
      <StageProgressionPanel workflowId="w1" stages={stages} stepsByRunId={stepsByRunId} />,
    );

    expect(screen.getByText("Stage metrics")).toBeInTheDocument();
    expect(screen.queryByText("Confidence by stage")).not.toBeInTheDocument();
  });
});
