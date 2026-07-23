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
    render(<EvidencePanel evidence={sampleEvidence} />);
    expect(screen.getByText("Found 5 components across 2 repos")).toBeInTheDocument();
    expect(screen.getByText("Queried 3 repositories")).toBeInTheDocument();
    expect(screen.getByText("LLM synthesized a 4-step plan")).toBeInTheDocument();
  });

  it("shows evidence count", () => {
    render(<EvidencePanel evidence={sampleEvidence} />);
    expect(screen.getByText("3 items")).toBeInTheDocument();
  });

  it("renders empty state", () => {
    render(<EvidencePanel evidence={[]} />);
    expect(screen.getByText("No evidence recorded for this run.")).toBeInTheDocument();
  });

  it("renders failed evidence with distinct styling", () => {
    render(<EvidencePanel evidence={failedEvidence} />);
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
    render(<EvidencePanel evidence={sampleEvidence} />);
    expect(screen.getByRole("list", { name: "Evidence items" })).toBeInTheDocument();
  });
});
