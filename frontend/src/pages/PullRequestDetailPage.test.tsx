import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { routes } from "../app/router";
import { AiModelProvider } from "../app/AiModelContext";
import { AuthProvider } from "../app/AuthContext";
import * as authApi from "../lib/api/auth";
import * as githubApi from "../lib/api/github";
import * as repositoriesApi from "../lib/api/repositories";
import * as analysisApi from "../lib/api/analysis";
import { ApiError } from "../lib/api/client";
import type { AIAnalysis } from "../types/analysis";
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

const FAKE_AI_ANALYSIS: AIAnalysis = {
  id: "ai-1",
  pull_request_id: "pr-1",
  executive_summary: "No breaking changes.",
  breaking_changes: [],
  migration_advice: [],
  suggested_reviewers: [],
  regression_tests: [],
  confidence_score: 0.9,
  confidence_reasoning: "Clear analysis",
  prompt_version: "1.4",
  analyzed_at: "2026-07-20T00:00:00Z",
};

function renderPage(initialPath = "/pull-requests/pr-1") {
  const router = createMemoryRouter(routes, { initialEntries: [initialPath] });
  return render(
    <AuthProvider>
      <AiModelProvider>
        <RouterProvider router={router} />
      </AiModelProvider>
    </AuthProvider>,
  );
}

describe("PullRequestDetailPage - Publish Review", () => {
  beforeEach(() => {
    localStorage.setItem("graphforge.token", "fake-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue(FAKE_USER);
    vi.spyOn(githubApi, "listTrackedRepositories").mockResolvedValue([FAKE_REPO]);
    vi.spyOn(repositoriesApi, "listPullRequests").mockResolvedValue([FAKE_PR]);
    vi.spyOn(analysisApi, "getDeterministicAnalysis").mockRejectedValue(NOT_FOUND);
  });

  afterEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("disables Publish Review until an AI analysis exists", async () => {
    vi.spyOn(analysisApi, "getAiAnalysis").mockRejectedValue(NOT_FOUND);

    renderPage();

    const button = await screen.findByRole("button", { name: "Publish Review" }, { timeout: 3000 });
    expect(button).toBeDisabled();
  });

  it("enables Publish Review once an AI analysis exists and publishes it", async () => {
    const user = userEvent.setup();
    vi.spyOn(analysisApi, "getAiAnalysis").mockResolvedValue(FAKE_AI_ANALYSIS);
    vi.spyOn(analysisApi, "publishReview").mockResolvedValue({
      comment_id: 42,
      comment_url: "https://github.com/local/order-service/pull/1#issuecomment-42",
    });

    renderPage();

    const button = await screen.findByRole("button", { name: "Publish Review" }, { timeout: 3000 });
    expect(button).toBeEnabled();

    await user.click(button);

    expect(analysisApi.publishReview).toHaveBeenCalledWith("fake-token", "pr-1");
    expect(
      await screen.findByRole("button", { name: "✓ Review published" }, { timeout: 3000 }),
    ).toBeInTheDocument();
    const link = await screen.findByRole(
      "link",
      { name: "View published comment on GitHub →" },
      { timeout: 3000 },
    );
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/local/order-service/pull/1#issuecomment-42",
    );
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("has no detectable accessibility violations once the AI analysis has loaded (KAN-38)", async () => {
    vi.spyOn(analysisApi, "getAiAnalysis").mockResolvedValue(FAKE_AI_ANALYSIS);

    const { container } = renderPage();
    await screen.findByRole("button", { name: "Publish Review" }, { timeout: 3000 });

    expect(await axe(container)).toHaveNoViolations();
  });

  it("disables Publish Review while another AI action is in flight", async () => {
    const user = userEvent.setup();
    vi.spyOn(analysisApi, "getAiAnalysis").mockResolvedValue(FAKE_AI_ANALYSIS);
    let resolveRunAi: (() => void) | undefined;
    vi.spyOn(analysisApi, "runAiAnalysis").mockReturnValue(
      new Promise((resolve) => {
        resolveRunAi = () =>
          resolve({
            executive_summary: "Updated summary.",
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
            confidence: { score: 0.9, reasoning: "" },
            prompt_version: "1.4",
          });
      }),
    );

    renderPage();

    const publishButton = await screen.findByRole(
      "button",
      { name: "Publish Review" },
      { timeout: 3000 },
    );
    expect(publishButton).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Re-run AI analysis" }));

    expect(screen.getByRole("button", { name: "Publish Review" })).toBeDisabled();

    resolveRunAi?.();
    await waitFor(() => expect(screen.getByText("Updated summary.")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Publish Review" })).toBeEnabled();
  });

  it("shows an error banner when publishing fails, without leaving the button stuck", async () => {
    const user = userEvent.setup();
    vi.spyOn(analysisApi, "getAiAnalysis").mockResolvedValue(FAKE_AI_ANALYSIS);
    vi.spyOn(analysisApi, "publishReview").mockRejectedValue(
      new Error("GitHub is not connected for this user."),
    );

    renderPage();

    const button = await screen.findByRole("button", { name: "Publish Review" }, { timeout: 3000 });
    await user.click(button);

    expect(await screen.findByText("GitHub is not connected for this user.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish Review" })).toBeEnabled();
    expect(screen.queryByText("View published comment on GitHub →")).not.toBeInTheDocument();
  });

  it("disables View Visual Report until an AI analysis exists", async () => {
    vi.spyOn(analysisApi, "getAiAnalysis").mockRejectedValue(NOT_FOUND);

    renderPage();

    const button = await screen.findByRole(
      "button",
      { name: "View Visual Report" },
      { timeout: 3000 },
    );
    expect(button).toBeDisabled();
  });

  it("fetches the HTML report and opens it in a new tab once an AI analysis exists", async () => {
    const user = userEvent.setup();
    vi.spyOn(analysisApi, "getAiAnalysis").mockResolvedValue(FAKE_AI_ANALYSIS);
    vi.spyOn(analysisApi, "getReviewReportHtml").mockResolvedValue("<html>report</html>");
    const fakeWindow = { location: { href: "" }, close: vi.fn() } as unknown as Window;
    const openSpy = vi.spyOn(window, "open").mockReturnValue(fakeWindow);

    renderPage();

    const button = await screen.findByRole(
      "button",
      { name: "View Visual Report" },
      { timeout: 3000 },
    );
    expect(button).toBeEnabled();

    await user.click(button);

    expect(openSpy).toHaveBeenCalledWith("", "_blank");
    await waitFor(() =>
      expect(analysisApi.getReviewReportHtml).toHaveBeenCalledWith("fake-token", "pr-1"),
    );
    await waitFor(() => expect(fakeWindow.location.href).toMatch(/^blob:/));
  });
});
