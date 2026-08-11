import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "jest-axe";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { PaletteContext } from "../app/palette-context";
import { MissionControlPage } from "./MissionControlPage";
import * as systemApi from "../lib/api/system";
import * as githubApi from "../lib/api/github";
import * as workflowsApi from "../lib/api/workflows";
import * as architectureApi from "../lib/api/architecture";
import * as investigationIntelligenceApi from "../lib/api/investigationIntelligence";
import * as reportsApi from "../lib/api/reports";
import type { SystemStatusResponse } from "../lib/api/system";
import type { GitHubConnectionStatus } from "../types/github";
import type { ListWorkflowsParams } from "../lib/api/workflows";
import type { WorkflowListItem, WorkflowListResponse } from "../types/agent";
import type { ArchitectureSummary } from "../types/architecture";
import type { InvestigationIntelligenceSummaryResponse } from "../lib/api/investigationIntelligence";
import type { ReportSummary } from "../lib/api/reports";

vi.mock("../lib/api/system", () => ({ getSystemStatus: vi.fn() }));
vi.mock("../lib/api/github", () => ({ getConnectionStatus: vi.fn() }));
vi.mock("../lib/api/workflows", () => ({ listWorkflows: vi.fn() }));
vi.mock("../lib/api/architecture", () => ({ getArchitectureSummary: vi.fn() }));
vi.mock("../lib/api/investigationIntelligence", () => ({
  getInvestigationIntelligenceSummary: vi.fn(),
}));
vi.mock("../lib/api/reports", () => ({ listReports: vi.fn() }));

const healthyStatus: SystemStatusResponse = {
  platform_status: "healthy",
  environment: "development",
  version: "0.1.0",
  ai_provider: { name: "groq", configured: true, active: true, model: "llama-3.3-70b-versatile" },
  ai_providers: [{ name: "groq", configured: true, active: true, model: "llama-3.3-70b-versatile" }],
  connections: [
    { name: "Neo4j", status: "connected", detail: "bolt://localhost:7687" },
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

const emptyWorkflowResponse: WorkflowListResponse = {
  items: [],
  page: 1,
  page_size: 10,
  total: 0,
  has_more: false,
};

function makeWorkflowItem(overrides: Partial<WorkflowListItem> = {}): WorkflowListItem {
  return {
    workflow_id: "wf-1",
    title: "Add rate limiting to gateway",
    workflow_type: "planning",
    current_stage: "engineering_review",
    status: "awaiting_approval",
    stages: [
      { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
      { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
      {
        stage: "engineering_review",
        label: "Engineering Review",
        status: "completed",
        run_id: "run-3",
      },
    ],
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-02T10:00:00Z",
    approved_by: null,
    version: 1,
    parent_workflow_id: null,
    ...overrides,
  };
}

const emptyArchitectureSummary: ArchitectureSummary = {
  total_repositories: 0,
  total_nodes: 0,
  total_cross_repository_edges: 0,
  repositories: [],
  domains: [],
  unindexed_count: 0,
  stale_count: 0,
};

const emptyInvestigationSummary: InvestigationIntelligenceSummaryResponse = {
  window_days: 30,
  total_provider_events: 0,
  total_investigations: 0,
  providers: [],
  confidence_improvement_distribution: [],
  latency_distribution: [],
  cycles_by_terminal_outcome: [],
  priority_boost_usage: {
    total_events: 0,
    boosted_events: 0,
    boost_usage_rate: 0,
    memory_influenced_events: 0,
    memory_hit_rate: 0,
  },
  repeated_failure_groups: [],
};

/** Wires listWorkflows' mock to answer every distinct query this page
 * issues (awaiting_approval / awaiting_clarification / in_progress / the
 * unfiltered "recent" feed) from one table of canned responses, keyed by
 * status the same way the real endpoint is queried. */
function mockWorkflowsByStatus(byStatus: Partial<Record<string, WorkflowListItem[]>>) {
  vi.mocked(workflowsApi.listWorkflows).mockImplementation(
    (_token: string, params: ListWorkflowsParams = {}) => {
      const items = params.status ? (byStatus[params.status] ?? []) : (byStatus["__all__"] ?? []);
      return Promise.resolve({ ...emptyWorkflowResponse, items, total: items.length });
    },
  );
}

function renderPage() {
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

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const openPalette = vi.fn();

  return {
    openPalette,
    ...render(
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={authValue}>
          <PaletteContext.Provider value={{ openPalette }}>
            <MemoryRouter>
              <MissionControlPage />
            </MemoryRouter>
          </PaletteContext.Provider>
        </AuthContext.Provider>
      </QueryClientProvider>,
    ),
  };
}

function mockEverythingEmpty() {
  vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
  vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);
  mockWorkflowsByStatus({});
  vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(emptyArchitectureSummary);
  vi.mocked(investigationIntelligenceApi.getInvestigationIntelligenceSummary).mockResolvedValue(
    emptyInvestigationSummary,
  );
  vi.mocked(reportsApi.listReports).mockResolvedValue([]);
}

describe("MissionControlPage", () => {
  it("displays the Mission Control heading and subtitle", async () => {
    mockEverythingEmpty();
    renderPage();

    expect(await screen.findByText("Mission Control")).toBeInTheDocument();
    expect(screen.getByText("Your engineering intelligence command center")).toBeInTheDocument();
  });

  it("opens the shared command palette from the header search affordance, not a second search system", async () => {
    mockEverythingEmpty();
    const user = userEvent.setup();
    const { openPalette } = renderPage();

    await user.click(await screen.findByRole("button", { name: /Search GraphForge/ }));
    expect(openPalette).toHaveBeenCalledTimes(1);
  });

  it("shows a strong all-clear empty state when nothing needs attention", async () => {
    mockEverythingEmpty();
    renderPage();

    expect(await screen.findByText("You're all clear")).toBeInTheDocument();
    expect(screen.getByText("GraphForge has nothing waiting for you.")).toBeInTheDocument();
  });

  it("surfaces workflows awaiting approval and clarification under Needs your attention", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);
    mockWorkflowsByStatus({
      awaiting_approval: [makeWorkflowItem({ workflow_id: "wf-approve", title: "Ready mission" })],
      awaiting_clarification: [
        makeWorkflowItem({
          workflow_id: "wf-clarify",
          title: "Blocked mission",
          status: "awaiting_clarification",
        }),
      ],
    });
    vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(emptyArchitectureSummary);
    vi.mocked(investigationIntelligenceApi.getInvestigationIntelligenceSummary).mockResolvedValue(
      emptyInvestigationSummary,
    );
    vi.mocked(reportsApi.listReports).mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText("Ready mission")).toBeInTheDocument();
    expect(screen.getByText("Blocked mission")).toBeInTheDocument();
    expect(screen.getByText("Context Discovery needs an answer")).toBeInTheDocument();
    // Badge count: both items.
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("elevates an unhealthy platform status into Needs your attention", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue({
      ...healthyStatus,
      platform_status: "degraded",
    });
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);
    mockWorkflowsByStatus({});
    vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(emptyArchitectureSummary);
    vi.mocked(investigationIntelligenceApi.getInvestigationIntelligenceSummary).mockResolvedValue(
      emptyInvestigationSummary,
    );
    vi.mocked(reportsApi.listReports).mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText("Platform issue")).toBeInTheDocument();
    expect(
      screen.getByText("Degraded — check AI provider and connection configuration"),
    ).toBeInTheDocument();
  });

  it("shows in-progress workflows under Active missions with their pipeline", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);
    mockWorkflowsByStatus({
      in_progress: [
        makeWorkflowItem({
          workflow_id: "wf-active",
          title: "Investigate flaky ingestion",
          status: "in_progress",
        }),
      ],
    });
    vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(emptyArchitectureSummary);
    vi.mocked(investigationIntelligenceApi.getInvestigationIntelligenceSummary).mockResolvedValue(
      emptyInvestigationSummary,
    );
    vi.mocked(reportsApi.listReports).mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText("Investigate flaky ingestion")).toBeInTheDocument();
  });

  it("shows a neutral empty state under Active missions when nothing is running", async () => {
    mockEverythingEmpty();
    renderPage();

    expect(await screen.findByText("Nothing in progress")).toBeInTheDocument();
  });

  it("labels repeated retrieval failures as a retrieval issue, never as a code bug", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);
    mockWorkflowsByStatus({});
    vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(emptyArchitectureSummary);
    vi.mocked(investigationIntelligenceApi.getInvestigationIntelligenceSummary).mockResolvedValue({
      ...emptyInvestigationSummary,
      repeated_failure_groups: [
        {
          scope_type: "repository",
          scope_id: "repo-1",
          capability: "documentation",
          provider: "confluence",
          failure_count: 5,
          most_recent_at: "2026-01-02T10:00:00Z",
        },
      ],
    });
    vi.mocked(reportsApi.listReports).mockResolvedValue([]);

    renderPage();

    expect(
      await screen.findByText("Repeated knowledge retrieval failures detected"),
    ).toBeInTheDocument();
    expect(screen.getByText("confluence · documentation")).toBeInTheDocument();
    expect(screen.getByText(/5 retrieval failures/)).toBeInTheDocument();
    // Never phrased as a code-level engineering failure.
    expect(screen.queryByText(/engineering failure/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/bug/i)).not.toBeInTheDocument();
  });

  it("shows an intentional empty state when there are no agent insights", async () => {
    mockEverythingEmpty();
    renderPage();

    expect(await screen.findByText("No new agent insights")).toBeInTheDocument();
    expect(
      screen.getByText("GraphForge hasn't surfaced anything requiring attention."),
    ).toBeInTheDocument();
  });

  it("shows real Knowledge Coverage counts, never a fabricated percentage", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);
    mockWorkflowsByStatus({});
    vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue({
      total_repositories: 17,
      total_nodes: 1284,
      total_cross_repository_edges: 342,
      repositories: [],
      domains: [],
      unindexed_count: 2,
      stale_count: 3,
    });
    vi.mocked(investigationIntelligenceApi.getInvestigationIntelligenceSummary).mockResolvedValue(
      emptyInvestigationSummary,
    );
    vi.mocked(reportsApi.listReports).mockResolvedValue([]);

    renderPage();

    // Wait on a data-bearing value, not the section heading — the heading
    // itself is also present in the loading-skeleton render, so awaiting
    // it alone doesn't guarantee the query has actually resolved yet.
    expect(await screen.findByText("1,284")).toBeInTheDocument(); // components
    expect(screen.getByText("15")).toBeInTheDocument(); // indexed = 17 - 2
    expect(screen.getByText("3")).toBeInTheDocument(); // stale
    expect(screen.getByText("2")).toBeInTheDocument(); // unindexed
    expect(screen.getByText("342")).toBeInTheDocument(); // cross-repo edges
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("shows a compact System health strip driven by real provider/connection data", async () => {
    mockEverythingEmpty();
    renderPage();

    expect(await screen.findByText("groq")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("merges recent workflow, report, and indexing events into Recent activity", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);
    mockWorkflowsByStatus({
      __all__: [
        makeWorkflowItem({
          workflow_id: "wf-done",
          title: "Completed mission",
          status: "completed",
          updated_at: "2026-01-05T10:00:00Z",
        }),
      ],
    });
    vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue({
      ...emptyArchitectureSummary,
      repositories: [
        {
          repository_id: "repo-1",
          name: "order-service",
          full_name: "acme/order-service",
          domain: null,
          indexing_status: "completed",
          last_indexed_at: "2026-01-04T10:00:00Z",
          node_count: 12,
          node_counts_by_label: {},
          is_stale: false,
        },
      ],
    });
    vi.mocked(investigationIntelligenceApi.getInvestigationIntelligenceSummary).mockResolvedValue(
      emptyInvestigationSummary,
    );
    const report: ReportSummary = {
      id: "report-1",
      workflow_id: "wf-done",
      workflow_title: "Completed mission",
      title: "Report for Completed mission",
      status: "completed",
      error_message: null,
      created_at: "2026-01-03T10:00:00Z",
      completed_at: "2026-01-03T10:05:00Z",
    };
    vi.mocked(reportsApi.listReports).mockResolvedValue([report]);

    renderPage();

    expect(await screen.findByText("Workflow completed")).toBeInTheDocument();
    expect(screen.getByText("Report generated")).toBeInTheDocument();
    expect(screen.getByText("Repository indexed")).toBeInTheDocument();
    expect(screen.getByText("acme/order-service")).toBeInTheDocument();
  });

  it("shows an intentional empty state when there is no recent activity", async () => {
    mockEverythingEmpty();
    renderPage();

    expect(
      await screen.findByText(
        "Nothing has happened yet — activity will appear here as GraphForge works.",
      ),
    ).toBeInTheDocument();
  });

  it("handles a failed platform-status fetch without crashing the rest of the page", async () => {
    vi.mocked(systemApi.getSystemStatus).mockRejectedValue(new Error("Network error"));
    vi.mocked(githubApi.getConnectionStatus).mockRejectedValue(new Error("Network error"));
    mockWorkflowsByStatus({});
    vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(emptyArchitectureSummary);
    vi.mocked(investigationIntelligenceApi.getInvestigationIntelligenceSummary).mockResolvedValue(
      emptyInvestigationSummary,
    );
    vi.mocked(reportsApi.listReports).mockResolvedValue([]);

    renderPage();

    // The page itself, and sections independent of system-status, still render.
    expect(await screen.findByText("Mission Control")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("Nothing in progress")).toBeInTheDocument();
    });
  });

  it("has no detectable accessibility violations (KAN-38)", async () => {
    vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
    vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);
    mockWorkflowsByStatus({
      awaiting_approval: [makeWorkflowItem()],
    });
    vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue({
      ...emptyArchitectureSummary,
      total_repositories: 5,
      total_nodes: 40,
      total_cross_repository_edges: 6,
      unindexed_count: 1,
    });
    vi.mocked(investigationIntelligenceApi.getInvestigationIntelligenceSummary).mockResolvedValue(
      emptyInvestigationSummary,
    );
    vi.mocked(reportsApi.listReports).mockResolvedValue([]);

    const { container } = renderPage();
    await screen.findByText("Add rate limiting to gateway");
    await screen.findByText("Nothing in progress");

    expect(await axe(container)).toHaveNoViolations();
  });

  // The case above deliberately has no active missions, so it never renders
  // a compact PipelineGraph — the representation Active Missions actually
  // ships. This covers that gap.
  describe("with active missions on screen", () => {
    const missionA = makeWorkflowItem({
      workflow_id: "wf-a",
      title: "Investigate ingestion failures",
      status: "in_progress",
      stages: [
        { stage: "context", label: "Context Discovery", status: "completed", run_id: "run-a1" },
        { stage: "planning", label: "Planning", status: "running", run_id: "run-a2" },
        { stage: "development", label: "Development", status: "queued", run_id: "run-a3" },
        { stage: "docs", label: "Documentation Planning", status: "pending", run_id: null },
        { stage: "review", label: "Engineering Review", status: "pending", run_id: null },
      ],
    });
    const missionB = makeWorkflowItem({
      workflow_id: "wf-b",
      title: "Bump connection pool size",
      status: "in_progress",
      stages: [
        { stage: "context", label: "Context Discovery", status: "completed", run_id: "run-b1" },
        { stage: "planning", label: "Planning", status: "partial", run_id: "run-b2" },
        { stage: "development", label: "Development", status: "failed", run_id: "run-b3" },
      ],
    });

    function mockActiveMissions() {
      vi.mocked(systemApi.getSystemStatus).mockResolvedValue(healthyStatus);
      vi.mocked(githubApi.getConnectionStatus).mockResolvedValue(githubConnected);
      mockWorkflowsByStatus({ in_progress: [missionA, missionB] });
      vi.mocked(architectureApi.getArchitectureSummary).mockResolvedValue(emptyArchitectureSummary);
      vi.mocked(investigationIntelligenceApi.getInvestigationIntelligenceSummary).mockResolvedValue(
        emptyInvestigationSummary,
      );
      vi.mocked(reportsApi.listReports).mockResolvedValue([]);
    }

    it("has no detectable accessibility violations while compact pipelines are rendered", async () => {
      mockActiveMissions();
      const { container } = renderPage();
      await screen.findByText("Investigate ingestion failures");
      expect(screen.getAllByRole("list", { name: /^Workflow pipeline for / })).toHaveLength(2);

      expect(await axe(container)).toHaveNoViolations();
    });

    it("gives each mission's pipeline an accessible name identifying that mission", async () => {
      mockActiveMissions();
      renderPage();
      expect(
        await screen.findByRole("list", {
          name: "Workflow pipeline for Investigate ingestion failures",
        }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("list", { name: "Workflow pipeline for Bump connection pool size" }),
      ).toBeInTheDocument();
    });

    it("keeps stage accessible names complete even though the visible labels truncate", async () => {
      mockActiveMissions();
      renderPage();
      const pipeline = await screen.findByRole("list", {
        name: "Workflow pipeline for Investigate ingestion failures",
      });
      // "Documentation Planning" renders visually as roughly "Docu…" at
      // Mission Control's card width; the accessible name must not truncate.
      expect(
        within(pipeline).getByRole("button", { name: "Documentation Planning: Queued" }),
      ).toBeInTheDocument();
      expect(
        within(pipeline).getByRole("button", { name: "Context Discovery: Complete" }),
      ).toBeInTheDocument();
      expect(
        within(pipeline).getByRole("button", { name: "Planning: Running…" }),
      ).toBeInTheDocument();
    });
  });
});
