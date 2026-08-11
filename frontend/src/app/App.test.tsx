import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { routes } from "./router";
import { AiModelProvider } from "./AiModelContext";
import { AuthProvider } from "./AuthContext";
import { ThemeProvider } from "../theme/ThemeProvider";
import * as authApi from "../lib/api/auth";
import * as githubApi from "../lib/api/github";
import * as systemApi from "../lib/api/system";
import * as repositoriesApi from "../lib/api/repositories";
import * as analysisApi from "../lib/api/analysis";
import { ApiError } from "../lib/api/client";
import type { AIAnalysisResult } from "../types/analysis";
import type { User } from "../types/auth";
import type { TrackedRepository } from "../types/github";
import type { PullRequest } from "../types/pullRequest";

const FAKE_USER: User = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "ada@example.com",
  full_name: "Ada Lovelace",
  auth_provider: "local",
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
};

function renderApp(initialPath = "/") {
  const router = createMemoryRouter(routes, { initialEntries: [initialPath] });
  // A fresh QueryClient per render — see MissionControlPage.test.tsx's
  // equivalent comment for why (this file's dashboard route now renders
  // MissionControlPage, which uses useQuery as of KAN-37).
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <AiModelProvider>
            <RouterProvider router={router} />
          </AiModelProvider>
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

const FAKE_REPO: TrackedRepository = {
  id: "repo-1",
  github_repo_id: "local-1",
  source: "local",
  owner: "local",
  name: "order-service",
  full_name: "local/order-service",
  private: false,
  default_branch: "main",
  html_url: "/demo/repositories/order-service",
  created_at: "2026-07-01T00:00:00Z",
};

const FAKE_PR: PullRequest = {
  id: "pr-1",
  number: 1,
  title: "Rename OrderCreated.total to totalCents",
  state: "open",
  is_draft: false,
  author_login: "tester",
  html_url: "https://example.invalid/pr/1",
  head_ref: "pr-1",
  base_ref: "main",
  github_created_at: "2026-07-20T00:00:00Z",
  github_updated_at: "2026-07-20T00:00:00Z",
};

const NOT_FOUND = new ApiError(404, "not_found", "not found");

const _FAKE_AI_RESULT: AIAnalysisResult = {
  executive_summary: "No breaking changes.",
  breaking_changes: [],
  migration_advice: [],
  suggested_reviewers: [],
  regression_tests: [],
  release_coordination_plan: {
    deployment_order: [],
    repositories_to_notify: [],
    rollout_strategy: "",
    backward_compatibility_advice: "",
    communication_summary: "",
    rollout_risks: [],
  },
  confidence: { score: 0.9, reasoning: "Clear analysis" },
  prompt_version: "1.4",
};

describe("App navigation (authenticated)", () => {
  beforeEach(() => {
    localStorage.setItem("graphforge.token", "fake-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue(FAKE_USER);
    vi.spyOn(systemApi, "getSystemStatus").mockResolvedValue({
      platform_status: "healthy",
      environment: "development",
      version: "0.1.0",
      ai_provider: { name: "openai", configured: true, active: true, model: "gpt-4o" },
      ai_providers: [{ name: "openai", configured: true, active: true, model: "gpt-4o" }],
      connections: [{ name: "PostgreSQL", status: "connected", detail: null }],
      knowledge_base: {
        repositories_tracked: 0,
        repositories_indexed: 0,
        repositories_pending: 0,
        repositories_graph_missing: 0,
      },
    });
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue({
      connected: false,
      github_username: null,
      connected_at: null,
      auth_method: null,
      scope_warning: null,
    });
  });

  afterEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders the sidebar with a link to every page", async () => {
    renderApp();
    const nav = await screen.findByRole("navigation");

    for (const label of [
      "Mission Control",
      "AI Workspace",
      "New Workflow",
      "Runs",
      "Repositories",
      "Architecture",
      "Reports",
      "Settings",
    ]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
  });

  it("defaults to the Mission Control page", async () => {
    renderApp();
    // Topbar shows the same label as plain chrome (a <p>, not a heading —
    // it mirrors the sidebar selection); only the page itself renders it
    // as the real <h1>, so this assertion is unambiguous either way.
    expect(
      await screen.findByRole("heading", { level: 1, name: "Mission Control" }),
    ).toBeInTheDocument();
  });

  it("navigates to AI Workspace when its sidebar link is clicked", async () => {
    const user = userEvent.setup();
    renderApp();

    const nav = await screen.findByRole("navigation");
    await user.click(within(nav).getByRole("link", { name: "AI Workspace" }));

    expect(
      await screen.findByRole("heading", { level: 1, name: "AI Workspace" }),
    ).toBeInTheDocument();
  });

  it.each([
    ["/repositories", "Repositories"],
    ["/architecture", "Architecture"],
    ["/reports", "Reports"],
    ["/settings", "Settings"],
  ])("renders the %s page at %s", async (path, heading) => {
    // Both RepositoriesPage (via useDashboardData) and ArchitecturePage
    // call this eagerly on mount and it wasn't mocked here — with no
    // tracked repos, an unmocked call fell through to whatever is actually
    // listening on the configured API base URL. Mocking it keeps this test
    // hermetic regardless of what else is reachable on that URL.
    vi.spyOn(githubApi, "listTrackedRepositories").mockResolvedValue([]);
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
  });

  it("renders the repository detail page and offers to run indexing", async () => {
    vi.spyOn(githubApi, "listTrackedRepositories").mockResolvedValue([FAKE_REPO]);
    vi.spyOn(repositoriesApi, "listPullRequests").mockResolvedValue([FAKE_PR]);
    vi.spyOn(repositoriesApi, "getLatestIndexingJob").mockRejectedValue(NOT_FOUND);

    renderApp("/repositories/repo-1");

    expect(
      await screen.findByRole("heading", { level: 1, name: "local/order-service" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run indexing" })).toBeInTheDocument();
    expect(await screen.findByText("Rename OrderCreated.total to totalCents")).toBeInTheDocument();
  });

  it("removes the repository and navigates away when the confirm dialog is accepted", async () => {
    const user = userEvent.setup();
    vi.spyOn(githubApi, "listTrackedRepositories").mockResolvedValue([FAKE_REPO]);
    vi.spyOn(repositoriesApi, "listPullRequests").mockResolvedValue([FAKE_PR]);
    vi.spyOn(repositoriesApi, "getLatestIndexingJob").mockRejectedValue(NOT_FOUND);
    vi.spyOn(repositoriesApi, "removeRepository").mockResolvedValue(undefined);
    // This test navigates to /repositories after removal, which renders
    // RepositoriesPage's useDashboardData — it calls this per pull request
    // and it wasn't mocked here, so it fell through to whatever is
    // actually reachable at the configured API base URL.
    vi.spyOn(analysisApi, "getDeterministicAnalysis").mockRejectedValue(NOT_FOUND);

    renderApp("/repositories/repo-1");
    await screen.findByRole("heading", { level: 1, name: "local/order-service" });

    // Opens the in-app ConfirmDialog (was window.confirm) — removal only
    // happens once its destructive action is chosen, so the first click
    // must not delete anything on its own.
    await user.click(screen.getByRole("button", { name: "Remove repository" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(repositoriesApi.removeRepository).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Remove repository" }));
    expect(repositoriesApi.removeRepository).toHaveBeenCalledWith("fake-token", "repo-1");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Repositories" }),
    ).toBeInTheDocument();
  });

  it("does not remove the repository when the confirm dialog is declined", async () => {
    const user = userEvent.setup();
    vi.spyOn(githubApi, "listTrackedRepositories").mockResolvedValue([FAKE_REPO]);
    vi.spyOn(repositoriesApi, "listPullRequests").mockResolvedValue([FAKE_PR]);
    vi.spyOn(repositoriesApi, "getLatestIndexingJob").mockRejectedValue(NOT_FOUND);
    const removeSpy = vi.spyOn(repositoriesApi, "removeRepository");

    renderApp("/repositories/repo-1");
    await screen.findByRole("heading", { level: 1, name: "local/order-service" });

    await user.click(screen.getByRole("button", { name: "Remove repository" }));
    const dialog = await screen.findByRole("alertdialog");

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(removeSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "local/order-service" }),
    ).toBeInTheDocument();
  });

  it("renders the pull request detail page with analysis trigger buttons", async () => {
    vi.spyOn(githubApi, "listTrackedRepositories").mockResolvedValue([FAKE_REPO]);
    vi.spyOn(repositoriesApi, "listPullRequests").mockResolvedValue([FAKE_PR]);
    vi.spyOn(analysisApi, "getDeterministicAnalysis").mockRejectedValue(NOT_FOUND);
    vi.spyOn(analysisApi, "getAiAnalysis").mockRejectedValue(NOT_FOUND);

    renderApp("/pull-requests/pr-1");

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Rename OrderCreated.total to totalCents",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run analysis" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run AI analysis" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Investigate (Agent)" })).toBeInTheDocument();

    // AI model selector: defaults to GPT-5, shows provider/reasoning/cost info.
    expect(screen.getByLabelText("AI model")).toHaveValue("gpt-5");
    expect(screen.getByText("OpenAI")).toBeInTheDocument();
    expect(screen.getByText("~₹3 / PR Analysis")).toBeInTheDocument();
  });

  it("runs the investigation agent and shows its reasoning log, cleared on a plain AI run", async () => {
    const user = userEvent.setup();
    vi.spyOn(githubApi, "listTrackedRepositories").mockResolvedValue([FAKE_REPO]);
    vi.spyOn(repositoriesApi, "listPullRequests").mockResolvedValue([FAKE_PR]);
    vi.spyOn(analysisApi, "getDeterministicAnalysis").mockRejectedValue(NOT_FOUND);
    vi.spyOn(analysisApi, "getAiAnalysis").mockRejectedValue(NOT_FOUND);
    vi.spyOn(analysisApi, "investigatePullRequest").mockResolvedValue({
      ..._FAKE_AI_RESULT,
      executive_summary: "Agent-generated summary.",
      reasoning_log: [
        {
          step_number: 1,
          goal: "Determine whether this change touches the indexed architecture graph.",
          plan: "Always map changed files to graph nodes first.",
          tool_selected: "read_dependency_graph",
          observation: { tool_name: "read_dependency_graph", summary: "Matched 1 node." },
          decision: "Proceeding to decide whether downstream traversal is warranted.",
        },
      ],
    });
    vi.spyOn(analysisApi, "runAiAnalysis").mockResolvedValue(_FAKE_AI_RESULT);

    renderApp("/pull-requests/pr-1");
    await screen.findByRole("button", { name: "Investigate (Agent)" });

    await user.click(screen.getByRole("button", { name: "Investigate (Agent)" }));

    expect(await screen.findByText("Agent-generated summary.")).toBeInTheDocument();
    expect(screen.getByText("Agent reasoning log")).toBeInTheDocument();
    expect(screen.getByText("read_dependency_graph")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Re-run AI analysis" }));

    expect(await screen.findByText(_FAKE_AI_RESULT.executive_summary)).toBeInTheDocument();
    expect(screen.queryByText("Agent reasoning log")).not.toBeInTheDocument();
  });

  it("shows the logged-in user's name and logs out via the sidebar", async () => {
    const user = userEvent.setup();
    renderApp();

    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();

    await user.click(screen.getByTitle("Log out"));

    expect(
      await screen.findByRole("heading", { name: "Sign in to GraphForge" }),
    ).toBeInTheDocument();
    expect(localStorage.getItem("graphforge.token")).toBeNull();
  });
});

describe("App navigation (unauthenticated)", () => {
  afterEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("redirects a page route to /login when there is no token", async () => {
    renderApp("/pull-requests");

    expect(
      await screen.findByRole("heading", { name: "Sign in to GraphForge" }),
    ).toBeInTheDocument();
  });

  it("drops an invalid token and redirects to /login", async () => {
    localStorage.setItem("graphforge.token", "invalid-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockRejectedValue(new Error("401"));

    renderApp("/");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Sign in to GraphForge" })).toBeInTheDocument(),
    );
    expect(localStorage.getItem("graphforge.token")).toBeNull();
  });
});
