import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { RunHistoryPage } from "./RunHistoryPage";

vi.mock("../lib/api/agentRuns", () => ({
  listAgentRuns: vi.fn().mockResolvedValue({
    items: [],
    page: 1,
    page_size: 25,
    total: 0,
    has_more: false,
  }),
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
        <RunHistoryPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("RunHistoryPage", () => {
  it("renders the run history heading", () => {
    renderWithAuth();
    expect(screen.getByText("Run History")).toBeInTheDocument();
  });

  it("shows empty state message", async () => {
    renderWithAuth();
    expect(await screen.findByText("No agent runs yet.")).toBeInTheDocument();
  });

  it("has a refresh button", () => {
    renderWithAuth();
    expect(screen.getByRole("button", { name: "Refresh run history" })).toBeInTheDocument();
  });
});
