import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { WorkflowLLMUsagePage } from "./WorkflowLLMUsagePage";
import * as metricsApi from "../lib/api/metrics";
import type { WorkflowLLMUsageResponse } from "../types/metrics";

vi.mock("../lib/api/metrics", () => ({
  getWorkflowLLMUsage: vi.fn(),
}));

function renderPage(workflowId = "wf-1") {
  const authValue: AuthContextValue = {
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
      <AuthContext.Provider value={authValue}>
        <MemoryRouter initialEntries={[`/metrics/workflows/${workflowId}`]}>
          <Routes>
            <Route path="/metrics/workflows/:workflowId" element={<WorkflowLLMUsagePage />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

const usage: WorkflowLLMUsageResponse = {
  workflow_id: "wf-1",
  workflow_title: "Add rate limiting to the payment API",
  stages: [
    {
      stage: "planning",
      models: ["claude-haiku", "gpt-4o"],
      calls: 2,
      input_tokens: 300,
      output_tokens: 150,
      total_tokens: 450,
      cost_usd: 0.03,
      avg_latency_ms: 1000,
    },
    {
      stage: "development",
      models: ["gpt-4o"],
      calls: 1,
      input_tokens: 500,
      output_tokens: 300,
      total_tokens: 800,
      cost_usd: 0.05,
      avg_latency_ms: 2000,
    },
  ],
};

describe("WorkflowLLMUsagePage", () => {
  it("renders the workflow title and per-stage breakdown", async () => {
    vi.mocked(metricsApi.getWorkflowLLMUsage).mockResolvedValue(usage);

    renderPage();

    expect(await screen.findByText("Add rate limiting to the payment API")).toBeInTheDocument();
    // "Planning"/"Development" also appear as bar-chart labels - assert
    // against the table cell specifically, not just any matching text.
    expect(screen.getByRole("cell", { name: "Planning" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Development" })).toBeInTheDocument();
    expect(screen.getByText("claude-haiku, gpt-4o")).toBeInTheDocument();
  });

  it("shows aggregated totals across stages", async () => {
    vi.mocked(metricsApi.getWorkflowLLMUsage).mockResolvedValue(usage);

    renderPage();

    await screen.findByText("Add rate limiting to the payment API");
    // 2 calls + 1 call = 3 total LLM calls
    expect(screen.getByText("3")).toBeInTheDocument();
    // $0.03 + $0.05 = $0.08
    expect(screen.getByText("$0.0800")).toBeInTheDocument();
  });

  it("shows an empty state when the workflow has no LLM calls yet", async () => {
    vi.mocked(metricsApi.getWorkflowLLMUsage).mockResolvedValue({
      workflow_id: "wf-2",
      workflow_title: "Brand new workflow",
      stages: [],
    });

    renderPage("wf-2");

    expect(
      await screen.findByText("No LLM calls recorded for this workflow yet."),
    ).toBeInTheDocument();
  });

  it("shows an error message when the fetch fails", async () => {
    vi.mocked(metricsApi.getWorkflowLLMUsage).mockRejectedValue(new Error("boom"));

    renderPage();

    expect(
      await screen.findByText("Failed to load this workflow's LLM usage."),
    ).toBeInTheDocument();
  });
});
