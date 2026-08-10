import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../../app/auth-context";
import { RepositorySelector } from "./RepositorySelector";
import type { ContextDiscoveryResult } from "../../types/agent";
import { overrideStageResult } from "../../lib/api/workflows";

vi.mock("../../lib/api/workflows", () => ({
  overrideStageResult: vi.fn(),
}));

function renderWithAuth(ui: React.ReactElement) {
  const auth: AuthContextValue = {
    user: {
      id: "u1",
      email: "t@t.com",
      full_name: "T",
      auth_provider: "local",
      role: "user",
      created_at: "2026-01-01T00:00:00Z",
    },
    token: "tok",
    isLoading: false,
    login: vi.fn(),
    loginWithToken: vi.fn(),
    logout: vi.fn(),
  };
  return render(
    <AuthContext.Provider value={auth}>
      <MemoryRouter>{ui}</MemoryRouter>
    </AuthContext.Provider>,
  );
}

function makeResult(overrides: Partial<ContextDiscoveryResult> = {}): ContextDiscoveryResult {
  return {
    original_request: "req",
    enriched_text: "req",
    resolved_references: [],
    indexed_repositories: [],
    graph_components: [],
    graph_topics: [],
    ranked_repository_names: ["ingestion-framework", "etl-core", "streaming-pipeline"],
    implementation_candidates: ["ingestion-framework", "etl-core"],
    repositories: [
      {
        name: "ingestion-framework",
        source: "explicit",
        selected: true,
        reason: "Named directly in the request.",
      },
      {
        name: "etl-core",
        source: "explicit",
        selected: true,
        reason: "Named directly in the request.",
      },
      {
        name: "streaming-pipeline",
        source: "suggested",
        selected: false,
        reason: "Shares Kafka topic 'orders-created' with etl-core.",
        confidence: "structural",
      },
      {
        name: "shared-utils",
        source: "suggested",
        selected: false,
        reason: "ingestion-framework declares a dependency matching this repository's name.",
        confidence: "heuristic",
      },
    ],
    // Legacy projections — present for type completeness, not read by
    // RepositorySelector (ADR 0010, invariant I6: it reads `repositories`).
    explicit_repositories: [],
    suggested_repositories: [],
    selected_repositories: [],
    graph_context_text: "",
    graph_available: true,
    graph_has_data: true,
    planning_metadata: {},
    prompt_version: "1.0",
    goal: "req",
    readiness: "READY",
    completion_status: "COMPLETED",
    confidence: 0.9,
    capability_confidence: {},
    clarification_rounds: 0,
    blocking_reasons: [],
    remediation_steps: [],
    assumptions: [],
    user_answers: {},
    unresolved_questions: [],
    discovery_report: {
      readiness: "READY",
      confidence: 0.9,
      headline: "",
      transcript: [],
      confidence_breakdown: [],
      findings: [],
      interpretations: [],
      gaps: [],
      investigation: [],
    },
    working_memory: {},
    ...overrides,
  };
}

describe("RepositorySelector", () => {
  it("badges a heuristic-confidence suggestion but not a structural one", () => {
    renderWithAuth(
      <RepositorySelector
        workflowId="wf1"
        result={makeResult()}
        humanOverride={null}
        onOverridden={vi.fn()}
      />,
    );

    const heuristicLabel = screen.getByText("shared-utils").closest("label");
    expect(heuristicLabel).toHaveTextContent("possible match");

    const structuralLabel = screen.getByText("streaming-pipeline").closest("label");
    expect(structuralLabel).not.toHaveTextContent("possible match");

    const explicitLabel = screen.getByText("ingestion-framework").closest("label");
    expect(explicitLabel).not.toHaveTextContent("possible match");
  });

  it("pre-checks explicit repositories and leaves suggested ones unchecked", () => {
    renderWithAuth(
      <RepositorySelector
        workflowId="wf1"
        result={makeResult()}
        humanOverride={null}
        onOverridden={vi.fn()}
      />,
    );

    expect(
      screen.getByText("I found these repositories explicitly referenced in the Jira."),
    ).toBeInTheDocument();
    expect(screen.getByText("ingestion-framework")).toBeInTheDocument();
    expect(screen.getByText("etl-core")).toBeInTheDocument();
    expect(screen.getByText("streaming-pipeline")).toBeInTheDocument();
    expect(
      screen.getByText(/Shares Kafka topic 'orders-created' with etl-core\./),
    ).toBeInTheDocument();

    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(4);
    const ingestion = screen.getByText("ingestion-framework").closest("label");
    const streaming = screen.getByText("streaming-pipeline").closest("label");
    expect(ingestion?.querySelector('[role="checkbox"]')).toHaveAttribute("aria-checked", "true");
    expect(streaming?.querySelector('[role="checkbox"]')).toHaveAttribute("aria-checked", "false");

    // Nothing changed yet — no save button.
    expect(screen.queryByRole("button", { name: /save selection/i })).not.toBeInTheDocument();
  });

  it("saves the edited selection as a context_discovery override", async () => {
    const user = userEvent.setup();
    const onOverridden = vi.fn();
    renderWithAuth(
      <RepositorySelector
        workflowId="wf1"
        result={makeResult()}
        humanOverride={null}
        onOverridden={onOverridden}
      />,
    );

    const streamingCheckbox = screen
      .getByText("streaming-pipeline")
      .closest("label")
      ?.querySelector('[role="checkbox"]');
    expect(streamingCheckbox).toBeTruthy();
    await user.click(streamingCheckbox as Element);

    const saveButton = await screen.findByRole("button", { name: /save selection/i });
    await user.click(saveButton);

    expect(overrideStageResult).toHaveBeenCalledWith(
      "tok",
      "wf1",
      "context_discovery",
      expect.objectContaining({
        override: expect.objectContaining({
          repositories: expect.arrayContaining([
            expect.objectContaining({ name: "ingestion-framework", selected: true }),
            expect.objectContaining({ name: "etl-core", selected: true }),
            expect.objectContaining({ name: "streaming-pipeline", selected: true }),
          ]),
        }),
      }),
    );
    expect(onOverridden).toHaveBeenCalled();
  });

  it("reads a human override's repositories field, ignoring the base result", () => {
    // ADR 0010, invariant I6: the override always targets `repositories`
    // itself, never `selected_repositories` — a previously-saved override
    // must win over whatever the AI's own base result says.
    renderWithAuth(
      <RepositorySelector
        workflowId="wf1"
        result={makeResult()}
        humanOverride={{
          repositories: [
            { name: "ingestion-framework", source: "explicit", selected: false, reason: "" },
          ],
        }}
        onOverridden={vi.fn()}
      />,
    );

    expect(screen.getByText("ingestion-framework")).toBeInTheDocument();
    expect(screen.queryByText("etl-core")).not.toBeInTheDocument();
    const checkbox = screen.getByText("ingestion-framework").closest("label")
      ?.querySelector('[role="checkbox"]');
    expect(checkbox).toHaveAttribute("aria-checked", "false");
  });

  it("renders nothing when discovery found no repositories at all", () => {
    const { container } = renderWithAuth(
      <RepositorySelector
        workflowId="wf1"
        result={makeResult({ repositories: [] })}
        humanOverride={null}
        onOverridden={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
