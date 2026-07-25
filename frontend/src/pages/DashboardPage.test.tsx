import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { DashboardPage } from "./DashboardPage";
import * as workflowsApi from "../lib/api/workflows";
import type { WorkflowListItem, WorkflowListResponse } from "../types/agent";

vi.mock("../lib/api/workflows", () => ({
  listWorkflows: vi.fn(),
}));

function renderWithAuth() {
  const authValue: AuthContextValue = {
    user: { id: "u1", email: "test@test.com", full_name: "Test User", is_active: true },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };

  return render(
    <AuthContext.Provider value={authValue}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

function makeWorkflowItem(overrides: Partial<WorkflowListItem> = {}): WorkflowListItem {
  return {
    workflow_id: "wf-1",
    title: "Add rate limiting",
    workflow_type: "planning",
    current_stage: "development",
    status: "in_progress",
    stages: [{ stage: "planning", label: "Planning", status: "completed", run_id: "run-1" }],
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-01T10:00:02Z",
    approved_by: null,
    ...overrides,
  };
}

function listResponse(items: WorkflowListItem[], total = items.length): WorkflowListResponse {
  return { items, page: 1, page_size: 5, total, has_more: false };
}

describe("DashboardPage", () => {
  it("computes the Planning count from the recent workflows list", async () => {
    vi.mocked(workflowsApi.listWorkflows).mockImplementation((_token, params) => {
      if (params?.status === "approved") return Promise.resolve(listResponse([], 0));
      return Promise.resolve(
        listResponse([
          makeWorkflowItem({ workflow_id: "wf-1" }),
          makeWorkflowItem({ workflow_id: "wf-2" }),
        ]),
      );
    });

    renderWithAuth();

    expect(await screen.findByText(/2 Planning/)).toBeInTheDocument();
  });

  it("fetches and displays the real approved total from a separate status=approved call", async () => {
    vi.mocked(workflowsApi.listWorkflows).mockImplementation((_token, params) => {
      if (params?.status === "approved") return Promise.resolve(listResponse([], 12));
      return Promise.resolve(listResponse([makeWorkflowItem()]));
    });

    renderWithAuth();

    expect(await screen.findByText(/12 Approved/)).toBeInTheDocument();
    expect(await screen.findByText(/View Approved Queue \(12\)/)).toBeInTheDocument();
  });

  it("links the Approved Queue action to /workflows/approved", async () => {
    vi.mocked(workflowsApi.listWorkflows).mockImplementation((_token, params) => {
      if (params?.status === "approved") return Promise.resolve(listResponse([], 3));
      return Promise.resolve(listResponse([makeWorkflowItem()]));
    });

    renderWithAuth();

    const link = await screen.findByRole("link", { name: /View Approved Queue/ });
    expect(link).toHaveAttribute("href", "/workflows/approved");
  });

  it("omits the Approved count if that request fails, without breaking the page", async () => {
    vi.mocked(workflowsApi.listWorkflows).mockImplementation((_token, params) => {
      if (params?.status === "approved") return Promise.reject(new Error("boom"));
      return Promise.resolve(listResponse([makeWorkflowItem()]));
    });

    renderWithAuth();

    expect(await screen.findByText(/1 Planning/)).toBeInTheDocument();
    // The subtitle's count is omitted, but the queue link itself (with no
    // count suffix) still renders — a failed count fetch shouldn't hide a
    // real, working page.
    expect(screen.queryByText(/\d+ Approved/)).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: "View Approved Queue →" });
    expect(link).toBeInTheDocument();
  });

  it("never renders the raw 'auto_execution' backend type name", async () => {
    vi.mocked(workflowsApi.listWorkflows).mockImplementation((_token, params) => {
      if (params?.status === "approved") return Promise.resolve(listResponse([], 0));
      return Promise.resolve(listResponse([makeWorkflowItem()]));
    });

    renderWithAuth();

    await screen.findByText(/1 Planning/);
    expect(screen.queryByText(/auto_execution/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Execution\b/)).not.toBeInTheDocument();
  });
});
