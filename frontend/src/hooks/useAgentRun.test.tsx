import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { useAgentRun } from "./useAgentRun";
import * as agentRunsApi from "../lib/api/agentRuns";
import type { RunDetail } from "../types/agent";
import type { ReactNode } from "react";

vi.mock("../lib/api/agentRuns", () => ({
  createAgentRun: vi.fn(),
  getAgentRun: vi.fn(),
}));

function wrapper({ children }: { children: ReactNode }) {
  const authValue: AuthContextValue = {
    user: { id: "u1", email: "test@test.com", full_name: "Test User", auth_provider: "local", role: "user", created_at: "2026-01-01T00:00:00Z" },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };
  return <AuthContext.Provider value={authValue}>{children}</AuthContext.Provider>;
}

function makeRun(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    run_id: "run-1",
    goal: "plan_freeform",
    status: "queued",
    subject: { subject_id: "freetext:abc", subject_type: "freetext", display_name: "Test" },
    title: null,
    provider: null,
    user: null,
    repository: null,
    model: null,
    error_message: null,
    started_at: null,
    completed_at: null,
    created_at: "2026-01-01T10:00:00Z",
    steps: [],
    workflow_id: null,
    workflow_stage: null,
    previous_run_id: null,
    ...overrides,
  };
}

describe("useAgentRun", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("resolves submit() as soon as the run is created — it does not wait for completion", async () => {
    vi.mocked(agentRunsApi.createAgentRun).mockResolvedValue({
      run_id: "run-1",
      status: "queued",
      subject: { subject_id: "freetext:abc", subject_type: "freetext", display_name: "Test" },
      goal: "plan_freeform",
    });
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun());

    const { result } = renderHook(() => useAgentRun(), { wrapper });

    await act(async () => {
      await result.current.submit({ subject_reference: "freetext:test", goal: "plan_freeform" });
    });

    // submit() itself resolved without needing the run to reach a
    // terminal status — createAgentRun's own promise is all it awaited.
    expect(agentRunsApi.createAgentRun).toHaveBeenCalledTimes(1);
  });

  it("stops polling after unmount instead of continuing to hit the backend", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(agentRunsApi.createAgentRun).mockResolvedValue({
        run_id: "run-1",
        status: "queued",
        subject: { subject_id: "freetext:abc", subject_type: "freetext", display_name: "Test" },
        goal: "plan_freeform",
      });
      vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun({ status: "running" }));

      const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });

      await act(async () => {
        await result.current.submit({ subject_reference: "freetext:test", goal: "plan_freeform" });
      });
      // The polling effect's first tick (immediate, via poll() called
      // synchronously inside the effect).
      await act(async () => {
        await Promise.resolve();
      });
      const callsBeforeUnmount = vi.mocked(agentRunsApi.getAgentRun).mock.calls.length;
      expect(callsBeforeUnmount).toBeGreaterThan(0);

      unmount();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });

      // This is the exact bug the old manual `while` loop had: no
      // unmount cleanup at all, so it kept polling (and calling setState
      // on an unmounted component) after the consuming page navigated
      // away. The useEffect-based rewrite's cleanup must prevent this.
      expect(vi.mocked(agentRunsApi.getAgentRun).mock.calls.length).toBe(callsBeforeUnmount);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reset() clears run/error state for a fresh submission", async () => {
    vi.mocked(agentRunsApi.createAgentRun).mockResolvedValue({
      run_id: "run-1",
      status: "queued",
      subject: { subject_id: "freetext:abc", subject_type: "freetext", display_name: "Test" },
      goal: "plan_freeform",
    });
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(makeRun({ status: "completed" }));

    const { result } = renderHook(() => useAgentRun(), { wrapper });

    await act(async () => {
      await result.current.submit({ subject_reference: "freetext:test", goal: "plan_freeform" });
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.run).not.toBeNull();

    act(() => {
      result.current.reset();
    });

    expect(result.current.run).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.isSubmitting).toBe(false);
  });
});
