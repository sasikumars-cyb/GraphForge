import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { ReviewPage } from "./ReviewPage";

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
    logout: vi.fn(),
    ...authValue,
  };

  return render(
    <AuthContext.Provider value={defaultAuth}>
      <MemoryRouter>
        <ReviewPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("ReviewPage", () => {
  it("renders the review heading", () => {
    renderWithAuth();
    expect(screen.getByText("Review Pull Request")).toBeInTheDocument();
  });

  it("renders the URL input", () => {
    renderWithAuth();
    expect(screen.getByLabelText("GitHub Pull Request URL")).toBeInTheDocument();
  });

  it("disables submit when input is empty", () => {
    renderWithAuth();
    const button = screen.getByRole("button", { name: "Submit review request" });
    expect(button).toBeDisabled();
  });

  it("shows validation error for invalid URL", async () => {
    const user = userEvent.setup();
    renderWithAuth();
    const input = screen.getByLabelText("GitHub Pull Request URL");
    await user.type(input, "not-a-url");
    const button = screen.getByRole("button", { name: "Submit review request" });
    await user.click(button);
    expect(screen.getByRole("alert")).toHaveTextContent(/valid GitHub PR URL/);
  });

  it("clears validation error when input changes", async () => {
    const user = userEvent.setup();
    renderWithAuth();
    const input = screen.getByLabelText("GitHub Pull Request URL");
    await user.type(input, "bad");
    const button = screen.getByRole("button", { name: "Submit review request" });
    await user.click(button);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await user.type(input, "x");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("has a link to run history", () => {
    renderWithAuth();
    expect(screen.getByRole("link", { name: "View run history" })).toHaveAttribute("href", "/runs");
  });
});
