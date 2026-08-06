import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { ReviewPage } from "./ReviewPage";

vi.mock("../lib/api/agentRuns", () => ({
  createAgentRun: vi.fn(),
  getAgentRun: vi.fn(),
}));

import { createAgentRun, getAgentRun } from "../lib/api/agentRuns";

const mockedCreateAgentRun = vi.mocked(createAgentRun);
const mockedGetAgentRun = vi.mocked(getAgentRun);

function renderWithAuth(authValue?: Partial<AuthContextValue>) {
  const defaultAuth: AuthContextValue = {
    user: { id: "u1", email: "test@test.com", full_name: "Test User", auth_provider: "local", role: "user", created_at: "2026-01-01T00:00:00Z" },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    loginWithToken: vi.fn(),
    logout: vi.fn(),
    ...authValue,
  };

  return render(
    <AuthContext.Provider value={defaultAuth}>
      <MemoryRouter>
        <ReviewPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("ReviewPage", () => {
  it("renders the review heading", () => {
    renderWithAuth();
    expect(screen.getByText("Review Pull Request")).toBeInTheDocument();
  });

  it("renders the URL input", () => {
    renderWithAuth();
    expect(screen.getByLabelText("GitHub Pull Request URL")).toBeInTheDocument();
  });

  it("disables submit when input is empty", () => {
    renderWithAuth();
    const button = screen.getByRole("button", { name: "Submit review request" });
    expect(button).toBeDisabled();
  });

  it("shows validation error for invalid URL", async () => {
    const user = userEvent.setup();
    renderWithAuth();
    const input = screen.getByLabelText("GitHub Pull Request URL");
    await user.type(input, "not-a-url");
    const button = screen.getByRole("button", { name: "Submit review request" });
    await user.click(button);
    expect(screen.getByRole("alert")).toHaveTextContent(/valid GitHub PR URL/);
  });

  it("clears validation error when input changes", async () => {
    const user = userEvent.setup();
    renderWithAuth();
    const input = screen.getByLabelText("GitHub Pull Request URL");
    await user.type(input, "bad");
    const button = screen.getByRole("button", { name: "Submit review request" });
    await user.click(button);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await user.type(input, "x");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("has a link to run history", () => {
    renderWithAuth();
    expect(screen.getByRole("link", { name: "View run history" })).toHaveAttribute("href", "/runs");
  });

  it("submits a pasted GitHub PR URL as the raw subject_reference", async () => {
    const user = userEvent.setup();
    mockedCreateAgentRun.mockResolvedValue({ run_id: "run-1", status: "queued", subject: { subject_id: "pr:x", subject_type: "pull_request", display_name: "acme/widgets#42" }, goal: "review_pr" });
    mockedGetAgentRun.mockResolvedValue({
      run_id: "run-1",
      goal: "review_pr",
      status: "completed",
      subject: { subject_id: "pr:x", subject_type: "pull_request", display_name: "acme/widgets#42" },
      title: null,
      provider: null,
      user: null,
      repository: null,
      model: null,
      error_message: null,
      started_at: null,
      completed_at: null,
      created_at: "2026-01-01T00:00:00Z",
      workflow_id: null,
      workflow_stage: null,
      previous_run_id: null,
      steps: [],
    });

    renderWithAuth();
    const input = screen.getByLabelText("GitHub Pull Request URL");
    await user.type(input, "https://github.com/acme/widgets/pull/42");
    await user.click(screen.getByRole("button", { name: "Submit review request" }));

    await waitFor(() => {
      expect(mockedCreateAgentRun).toHaveBeenCalledWith("test-token", {
        subject_reference: "https://github.com/acme/widgets/pull/42",
        goal: "review_pr",
      });
    });
  });

  it("renders the expanded review scorecard, findings, and observations", async () => {
    const user = userEvent.setup();
    mockedCreateAgentRun.mockResolvedValue({ run_id: "run-2", status: "queued", subject: { subject_id: "pr:x", subject_type: "pull_request", display_name: "acme/widgets#42" }, goal: "review_pr" });
    mockedGetAgentRun.mockResolvedValue({
      run_id: "run-2",
      goal: "review_pr",
      status: "completed",
      subject: { subject_id: "pr:x", subject_type: "pull_request", display_name: "acme/widgets#42" },
      title: null,
      provider: null,
      user: null,
      repository: null,
      model: null,
      error_message: null,
      started_at: null,
      completed_at: null,
      created_at: "2026-01-01T00:00:00Z",
      workflow_id: null,
      workflow_stage: null,
      previous_run_id: null,
      steps: [
        {
          step_id: "s1",
          agent_id: "review",
          status: "completed",
          confidence: { score: 0.9, reasoning: "grounded" },
          evidence: [],
          prompt_version: "1.5",
          output_ref: null,
          error_message: null,
          latency_ms: 1200,
          created_at: null,
          completed_at: null,
          result: {
            executive_summary: "Adds caching to the widget service.",
            quality_score: 82,
            risk_score: 28,
            merge_recommendation: "approve_with_comments",
            findings: [
              {
                category: "reliability",
                severity: "high",
                title: "No cache eviction",
                description: "The new cache has no TTL or eviction policy.",
                confidence: { score: 0.8, reasoning: "clear from diff" },
              },
            ],
            architecture_observations: ["Introduces a new in-memory cache layer."],
            maintainability_observations: [],
            reliability_observations: [],
            testing_review: "No new tests were added for the cache layer.",
            documentation_review: "",
            positive_findings: ["Clear separation of cache logic into its own module."],
            suggested_improvements: ["Add a TTL to the new cache to bound memory growth."],
            breaking_changes: [],
            migration_advice: [],
            suggested_reviewers: [],
            regression_tests: [],
          },
        },
      ],
    });

    renderWithAuth();
    const input = screen.getByLabelText("GitHub Pull Request URL");
    await user.type(input, "https://github.com/acme/widgets/pull/42");
    await user.click(screen.getByRole("button", { name: "Submit review request" }));

    await waitFor(() => {
      expect(screen.getByText("Scorecard")).toBeInTheDocument();
    });
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(screen.getByText("28")).toBeInTheDocument();
    expect(screen.getByText("Approve with Comments")).toBeInTheDocument();
    expect(screen.getByText("No cache eviction")).toBeInTheDocument();
    expect(screen.getByText("Introduces a new in-memory cache layer.", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/No new tests were added/)).toBeInTheDocument();
    expect(screen.getByText(/Clear separation of cache logic/)).toBeInTheDocument();
    expect(screen.getByText(/Add a TTL to the new cache/)).toBeInTheDocument();
  });
});
