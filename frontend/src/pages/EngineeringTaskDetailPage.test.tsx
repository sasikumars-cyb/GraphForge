import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { EngineeringTaskDetailPage } from "./EngineeringTaskDetailPage";
import { ApiError } from "../lib/api/client";
import * as engineeringTasksApi from "../lib/api/engineeringTasks";
import type { EngineeringTask } from "../types/engineeringTask";

vi.mock("../lib/api/engineeringTasks", () => ({
  getEngineeringTask: vi.fn(),
}));

function makeTask(overrides: Partial<EngineeringTask> = {}): EngineeringTask {
  return {
    task_id: "11111111-1111-1111-1111-111111111111",
    created_at: "2026-08-18T10:00:00Z",
    goal_event_id: "22222222-2222-2222-2222-222222222222",
    goal: {
      description: "find repositories containing payment processing code",
      postconditions: ["at least one repository identified"],
    },
    plan_event_id: "33333333-3333-3333-3333-333333333333",
    plan_step_event_id: "44444444-4444-4444-4444-444444444444",
    plan_step: {
      event_id: "44444444-4444-4444-4444-444444444444",
      description: "find repositories containing payment processing code",
      postcondition: "query_knowledge_graph returns a non-empty summary for: ...",
      invalidated: false,
    },
    generator_observation: {
      success: true,
      outcome: "completed",
      classification: "expected",
      actor: null,
      summary: "Knowledge Graph: 3 repositories, 12 components.",
      error: null,
      capability: "query_knowledge_graph",
    },
    verifier_observation: {
      success: true,
      outcome: "completed",
      classification: "expected",
      actor: "control_plane_verifier",
      summary: "Knowledge Graph: 3 repositories, 12 components.",
      error: null,
      capability: "query_knowledge_graph",
    },
    ...overrides,
  };
}

function renderPage(taskId = "11111111-1111-1111-1111-111111111111") {
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
  return render(
    <AuthContext.Provider value={authValue}>
      <MemoryRouter initialEntries={[`/engineering-tasks/${taskId}`]}>
        <Routes>
          <Route path="/engineering-tasks/:taskId" element={<EngineeringTaskDetailPage />} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("EngineeringTaskDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the task's Goal, PlanStep, and Observation data once loaded", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockResolvedValue(makeTask());

    renderPage();

    expect(
      (await screen.findAllByText("find repositories containing payment processing code"))
        .length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("at least one repository identified")).toBeInTheDocument();
    // Top-level status badge + Execution card + Verification card — all
    // three read the same underlying classification via the same shared
    // helper, so they can never disagree.
    expect(screen.getAllByText("Verified as expected")).toHaveLength(3);
    expect(screen.getByText("control_plane_verifier")).toBeInTheDocument();
  });

  it("shows a 'Back to Engineering Tasks' link", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockResolvedValue(makeTask());
    renderPage();
    await screen.findAllByText("find repositories containing payment processing code");
    expect(screen.getByRole("link", { name: /Back to Engineering Tasks/ })).toHaveAttribute(
      "href",
      "/engineering-tasks",
    );
  });

  it("explains what the classification means in human terms", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockResolvedValue(makeTask());
    renderPage();
    await screen.findAllByText("find repositories containing payment processing code");
    expect(
      screen.getAllByText(
        "The predicted outcome was confirmed by Independent Verification.",
      ).length,
    ).toBeGreaterThan(0);
  });

  it("explains that control_plane_verifier is automated, not a human", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockResolvedValue(makeTask());
    renderPage();
    await screen.findAllByText("find repositories containing payment processing code");
    expect(
      screen.getByText(/automated verification, not a human reviewer/),
    ).toBeInTheDocument();
  });

  it("renders an anomaly classification with its own explanation and warning tone", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockResolvedValue(
      makeTask({
        generator_observation: {
          success: false,
          outcome: "completed",
          classification: "anomaly",
          actor: null,
          summary: null,
          error: "ToolInput.parameters['db'] (AsyncSession) is required.",
          capability: "query_knowledge_graph",
        },
        verifier_observation: {
          success: false,
          outcome: "completed",
          classification: "anomaly",
          actor: "control_plane_verifier",
          summary: null,
          error: "ToolInput.parameters['db'] (AsyncSession) is required.",
          capability: "query_knowledge_graph",
        },
      }),
    );
    renderPage();
    await screen.findAllByText("find repositories containing payment processing code");
    expect(screen.getAllByText("Anomaly").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/Something unexpected happened at the infrastructure/).length,
    ).toBeGreaterThan(0);
  });

  it("renders the result summary when present (Phase 8)", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockResolvedValue(makeTask());
    renderPage();
    await screen.findAllByText("find repositories containing payment processing code");
    expect(
      screen.getAllByText("Knowledge Graph: 3 repositories, 12 components.").length,
    ).toBeGreaterThan(0);
  });

  it("renders the actual tool-reported error for an anomaly (Phase 8)", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockResolvedValue(
      makeTask({
        generator_observation: {
          success: false,
          outcome: "completed",
          classification: "anomaly",
          actor: null,
          summary: null,
          error: "ToolInput.parameters['db'] (AsyncSession) is required.",
          capability: "query_knowledge_graph",
        },
        verifier_observation: {
          success: false,
          outcome: "completed",
          classification: "anomaly",
          actor: "control_plane_verifier",
          summary: null,
          error: "ToolInput.parameters['db'] (AsyncSession) is required.",
          capability: "query_knowledge_graph",
        },
      }),
    );
    renderPage();
    await screen.findAllByText("find repositories containing payment processing code");
    expect(
      screen.getAllByText("ToolInput.parameters['db'] (AsyncSession) is required.").length,
    ).toBeGreaterThan(0);
  });

  it("renders the capability for Execution and Verification (Phase 8)", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockResolvedValue(makeTask());
    renderPage();
    await screen.findAllByText("find repositories containing payment processing code");
    expect(screen.getAllByText("query_knowledge_graph").length).toBeGreaterThan(0);
  });

  it("shows a dash for capability/summary/error when absent, never a crash (Phase 8)", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockResolvedValue(
      makeTask({
        generator_observation: {
          success: null,
          outcome: null,
          classification: null,
          actor: null,
          summary: null,
          error: null,
          capability: null,
        },
      }),
    );
    renderPage();
    await screen.findAllByText("find repositories containing payment processing code");
    // No throw, and the capability dash renders somewhere.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("distinguishes outcome_unknown from a pending/no-observation state (Phase 8)", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockResolvedValue(
      makeTask({
        generator_observation: {
          success: null,
          outcome: "outcome_unknown",
          classification: null,
          actor: null,
          summary: null,
          error: null,
          capability: "query_knowledge_graph",
        },
      }),
    );
    renderPage();
    await screen.findAllByText("find repositories containing payment processing code");
    expect(screen.getByText("Outcome unknown")).toBeInTheDocument();
    expect(
      screen.getByText(/awaiting reconciliation/),
    ).toBeInTheDocument();
  });

  it("shows a loading state before the response resolves", () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(screen.getByText(/loading engineering task/i)).toBeInTheDocument();
  });

  it("shows a not-found state on a 404", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockRejectedValue(
      new ApiError(404, "not_found", "not found"),
    );

    renderPage();

    expect(await screen.findByText(/not found/i)).toBeInTheDocument();
  });

  it("shows a friendly message on a malformed task id (422/validation_error), not the raw backend text", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockRejectedValue(
      new ApiError(422, "validation_error", "Request validation failed."),
    );

    renderPage("not-a-real-uuid");

    expect(await screen.findByText(/doesn't look like a valid Engineering Task link/i)).toBeInTheDocument();
    expect(screen.queryByText("Request validation failed.")).not.toBeInTheDocument();
  });

  it("shows an error state on a non-404 API failure", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockRejectedValue(
      new ApiError(500, "internal_error", "something broke"),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("something broke")).toBeInTheDocument();
    });
  });

  it("renders no mutation/action controls — a pure viewer", async () => {
    vi.mocked(engineeringTasksApi.getEngineeringTask).mockResolvedValue(makeTask());

    renderPage();

    await screen.findAllByText("find repositories containing payment processing code");

    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryByText(/approve/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/reject/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/replan/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/retry/i)).not.toBeInTheDocument();
  });
});
