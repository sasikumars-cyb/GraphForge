import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "jest-axe";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { ArchitecturePage } from "./ArchitecturePage";
import * as architectureApi from "../lib/api/architecture";
import * as repositoriesApi from "../lib/api/repositories";
import type { ArchitectureSummary } from "../types/architecture";
import type { Graph, GraphNode } from "../types/graph";

vi.mock("../lib/api/architecture", () => ({
  getArchitectureSummary: vi.fn(),
}));

vi.mock("../lib/api/repositories", () => ({
  getRepositoryGraph: vi.fn(),
  getRepositoryGraphTypes: vi.fn(),
  getRepositoryGraphNodeNeighbors: vi.fn(),
}));

// This page's own responsibility is navigation/data-fetching — the graph
// rendering itself (React Flow, dagre, ResizeObserver) isn't exercised
// here. Stubbed to a plain node list a test can click through, forwarding
// `onNodeSelect` the same way the real component does.
vi.mock("../components/graph/DependencyGraph", () => ({
  DependencyGraph: ({
    graph,
    onNodeSelect,
  }: {
    graph: { nodes: GraphNode[] };
    onNodeSelect?: (node: GraphNode | null) => void;
  }) => (
    <div data-testid="dependency-graph">
      {graph.nodes.length} nodes
      {graph.nodes.map((n) => (
        <button key={n.id} onClick={() => onNodeSelect?.(n)}>
          select {String(n.properties.name ?? n.id)}
        </button>
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

  // A fresh QueryClient per render — a shared/module-level client would
  // leak cached responses across tests in this file. `retry: false` so a
  // rejected mock response fails the query immediately.
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

const EMPTY_SUMMARY: ArchitectureSummary = {
  total_repositories: 0,
  total_nodes: 0,
  total_cross_repository_edges: 0,
  repositories: [],
  domains: [],
  unindexed_count: 0,
  stale_count: 0,
};

const SUMMARY: ArchitectureSummary = {
  total_repositories: 2,
  total_nodes: 42,
  total_cross_repository_edges: 1,
  unindexed_count: 0,
  stale_count: 0,
  domains: [
    { domain: "Payments", repository_count: 1, node_count: 30 },
    { domain: null, repository_count: 1, node_count: 12 },
  ],
  repositories: [
    {
      repository_id: "repo-1",
      name: "billing-service",
      full_name: "acme/billing-service",
      domain: "Payments",
      indexing_status: "completed",
      last_indexed_at: "2026-08-01T00:00:00Z",
      node_count: 30,
      node_counts_by_label: { Service: 30 },
      is_stale: false,
    },
    {
      repository_id: "repo-2",
      name: "notes",
      full_name: "acme/notes",
      domain: null,
      indexing_status: "completed",
      last_indexed_at: "2026-08-01T00:00:00Z",
      node_count: 12,
      node_counts_by_label: { Module: 12 },
      is_stale: false,
    },
  ],
};

const REPO_GRAPH: Graph = {
  nodes: [
    { id: "n1", labels: ["GraphNode", "Service"], properties: { name: "billing" } },
    { id: "n2", labels: ["GraphNode", "Endpoint"], properties: { name: "/pay" } },
  ],
  edges: [{ source_id: "n1", target_id: "n2", type: "EXPOSES", properties: {} }],
  truncated: false,
  total_node_count: 2,
  next_cursor: null,
};

const NEIGHBORS_GRAPH: Graph = {
  nodes: [
    { id: "n1", labels: ["GraphNode", "Service"], properties: { name: "billing", hop_distance: 0 } },
    { id: "n3", labels: ["GraphNode", "Component"], properties: { name: "helper", hop_distance: 1 } },
  ],
  edges: [{ source_id: "n1", target_id: "n3", type: "CALLS", properties: {} }],
};

describe("ArchitecturePage", () => {
  it("displays the Architecture heading", async () => {
    vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(SUMMARY);
    renderWithAuth();
    expect(await screen.findByText("Architecture")).toBeInTheDocument();
  });

  it("shows an empty state when no repositories are tracked", async () => {
    vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(EMPTY_SUMMARY);
    renderWithAuth();

    expect(await screen.findByText("Your architecture graph appears here")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect GitHub" })).toBeInTheDocument();
  });

  it("surfaces a summary load failure", async () => {
    vi.mocked(architectureApi.getArchitectureSummary).mockRejectedValue(new Error("Network error"));
    renderWithAuth();

    expect(await screen.findByText("Failed to load the architecture summary.")).toBeInTheDocument();
  });

  describe("landing view", () => {
    it("shows org-wide stats", async () => {
      vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(SUMMARY);
      renderWithAuth();

      expect(await screen.findByText("42")).toBeInTheDocument(); // total nodes
      expect(screen.getByText("2")).toBeInTheDocument(); // total repositories
    });

    it("shows a domain card and an ungrouped repository", async () => {
      vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(SUMMARY);
      renderWithAuth();

      expect(await screen.findByRole("button", { name: /Payments/ })).toBeInTheDocument();
      expect(await screen.findByText("acme/notes")).toBeInTheDocument();
    });

    it("filters repositories by search", async () => {
      const user = userEvent.setup();
      vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(SUMMARY);
      renderWithAuth();
      await screen.findByText("acme/notes");

      await user.type(screen.getByLabelText("Search repositories and domains"), "billing");

      expect(await screen.findByText("acme/billing-service")).toBeInTheDocument();
      expect(screen.queryByText("acme/notes")).not.toBeInTheDocument();
    });
  });

  describe("drill-down navigation", () => {
    it("domain card -> domain view -> breadcrumb back to landing", async () => {
      const user = userEvent.setup();
      vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(SUMMARY);
      renderWithAuth();

      await user.click(await screen.findByRole("button", { name: /Payments/ }));

      expect(await screen.findByText("acme/billing-service")).toBeInTheDocument();
      expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent(
        "Architecture" + "Payments",
      );

      await user.click(screen.getByRole("button", { name: "Architecture" }));

      expect(await screen.findByRole("button", { name: /Payments/ })).toBeInTheDocument();
    });

    it("selecting a repository loads its graph lazily", async () => {
      // acme/notes (repo-2) is ungrouped — reachable directly from the
      // landing view without an intermediate domain click, keeping this
      // test focused on graph lazy-loading rather than domain navigation
      // (covered separately above).
      const user = userEvent.setup();
      vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(SUMMARY);
      vi.mocked(repositoriesApi.getRepositoryGraphTypes).mockResolvedValue({ counts: { Module: 12 } });
      vi.mocked(repositoriesApi.getRepositoryGraph).mockResolvedValue(REPO_GRAPH);
      renderWithAuth();

      await user.click(await screen.findByText("acme/notes"));

      expect(await screen.findByTestId("dependency-graph")).toHaveTextContent("2 nodes");
      expect(repositoriesApi.getRepositoryGraph).toHaveBeenCalledWith(
        "test-token",
        "repo-2",
        expect.objectContaining({ limit: 500 }),
        expect.anything(),
      );
    });

    it("node click opens the detail panel; explore neighbors pivots to the neighborhood view", async () => {
      const user = userEvent.setup();
      vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(SUMMARY);
      vi.mocked(repositoriesApi.getRepositoryGraphTypes).mockResolvedValue({ counts: {} });
      vi.mocked(repositoriesApi.getRepositoryGraph).mockResolvedValue(REPO_GRAPH);
      vi.mocked(repositoriesApi.getRepositoryGraphNodeNeighbors).mockResolvedValue(NEIGHBORS_GRAPH);
      renderWithAuth();

      await user.click(await screen.findByText("acme/notes"));
      await screen.findByTestId("dependency-graph");
      await user.click(screen.getByRole("button", { name: "select billing" }));

      expect(await screen.findByRole("heading", { name: "billing" })).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Explore neighbors" }));

      await waitFor(() =>
        expect(repositoriesApi.getRepositoryGraphNodeNeighbors).toHaveBeenCalledWith(
          "test-token",
          "repo-2",
          "n1",
          expect.objectContaining({ hops: 1 }),
          expect.anything(),
        ),
      );
      expect(await screen.findByTestId("dependency-graph")).toHaveTextContent("2 nodes");
      expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("billing");
    });
  });

  it("has no detectable accessibility violations (KAN-38)", async () => {
    vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(SUMMARY);

    const { container } = renderWithAuth();
    await screen.findByText("acme/notes");

    expect(await axe(container)).toHaveNoViolations();
  });
});
