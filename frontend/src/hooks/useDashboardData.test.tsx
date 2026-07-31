import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../app/AuthContext";
import * as authApi from "../lib/api/auth";
import * as githubApi from "../lib/api/github";
import * as repositoriesApi from "../lib/api/repositories";
import * as analysisApi from "../lib/api/analysis";
import { ApiError } from "../lib/api/client";
import { useDashboardData } from "./useDashboardData";
import type { TrackedRepository } from "../types/github";
import type { PullRequest } from "../types/pullRequest";
import type { PullRequestAnalysis } from "../types/analysis";

const FAKE_USER = {
  id: "1",
  email: "ada@example.com",
  full_name: "Ada Lovelace",
  auth_provider: "local",
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
};

const REPOS: TrackedRepository[] = [
  {
    id: "repo-order",
    github_repo_id: "local-order",
    source: "local",
    owner: "local",
    name: "order-service",
    full_name: "local/order-service",
    private: false,
    default_branch: "main",
    html_url: "/demo/repositories/order-service",
    created_at: "2026-07-01T00:00:00Z",
  },
  {
    id: "repo-payment",
    github_repo_id: "local-payment",
    source: "local",
    owner: "local",
    name: "payment-service",
    full_name: "local/payment-service",
    private: false,
    default_branch: "main",
    html_url: "/demo/repositories/payment-service",
    created_at: "2026-07-01T00:00:00Z",
  },
];

// A fixed calendar date here is a time bomb: useDashboardData's
// highRiskThisWeekCount only counts PRs updated within the last 7 days of
// *whenever the test happens to run*, so a literal date eventually falls
// outside that rolling window and the count silently drops to 0 with no
// code change. One day ago, computed at test-run time, is always "this week."
const ONE_DAY_AGO = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();

function pr(overrides: Partial<PullRequest>): PullRequest {
  return {
    id: "pr-1",
    number: 1,
    title: "Some change",
    state: "open",
    is_draft: false,
    author_login: "tester",
    html_url: "https://example.invalid",
    head_ref: "pr-1",
    base_ref: "main",
    github_created_at: ONE_DAY_AGO,
    github_updated_at: ONE_DAY_AGO,
    ...overrides,
  };
}

function analysis(overrides: Partial<PullRequestAnalysis>): PullRequestAnalysis {
  return {
    id: "analysis-1",
    pull_request_id: "pr-1",
    risk: "HIGH",
    directly_impacted_services: [],
    indirectly_impacted_services: [],
    impacted_apis: [],
    impacted_topics: [],
    impacted_libraries: [],
    dependency_paths: [],
    analyzed_at: "2026-07-20T00:00:00Z",
    ...overrides,
  };
}

const NOT_FOUND = new ApiError(404, "not_found", "not found");

function DashboardProbe() {
  const { stats, recentPullRequests, repositories, isLoading } = useDashboardData();
  if (isLoading) return <p>Loading…</p>;
  return (
    <div>
      <p data-testid="repos-monitored">{stats.repositoriesMonitored}</p>
      <p data-testid="org-count">{stats.organizationCount}</p>
      <p data-testid="open-prs">{stats.openPullRequestCount}</p>
      <p data-testid="awaiting-analysis">{stats.awaitingAnalysisCount}</p>
      <p data-testid="high-risk">{stats.highRiskThisWeekCount}</p>
      <p data-testid="pr-count">{recentPullRequests.length}</p>
      <p data-testid="repo-health-order">
        {repositories.find((r) => r.id === "repo-order")?.health}
      </p>
    </div>
  );
}

function renderProbe() {
  return render(
    <AuthProvider>
      <DashboardProbe />
    </AuthProvider>,
  );
}

describe("useDashboardData", () => {
  beforeEach(() => {
    localStorage.setItem("graphforge.token", "fake-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue(FAKE_USER);
    vi.spyOn(repositoriesApi, "getLatestIndexingJob").mockRejectedValue(NOT_FOUND);
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("counts open PRs, unanalyzed PRs, and high-risk PRs across all tracked repositories", async () => {
    vi.spyOn(githubApi, "listTrackedRepositories").mockResolvedValue(REPOS);
    vi.spyOn(repositoriesApi, "listPullRequests").mockImplementation(async (_token, repoId) => {
      if (repoId === "repo-order") {
        return [pr({ id: "pr-1", title: "Breaking Kafka schema change" })];
      }
      return [pr({ id: "pr-2", title: "Add refund endpoint" })];
    });
    vi.spyOn(analysisApi, "getDeterministicAnalysis").mockImplementation(async (_token, prId) => {
      if (prId === "pr-1") {
        return analysis({ pull_request_id: "pr-1", risk: "HIGH" });
      }
      throw NOT_FOUND;
    });

    renderProbe();

    expect(await screen.findByTestId("repos-monitored")).toHaveTextContent("2");
    expect(screen.getByTestId("org-count")).toHaveTextContent("1");
    expect(screen.getByTestId("open-prs")).toHaveTextContent("2");
    expect(screen.getByTestId("awaiting-analysis")).toHaveTextContent("1");
    expect(screen.getByTestId("high-risk")).toHaveTextContent("1");
    expect(screen.getByTestId("pr-count")).toHaveTextContent("2");
    expect(screen.getByTestId("repo-health-order")).toHaveTextContent("critical");
  });

  it("does not count closed pull requests toward the open PR stat", async () => {
    vi.spyOn(githubApi, "listTrackedRepositories").mockResolvedValue([REPOS[0]]);
    vi.spyOn(repositoriesApi, "listPullRequests").mockResolvedValue([
      pr({ id: "pr-closed", state: "closed" }),
    ]);
    vi.spyOn(analysisApi, "getDeterministicAnalysis").mockRejectedValue(NOT_FOUND);

    renderProbe();

    expect(await screen.findByTestId("open-prs")).toHaveTextContent("0");
    expect(screen.getByTestId("awaiting-analysis")).toHaveTextContent("0");
  });
});
