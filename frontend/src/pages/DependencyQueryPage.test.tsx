import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "jest-axe";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { DependencyQueryPage } from "./DependencyQueryPage";
import * as githubApi from "../lib/api/github";
import * as repositoriesApi from "../lib/api/repositories";
import * as agentRunsApi from "../lib/api/agentRuns";
import type { TrackedRepository } from "../types/github";
import type { Graph, GraphNode } from "../types/graph";
import type { RunDetail } from "../types/agent";

vi.mock("../lib/api/github", () => ({
  listTrackedRepositories: vi.fn(),
}));
vi.mock("../lib/api/repositories", () => ({
  getRepositoryGraphNodeNeighbors: vi.fn(),
}));
vi.mock("../lib/api/agentRuns", () => ({
  createAgentRun: vi.fn(),
  getAgentRun: vi.fn(),
}));

// This page's own job is orchestration (repo selection -> root fetch ->
// expand-on-click -> report generation), not graph rendering (ReactFlow/
// dagre) — stubbed the same way ArchitecturePage.test.tsx/ImpactAnalysisPage
// .test.tsx stub their own graph components, forwarding onNodeSelect the
// same way the real component does.
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

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  return render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={authValue}>
        <MemoryRouter>
          <DependencyQueryPage />
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

const REPOS: TrackedRepository[] = [
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

const ROOT_GRAPH: Graph = {
  nodes: [
    { id: "repo-1:repository", labels: ["GraphNode", "Repository"], properties: {} },
    { id: "repo-1:sdk", labels: ["GraphNode", "Component"], properties: { name: "payment-sdk" } },
  ],
  edges: [{ source_id: "repo-1:repository", target_id: "repo-1:sdk", type: "CALLS", properties: {} }],
};

const EXPANDED_GRAPH: Graph = {
  nodes: [
    { id: "repo-1:sdk", labels: ["GraphNode", "Component"], properties: { name: "payment-sdk" } },
    { id: "repo-1:queue", labels: ["GraphNode", "KafkaTopic"], properties: { name: "payments-queue" } },
  ],
  edges: [{ source_id: "repo-1:sdk", target_id: "repo-1:queue", type: "PUBLISHES_TO", properties: {} }],
};

const COMPLETED_RUN: RunDetail = {
  run_id: "run-1",
  goal: "analyze_dependency_query",
  status: "completed",
  subject: { subject_id: "repo-1", subject_type: "repository", display_name: "acme/billing-service" },
  title: null,
  provider: null,
  user: null,
  repository: null,
  model: null,
  error_message: null,
  started_at: "2026-01-01T00:00:00Z",
  completed_at: "2026-01-01T00:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
  workflow_id: null,
  workflow_stage: null,
  previous_run_id: null,
  steps: [
    {
      step_id: "step-1",
      agent_id: "dependency_query",
      status: "completed",
      confidence: { score: 0.9, reasoning: "" },
      evidence: [],
      result: {
        executive_summary: "billing-service has a shallow dependency tree.",
        direct_dependencies: ["payment-sdk"],
        direct_dependencies_summary: "1 direct dependency.",
        downstream_consumers: [],
        downstream_consumers_summary: "",
        downstream_consumers_caveat: "",
        verified_relationships: [],
        candidate_relationships: [],
        confidence_breakdown: { high: 1, medium: 0, low: 0 },
        architectural_notes: [],
      },
      prompt_version: "1",
      output_ref: null,
      error_message: null,
      latency_ms: null,
      created_at: "2026-01-01T00:00:00Z",
      completed_at: "2026-01-01T00:00:00Z",
    },
  ],
};

describe("DependencyQueryPage", () => {
  it("displays the Dependency heading", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue([]);
    renderWithAuth();
    expect(await screen.findByText("Dependency")).toBeInTheDocument();
  });

  it("shows an empty-state hint when no repositories are tracked", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue([]);
    renderWithAuth();
    expect(await screen.findByText(/No tracked repositories yet/)).toBeInTheDocument();
  });

  it("selecting a repository immediately renders its dependency tree, rooted at the repository, no submit needed", async () => {
    const user = userEvent.setup();
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(REPOS);
    vi.mocked(repositoriesApi.getRepositoryGraphNodeNeighbors).mockResolvedValue(ROOT_GRAPH);
    renderWithAuth();

    await screen.findByRole("option", { name: "acme/billing-service" });
    await user.selectOptions(screen.getByLabelText("Repository"), "repo-1");

    expect(await screen.findByTestId("dependency-graph")).toHaveTextContent("2 nodes");
    expect(repositoriesApi.getRepositoryGraphNodeNeighbors).toHaveBeenCalledWith(
      "test-token",
      "repo-1",
      "repo-1:repository",
      expect.objectContaining({ direction: "outgoing" }),
      expect.anything(),
    );
  });

  it("defaults to the 'Depends on' (outgoing) direction", async () => {
    const user = userEvent.setup();
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(REPOS);
    vi.mocked(repositoriesApi.getRepositoryGraphNodeNeighbors).mockResolvedValue(ROOT_GRAPH);
    renderWithAuth();

    await screen.findByRole("option", { name: "acme/billing-service" });
    await user.selectOptions(screen.getByLabelText("Repository"), "repo-1");
    await screen.findByTestId("dependency-graph");

    expect(screen.getByRole("button", { name: "Depends on" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("toggling direction re-seeds from the root in the opposite direction", async () => {
    const user = userEvent.setup();
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(REPOS);
    vi.mocked(repositoriesApi.getRepositoryGraphNodeNeighbors).mockResolvedValue(ROOT_GRAPH);
    renderWithAuth();

    await screen.findByRole("option", { name: "acme/billing-service" });
    await user.selectOptions(screen.getByLabelText("Repository"), "repo-1");
    await screen.findByTestId("dependency-graph");

    await user.click(screen.getByRole("button", { name: "Depended on by" }));

    await waitFor(() =>
      expect(repositoriesApi.getRepositoryGraphNodeNeighbors).toHaveBeenCalledWith(
        "test-token",
        "repo-1",
        "repo-1:repository",
        expect.objectContaining({ direction: "incoming" }),
        expect.anything(),
      ),
    );
  });

  it("clicking a node then Expand dependencies grows the tree", async () => {
    const user = userEvent.setup();
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(REPOS);
    vi.mocked(repositoriesApi.getRepositoryGraphNodeNeighbors)
      .mockResolvedValueOnce(ROOT_GRAPH)
      .mockResolvedValueOnce(EXPANDED_GRAPH);
    renderWithAuth();

    await screen.findByRole("option", { name: "acme/billing-service" });
    await user.selectOptions(screen.getByLabelText("Repository"), "repo-1");
    await screen.findByTestId("dependency-graph");

    await user.click(screen.getByRole("button", { name: "select payment-sdk" }));
    expect(await screen.findByRole("heading", { name: "payment-sdk" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Expand dependencies" }));

    await waitFor(() =>
      expect(repositoriesApi.getRepositoryGraphNodeNeighbors).toHaveBeenCalledWith(
        "test-token",
        "repo-1",
        "repo-1:sdk",
        expect.objectContaining({ direction: "outgoing" }),
      ),
    );
    // The merged tree now includes the newly-expanded node too, not a
    // replacement of the previous page — 3 unique nodes total
    // (repository, sdk, queue), not 2.
    expect(await screen.findByTestId("dependency-graph")).toHaveTextContent("3 nodes");
  });

  it("generates the detailed report on demand, not automatically", async () => {
    const user = userEvent.setup();
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(REPOS);
    vi.mocked(repositoriesApi.getRepositoryGraphNodeNeighbors).mockResolvedValue(ROOT_GRAPH);
    vi.mocked(agentRunsApi.createAgentRun).mockResolvedValue({
      run_id: "run-1",
      status: "completed",
      subject: COMPLETED_RUN.subject,
      goal: "analyze_dependency_query",
    });
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(COMPLETED_RUN);
    renderWithAuth();

    await screen.findByRole("option", { name: "acme/billing-service" });
    await user.selectOptions(screen.getByLabelText("Repository"), "repo-1");
    await screen.findByTestId("dependency-graph");

    expect(agentRunsApi.createAgentRun).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Generate detailed report" }));

    await waitFor(() => expect(agentRunsApi.createAgentRun).toHaveBeenCalledTimes(1));
    expect(
      await screen.findByText("billing-service has a shallow dependency tree."),
    ).toBeInTheDocument();
  });

  it("has no detectable accessibility violations", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(REPOS);
    vi.mocked(repositoriesApi.getRepositoryGraphNodeNeighbors).mockResolvedValue(ROOT_GRAPH);
    const user = userEvent.setup();

    const { container } = renderWithAuth();
    await screen.findByRole("option", { name: "acme/billing-service" });
    await user.selectOptions(screen.getByLabelText("Repository"), "repo-1");
    await screen.findByTestId("dependency-graph");

    expect(await axe(container)).toHaveNoViolations();
  });
});
