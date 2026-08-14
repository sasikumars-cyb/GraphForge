import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvidencePanel } from "./EvidencePanel";
import type { Evidence } from "../types/agent";

const sampleEvidence: Evidence[] = [
  { kind: "graph_traversal", reference: "traverse_architecture_graph", summary: "Found 5 components across 2 repos" },
  { kind: "tool_call", reference: "get_indexed_repositories", summary: "Queried 3 repositories" },
  { kind: "llm_reasoning", reference: "llm_synthesis", summary: "LLM synthesized a 4-step plan" },
];

const failedEvidence: Evidence[] = [
  { kind: "tool_call", reference: "traverse_architecture_graph", summary: "FAILED: Neo4j connection refused" },
];

describe("EvidencePanel", () => {
  it("renders all evidence items", () => {
    // Full per-item summaries only render in the expanded list view — the
    // default collapsed view shows one kind-label pill per item instead
    // (see "renders evidence kind labels" below), so this test opts in.
    render(<EvidencePanel evidence={sampleEvidence} defaultExpanded />);
    expect(screen.getByText("Found 5 components across 2 repos")).toBeInTheDocument();
    expect(screen.getByText("Queried 3 repositories")).toBeInTheDocument();
    expect(screen.getByText("LLM synthesized a 4-step plan")).toBeInTheDocument();
  });

  it("shows evidence count", () => {
    render(<EvidencePanel evidence={sampleEvidence} />);
    expect(screen.getByText(/3 verifiable item/)).toBeInTheDocument();
  });

  it("renders empty state", () => {
    render(<EvidencePanel evidence={[]} />);
    expect(screen.getByText(/No evidence was collected/)).toBeInTheDocument();
  });

  it("renders failed evidence with distinct styling", () => {
    render(<EvidencePanel evidence={failedEvidence} defaultExpanded />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("FAILED: Neo4j connection refused")).toBeInTheDocument();
  });

  it("shows one source-count chip per kind, not one pill per item", () => {
    // Regression: the collapsed view used to render one identically-labeled
    // "Tool Call" pill per evidence item (25 of them on a real
    // investigation, saying nothing) — it must now dedupe by source into a
    // single counted chip each.
    render(<EvidencePanel evidence={sampleEvidence} />);
    const chips = screen.getByLabelText("Evidence source counts");
    expect(within(chips).getByText("Graph Traversal")).toBeInTheDocument();
    expect(within(chips).getByText("Tool Call")).toBeInTheDocument();
    expect(within(chips).getByText("LLM Reasoning")).toBeInTheDocument();
  });

  it("shows a real evidence summary, not just a label, in the collapsed view", () => {
    // The collapsed view used to convey nothing beyond "3 tool calls
    // happened" — it must now surface what was actually found.
    render(<EvidencePanel evidence={sampleEvidence} />);
    expect(screen.getByText("Queried 3 repositories")).toBeInTheDocument();
  });

  it("counts repeated evidence from the same source into one chip", () => {
    const repeated: Evidence[] = [
      { kind: "tool_call", reference: "jira:fetch_work_item:X", summary: "Retrieved issue X." },
      { kind: "tool_call", reference: "jira:add_comment:X", summary: "Commented on X." },
      { kind: "tool_call", reference: "github:fetch_source_files:repo", summary: "Read 2 files." },
    ];
    render(<EvidencePanel evidence={repeated} />);
    const chips = screen.getByLabelText("Evidence source counts");
    expect(within(chips).getByText("Jira")).toBeInTheDocument();
    expect(within(chips).getByText("· 2")).toBeInTheDocument();
    expect(within(chips).getByText("GitHub")).toBeInTheDocument();
  });

  it("explains why a highlighted piece of evidence matters, not just what it is", () => {
    // "Why should I trust this?" — a source name and a summary alone
    // don't answer that; the highlight cards need a third line.
    const evidence: Evidence[] = [
      { kind: "tool_call", reference: "jira:fetch_work_item:X", summary: "Retrieved issue X." },
    ];
    render(<EvidencePanel evidence={evidence} />);
    const highlights = screen.getByLabelText("Evidence highlights");
    expect(within(highlights).getByText(/Why it matters:/)).toBeInTheDocument();
    expect(
      within(highlights).getByText(/Defines what this investigation is trying to resolve/),
    ).toBeInTheDocument();
  });

  it("omits 'why it matters' rather than inventing one when the source isn't recognized", () => {
    const evidence: Evidence[] = [
      { kind: "tool_call", reference: "some_future_source:do_thing", summary: "Did a thing." },
    ];
    render(<EvidencePanel evidence={evidence} />);
    expect(screen.queryByText(/Why it matters:/)).not.toBeInTheDocument();
  });

  it("has accessible list role", () => {
    render(<EvidencePanel evidence={sampleEvidence} defaultExpanded />);
    expect(screen.getByRole("list", { name: "Evidence items" })).toBeInTheDocument();
  });
});
