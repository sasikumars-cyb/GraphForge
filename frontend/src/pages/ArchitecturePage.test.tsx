import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { axe } from "jest-axe";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { ArchitecturePage } from "./ArchitecturePage";
import * as githubApi from "../lib/api/github";
import * as repositoriesApi from "../lib/api/repositories";
import type { TrackedRepository } from "../types/github";
import type { Graph, IndexingJob } from "../types/graph";

vi.mock("../lib/api/github", () => ({
  listTrackedRepositories: vi.fn(),
}));

vi.mock("../lib/api/repositories", () => ({
  getLatestIndexingJob: vi.fn(),
  getAllCrossRepositoryLinks: vi.fn(),
  getAllCrossRepositoryEdges: vi.fn(),
  getRepositoryGraph: vi.fn(),
  getCrossRepositoryLinks: vi.fn(),
}));

// This page's own responsibility is data-fetching/caching (KAN-37) - the
// graph rendering itself (React Flow, ResizeObserver, canvas layout) isn't
// exercised here, so the visualization is stubbed out to a plain summary a
// test can assert against instead.
vi.mock("../components/graph/DependencyGraph", () => ({
  DependencyGraph: ({ graph }: { graph: { nodes: unknown[] } }) => (
    <div data-testid="dependency-graph">{graph.nodes.length} nodes</div>
  ),
  RepositoryOverviewGraph: ({ repositories }: { repositories: { id: string; name: string }[] }) => (
    <div data-testid="repository-overview-graph">
      {repositories.map((r) => (
        <div key={r.id}>{r.name}</div>
      ))}
    </div>
  ),
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
    loginWithToken: vi.fn(),
    logout: vi.fn(),
  };

  // A fresh QueryClient per render — a shared/module-level client would leak
  // cached graph/repository responses across tests in this file. `retry:
  // false` so a rejected mock response fails the query immediately.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={authValue}>
        <MemoryRouter>
          <ArchitecturePage />
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

const repos: TrackedRepository[] = [
  {
    id: "repo-1",
    github_repo_id: "1",
    source: "github",
    owner: "acme",
    name: "billing-service",
    full_name: "acme/billing-service",
    private: false,
    default_branch: "main",
    html_url: "https://github.com/acme/billing-service",
    created_at: "2026-01-01T00:00:00Z",
  },
];

const indexingJob: IndexingJob = {
  id: "job-1",
  repository_id: "repo-1",
  status: "completed",
  error_message: null,
  result_summary: { Service: 3, Endpoint: 5 },
  started_at: "2026-01-01T00:00:00Z",
  finished_at: "2026-01-01T00:05:00Z",
  created_at: "2026-01-01T00:00:00Z",
};

const repoGraph: Graph = {
  nodes: [
    { id: "n1", labels: ["Service"], properties: { name: "billing" } },
    { id: "n2", labels: ["Endpoint"], properties: { name: "/pay" } },
  ],
  edges: [{ source_id: "n1", target_id: "n2", type: "EXPOSES", properties: {} }],
};

describe("ArchitecturePage", () => {
  it("displays the Architecture heading", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(repos);
    vi.mocked(repositoriesApi.getLatestIndexingJob).mockResolvedValue(indexingJob);
    vi.mocked(repositoriesApi.getAllCrossRepositoryLinks).mockResolvedValue([]);
    vi.mocked(repositoriesApi.getAllCrossRepositoryEdges).mockResolvedValue([]);

    renderWithAuth();

    expect(await screen.findByText("Architecture")).toBeInTheDocument();
  });

  it("shows an empty state when no repositories are tracked", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue([]);
    vi.mocked(repositoriesApi.getAllCrossRepositoryLinks).mockResolvedValue([]);
    vi.mocked(repositoriesApi.getAllCrossRepositoryEdges).mockResolvedValue([]);

    renderWithAuth();

    expect(await screen.findByText("No repositories tracked yet.")).toBeInTheDocument();
  });

  it("renders one overview card per tracked repository", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(repos);
    vi.mocked(repositoriesApi.getLatestIndexingJob).mockResolvedValue(indexingJob);
    vi.mocked(repositoriesApi.getAllCrossRepositoryLinks).mockResolvedValue([]);
    vi.mocked(repositoriesApi.getAllCrossRepositoryEdges).mockResolvedValue([]);

    renderWithAuth();

    const card = await screen.findByTestId("repository-overview-graph");
    expect(card).toHaveTextContent("acme/billing-service");
  });

  it("loads a repository's graph on demand when selected", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(repos);
    vi.mocked(repositoriesApi.getLatestIndexingJob).mockResolvedValue(indexingJob);
    vi.mocked(repositoriesApi.getAllCrossRepositoryLinks).mockResolvedValue([]);
    vi.mocked(repositoriesApi.getAllCrossRepositoryEdges).mockResolvedValue([]);
    vi.mocked(repositoriesApi.getRepositoryGraph).mockResolvedValue(repoGraph);
    vi.mocked(repositoriesApi.getCrossRepositoryLinks).mockResolvedValue([]);

    renderWithAuth();

    const select = await screen.findByLabelText("Repository");
    select.dispatchEvent(new Event("change", { bubbles: true }));
    (select as HTMLSelectElement).value = "repo-1";
    select.dispatchEvent(new Event("change", { bubbles: true }));

    expect(await screen.findByText("← Back to overview")).toBeInTheDocument();
  });

  it("surfaces a repository load failure", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockRejectedValue(new Error("Network error"));

    renderWithAuth();

    expect(await screen.findByText("Network error")).toBeInTheDocument();
  });

  it("has no detectable accessibility violations (KAN-38)", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(repos);
    vi.mocked(repositoriesApi.getLatestIndexingJob).mockResolvedValue(indexingJob);
    vi.mocked(repositoriesApi.getAllCrossRepositoryLinks).mockResolvedValue([]);
    vi.mocked(repositoriesApi.getAllCrossRepositoryEdges).mockResolvedValue([]);

    const { container } = renderWithAuth();
    await screen.findByTestId("repository-overview-graph");

    expect(await axe(container)).toHaveNoViolations();
  });
});
