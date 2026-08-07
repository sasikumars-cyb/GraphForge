import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { PlanningPage } from "./PlanningPage";
import * as agentRunsApi from "../lib/api/agentRuns";
import type { RunDetail } from "../types/agent";

// PlanningConfidencePanel reads live connection state from /system/status
// via TanStack Query, so this page now needs a QueryClient the way the real
// app provides one in App.tsx. Retries off so a rejected query fails fast
// instead of stalling the test.
vi.mock("../lib/api/system", () => ({ getSystemStatus: vi.fn().mockResolvedValue({ connections: [] }) }));

// Mock the API module
vi.mock("../lib/api/agentRuns", () => ({
  createAgentRun: vi.fn(),
  getAgentRun: vi.fn(),
}));

function renderWithAuth(authValue?: Partial<AuthContextValue>) {
  const defaultAuth: AuthContextValue = {
    user: { id: "u1", email: "test@test.com", full_name: "Test User", auth_provider: "local", role: "user", created_at: "2026-01-01T00:00:00Z" },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    loginWithToken: vi.fn(),
    logout: vi.fn(),
    ...authValue,
  };

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={defaultAuth}>
        <MemoryRouter>
          <PlanningPage />
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

describe("PlanningPage", () => {
  it("renders the planning assistant heading", () => {
    renderWithAuth();
    expect(screen.getByText("Planning Assistant")).toBeInTheDocument();
  });

  it("renders the text input", () => {
    renderWithAuth();
    expect(screen.getByLabelText("What would you like to plan?")).toBeInTheDocument();
  });

  it("renders example buttons", () => {
    renderWithAuth();
    expect(screen.getByText("Plan migration from Kafka to Google PubSub")).toBeInTheDocument();
  });

  it("disables submit button when input is empty", () => {
    renderWithAuth();
    const button = screen.getByRole("button", { name: "Generate Plan" });
    expect(button).toBeDisabled();
  });

  it("enables submit button when input has text", async () => {
    const user = userEvent.setup();
    renderWithAuth();
    const textarea = screen.getByLabelText("What would you like to plan?");
    await user.type(textarea, "Plan a new feature");
    const button = screen.getByRole("button", { name: "Generate Plan" });
    expect(button).toBeEnabled();
  });

  it("fills textarea when example is clicked", async () => {
    const user = userEvent.setup();
    renderWithAuth();
    const example = screen.getByText("Plan migration from Kafka to Google PubSub");
    await user.click(example);
    const textarea = screen.getByLabelText("What would you like to plan?") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Plan migration from Kafka to Google PubSub");
  });

  it("has a link to run history", () => {
    renderWithAuth();
    expect(screen.getByRole("link", { name: "View run history" })).toHaveAttribute("href", "/runs");
  });

  it("shows the error banner (not a crash) for a failed run with an empty step result", async () => {
    // Regression test: AgentStep.result is a JSON column that defaults to
    // `{}` and is only ever populated on the success path (see
    // RunCoordinator.execute_run) — a failed run's step keeps that empty
    // default, never `null`. `{}` is truthy, so any code path that reused
    // it as if it were a real PlanningResult (e.g.
    // GreenfieldRecommendations calling `.map()` on
    // `result.affected_components`) crashed the whole page. Reproduces a
    // real failure: a run that failed with "The security token included
    // in the request is expired" (expired Bedrock/AWS credentials) while
    // its one step never got past status="running".
    const user = userEvent.setup();
    const failedRun: RunDetail = {
      run_id: "run-failed-1",
      goal: "plan_freeform",
      status: "failed",
      subject: {
        subject_id: "freetext:abc",
        subject_type: "freetext",
        display_name: "Prepare implementation plan for https://example.atlassian.net/browse/PROT-1",
      },
      title: null,
      provider: null,
      user: null,
      repository: null,
      model: null,
      error_message: "The security token included in the request is expired",
      started_at: "2026-01-01T10:00:00Z",
      completed_at: "2026-01-01T10:00:01Z",
      created_at: "2026-01-01T10:00:00Z",
      steps: [
        {
          step_id: "step-1",
          agent_id: "planning",
          status: "running",
          confidence: { score: 0, reasoning: "" },
          evidence: [],
          result: {},
          prompt_version: "1.0",
          output_ref: null,
          error_message: "The security token included in the request is expired",
          latency_ms: null,
          created_at: "2026-01-01T10:00:00Z",
          completed_at: null,
        },
      ],
      workflow_id: null,
      workflow_stage: null,
      previous_run_id: null,
    };

    vi.mocked(agentRunsApi.createAgentRun).mockResolvedValue({
      run_id: "run-failed-1",
      status: "queued",
      subject: failedRun.subject,
      goal: failedRun.goal,
    });
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(failedRun);

    renderWithAuth();
    const textarea = screen.getByLabelText("What would you like to plan?");
    await user.type(textarea, "Prepare implementation plan for a Jira ticket");
    await user.click(screen.getByRole("button", { name: "Generate Plan" }));

    expect(
      await screen.findByText("The security token included in the request is expired"),
    ).toBeInTheDocument();
    // The crash this guards against happens during render, so getting here
    // at all (no thrown error, page still showing real content) is most of
    // the assertion — this just also confirms the greenfield section,
    // which requires a real completed result, correctly stayed off.
    await waitFor(() => {
      expect(screen.queryByText("Suggested Repositories")).not.toBeInTheDocument();
    });
  });
});
