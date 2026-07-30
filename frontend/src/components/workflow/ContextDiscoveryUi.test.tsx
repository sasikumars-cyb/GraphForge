import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../../app/auth-context";
import { ContextClarificationBanner } from "./ContextClarificationBanner";
import { ContextExplorerPanel } from "./ContextExplorerPanel";
import type { ContextDiscoveryResult, PendingClarification } from "../../types/agent";

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
    logout: vi.fn(),
  };
  return render(
    <AuthContext.Provider value={auth}>
      <MemoryRouter>{ui}</MemoryRouter>
    </AuthContext.Provider>,
  );
}

const clarification: PendingClarification = {
  question_id: "gap_repository",
  question: "Which repository should I use for this work?",
  why: "I narrowed it to 2 equally-plausible repositories and can't separate them.",
  options: ["payment-service", "billing-service"],
  investigated: [
    "— No new external references found in the text.",
    "✓ Queried the knowledge graph: 2 indexed repository(ies).",
  ],
};

describe("ContextClarificationBanner", () => {
  it("shows the question and the reason it is being asked", () => {
    renderWithAuth(
      <ContextClarificationBanner
        pendingClarification={clarification}
        isSubmitting={false}
        onAnswer={vi.fn()}
      />,
    );
    expect(screen.getByText(clarification.question)).toBeInTheDocument();
    expect(screen.getByText(clarification.why)).toBeInTheDocument();
  });

  it("discloses what was already investigated, so the question reads as a last resort", async () => {
    renderWithAuth(
      <ContextClarificationBanner
        pendingClarification={clarification}
        isSubmitting={false}
        onAnswer={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByText(/I tried 2 things first/));
    expect(screen.getByText(/Queried the knowledge graph/)).toBeInTheDocument();
  });

  it("submits the option's real value as the answer", async () => {
    const onAnswer = vi.fn();
    renderWithAuth(
      <ContextClarificationBanner
        pendingClarification={clarification}
        isSubmitting={false}
        onAnswer={onAnswer}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "billing-service" }));
    expect(onAnswer).toHaveBeenCalledWith("gap_repository", "billing-service");
  });

  it("tells the user their answer will be verified rather than trusted", () => {
    renderWithAuth(
      <ContextClarificationBanner
        pendingClarification={clarification}
        isSubmitting={false}
        onAnswer={vi.fn()}
      />,
    );
    expect(screen.getByText(/verify your answer against the knowledge graph/)).toBeInTheDocument();
  });

  it("falls back to free text when there are no candidate values to offer", () => {
    renderWithAuth(
      <ContextClarificationBanner
        pendingClarification={{ ...clarification, options: [] }}
        isSubmitting={false}
        onAnswer={vi.fn()}
      />,
    );
    expect(screen.getByPlaceholderText("Type your answer…")).toBeInTheDocument();
  });
});

function makeResult(overrides: Partial<ContextDiscoveryResult> = {}): ContextDiscoveryResult {
  return {
    original_request: "Add retry backoff",
    enriched_text: "Add retry backoff",
    resolved_references: [],
    indexed_repositories: [{ name: "payment-service" }],
    graph_components: [{ name: "RetryHandler" }],
    graph_topics: [],
    ranked_repository_names: ["payment-service"],
    implementation_candidates: ["payment-service"],
    graph_context_text: "graph blob",
    graph_available: true,
    graph_has_data: true,
    planning_metadata: {},
    prompt_version: "4.0",
    goal: "Add retry backoff",
    readiness: "PARTIAL",
    confidence: 0.72,
    capability_confidence: { repository: 1, architecture: 0.29 },
    clarification_rounds: 0,
    blocking_reasons: [],
    remediation_steps: [],
    assumptions: [],
    user_answers: {},
    unresolved_questions: [],
    working_memory: {},
    discovery_report: {
      readiness: "PARTIAL",
      confidence: 0.72,
      headline: "I have everything Planning strictly requires, but design docs are missing.",
      transcript: [
        {
          kind: "intent",
          text: "I'll search the indexed repositories.",
          iteration: 1,
          evidence_ids: [],
        },
        {
          kind: "observation",
          text: "Only one repository is indexed.",
          iteration: 1,
          evidence_ids: ["ev1"],
        },
        { kind: "conclusion", text: "Ready enough to plan.", iteration: 2, evidence_ids: [] },
      ],
      confidence_breakdown: [
        {
          capability: "architecture",
          label: "Architecture",
          necessity: "required",
          score: 0.29,
          satisfied: false,
          explanation: "29% — ✓ Knowledge graph reachable; ✗ Architecture components discovered",
          signals: [
            {
              label: "Knowledge graph reachable",
              satisfied: true,
              detail: "",
              evidence_ids: ["ev1"],
            },
            {
              label: "Architecture components discovered",
              satisfied: false,
              detail: "the repository is likely not indexed yet",
              evidence_ids: [],
            },
          ],
        },
        {
          capability: "work_item",
          label: "Work item",
          necessity: "not_applicable",
          score: 0,
          satisfied: false,
          explanation: "",
          signals: [],
        },
      ],
      findings: [
        {
          kind: "repository",
          total: 2,
          items: [
            {
              fact_id: "f1",
              subject: "payment-service",
              provider: "graph",
              verified: true,
              evidence: {
                evidence_id: "ev1",
                summary: "Queried the graph: 1 repository.",
                outcome: "success",
              },
            },
            {
              fact_id: "f2",
              subject: "ghost-service",
              provider: "user",
              verified: false,
              evidence: {
                evidence_id: "ev2",
                summary: "You answered: ghost-service",
                outcome: "success",
              },
            },
          ],
        },
      ],
      interpretations: [],
      gaps: [
        {
          gap_id: "gap_documentation",
          capability: "documentation",
          summary: "No design documentation was found for this work.",
          why: "Design docs often carry constraints a ticket omits.",
          severity: "advisory",
          status: "unresolvable",
          missing: ["Documentation source reachable — Confluence is not connected"],
          recommended_action: ["Connect Confluence"],
          resolution_note: "Optional context that could not be retrieved.",
          user_claim: null,
        },
      ],
      investigation: [
        {
          evidence_id: "ev1",
          provider: "graph",
          action: "survey_architecture",
          outcome: "success",
          summary: "Queried the graph: 1 repository.",
          intent: "Work out which service this belongs to.",
          iteration: 1,
        },
      ],
    },
    ...overrides,
  };
}

describe("ContextExplorerPanel", () => {
  it("leads with the verdict in the engine's own words", () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    expect(screen.getByText("PARTIAL")).toBeInTheDocument();
    expect(screen.getByText(/design docs are missing/)).toBeInTheDocument();
    expect(screen.getByText("72% confidence")).toBeInTheDocument();
  });

  it("narrates how it reached the conclusion", () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    expect(screen.getByText("I'll search the indexed repositories.")).toBeInTheDocument();
    expect(screen.getByText("Only one repository is indexed.")).toBeInTheDocument();
  });

  it("shows each capability score with the signals that produced it", () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    expect(screen.getByText("29%")).toBeInTheDocument();
    expect(screen.getByText("Knowledge graph reachable")).toBeInTheDocument();
    // An unsatisfied signal must explain what is missing, not just show a cross.
    expect(screen.getByText(/the repository is likely not indexed yet/)).toBeInTheDocument();
  });

  it("omits capabilities that do not apply to the request", () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    expect(screen.queryByText("Work item")).not.toBeInTheDocument();
  });

  it("shows each finding with the evidence that established it", () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    expect(screen.getByText("payment-service")).toBeInTheDocument();
    expect(screen.getAllByText(/Queried the graph: 1 repository\./).length).toBeGreaterThan(0);
  });

  it("labels an uncorroborated human claim rather than presenting it as knowledge", () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    expect(screen.getByText("ghost-service")).toBeInTheDocument();
    expect(screen.getByText("unverified claim")).toBeInTheDocument();
  });

  it("states what is missing, why it matters, and what to do about it", () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    expect(
      screen.getByText("No design documentation was found for this work."),
    ).toBeInTheDocument();
    expect(screen.getByText(/constraints a ticket omits/)).toBeInTheDocument();
    expect(screen.getByText(/Connect Confluence/)).toBeInTheDocument();
  });

  it("discloses the full investigation trail", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await userEvent.click(screen.getByText(/What I searched \(1 step\)/));
    expect(screen.getByText(/Work out which service this belongs to\./)).toBeInTheDocument();
  });

  it("shows a saved human correction, not the agent's superseded text", async () => {
    // Regression: the panel read `result` only, but `result` stays the AI's
    // unedited output — so after saving a correction the old text came back and
    // the edit looked silently discarded.
    renderWithAuth(
      <ContextExplorerPanel
        workflowId="w1"
        result={makeResult({ graph_context_text: "agent original text" })}
        humanOverride={{ graph_context_text: "human corrected text" }}
        onOverridden={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByText(/View graph context passed to Planning/));
    expect(screen.getByText("human corrected text")).toBeInTheDocument();
    expect(screen.queryByText("agent original text")).not.toBeInTheDocument();
    expect(screen.getByText("edited by you")).toBeInTheDocument();
    expect(screen.getByText(/agent's original text is kept unchanged/)).toBeInTheDocument();
  });

  it("shows the agent's own text when no correction has been made", async () => {
    renderWithAuth(
      <ContextExplorerPanel
        workflowId="w1"
        result={makeResult({ graph_context_text: "agent original text" })}
        onOverridden={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByText(/View graph context passed to Planning/));
    expect(screen.getByText("agent original text")).toBeInTheDocument();
    expect(screen.queryByText("edited by you")).not.toBeInTheDocument();
  });

  it("seeds the correction editor from the current effective text", async () => {
    renderWithAuth(
      <ContextExplorerPanel
        workflowId="w1"
        result={makeResult({ graph_context_text: "agent original text" })}
        humanOverride={{ graph_context_text: "human corrected text" }}
        onOverridden={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Correct/ }));
    expect(screen.getByRole("textbox")).toHaveValue("human corrected text");
  });

  it("renders without a report, for a run persisted before the reasoning engine", () => {
    const legacy = makeResult({ discovery_report: undefined as never });
    renderWithAuth(<ContextExplorerPanel workflowId="w1" result={legacy} onOverridden={vi.fn()} />);
    expect(screen.getByText("PARTIAL")).toBeInTheDocument();
  });
});
