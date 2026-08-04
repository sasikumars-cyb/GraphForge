import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { axe } from "jest-axe";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { ControlCenterPage } from "./ControlCenterPage";
import * as systemApi from "../lib/api/system";
import * as githubApi from "../lib/api/github";
import type { SystemStatusResponse } from "../lib/api/system";
import type { GitHubConnectionStatus } from "../types/github";

vi.mock("../lib/api/system", () => ({
  getSystemStatus: vi.fn(),
}));

vi.mock("../lib/api/github", () => ({
  getConnectionStatus: vi.fn(),
}));

function renderWithAuth() {
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
    logout: vi.fn(),
  };

  return render(
    <AuthContext.Provider value={authValue}>
      <MemoryRouter>
        <ControlCenterPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

const healthyStatus: SystemStatusResponse = {
  platform_status: "healthy",
  environment: "development",
  version: "0.1.0",
  ai_provider: { name: "openai", configured: true, active: true, model: "gpt-4o" },
  ai_providers: [
    { name: "openai", configured: true, active: true, model: "gpt-4o" },
    { name: "gemini", configured: false, active: false, model: null },
    { name: "groq", configured: true, active: false, model: null },
  ],
  connections: [
    { name: "GitHub", status: "configured", detail: "OAuth app configured" },
    { name: "Neo4j", status: "configured", detail: "bolt://localhost:7687" },
    { name: "PostgreSQL", status: "connected", detail: "Primary datastore" },
    { name: "Jira", status: "not_configured", detail: null },
  ],
  knowledge_base: {
    repositories_tracked: 5,
    repositories_indexed: 3,
    repositories_pending: 1,
    repositories_graph_missing: 0,
  },
};

const githubConnected: GitHubConnectionStatus = {
  connected: true,
  github_username: "octocat",
  connected_at: "2026-01-15T10:30:00Z",
  auth_method: "oauth",
  scope_warning: null,
};

describe("ControlCenterPage", () => {
  it("displays the Control Center heading", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);

    renderWithAuth();

    expect(await screen.findByText("Control Center")).toBeInTheDocument();
  });

  it("shows platform health status when all systems are operational", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);

    renderWithAuth();

    expect(await screen.findByText("All systems operational")).toBeInTheDocument();
  });

  it("shows degraded status when AI provider is not configured", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue({
      ...healthyStatus,
      platform_status: "degraded",
      ai_provider: { name: "openai", configured: false, active: true, model: null },
    });
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);

    renderWithAuth();

    expect(await screen.findByText(/Degraded/)).toBeInTheDocument();
  });

  it("hides the graph-missing row when no repositories are affected", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);

    renderWithAuth();

    await screen.findByText("Repositories indexed");
    expect(screen.queryByText(/Graph missing/)).not.toBeInTheDocument();
  });

  it("surfaces repositories whose indexing job completed but whose graph is missing", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue({
      ...healthyStatus,
      knowledge_base: {
        ...healthyStatus.knowledge_base,
        repositories_graph_missing: 2,
      },
    });
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);

    renderWithAuth();

    expect(await screen.findByText(/Graph missing/)).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("displays AI providers with their configuration state", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);

    renderWithAuth();

    // Wait for data to load
    await screen.findByText("AI Providers");
    // All three providers are rendered
    const openaiElements = screen.getAllByText("openai");
    expect(openaiElements.length).toBeGreaterThan(0);
    expect(screen.getByText("gemini")).toBeInTheDocument();
    expect(screen.getByText("groq")).toBeInTheDocument();
    // Active provider shows its model
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
  });

  it("displays GitHub connection with username when connected", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);

    renderWithAuth();

    expect(await screen.findByText("@octocat")).toBeInTheDocument();
  });

  it("shows knowledge base statistics", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);

    renderWithAuth();

    expect(await screen.findByText("5")).toBeInTheDocument(); // repos tracked
    expect(screen.getByText("3")).toBeInTheDocument(); // repos indexed
    expect(screen.getByText("1")).toBeInTheDocument(); // repos pending
  });

  it("displays platform version and environment", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);

    renderWithAuth();

    expect(await screen.findByText("0.1.0")).toBeInTheDocument();
    // environment appears in both status indicator and platform info
    const devElements = screen.getAllByText("development");
    expect(devElements.length).toBeGreaterThan(0);
  });

  it("handles API errors gracefully", async () => {
    vi.mocked(systemApi.getSystemStatus).mockRejectedValue(new Error("Network error"));
    vi.mocked(githubApi.getConnectionStatus).mockRejectedValue(new Error("Network error"));

    renderWithAuth();

    expect(await screen.findByText("Failed to load platform status.")).toBeInTheDocument();
  });

  it("shows connections from the system API", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue({
      connected: false,
      github_username: null,
      connected_at: null,
      auth_method: null,
      scope_warning: null,
    });

    renderWithAuth();

    expect(await screen.findByText("Neo4j")).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("Jira")).toBeInTheDocument();
  });

  it("has no detectable accessibility violations (KAN-38)", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);

    const { container } = renderWithAuth();
    await screen.findByText("Control Center");

    expect(await axe(container)).toHaveNoViolations();
  });
});
