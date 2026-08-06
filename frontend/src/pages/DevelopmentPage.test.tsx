import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { DevelopmentPage } from "./DevelopmentPage";

// Mock the API module
vi.mock("../lib/api/agentRuns", () => ({
  createAgentRun: vi.fn(),
  getAgentRun: vi.fn(),
  listAgentRuns: vi.fn().mockResolvedValue({ items: [], page: 1, page_size: 10, total: 0, has_more: false }),
}));

function renderWithAuth(authValue?: Partial<AuthContextValue>) {
  const defaultAuth: AuthContextValue = {
    user: { id: "u1", email: "test@test.com", full_name: "Test User", auth_provider: "local", role: "user", created_at: "2026-01-01T00:00:00Z" },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    loginWithToken: vi.fn(),
    logout: vi.fn(),
    ...authValue,
  };

  return render(
    <AuthContext.Provider value={defaultAuth}>
      <MemoryRouter>
        <DevelopmentPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("DevelopmentPage", () => {
  it("renders the development agent heading", () => {
    renderWithAuth();
    expect(screen.getByText("Development Agent")).toBeInTheDocument();
  });

  it("renders the text input", () => {
    renderWithAuth();
    expect(screen.getByLabelText("What would you like to implement?")).toBeInTheDocument();
  });

  it("renders example buttons", () => {
    renderWithAuth();
    expect(screen.getByText("Implement JWT authentication for all services")).toBeInTheDocument();
  });

  it("disables submit button when input is empty", () => {
    renderWithAuth();
    const button = screen.getByRole("button", { name: "Submit development request" });
    expect(button).toBeDisabled();
  });

  it("enables submit button when input has text", async () => {
    const user = userEvent.setup();
    renderWithAuth();
    const textarea = screen.getByLabelText("What would you like to implement?");
    await user.type(textarea, "Add retry support");
    const button = screen.getByRole("button", { name: "Submit development request" });
    expect(button).toBeEnabled();
  });

  it("fills textarea when example is clicked", async () => {
    const user = userEvent.setup();
    renderWithAuth();
    const example = screen.getByText("Implement JWT authentication for all services");
    await user.click(example);
    const textarea = screen.getByLabelText("What would you like to implement?") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Implement JWT authentication for all services");
  });

  it("has a link to run history", () => {
    renderWithAuth();
    expect(screen.getByRole("link", { name: "View run history" })).toHaveAttribute("href", "/runs");
  });
});
