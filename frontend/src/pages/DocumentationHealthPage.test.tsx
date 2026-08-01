import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { DocumentationHealthPage } from "./DocumentationHealthPage";

vi.mock("../lib/api/agentRuns", () => ({
  createAgentRun: vi.fn(),
  getAgentRun: vi.fn(),
}));

vi.mock("../lib/api/github", () => ({
  listTrackedRepositories: vi.fn(),
}));

import { listTrackedRepositories } from "../lib/api/github";

const mockedListTrackedRepositories = vi.mocked(listTrackedRepositories);

function renderWithAuth(authValue?: Partial<AuthContextValue>) {
  const defaultAuth: AuthContextValue = {
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
    logout: vi.fn(),
    ...authValue,
  };

  return render(
    <AuthContext.Provider value={defaultAuth}>
      <MemoryRouter>
        <DocumentationHealthPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("DocumentationHealthPage", () => {
  it("renders the documentation health heading", () => {
    mockedListTrackedRepositories.mockResolvedValue([]);
    renderWithAuth();
    expect(screen.getByText("Documentation Health")).toBeInTheDocument();
  });

  it("disables Run Documentation Health until a repository is selected", () => {
    mockedListTrackedRepositories.mockResolvedValue([]);
    renderWithAuth();
    const button = screen.getByRole("button", { name: "Run documentation health analysis" });
    expect(button).toBeDisabled();
  });

  it("populates the repository dropdown from listTrackedRepositories", async () => {
    mockedListTrackedRepositories.mockResolvedValue([
      {
        id: "repo-1",
        github_repo_id: "123",
        source: "github",
        owner: "acme",
        name: "widgets",
        full_name: "acme/widgets",
        private: false,
        default_branch: "main",
        html_url: "https://github.com/acme/widgets",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    renderWithAuth();

    await waitFor(() => {
      expect(screen.getByRole("option", { name: "acme/widgets" })).toBeInTheDocument();
    });
  });

  it("enables Run Documentation Health once a repository is selected", async () => {
    const user = userEvent.setup();
    mockedListTrackedRepositories.mockResolvedValue([
      {
        id: "repo-1",
        github_repo_id: "123",
        source: "github",
        owner: "acme",
        name: "widgets",
        full_name: "acme/widgets",
        private: false,
        default_branch: "main",
        html_url: "https://github.com/acme/widgets",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    renderWithAuth();

    const select = await screen.findByLabelText("Repository");
    await user.selectOptions(select, "repo-1");

    const button = screen.getByRole("button", { name: "Run documentation health analysis" });
    expect(button).toBeEnabled();
  });

  it("has a link to run history", () => {
    mockedListTrackedRepositories.mockResolvedValue([]);
    renderWithAuth();
    expect(screen.getByRole("link", { name: "View run history" })).toHaveAttribute("href", "/runs");
  });

  it("shows an error message if repositories fail to load", async () => {
    mockedListTrackedRepositories.mockRejectedValue(new Error("boom"));
    renderWithAuth();
    await waitFor(() => {
      expect(screen.getByText("boom")).toBeInTheDocument();
    });
  });
});
