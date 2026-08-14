import { render, screen, waitFor, within } from "@testing-library/react";
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
    completion_status: "PARTIAL",
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
    relevant_areas: [{ name: "Retry handling", components: ["RetryHandler"], total: 1 }],
    files_to_review: [],
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
    completion_status: "PARTIAL",
    reasoning_summary: {
      has_reasoning: false,
      degraded: false,
      hypotheses: [],
      contradictions: [],
      open_contradiction_count: 0,
      resolved_contradiction_count: 0,
      strongest_hypothesis_id: null,
      dead_ends: [],
      next_investigation: [],
      last_update: "",
    },
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

  it("P1 regression: Files to Review reads the curated files_to_review field, not raw graph_components", async () => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockResolvedValue(
      makeUnderstandingDto({ files_to_review: ["soco_ingest/src/transforms/rate_association.py"] }),
    );
    renderWithAuth(
      <ContextExplorerPanel
        workflowId="w1"
        result={makeResult({
          // The real-world bug shape: the raw component list is entirely
          // test files. If "Files to Review" fell back to reading this,
          // it would show test_export_rate_association.py, not the real
          // production file — exactly the audit's finding.
          graph_components: [{ name: "test_export_rate_association", file_path: "tests/test_export_rate_association.py" }],
        })}
        onOverridden={vi.fn()}
      />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(
      screen.getByText("soco_ingest/src/transforms/rate_association.py"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("tests/test_export_rate_association.py"),
    ).not.toBeInTheDocument();
  });

  it("P1/P2 regression: an empty files_to_review shows an honest empty state, never a raw graph_components dump", async () => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockResolvedValue(
      makeUnderstandingDto({ files_to_review: [] }),
    );
    renderWithAuth(
      <ContextExplorerPanel
        workflowId="w1"
        result={makeResult({
          // The blocked/low-confidence investigation shape from the live
          // QA: curation legitimately found nothing to recommend, but
          // graph_components still has raw (mostly test) entries. The old
          // fallback would render every one of these; the fix must not.
          graph_components: [
            { name: "test_ts04_start_datetime", file_path: "tests/unittest/test_ts04.py" },
            { name: "test_ts11_update_datetime", file_path: "tests/unittest/test_ts11.py" },
          ],
        })}
        onOverridden={vi.fn()}
      />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(screen.getByText("No clearly relevant files identified.")).toBeInTheDocument();
    expect(
      screen.getByText(/did not identify a production file with sufficient evidence/),
    ).toBeInTheDocument();
    expect(screen.queryByText("tests/unittest/test_ts04.py")).not.toBeInTheDocument();
    expect(screen.queryByText("tests/unittest/test_ts11.py")).not.toBeInTheDocument();
  });

  it("P1 regression: Relevant Areas shows tier counts, and collapses Tests by default", async () => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockResolvedValue(
      makeUnderstandingDto({
        relevant_areas: [
          { name: "Production Code", components: ["rate_association.py"], total: 1 },
          {
            name: "Tests",
            components: Array.from({ length: 12 }, (_, i) => `test_${i}`),
            total: 340,
          },
        ],
      }),
    );
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    await userEvent.click(screen.getByText("Technical Details"));

    // Production Code is open by default; its count is visible without
    // any further interaction. Scoped to the area's own heading (not a
    // bare `getByText("1")`) because the Knowledge Ledger and Investigation
    // Timeline nodes above it can legitimately show their own "1"s too.
    expect(screen.getByText("rate_association.py")).toBeVisible();
    const productionCodeHeading = screen.getByText("Production Code").parentElement;
    expect(productionCodeHeading).not.toBeNull();
    expect(within(productionCodeHeading as HTMLElement).getByText("1")).toBeInTheDocument();

    // Tests is collapsed by default (a secondary tier) — its own items
    // aren't visible until expanded, but the honest total (340, not just
    // the 12 shown once opened) is visible immediately.
    expect(screen.getByText("340")).toBeInTheDocument();
    expect(screen.queryByText(/test_0/)).not.toBeVisible();
    await userEvent.click(screen.getByText("Tests"));
    expect(screen.getByText(/test_0/)).toBeVisible();
    expect(screen.getByText(/and 328 more/)).toBeInTheDocument();
  });

  it("preserves the readiness verdict, in the engine's own words", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(screen.getByText("PARTIAL")).toBeInTheDocument();
    // The confidence gauge (ReasoningOverview) carries the percentage as
    // its accessible name rather than combined visible text, since the
    // digits and the "%" are two separately-styled DOM nodes.
    expect(screen.getByRole("img", { name: "72% context completeness" })).toBeInTheDocument();
  });

  it("P3 regression: never renders NaN% confidence when no completed result exists yet", async () => {
    // The awaiting_input live-QA shape: the in-flight AgentStep.result has
    // no confidence score yet (mid-clarification, nothing completed) —
    // `Math.round(undefined * 100)` used to silently render "NaN%".
    renderWithAuth(
      <ContextExplorerPanel
        workflowId="w1"
        result={makeResult({ confidence: undefined as unknown as number })}
        onOverridden={vi.fn()}
      />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(screen.getByRole("img", { name: "Context completeness not yet available" })).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
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

  it("shows a plain-language 'what we know' checklist on the default view, full readiness detail and constraints only in Technical Details", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");

    // Recommendations are now visible on the first screen as "Proposed Change".
    expect(screen.getByText(/Reuse the existing backoff utility/)).toBeVisible();

    // Confidence explanation is visible on the first screen in
    // "Why GraphForge believes this".
    expect(screen.getByText(/Completed: Code understanding/)).toBeVisible();

    // "What we know" — the same satisfied/outstanding capability checklist
    // Technical Details' fuller "Capability Readiness" section shows,
    // surfaced here in plain language so a reader never has to expand
    // anything to see what's actually backing the confidence number.
    // Scoped to the checklist itself since "Code understanding"
    // legitimately appears a second time inside Technical Details' own,
    // more detailed rendering of the same underlying reasons.
    const whatWeKnow = screen.getByText("What we know").closest("div") as HTMLElement;
    expect(within(whatWeKnow).getByText("Code understanding")).toBeVisible();
    expect(within(whatWeKnow).getByText("Architecture components discovered")).toBeVisible();

    // Constraints and documentation status are in Technical Details —
    // present in the DOM but not visible until expanded.
    expect(screen.getByText("Documentation requirements not yet satisfied.")).not.toBeVisible();
    expect(screen.getByText(/Must not exceed 3 retries/)).not.toBeVisible();

    await userEvent.click(screen.getByText("Technical Details"));

    expect(screen.getByText("Documentation requirements not yet satisfied.")).toBeVisible();
    expect(screen.getByText(/Must not exceed 3 retries/)).toBeVisible();
    expect(screen.getByText(/Must-modify \(1\): RetryHandler/)).toBeInTheDocument();
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
    await userEvent.click(screen.getByText("Technical Details"));

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
    await userEvent.click(screen.getByText("Technical Details"));

    expect(await screen.findByText("ghost-service")).toBeVisible();
    expect(screen.getByText("unverified claim")).toBeVisible();
    expect(screen.getAllByText(/Queried the graph: 1 repository\./).length).toBeGreaterThan(0);
  });

  it("opening Advanced Details alone triggers the shared ?debug=true fetch", async () => {
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    await userEvent.click(screen.getByText("Technical Details"));

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

    // P2 regression: the raw backend error text used to render verbatim
    // ("No completed context discovery result for this workflow"),
    // directly contradicting the readiness/confidence line shown right
    // below it (which is real — sourced from `result`, always present).
    // The message must now say plainly that a result *does* exist.
    const banner = await screen.findByText(/Couldn.t load the full engineering understanding/);
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain("Network error");
    expect(screen.queryByText("Network error", { exact: true })).not.toBeInTheDocument();
    // The readiness verdict comes from `result`, not the understanding
    // fetch, so it still renders even when Engineering Understanding fails.
    expect(screen.getByText("PARTIAL")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// completion_status — the audit's "budget exhaustion reads identically to
// completion" finding. The backend is the sole source of truth; these tests
// only check the frontend renders whatever it was sent, unchanged.
// ---------------------------------------------------------------------------

describe("ContextExplorerPanel / completion status", () => {
  beforeEach(() => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockResolvedValue(makeUnderstandingDto());
  });

  it("shows no extra badge and no budget banner for a genuine completion", async () => {
    renderWithAuth(
      <ContextExplorerPanel
        workflowId="w1"
        result={makeResult({ readiness: "READY", completion_status: "COMPLETED" })}
        onOverridden={vi.fn()}
      />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(screen.queryByText("Stopped at cycle limit")).not.toBeInTheDocument();
    expect(screen.queryByText("Every automated avenue tried")).not.toBeInTheDocument();
    expect(screen.queryByText(/reached its cycle limit/)).not.toBeInTheDocument();
  });

  it("never shows the genuine-exhaustion phrasing for a budget cutoff, and explains what happened instead", async () => {
    renderWithAuth(
      <ContextExplorerPanel
        workflowId="w1"
        result={makeResult({ readiness: "PARTIAL", completion_status: "BUDGET_EXHAUSTED" })}
        onOverridden={vi.fn()}
      />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(screen.getByText("Stopped at cycle limit")).toBeInTheDocument();
    expect(
      screen.getByText(/reached its cycle limit — not because every avenue was exhausted/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^I've gathered everything I can on my own/)).not.toBeInTheDocument();
  });

  it("shows the providers-exhausted badge distinctly from a budget cutoff", async () => {
    renderWithAuth(
      <ContextExplorerPanel
        workflowId="w1"
        result={makeResult({ readiness: "BLOCKED", completion_status: "PROVIDERS_EXHAUSTED" })}
        onOverridden={vi.fn()}
      />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(screen.getByText("Every automated avenue tried")).toBeInTheDocument();
    expect(screen.queryByText("Stopped at cycle limit")).not.toBeInTheDocument();
  });

  it("shows no extra badge for BLOCKED — the readiness badge already says it", async () => {
    renderWithAuth(
      <ContextExplorerPanel
        workflowId="w1"
        result={makeResult({ readiness: "BLOCKED", completion_status: "BLOCKED" })}
        onOverridden={vi.fn()}
      />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(screen.queryByText("Stopped at cycle limit")).not.toBeInTheDocument();
    expect(screen.queryByText("Every automated avenue tried")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Reasoning section — hypotheses, contradictions, next-investigation.
// ---------------------------------------------------------------------------

describe("ReasoningSection", () => {
  it("shows an honest empty state when nothing was reasoned about", async () => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockResolvedValue(makeUnderstandingDto());
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    // ReasoningSection now renders inside Advanced Details (see
    // AdvancedDetailsSection) rather than always-open in the primary flow.
    await userEvent.click(screen.getByText("Technical Details"));
    expect(
      screen.getByText(/No competing hypotheses or contradictions were needed/),
    ).toBeInTheDocument();
  });

  it("renders a single hypothesis as strongest, with its evidence collapsed behind a toggle", async () => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockResolvedValue(
      makeUnderstandingDto({
        reasoning_summary: {
          has_reasoning: true,
          degraded: false,
          hypotheses: [
            {
              id: "hyp_0",
              description: "The retry loop lacks backoff entirely.",
              status: "supported",
              confidence: 0.82,
              supporting_evidence: ["RetryHandler.java has no delay between attempts"],
              contradicting_evidence: [],
              is_strongest: true,
            },
          ],
          contradictions: [],
          open_contradiction_count: 0,
          resolved_contradiction_count: 0,
          strongest_hypothesis_id: "hyp_0",
          dead_ends: [],
          next_investigation: [],
          last_update: "Cycle 2: re-synthesized over 4 evidence record(s) — 1 hypothesis/es.",
        },
      }),
    );
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    // ReasoningSection now renders inside Advanced Details (see
    // AdvancedDetailsSection) rather than always-open in the primary flow.
    await userEvent.click(screen.getByText("Technical Details"));

    // Rendered twice by design — once in the "Strongest explanation"
    // summary line, once as the hypothesis card's own description.
    expect(screen.getAllByText("The retry loop lacks backoff entirely.").length).toBe(2);
    expect(screen.getByText("Strongest")).toBeInTheDocument();
    expect(screen.getByText("82%")).toBeInTheDocument();
    // The strongest hypothesis starts expanded — its evidence is visible
    // without an extra click.
    expect(screen.getByText(/RetryHandler\.java has no delay between attempts/)).toBeVisible();
    expect(screen.getByText("Hide evidence")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Hide evidence"));
    expect(await screen.findByText("Show evidence")).toBeInTheDocument();
    expect(
      screen.queryByText(/RetryHandler\.java has no delay between attempts/),
    ).not.toBeInTheDocument();
  });

  it("marks the highest-confidence non-rejected hypothesis strongest among several", async () => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockResolvedValue(
      makeUnderstandingDto({
        reasoning_summary: {
          has_reasoning: true,
          degraded: false,
          hypotheses: [
            {
              id: "hyp_0",
              description: "Eliminated: caching layer.",
              status: "rejected",
              confidence: 0.95,
              supporting_evidence: [],
              contradicting_evidence: [],
              is_strongest: false,
            },
            {
              id: "hyp_1",
              description: "Missing backoff configuration.",
              status: "supported",
              confidence: 0.7,
              supporting_evidence: [],
              contradicting_evidence: [],
              is_strongest: true,
            },
          ],
          contradictions: [],
          open_contradiction_count: 0,
          resolved_contradiction_count: 0,
          strongest_hypothesis_id: "hyp_1",
          dead_ends: [],
          next_investigation: [],
          last_update: "",
        },
      }),
    );
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(
      screen.getByText(
        (_content, element) =>
          element?.tagName.toLowerCase() === "p" &&
          element.textContent === "Strongest explanation: Missing backoff configuration.",
      ),
    ).toBeInTheDocument();
    // The rejected hypothesis must never be labelled strongest, however
    // high its own confidence was before elimination.
    expect(screen.queryByText("Eliminated: caching layer.")).toBeInTheDocument();
    const rejectedCard = screen.getByText("Eliminated: caching layer.").closest("div");
    expect(rejectedCard?.textContent).not.toContain("Strongest");
  });

  it("renders one unresolved contradiction as open, with evidence on both sides", async () => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockResolvedValue(
      makeUnderstandingDto({
        reasoning_summary: {
          has_reasoning: true,
          degraded: false,
          hypotheses: [],
          contradictions: [
            {
              id: "contra_0",
              description: "Ticket says retries are enabled; code shows none configured.",
              evidence_for: ["Ticket description: 'retries are already on'"],
              evidence_against: ["RetryHandler.java has no retry annotation"],
              resolved: false,
              resolution_note: "",
            },
          ],
          open_contradiction_count: 1,
          resolved_contradiction_count: 0,
          strongest_hypothesis_id: null,
          dead_ends: [],
          next_investigation: [],
          last_update: "",
        },
      }),
    );
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    // The open-contradiction count is visible in the collapsed summary too.
    expect(screen.getByText("1 contradiction (1 open)")).toBeInTheDocument();
    expect(screen.getByText("Open — being investigated")).toBeInTheDocument();
    expect(screen.getByText("Ticket description: 'retries are already on'")).toBeInTheDocument();
    expect(screen.getByText("RetryHandler.java has no retry annotation")).toBeInTheDocument();
  });

  it("renders a resolved contradiction distinctly, with its resolution note", async () => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockResolvedValue(
      makeUnderstandingDto({
        reasoning_summary: {
          has_reasoning: true,
          degraded: false,
          hypotheses: [],
          contradictions: [
            {
              id: "contra_0",
              description: "Two docs disagreed on the retry limit.",
              evidence_for: [],
              evidence_against: [],
              resolved: true,
              resolution_note: "The newer design doc is authoritative — 3 retries confirmed.",
            },
          ],
          open_contradiction_count: 0,
          resolved_contradiction_count: 1,
          strongest_hypothesis_id: null,
          dead_ends: [],
          next_investigation: [],
          last_update: "",
        },
      }),
    );
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(screen.getByText("1 contradiction (resolved)")).toBeInTheDocument();
    expect(screen.getByText("Resolved")).toBeInTheDocument();
    expect(
      screen.getByText(/The newer design doc is authoritative/),
    ).toBeInTheDocument();
  });

  it("renders multiple contradictions with a mixed resolved/open count", async () => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockResolvedValue(
      makeUnderstandingDto({
        reasoning_summary: {
          has_reasoning: true,
          degraded: false,
          hypotheses: [],
          contradictions: [
            {
              id: "contra_0",
              description: "a",
              evidence_for: [],
              evidence_against: [],
              resolved: true,
              resolution_note: "",
            },
            {
              id: "contra_1",
              description: "b",
              evidence_for: [],
              evidence_against: [],
              resolved: false,
              resolution_note: "",
            },
            {
              id: "contra_2",
              description: "c",
              evidence_for: [],
              evidence_against: [],
              resolved: false,
              resolution_note: "",
            },
          ],
          open_contradiction_count: 2,
          resolved_contradiction_count: 1,
          strongest_hypothesis_id: null,
          dead_ends: [],
          next_investigation: [
            { capability: "architecture", label: "Architecture", priority: 0.4 },
          ],
          last_update: "",
        },
      }),
    );
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(screen.getByText("3 contradictions (2 open)")).toBeInTheDocument();
    expect(screen.getAllByText("Resolved")).toHaveLength(1);
    expect(screen.getAllByText("Open — being investigated")).toHaveLength(2);
    // "Next investigation" moved out of ReasoningSection into
    // `UnknownsAndNext` (Reasoning Story node 6) so it isn't shown twice —
    // see that component's own tests for its heading/label text.
    expect(screen.getByText("Investigating next: Architecture")).toBeInTheDocument();
  });

  it("shows the degraded notice when synthesis fell back to a deterministic summary", async () => {
    vi.mocked(workflowsApi.fetchUnderstanding).mockReset();
    vi.mocked(workflowsApi.fetchUnderstanding).mockResolvedValue(
      makeUnderstandingDto({
        reasoning_summary: {
          has_reasoning: false,
          degraded: true,
          hypotheses: [],
          contradictions: [],
          open_contradiction_count: 0,
          resolved_contradiction_count: 0,
          strongest_hypothesis_id: null,
          dead_ends: [],
          next_investigation: [],
          last_update: "Cycle 1: synthesis degraded to a deterministic summary over 3 evidence record(s).",
        },
      }),
    );
    renderWithAuth(
      <ContextExplorerPanel workflowId="w1" result={makeResult()} onOverridden={vi.fn()} />,
    );
    await screen.findByText("Add retry backoff for flaky downstream calls");
    expect(screen.getByText(/didn't complete cleanly/)).toBeInTheDocument();
  });
});
