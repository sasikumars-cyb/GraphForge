import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { TestingPage } from "./TestingPage";

// Mock the API module
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
        <TestingPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("TestingPage", () => {
  it("renders the test planning agent heading", () => {
    renderWithAuth();
    expect(screen.getByText("Test Planning Agent")).toBeInTheDocument();
  });

  it("renders the text input", () => {
    renderWithAuth();
    expect(screen.getByLabelText("What change needs testing?")).toBeInTheDocument();
  });

  it("renders example buttons", () => {
    renderWithAuth();
    expect(screen.getByText("Test strategy for JWT authentication across all services")).toBeInTheDocument();
  });

  it("disables submit button when input is empty", () => {
    renderWithAuth();
    const button = screen.getByRole("button", { name: "Submit testing request" });
    expect(button).toBeDisabled();
  });

  it("enables submit button when input has text", async () => {
    const user = userEvent.setup();
    renderWithAuth();
    const textarea = screen.getByLabelText("What change needs testing?");
    await user.type(textarea, "Test JWT auth");
    const button = screen.getByRole("button", { name: "Submit testing request" });
    expect(button).toBeEnabled();
  });

  it("fills textarea when example is clicked", async () => {
    const user = userEvent.setup();
    renderWithAuth();
    const example = screen.getByText("Test strategy for JWT authentication across all services");
    await user.click(example);
    const textarea = screen.getByLabelText("What change needs testing?") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Test strategy for JWT authentication across all services");
  });

  it("has a link to run history", () => {
    renderWithAuth();
    expect(screen.getByRole("link", { name: "View run history" })).toHaveAttribute("href", "/runs");
  });
});
