import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ReportViewModel } from "../../lib/api/reports";
import { ReportView } from "./ReportView";

/**
 * Document-level tests: these assert on the whole rendered post–Engineering
 * Review document, not on one card. What's under test is the properties a
 * reader relies on — proof separated from speculation, one consistent
 * blocking count, a stated outcome, and an actionable recommendation.
 */

function model(overrides: Partial<ReportViewModel> = {}): ReportViewModel {
  return {
    header: {
      question: "Filtered rows are still being exported",
      workflow_title: "Export filter investigation",
      repository: "ingest-service",
      readiness: "needs_revision",
      reported_readiness: "needs_revision",
      generated_at: "2026-01-01T00:00:00Z",
    },
    review_outcome: {
      availability: { status: "available", reason: null },
      readiness: "needs_revision",
      reported_readiness: "needs_revision",
      outcome_label: "Needs Revision",
      outcome_statement: "Engineering Review did not approve implementation.",
      reasons: [
        "The strongest candidate explanation (95% confidence) is not confirmed.",
        "Unresolved contradiction in the evidence: the ticket and the raw input disagree",
      ],
      recommendation:
        "Do not implement the proposed change yet. Validate which of the 2 competing explanations is correct before changing any code.",
      blocking_count: 1,
      advisory_count: 1,
    },
    confidence: {
      availability: { status: "available", reason: null },
      current: 0.45,
      points: [],
      summary_sentence: "Dropped after Engineering Review (70→45).",
      breakdown: {
        overall: 0.45,
        overall_label: "Overall resolution confidence",
        overall_basis: "45% — confidence that the issue is understood well enough to fix.",
        top_hypothesis_confidence: 0.95,
        top_hypothesis_statement: "the export filter is the wrong place",
        top_hypothesis_label: "Root-cause candidate confidence",
        divergence_note: "These two numbers measure different things.",
      },
    },
    timeline: {
      availability: { status: "available", reason: null },
      steps: [
        { cycle: 1, provider: "graph", action: "a", outcome: "success", summary: "step one", intent: "i" },
      ],
      truncated_count: 24,
    },
    knowledge: {
      availability: { status: "available", reason: null },
      known: ["3 repositories recorded, 1 verified."],
      known_truncated_count: 0,
      unknown: ["where the unexpected value is introduced"],
      unknown_truncated_count: 0,
    },
    findings: {
      availability: { status: "available", reason: null },
      items: [
        {
          statement: "repository: ingest-service",
          source_stage: "context_discovery",
          source_field: "discovery_report.findings[repository].items[0]",
          evidence_summary: "indexed in the graph",
        },
      ],
      truncated_count: 0,
    },
    hypotheses: {
      availability: { status: "available", reason: null },
      synthesis_state: "completed",
      items: [
        {
          entry: {
            statement: "the export filter is the wrong place",
            status: "supported",
            confidence: 0.95,
            supporting_evidence: ["the filter reads the unit column"],
            contradicting_evidence: [],
          },
          verification_status: null,
        },
      ],
      truncated_count: 0,
    },
    contradictions: {
      availability: { status: "available", reason: null },
      synthesis_state: "completed",
      items: [
        {
          entry: {
            statement: "the ticket and the raw input disagree",
            evidence_for: ["the ticket lists the value as exported"],
            evidence_against: ["the raw input never contains that value"],
            resolved: false,
            resolution_note: "",
          },
          is_blocking: true,
          impact: "Blocks the outcome: the conclusion cannot be relied on.",
          required_resolution: "Establish which side is correct.",
        },
      ],
    },
    evidence: {
      availability: { status: "available", reason: null },
      categories: [{ kind: "graph_traversal", count: 12 }],
      total: 12,
    },
    next_actions: {
      availability: { status: "available", reason: null },
      questions: [
        {
          text: "Unresolved contradiction: the ticket and the raw input disagree",
          source_stage: "context_discovery",
          is_blocking: true,
          kind: "unresolved_contradiction",
        },
        {
          text: "where the unexpected value is introduced",
          source_stage: "context_discovery",
          is_blocking: false,
          kind: "knowledge_gap",
        },
      ],
      blocking_count: 1,
      advisory_count: 1,
    },
    executive_summary: "The relevant filtering logic was identified but is not confirmed.",
    ...overrides,
  };
}

describe("ReportView — the post–Engineering Review document", () => {
  it("states the Engineering Review outcome, its reasons, and an actionable recommendation", () => {
    render(<ReportView model={model()} />);
    expect(
      screen.getByText(/Engineering Review Outcome: Needs Revision/),
    ).toBeInTheDocument();
    expect(screen.getByText(/did not approve implementation/)).toBeInTheDocument();
    expect(screen.getByText(/Unresolved contradiction in the evidence/)).toBeInTheDocument();
    expect(screen.getByText(/Do not implement the proposed change yet/)).toBeInTheDocument();
  });

  it("separates confirmed findings from hypotheses, and never labels a hypothesis a root cause", () => {
    render(<ReportView model={model()} />);
    const confirmed = screen.getByText("Confirmed findings").closest("div")!.parentElement!
      .parentElement!;
    expect(within(confirmed).getByText("repository: ingest-service")).toBeInTheDocument();

    expect(screen.getByText("Potential root cause / hypotheses")).toBeInTheDocument();
    expect(screen.getByText(/none of these is a confirmed root cause/)).toBeInTheDocument();
    expect(screen.getByText(/These are candidate explanations, not conclusions/)).toBeInTheDocument();
  });

  it("labels the two confidence numbers separately and explains the gap", () => {
    render(<ReportView model={model()} />);
    expect(screen.getByText("Overall resolution confidence")).toBeInTheDocument();
    expect(screen.getByText("45%")).toBeInTheDocument();
    expect(screen.getByText("Root-cause candidate confidence")).toBeInTheDocument();
    // 95% appears twice by design — once as the labelled root-cause
    // candidate number here, once on the hypothesis card itself.
    expect(screen.getAllByText("95%").length).toBeGreaterThan(0);
    expect(screen.getByText("These two numbers measure different things.")).toBeInTheDocument();
  });

  it("reports one blocking count everywhere — the regression that let two sections disagree", () => {
    render(<ReportView model={model()} />);
    const counts = screen.getAllByText(/1 blocking, 1 advisory/);
    // Both the outcome card and the next-steps card state it, identically,
    // because both read the same backend-computed counts.
    expect(counts.length).toBe(2);
    expect(screen.queryByText(/0 blocking/)).not.toBeInTheDocument();
  });

  it("shows an unresolved contradiction with its impact and required resolution", () => {
    render(<ReportView model={model()} />);
    expect(screen.getByText("Impact on conclusion:")).toBeInTheDocument();
    expect(screen.getByText("Establish which side is correct.")).toBeInTheDocument();
    expect(screen.getByText("Unresolved — blocking")).toBeInTheDocument();
  });

  it("keeps the execution timeline as collapsed provenance rather than the body of the document", () => {
    const { container } = render(<ReportView model={model()} />);
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect(details!.open).toBe(false);
    expect(screen.getByText(/audit detail, not part of the decision/)).toBeInTheDocument();
    // The provenance is still there in full — collapsed, never removed.
    expect(screen.getByText("step one")).toBeInTheDocument();
  });

  it("preserves traceability: repository, evidence counts, and per-finding sources stay visible", () => {
    render(<ReportView model={model()} />);
    expect(screen.getByText("ingest-service")).toBeInTheDocument();
    expect(
      screen.getByText(/discovery_report.findings\[repository\].items\[0\]/),
    ).toBeInTheDocument();
    expect(screen.getByText(/graph.traversal/i)).toBeInTheDocument();
    // The same gap appears as an "unknown" and as an advisory next step —
    // both from the one open-item list, never independently derived.
    expect(screen.getAllByText("where the unexpected value is introduced")).toHaveLength(2);
  });

  it("does not present coverage counts as a second 'known' section competing with confirmed findings", () => {
    // Rendering a real report showed "Known" (per-kind coverage counts)
    // reading as a duplicate of "Confirmed findings" (the specific proven
    // statements) — two different claims wearing the same word.
    render(<ReportView model={model()} />);
    expect(screen.getByText("Coverage & knowledge gaps")).toBeInTheDocument();
    expect(screen.getByText(/Recorded — how much ground was covered/)).toBeInTheDocument();
    expect(screen.getByText("Still unknown")).toBeInTheDocument();
    expect(screen.queryByText("Known")).not.toBeInTheDocument();
    expect(screen.getByText("3 repositories recorded, 1 verified.")).toBeInTheDocument();
  });

  it("renders an approved investigation as an approval, with no 'do not implement' framing", () => {
    render(
      <ReportView
        model={model({
          header: { ...model().header, readiness: "ready", reported_readiness: "ready" },
          review_outcome: {
            ...model().review_outcome,
            readiness: "ready",
            reported_readiness: "ready",
            outcome_label: "Approved",
            outcome_statement: "Engineering Review approved this investigation.",
            reasons: ["1 finding was confirmed by verified evidence and can be relied on."],
            recommendation: "Proceed with implementation as reviewed.",
            blocking_count: 0,
            advisory_count: 0,
          },
          next_actions: {
            availability: { status: "available", reason: null },
            questions: [],
            blocking_count: 0,
            advisory_count: 0,
          },
        })}
      />,
    );
    expect(screen.getByText(/Engineering Review Outcome: Approved/)).toBeInTheDocument();
    expect(screen.getByText("Proceed with implementation as reviewed.")).toBeInTheDocument();
    expect(screen.queryByText(/Do not implement/)).not.toBeInTheDocument();
  });

  it("surfaces a downgrade when Engineering Review said ready but blocking items remain", () => {
    render(
      <ReportView
        model={model({
          review_outcome: { ...model().review_outcome, reported_readiness: "ready" },
        })}
      />,
    );
    expect(screen.getByText(/Engineering Review itself reported/)).toBeInTheDocument();
    expect(screen.getByText(/more conservative outcome/)).toBeInTheDocument();
  });
});
