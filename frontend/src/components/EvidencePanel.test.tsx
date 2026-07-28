import { render, screen } from "@testing-library/react";
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

  it("renders evidence kind labels", () => {
    render(<EvidencePanel evidence={sampleEvidence} />);
    expect(screen.getByText("Graph Traversal")).toBeInTheDocument();
    expect(screen.getByText("Tool Call")).toBeInTheDocument();
    expect(screen.getByText("LLM Reasoning")).toBeInTheDocument();
  });

  it("has accessible list role", () => {
    render(<EvidencePanel evidence={sampleEvidence} defaultExpanded />);
    expect(screen.getByRole("list", { name: "Evidence items" })).toBeInTheDocument();
  });
});
