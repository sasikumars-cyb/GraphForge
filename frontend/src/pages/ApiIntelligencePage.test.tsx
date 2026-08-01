import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { ApiIntelligencePage } from "./ApiIntelligencePage";

vi.mock("../lib/api/agentRuns", () => ({
  createAgentRun: vi.fn(),
  getAgentRun: vi.fn(),
}));

vi.mock("../lib/api/github", () => ({
  listTrackedRepositories: vi.fn(),
}));

vi.mock("../lib/api/apiIntelligence", () => ({
  fetchApiIntelligenceExport: vi.fn(),
  downloadApiIntelligenceExport: vi.fn(),
}));

import { createAgentRun, getAgentRun } from "../lib/api/agentRuns";
import { listTrackedRepositories } from "../lib/api/github";
import { fetchApiIntelligenceExport } from "../lib/api/apiIntelligence";

const mockedCreateAgentRun = vi.mocked(createAgentRun);
const mockedGetAgentRun = vi.mocked(getAgentRun);
const mockedListTrackedRepositories = vi.mocked(listTrackedRepositories);
const mockedFetchExport = vi.mocked(fetchApiIntelligenceExport);

function renderWithAuth(authValue?: Partial<AuthContextValue>) {
  const defaultAuth: AuthContextValue = {
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
    logout: vi.fn(),
    ...authValue,
  };

  return render(
    <AuthContext.Provider value={defaultAuth}>
      <MemoryRouter>
        <ApiIntelligencePage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("ApiIntelligencePage", () => {
  it("renders the heading", () => {
    mockedListTrackedRepositories.mockResolvedValue([]);
    renderWithAuth();
    expect(screen.getByText("API Intelligence")).toBeInTheDocument();
  });

  it("disables Run Analysis until a repository is selected", () => {
    mockedListTrackedRepositories.mockResolvedValue([]);
    renderWithAuth();
    expect(screen.getByRole("button", { name: "Run API intelligence analysis" })).toBeDisabled();
  });

  it("populates the repository dropdown", async () => {
    mockedListTrackedRepositories.mockResolvedValue([
      {
        id: "repo-1",
        github_repo_id: "123",
        source: "github",
        owner: "acme",
        name: "widgets",
        full_name: "acme/widgets",
        private: false,
        default_branch: "main",
        html_url: "https://github.com/acme/widgets",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    renderWithAuth();
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "acme/widgets" })).toBeInTheDocument();
    });
  });

  it("submits repo:<id> as the subject_reference with the correct goal", async () => {
    const user = userEvent.setup();
    mockedListTrackedRepositories.mockResolvedValue([
      {
        id: "repo-1",
        github_repo_id: "123",
        source: "github",
        owner: "acme",
        name: "widgets",
        full_name: "acme/widgets",
        private: false,
        default_branch: "main",
        html_url: "https://github.com/acme/widgets",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    mockedCreateAgentRun.mockResolvedValue({
      run_id: "run-1",
      status: "queued",
      subject: { subject_id: "repo:repo-1", subject_type: "repository", display_name: "acme/widgets" },
      goal: "analyze_api_intelligence",
    });
    mockedGetAgentRun.mockResolvedValue({
      run_id: "run-1",
      goal: "analyze_api_intelligence",
      status: "queued",
      subject: { subject_id: "repo:repo-1", subject_type: "repository", display_name: "acme/widgets" },
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
    const select = await screen.findByLabelText("Repository");
    await user.selectOptions(select, "repo-1");
    await user.click(screen.getByRole("button", { name: "Run API intelligence analysis" }));

    await waitFor(() => {
      expect(mockedCreateAgentRun).toHaveBeenCalledWith("test-token", {
        subject_reference: "repo:repo-1",
        goal: "analyze_api_intelligence",
      });
    });
  });

  it("renders scores, endpoints, security findings, and missing information from a completed run", async () => {
    const user = userEvent.setup();
    mockedListTrackedRepositories.mockResolvedValue([
      {
        id: "repo-1",
        github_repo_id: "123",
        source: "github",
        owner: "acme",
        name: "widgets",
        full_name: "acme/widgets",
        private: false,
        default_branch: "main",
        html_url: "https://github.com/acme/widgets",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    mockedCreateAgentRun.mockResolvedValue({
      run_id: "run-2",
      status: "queued",
      subject: { subject_id: "repo:repo-1", subject_type: "repository", display_name: "acme/widgets" },
      goal: "analyze_api_intelligence",
    });
    mockedGetAgentRun.mockResolvedValue({
      run_id: "run-2",
      goal: "analyze_api_intelligence",
      status: "completed",
      subject: { subject_id: "repo:repo-1", subject_type: "repository", display_name: "acme/widgets" },
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
          agent_id: "api_intelligence",
          status: "completed",
          confidence: { score: 0.8, reasoning: "grounded" },
          evidence: [],
          prompt_version: "1.0",
          output_ref: null,
          error_message: null,
          latency_ms: 900,
          created_at: null,
          completed_at: null,
          result: {
            executive_summary: "A widgets API with basic CRUD.",
            endpoints: [
              { method: "GET", path: "/v1/widgets/{id}", description: "Fetch a widget", authentication_required: true },
            ],
            security_findings: [
              {
                category: "rate_limiting",
                severity: "high",
                title: "No rate limiting documented",
                description: "No rate limit mentioned.",
                why_it_matters: "Abuse risk.",
                recommendation: "Add rate limits.",
                confidence: 0.7,
              },
            ],
            missing_information: ["No documented error response schema."],
            scores: {
              documentation_completeness: 60,
              security_score: 40,
              api_quality_score: 70,
              readability_score: 80,
              consistency_score: 65,
              overall_readiness_score: 58,
            },
          },
        },
      ],
    });

    renderWithAuth();
    const select = await screen.findByLabelText("Repository");
    await user.selectOptions(select, "repo-1");
    await user.click(screen.getByRole("button", { name: "Run API intelligence analysis" }));

    await waitFor(() => {
      expect(screen.getByText("A widgets API with basic CRUD.")).toBeInTheDocument();
    });
    expect(screen.getByText("/v1/widgets/{id}")).toBeInTheDocument();
    expect(screen.getByText("No rate limiting documented")).toBeInTheDocument();
    expect(screen.getByText("No documented error response schema.", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("58")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Visual Dashboard" })).toBeInTheDocument();
  });

  it("fetches and embeds the HTML dashboard when Open Visual Dashboard is clicked", async () => {
    const user = userEvent.setup();
    mockedListTrackedRepositories.mockResolvedValue([
      {
        id: "repo-1",
        github_repo_id: "123",
        source: "github",
        owner: "acme",
        name: "widgets",
        full_name: "acme/widgets",
        private: false,
        default_branch: "main",
        html_url: "https://github.com/acme/widgets",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    mockedCreateAgentRun.mockResolvedValue({
      run_id: "run-3",
      status: "queued",
      subject: { subject_id: "repo:repo-1", subject_type: "repository", display_name: "acme/widgets" },
      goal: "analyze_api_intelligence",
    });
    mockedGetAgentRun.mockResolvedValue({
      run_id: "run-3",
      goal: "analyze_api_intelligence",
      status: "completed",
      subject: { subject_id: "repo:repo-1", subject_type: "repository", display_name: "acme/widgets" },
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
          agent_id: "api_intelligence",
          status: "completed",
          confidence: { score: 0.8, reasoning: "grounded" },
          evidence: [],
          prompt_version: "1.0",
          output_ref: null,
          error_message: null,
          latency_ms: 900,
          created_at: null,
          completed_at: null,
          result: { executive_summary: "x" },
        },
      ],
    });
    mockedFetchExport.mockResolvedValue("<html><body>dashboard content</body></html>");

    renderWithAuth();
    const select = await screen.findByLabelText("Repository");
    await user.selectOptions(select, "repo-1");
    await user.click(screen.getByRole("button", { name: "Run API intelligence analysis" }));

    const dashboardButton = await screen.findByRole("button", { name: "Open Visual Dashboard" });
    await user.click(dashboardButton);

    await waitFor(() => {
      expect(mockedFetchExport).toHaveBeenCalledWith("test-token", "run-3", "html");
    });
    await waitFor(() => {
      expect(document.querySelector('iframe[title="API Intelligence Dashboard"]')).toBeInTheDocument();
    });
  });
});
