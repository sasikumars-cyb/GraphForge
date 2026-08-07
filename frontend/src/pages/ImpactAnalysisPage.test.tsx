import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "jest-axe";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { ImpactAnalysisPage } from "./ImpactAnalysisPage";
import * as githubApi from "../lib/api/github";
import * as impactApi from "../lib/api/impact";
import * as agentRunsApi from "../lib/api/agentRuns";
import type { TrackedRepository } from "../types/github";
import type { BlastRadius } from "../types/impact";
import type { GraphNode } from "../types/graph";
import type { RunDetail } from "../types/agent";

vi.mock("../lib/api/github", () => ({
  listTrackedRepositories: vi.fn(),
}));
vi.mock("../lib/api/impact", () => ({
  getBlastRadius: vi.fn(),
}));
vi.mock("../lib/api/agentRuns", () => ({
  createAgentRun: vi.fn(),
  getAgentRun: vi.fn(),
}));

// This page's job is orchestration (repo selection -> blast radius fetch
// -> report generation), not graph rendering (ReactFlow/dagre) — stubbed
// the same way ArchitecturePage.test.tsx stubs DependencyGraph, forwarding
// onNodeSelect the same way the real component does.
vi.mock("../components/impact/BlastRadiusGraph", () => ({
  BlastRadiusGraph: ({
    graph,
    onNodeSelect,
  }: {
    graph: { nodes: GraphNode[] };
    onNodeSelect?: (node: GraphNode | null) => void;
  }) => (
    <div data-testid="blast-radius-graph">
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
          <ImpactAnalysisPage />
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

const BLAST_RADIUS: BlastRadius = {
  seed_node_id: "repo-1:repository",
  max_hops: 2,
  graph: {
    nodes: [
      { id: "repo-1:repository", labels: ["GraphNode", "Repository"], properties: { hop_distance: 0 } },
      {
        id: "repo-1:svc",
        labels: ["GraphNode", "Service"],
        properties: { name: "billing", hop_distance: 1 },
      },
    ],
    edges: [{ source_id: "repo-1:repository", target_id: "repo-1:svc", type: "CONTAINS", properties: {} }],
  },
  impacted_repositories: [],
  impacted_apis: [],
  impacted_databases: [],
  impacted_queues: [],
  relationships: [],
};

const COMPLETED_RUN: RunDetail = {
  run_id: "run-1",
  goal: "analyze_impact_analysis",
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
      agent_id: "impact_analysis",
      status: "completed",
      confidence: { score: 0.9, reasoning: "" },
      evidence: [],
      result: {
        executive_summary: "This change is low risk.",
        blast_radius_overview: "Touches 1 service.",
        directly_impacted_repositories: ["acme/billing-service"],
        indirectly_impacted_apis: [],
        indirect_impact_summary: "",
        high_risk_components: [],
        confidence_summary: { high: 1, medium: 0, low: 0 },
        risk_summary: "Low risk.",
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

describe("ImpactAnalysisPage", () => {
  it("displays the Impact Check heading", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue([]);
    renderWithAuth();
    expect(await screen.findByText("Impact Check")).toBeInTheDocument();
  });

  it("shows an empty-state hint when no repositories are tracked", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue([]);
    renderWithAuth();
    expect(
      await screen.findByText(/No tracked repositories yet/),
    ).toBeInTheDocument();
  });

  it("selecting a repository immediately renders its blast-radius graph, no submit needed", async () => {
    const user = userEvent.setup();
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(REPOS);
    vi.mocked(impactApi.getBlastRadius).mockResolvedValue(BLAST_RADIUS);
    renderWithAuth();

    await screen.findByRole("option", { name: "acme/billing-service" });
    await user.selectOptions(screen.getByLabelText("Repository"), "repo-1");

    expect(await screen.findByTestId("blast-radius-graph")).toHaveTextContent("2 nodes");
    expect(impactApi.getBlastRadius).toHaveBeenCalledWith(
      "test-token",
      "repo-1",
      expect.objectContaining({ maxHops: 2 }),
      expect.anything(),
    );
  });

  it("clicking a node opens its detail panel", async () => {
    const user = userEvent.setup();
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(REPOS);
    vi.mocked(impactApi.getBlastRadius).mockResolvedValue(BLAST_RADIUS);
    renderWithAuth();

    await screen.findByRole("option", { name: "acme/billing-service" });
    await user.selectOptions(screen.getByLabelText("Repository"), "repo-1");
    await screen.findByTestId("blast-radius-graph");
    await user.click(screen.getByRole("button", { name: "select billing" }));

    expect(await screen.findByRole("heading", { name: "billing" })).toBeInTheDocument();
    // The Architecture lens's own "explore neighbors" action doesn't apply
    // here — the blast radius already *is* a neighborhood.
    expect(screen.queryByRole("button", { name: "Explore neighbors" })).not.toBeInTheDocument();
  });

  it("shows the impacted-nodes table beneath the graph, synced to selection", async () => {
    const user = userEvent.setup();
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(REPOS);
    vi.mocked(impactApi.getBlastRadius).mockResolvedValue(BLAST_RADIUS);
    renderWithAuth();

    await screen.findByRole("option", { name: "acme/billing-service" });
    await user.selectOptions(screen.getByLabelText("Repository"), "repo-1");
    await screen.findByTestId("blast-radius-graph");

    const row = await screen.findByRole("row", { name: /billing/ });
    await user.click(row);

    expect(await screen.findByRole("heading", { name: "billing" })).toBeInTheDocument();
  });

  it("generates the detailed report on demand, not automatically", async () => {
    const user = userEvent.setup();
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(REPOS);
    vi.mocked(impactApi.getBlastRadius).mockResolvedValue(BLAST_RADIUS);
    vi.mocked(agentRunsApi.createAgentRun).mockResolvedValue({
      run_id: "run-1",
      status: "completed",
      subject: COMPLETED_RUN.subject,
      goal: "analyze_impact_analysis",
    });
    vi.mocked(agentRunsApi.getAgentRun).mockResolvedValue(COMPLETED_RUN);
    renderWithAuth();

    await screen.findByRole("option", { name: "acme/billing-service" });
    await user.selectOptions(screen.getByLabelText("Repository"), "repo-1");
    await screen.findByTestId("blast-radius-graph");

    // The report generator must not have fired on its own.
    expect(agentRunsApi.createAgentRun).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Generate detailed report" }));

    await waitFor(() => expect(agentRunsApi.createAgentRun).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("This change is low risk.")).toBeInTheDocument();
  });

  it("has no detectable accessibility violations (KAN-38)", async () => {
    vi.mocked(githubApi.listTrackedRepositories).mockResolvedValue(REPOS);
    vi.mocked(impactApi.getBlastRadius).mockResolvedValue(BLAST_RADIUS);
    const user = userEvent.setup();

    const { container } = renderWithAuth();
    await screen.findByRole("option", { name: "acme/billing-service" });
    await user.selectOptions(screen.getByLabelText("Repository"), "repo-1");
    await screen.findByTestId("blast-radius-graph");

    expect(await axe(container)).toHaveNoViolations();
  });
});
