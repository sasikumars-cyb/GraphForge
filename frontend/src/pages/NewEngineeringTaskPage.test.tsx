import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { NewEngineeringTaskPage } from "./NewEngineeringTaskPage";
import { ApiError } from "../lib/api/client";
import * as engineeringTasksApi from "../lib/api/engineeringTasks";
import type { EngineeringTask } from "../types/engineeringTask";

vi.mock("../lib/api/engineeringTasks", () => ({
  createEngineeringTask: vi.fn(),
  getEngineeringTask: vi.fn(),
  listEngineeringTasks: vi.fn(),
}));

function makeTask(overrides: Partial<EngineeringTask> = {}): EngineeringTask {
  return {
    task_id: "11111111-1111-1111-1111-111111111111",
    created_at: "2026-08-18T10:00:00Z",
    goal_event_id: "22222222-2222-2222-2222-222222222222",
    goal: { description: "find repositories", postconditions: ["found"] },
    plan_event_id: "33333333-3333-3333-3333-333333333333",
    plan_step_event_id: "44444444-4444-4444-4444-444444444444",
    plan_step: {
      event_id: "44444444-4444-4444-4444-444444444444",
      description: "find repositories",
      postcondition: "x",
      invalidated: false,
    },
    generator_observation: { success: true, outcome: "completed", classification: "expected", actor: null },
    verifier_observation: {
      success: true,
      outcome: "completed",
      classification: "expected",
      actor: "control_plane_verifier",
    },
    ...overrides,
  };
}

function renderPage() {
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
      <MemoryRouter initialEntries={["/engineering-tasks/new"]}>
        <Routes>
          <Route path="/engineering-tasks/new" element={<NewEngineeringTaskPage />} />
          <Route
            path="/engineering-tasks/:taskId"
            element={<div>Detail page placeholder</div>}
          />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("NewEngineeringTaskPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the goal and postconditions fields", () => {
    renderPage();
    expect(screen.getByLabelText("What's the engineering objective?")).toBeInTheDocument();
    expect(
      screen.getByLabelText("How will you know it's done? (one per line)"),
    ).toBeInTheDocument();
  });

  it("disables submit until both goal and a postcondition are entered", async () => {
    const user = userEvent.setup();
    renderPage();
    const button = screen.getByRole("button", { name: /Create Engineering Task/ });
    expect(button).toBeDisabled();

    await user.type(
      screen.getByLabelText("What's the engineering objective?"),
      "find repositories",
    );
    expect(button).toBeDisabled();

    await user.type(
      screen.getByLabelText("How will you know it's done? (one per line)"),
      "found",
    );
    expect(button).toBeEnabled();
  });

  it("submits description and postconditions to createEngineeringTask", async () => {
    const user = userEvent.setup();
    vi.mocked(engineeringTasksApi.createEngineeringTask).mockResolvedValue(makeTask());
    renderPage();

    await user.type(
      screen.getByLabelText("What's the engineering objective?"),
      "find repositories",
    );
    await user.type(
      screen.getByLabelText("How will you know it's done? (one per line)"),
      "found",
    );
    await user.click(screen.getByRole("button", { name: /Create Engineering Task/ }));

    await waitFor(() =>
      expect(engineeringTasksApi.createEngineeringTask).toHaveBeenCalledWith("test-token", {
        description: "find repositories",
        postconditions: ["found"],
      }),
    );
  });

  it("navigates to the new task's detail page on success", async () => {
    const user = userEvent.setup();
    vi.mocked(engineeringTasksApi.createEngineeringTask).mockResolvedValue(
      makeTask({ task_id: "created-task-id" }),
    );
    renderPage();

    await user.type(
      screen.getByLabelText("What's the engineering objective?"),
      "find repositories",
    );
    await user.type(
      screen.getByLabelText("How will you know it's done? (one per line)"),
      "found",
    );
    await user.click(screen.getByRole("button", { name: /Create Engineering Task/ }));

    expect(await screen.findByText("Detail page placeholder")).toBeInTheDocument();
  });

  it("shows an error message and does not navigate on API failure", async () => {
    const user = userEvent.setup();
    vi.mocked(engineeringTasksApi.createEngineeringTask).mockRejectedValue(
      new ApiError(500, "internal_error", "Something went wrong creating this task."),
    );
    renderPage();

    await user.type(
      screen.getByLabelText("What's the engineering objective?"),
      "find repositories",
    );
    await user.type(
      screen.getByLabelText("How will you know it's done? (one per line)"),
      "found",
    );
    await user.click(screen.getByRole("button", { name: /Create Engineering Task/ }));

    expect(
      await screen.findByText("Something went wrong creating this task."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Detail page placeholder")).not.toBeInTheDocument();
  });

  it("splits multi-line postconditions into an array, dropping blank lines", async () => {
    const user = userEvent.setup();
    vi.mocked(engineeringTasksApi.createEngineeringTask).mockResolvedValue(makeTask());
    renderPage();

    await user.type(
      screen.getByLabelText("What's the engineering objective?"),
      "find repositories",
    );
    await user.type(
      screen.getByLabelText("How will you know it's done? (one per line)"),
      "first{enter}{enter}second",
    );
    await user.click(screen.getByRole("button", { name: /Create Engineering Task/ }));

    await waitFor(() =>
      expect(engineeringTasksApi.createEngineeringTask).toHaveBeenCalledWith("test-token", {
        description: "find repositories",
        postconditions: ["first", "second"],
      }),
    );
  });
});
