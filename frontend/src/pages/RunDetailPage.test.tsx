import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { RunDetailPage } from "./RunDetailPage";
import * as agentRunsApi from "../lib/api/agentRuns";
import type { AgentStep, RunDetail } from "../types/agent";

vi.mock("../lib/api/agentRuns", () => ({
  getAgentRun: vi.fn(),
}));

function makeStep(overrides: Partial<AgentStep> = {}): AgentStep {
  return {
    step_id: "step-1",
    agent_id: "planning",
    status: "completed",
    confidence: { score: 0.9, reasoning: "" },
    evidence: [],
    result: {},
    prompt_version: "1.0",
    output_ref: null,
    error_message: null,
    latency_ms: 1200,
    created_at: "2026-01-01T10:00:00Z",
    completed_at: "2026-01-01T10:00:01Z",
    ...overrides,
  };
}

function renderRunDetailPage(runId = "run-1") {
  const authValue: AuthContextValue = {
    user: { id: "u1", email: "test@test.com", full_name: "Test User", auth_provider: "local", role: "user", created_at: "2026-01-01T00:00:00Z" },
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
  beforeEach(() => {
    // No global clearMocks/restoreMocks config in vite.config.ts — without
    // this, getAgentRun's call count (and mockResolvedValueOnce queue)
    // carries over between tests in this file, which the pre-existing
    // tests never noticed since they only asserted on rendered content,
    // never call counts.
    vi.clearAllMocks();
  });

  // Fake timers are scoped per-test (not a describe-level beforeEach) and
  // paired with a `finally` restore — mixing them with findBy*/waitFor's
  // own internal polling (used by the other tests below) is fragile, so
  // only the two tests that actually assert on the poll interval use them,
  // and they avoid findBy*/waitFor entirely in favor of directly advancing
  // the fake clock and asserting synchronously.

  it("polls while the run is queued/running and stops once it reaches a terminal status", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(agentRunsApi.getAgentRun)
        .mockResolvedValueOnce(makeRun({ status: "running", steps: [] }))
        .mockResolvedValueOnce(makeRun({ status: "running", steps: [] }))
        .mockResolvedValueOnce(makeRun({ status: "completed", steps: [makeStep()] }));

      renderRunDetailPage();

      // Flush the initial (mount-triggered) fetch.
      await act(async () => {
        await Promise.resolve();
      });
      expect(agentRunsApi.getAgentRun).toHaveBeenCalledTimes(1);

      // Two more poll ticks — one still running, one that reaches completed.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2500);
      });
      expect(agentRunsApi.getAgentRun).toHaveBeenCalledTimes(2);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2500);
      });
      expect(agentRunsApi.getAgentRun).toHaveBeenCalledTimes(3);

      // Now terminal — advancing further must not trigger another poll.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(agentRunsApi.getAgentRun).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops polling after the component unmounts (the exact 'navigate away' regression)", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun({ status: "running", steps: [] }));

      const { unmount } = renderRunDetailPage();
      await act(async () => {
        await Promise.resolve();
      });
      expect(agentRunsApi.getAgentRun).toHaveBeenCalledTimes(1);

      unmount();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      // No further polling after unmount — this is what actually matters:
      // the backend keeps executing regardless, but the frontend must not
      // keep hitting it (or calling setState) once nobody's watching.
      expect(agentRunsApi.getAgentRun).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders the persisted blueprint graph via StageResultPanel instead of a raw JSON dump", async () => {
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(
      makeRun({
        status: "completed",
        workflow_stage: "planning",
        steps: [
          makeStep({
            result: {
              blueprint: {
                diagrams: [{ id: "d1", title: "System Overview", nodes: [], edges: [] }],
              },
            },
          }),
        ],
      }),
    );

    renderRunDetailPage();

    expect(await screen.findByRole("tab", { name: /Visual Blueprint/ })).toBeInTheDocument();
    // The old behavior (raw JSON dump) must be gone.
    expect(screen.queryByText(/"blueprint":/)).not.toBeInTheDocument();
  });

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
