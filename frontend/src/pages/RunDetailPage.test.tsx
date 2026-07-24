import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { RunDetailPage } from "./RunDetailPage";
import * as agentRunsApi from "../lib/api/agentRuns";
import type { RunDetail } from "../types/agent";

vi.mock("../lib/api/agentRuns", () => ({
  getAgentRun: vi.fn(),
}));

function renderRunDetailPage(runId = "run-1") {
  const authValue: AuthContextValue = {
    user: { id: "u1", email: "test@test.com", full_name: "Test User", is_active: true },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };
  return render(
    <AuthContext.Provider value={authValue}>
      <MemoryRouter initialEntries={[`/runs/${runId}`]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

function makeRun(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    run_id: "run-1",
    goal: "plan_freeform",
    status: "completed",
    subject: {
      subject_id: "freetext:abc",
      subject_type: "freetext",
      display_name: "Add distributed tracing with OpenTelemetry",
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
    steps: [],
    workflow_id: null,
    workflow_stage: null,
    previous_run_id: null,
    ...overrides,
  };
}

describe("RunDetailPage", () => {
  it("shows a 'part of workflow' banner linking back when the run belongs to a workflow", async () => {
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(
      makeRun({ workflow_id: "wf-1", workflow_stage: "planning" }),
    );

    renderRunDetailPage();

    expect(await screen.findByText(/Part of workflow/)).toBeInTheDocument();
    // The same display name legitimately appears in both the banner and
    // the "Subject" field further down in Run Details.
    expect(
      screen.getAllByText("Add distributed tracing with OpenTelemetry").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(/Planning stage/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Open full workflow/ });
    expect(link).toHaveAttribute("href", "/workflows/wf-1");
  });

  it("does not show the banner for a standalone, non-workflow run", async () => {
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());

    renderRunDetailPage();

    await screen.findByText("Run Details");
    expect(screen.queryByText(/Part of workflow/)).not.toBeInTheDocument();
  });
});
