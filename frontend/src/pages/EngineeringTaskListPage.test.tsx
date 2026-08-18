import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { EngineeringTaskListPage } from "./EngineeringTaskListPage";
import { ApiError } from "../lib/api/client";
import * as engineeringTasksApi from "../lib/api/engineeringTasks";
import type { EngineeringTaskSummary } from "../types/engineeringTask";

vi.mock("../lib/api/engineeringTasks", () => ({
  listEngineeringTasks: vi.fn(),
  getEngineeringTask: vi.fn(),
  createEngineeringTask: vi.fn(),
}));

function makeSummary(overrides: Partial<EngineeringTaskSummary> = {}): EngineeringTaskSummary {
  return {
    task_id: "11111111-1111-1111-1111-111111111111",
    created_at: "2026-08-18T10:00:00Z",
    updated_at: "2026-08-18T10:00:05Z",
    description: "find repositories containing payment processing code",
    classification: "expected",
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
      <MemoryRouter initialEntries={["/engineering-tasks"]}>
        <Routes>
          <Route path="/engineering-tasks" element={<EngineeringTaskListPage />} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("EngineeringTaskListPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders a New Engineering Task link", async () => {
    vi.mocked(engineeringTasksApi.listEngineeringTasks).mockResolvedValue([]);
    renderPage();
    const link = await screen.findByRole("link", { name: /New Engineering Task/ });
    expect(link).toHaveAttribute("href", "/engineering-tasks/new");
  });

  it("shows a loading state before the response resolves", () => {
    vi.mocked(engineeringTasksApi.listEngineeringTasks).mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByLabelText(/Loading Engineering Tasks/i)).toBeInTheDocument();
  });

  it("shows an empty state with no tasks", async () => {
    vi.mocked(engineeringTasksApi.listEngineeringTasks).mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText("Engineering Tasks appear here")).toBeInTheDocument();
  });

  it("renders task rows with goal, status, and a link to the detail page", async () => {
    vi.mocked(engineeringTasksApi.listEngineeringTasks).mockResolvedValue([makeSummary()]);
    renderPage();

    expect(
      await screen.findByText("find repositories containing payment processing code"),
    ).toBeInTheDocument();
    expect(screen.getByText("Verified as expected")).toBeInTheDocument();
    const link = screen.getByRole("link", {
      name: /find repositories containing payment processing code/,
    });
    expect(link).toHaveAttribute(
      "href",
      "/engineering-tasks/11111111-1111-1111-1111-111111111111",
    );
  });

  it("renders a Pending badge for a task with no classification yet", async () => {
    vi.mocked(engineeringTasksApi.listEngineeringTasks).mockResolvedValue([
      makeSummary({ classification: null }),
    ]);
    renderPage();
    expect(await screen.findByText("Pending")).toBeInTheDocument();
  });

  it("shows an error message on API failure", async () => {
    vi.mocked(engineeringTasksApi.listEngineeringTasks).mockRejectedValue(
      new ApiError(500, "internal_error", "Failed to load the task list."),
    );
    renderPage();
    expect(await screen.findByText("Failed to load the task list.")).toBeInTheDocument();
  });
});
