import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { routes } from "./router";
import { AiModelProvider } from "./AiModelContext";
import { AuthProvider } from "./AuthContext";
import * as authApi from "../lib/api/auth";
import * as githubApi from "../lib/api/github";
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
  created_at: "2026-01-01T00:00:00Z",
};

function renderApp(initialPath = "/") {
  const router = createMemoryRouter(routes, { initialEntries: [initialPath] });
  return render(
    <AuthProvider>
      <AiModelProvider>
        <RouterProvider router={router} />
      </AiModelProvider>
    </AuthProvider>,
  );
}

const FAKE_REPO: TrackedRepository = {
  id: "repo-1",
  github_repo_id: "local-1",
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
      "Dashboard",
      "Pull Requests",
      "Repositories",
      "Architecture",
      "Reports",
      "Settings",
    ]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
  });

  it("defaults to the Dashboard page", async () => {
    renderApp();
    // Both the Topbar (h1) and the page itself (h2) show the label, so
    // assert on the page-level heading specifically.
    expect(
      await screen.findByRole("heading", { level: 2, name: "Welcome back to GraphForge" }),
    ).toBeInTheDocument();
  });

  it("navigates to Pull Requests when its sidebar link is clicked", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(await screen.findByRole("link", { name: "Pull Requests" }));

    expect(
      await screen.findByRole("heading", { level: 2, name: "Pull Requests" }),
    ).toBeInTheDocument();
  });

  it.each([
    ["/repositories", "Repositories"],
    ["/architecture", "Architecture"],
    ["/reports", "Reports"],
    ["/settings", "Settings"],
  ])("renders the %s page at %s", async (path, heading) => {
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 2, name: heading })).toBeInTheDocument();
  });

  it("renders the repository detail page and offers to run indexing", async () => {
    vi.spyOn(githubApi, "listTrackedRepositories").mockResolvedValue([FAKE_REPO]);
    vi.spyOn(repositoriesApi, "listPullRequests").mockResolvedValue([FAKE_PR]);
    vi.spyOn(repositoriesApi, "getLatestIndexingJob").mockRejectedValue(NOT_FOUND);

    renderApp("/repositories/repo-1");

    expect(
      await screen.findByRole("heading", { level: 2, name: "local/order-service" }),
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
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderApp("/repositories/repo-1");
    await screen.findByRole("heading", { level: 2, name: "local/order-service" });

    await user.click(screen.getByRole("button", { name: "Remove repository" }));

    expect(window.confirm).toHaveBeenCalled();
    expect(repositoriesApi.removeRepository).toHaveBeenCalledWith("fake-token", "repo-1");
    expect(
      await screen.findByRole("heading", { level: 2, name: "Repositories" }),
    ).toBeInTheDocument();
  });

  it("does not remove the repository when the confirm dialog is declined", async () => {
    const user = userEvent.setup();
    vi.spyOn(githubApi, "listTrackedRepositories").mockResolvedValue([FAKE_REPO]);
    vi.spyOn(repositoriesApi, "listPullRequests").mockResolvedValue([FAKE_PR]);
    vi.spyOn(repositoriesApi, "getLatestIndexingJob").mockRejectedValue(NOT_FOUND);
    const removeSpy = vi.spyOn(repositoriesApi, "removeRepository");
    vi.spyOn(window, "confirm").mockReturnValue(false);

    renderApp("/repositories/repo-1");
    await screen.findByRole("heading", { level: 2, name: "local/order-service" });

    await user.click(screen.getByRole("button", { name: "Remove repository" }));

    expect(window.confirm).toHaveBeenCalled();
    expect(removeSpy).not.toHaveBeenCalled();
    expect(
      screen.getByRole("heading", { level: 2, name: "local/order-service" }),
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
        level: 2,
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
