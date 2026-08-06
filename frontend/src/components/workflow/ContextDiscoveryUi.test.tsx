import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../../app/auth-context";
import * as workflowsApi from "../../lib/api/workflows";
import { ContextClarificationBanner } from "./ContextClarificationBanner";
import { ContextExplorerPanel } from "./ContextExplorerPanel";
import type {
  ContextDiscoveryResult,
  DebugBundleDTO,
  EngineeringUnderstandingDTO,
  PendingClarification,
} from "../../types/agent";

vi.mock("../../lib/api/workflows", () => ({
  overrideStageResult: vi.fn(),
  fetchUnderstanding: vi.fn(),
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
    repositories: [
      {
        name: "payment-service",
        source: "suggested",
        selected: true,
        reason: "Only indexed repository.",
      },
    ],
    explicit_repositories: [],
    suggested_repositories: [],
    selected_repositories: [
      { name: "payment-service", source: "suggested", selected: true, reason: "Only indexed repository." },
    ],
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

// ---------------------------------------------------------------------------
// ContextExplorerPanel now renders the Engineering Understanding projection
// (fetched via `fetchUnderstanding`) instead of reading `discovery_report`
// directly. These fixtures mirror what the backend mapper actually produces
// — see `backend/app/mappers/engineering_understanding_mapper.py` and its
// `DebugBundleDTO` pass-through fields in `_build_projection_input`.
// ---------------------------------------------------------------------------

function makeUnderstandingDto(
  overrides: Partial<EngineeringUnderstandingDTO> = {},
): EngineeringUnderstandingDTO {
  return {
    business_goal: "Add retry backoff for flaky downstream calls",
    current_situation: "Calls fail without retrying.",
    expected_outcome: "Calls retry with exponential backoff.",
    repository_summary: { primary: "payment-service", supporting: [], ownership: [] },
    architecture_summary: "payment-service calls the ledger service directly.",
    relevant_areas: [{ name: "Retry handling", components: ["RetryHandler"] }],
    known_constraints: ["Must not exceed 3 retries."],
    missing_information: ["Design docs for the retry policy"],
    unknowns: [{ category: "unknown", description: "Design docs for the retry policy" }],
    evidence_summary: ["Must-modify (1): RetryHandler"],
    recommendations: ["Reuse the existing backoff utility"],
    planning_assessment: {
      status: "PARTIAL",
      reasons: [
        { satisfied: true, description: "Code understanding" },
        { satisfied: false, description: "Architecture components discovered" },
      ],
    },
    confidence_explanation: "Completed: Code understanding. Outstanding: Architecture.",
    documentation_status: "Documentation requirements not yet satisfied.",
    next_step: "Resolve blocking issues: design docs missing",
    debug_bundle: null,
    ...overrides,
  };
}

function makeDebugBundle(): DebugBundleDTO {
  return {
    investigation_trail: [
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
    confidence_breakdown: [
      {
        capability: "architecture",
        label: "Architecture",
        necessity: "required",
        score: 0.29,
        satisfied: false,
        explanation: "29%",
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
    gaps: [
      {
        gap_id: "gap_documentation",
        capability: "documentation",
        summary: "No design documentation was found for this work.",
        why: "Design docs often carry constraints a ticket omits.",
        severity: "advisory",
        status: "unresolvable",
        missing: [],
        recommended_action: ["Connect Confluence"],
        resolution_note: "",
        user_claim: null,
      },
    ],
    transcript: [
      { kind: "intent", text: "I'll search the indexed repositories.", iteration: 1, evidence_ids: [] },
      {
        kind: "observation",
        text: "Only one repository is indexed.",
        iteration: 1,
        evidence_ids: ["ev1"],
      },
    ],
    graph_components: [{ name: "RetryHandler" }],
    graph_topics: [],
    repository_ranking: ["payment-service"],
    capability_confidence: { architecture: 0.29 },
    planning_metadata: {},
    working_memory: {},
    assumptions: ["Design docs are not required to proceed"],
    evidence_package_raw: { items: [{ name: "RetryHandler", tier: "must_modify" }] },
  };
}

describe("ContextExplorerPanel", () => {
  beforeEach(() => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockImplementation((_token, _workflowId, debug) =>
      Promise.resolve(
        debug ? { ...makeUnderstandingDto(), debug_bundle: makeDebugBundle() } : makeUnderstandingDto(),
      ),
    );
  });

  it("shows the Engineering Understanding projection as the default view", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    expect(
      await screen.findByText("Add retry backoff for flaky downstream calls"),
    ).toBeInTheDocument();
    expect(screen.getByText("Calls fail without retrying.")).toBeInTheDocument();
    expect(screen.getByText("Calls retry with exponential backoff.")).toBeInTheDocument();
    expect(screen.getByText("RetryHandler")).toBeInTheDocument();
    expect(screen.getByText(/Must not exceed 3 retries\./)).toBeInTheDocument();
  });

  it("preserves the readiness verdict, in the engine's own words", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(screen.getByText("PARTIAL")).toBeInTheDocument();
    expect(screen.getByText("72% confidence")).toBeInTheDocument();
  });

  it("hides Debug's implementation internals by default", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");

    expect(screen.getByText("Debug")).toBeInTheDocument();
    expect(screen.queryByText("I'll search the indexed repositories.")).not.toBeInTheDocument();
    expect(screen.queryByText("Knowledge graph reachable")).not.toBeInTheDocument();
    expect(screen.queryByText("ghost-service")).not.toBeInTheDocument();
    // Only the non-debug fetch has happened — expanding Debug is what
    // triggers the second, `?debug=true` request.
    expect(workflowsApi.fetchUnderstanding).toHaveBeenCalledTimes(1);
  });

  it("keeps capability readiness and known constraints inside Advanced Details, not the default view", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");

    // Recommendations are now visible on the first screen as "Proposed Change".
    expect(screen.getByText(/Reuse the existing backoff utility/)).toBeVisible();

    // Confidence explanation is visible on the first screen in
    // "Why GraphForge believes this".
    expect(screen.getByText(/Completed: Code understanding/)).toBeVisible();

    // Capability readiness items, constraints, and documentation status are
    // in Advanced Details — present in the DOM but not visible until expanded.
    expect(screen.getByText("Documentation requirements not yet satisfied.")).not.toBeVisible();
    expect(screen.getByText(/Must not exceed 3 retries/)).not.toBeVisible();

    await userEvent.click(screen.getByText("Advanced Details"));

    expect(screen.getByText("Documentation requirements not yet satisfied.")).toBeVisible();
    expect(screen.getByText(/Must not exceed 3 retries/)).toBeVisible();
    expect(screen.getByText(/Must-modify \(1\): RetryHandler/)).toBeInTheDocument();
    expect(screen.getByText("Code understanding")).toBeInTheDocument();
  });

  it("expands Debug on demand and fetches the debug bundle (?debug=true), not before", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");

    await userEvent.click(screen.getByText("Debug"));

    await waitFor(() => {
      expect(workflowsApi.fetchUnderstanding).toHaveBeenLastCalledWith("tok", "w1", true);
    });
  });

  it("shows the investigation trail inside Debug", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    await userEvent.click(screen.getByText("Debug"));

    expect(
      await screen.findByText("Work out which service this belongs to."),
    ).toBeInTheDocument();
    expect(screen.getByText("I'll search the indexed repositories.")).toBeInTheDocument();
    expect(screen.getByText("Only one repository is indexed.")).toBeInTheDocument();
  });

  it("shows capability signals inside Advanced Details, not Debug, omitting inapplicable capabilities", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    await userEvent.click(screen.getByText("Advanced Details"));

    expect(await screen.findByText("29%")).toBeVisible();
    expect(screen.getByText("Knowledge graph reachable")).toBeVisible();
    // An unsatisfied signal must explain what is missing, not just show a cross.
    expect(screen.getByText(/the repository is likely not indexed yet/)).toBeVisible();
    expect(screen.queryByText("Work item")).not.toBeInTheDocument();
  });

  it("shows evidence details inside Advanced Details, not Debug, labeling unverified claims", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    await userEvent.click(screen.getByText("Advanced Details"));

    expect(await screen.findByText("ghost-service")).toBeVisible();
    expect(screen.getByText("unverified claim")).toBeVisible();
    expect(screen.getAllByText(/Queried the graph: 1 repository\./).length).toBeGreaterThan(0);
  });

  it("opening Advanced Details alone triggers the shared ?debug=true fetch", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    await userEvent.click(screen.getByText("Advanced Details"));

    await waitFor(() => {
      expect(workflowsApi.fetchUnderstanding).toHaveBeenLastCalledWith("tok", "w1", true);
    });
  });

  it("keeps capability signals and evidence details out of Debug's own visible content", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    // Opening Debug alone (not Advanced Details) still populates the shared
    // bundle, so this proves the *placement* moved, not just data
    // availability: the signal/finding text is present in the DOM (inside
    // the still-collapsed Advanced Details), but not visible under Debug.
    await userEvent.click(screen.getByText("Debug"));

    expect(await screen.findByText("29%")).not.toBeVisible();
    expect(screen.getByText("ghost-service")).not.toBeVisible();
  });

  it("shows raw gaps, graph traversal, and the raw payload inside Debug", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    await userEvent.click(screen.getByText("Debug"));

    expect(
      await screen.findByText("No design documentation was found for this work."),
    ).toBeVisible();
    expect(screen.getByText(/constraints a ticket omits/)).toBeVisible();
    expect(screen.getByText(/Connect Confluence/)).toBeVisible();
    expect(screen.getByText(/Repository ranking:/)).toBeVisible();
    expect(screen.getByText(/"must_modify"/)).toBeVisible();
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

  it("shows a load error without crashing when Engineering Understanding fails to fetch", async () => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockRejectedValue(new Error("Network error"));
    const legacy = makeResult({ discovery_report: undefined as never });
    renderWithAuth(<ContextExplorerPanel workflowId="w1" result={legacy} onOverridden={vi.fn()} />);

    expect(await screen.findByText("Network error")).toBeInTheDocument();
    // The readiness verdict comes from `result`, not the understanding
    // fetch, so it still renders even when Engineering Understanding fails.
    expect(screen.getByText("PARTIAL")).toBeInTheDocument();
  });
});
