import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AgentStep, WorkflowDetail } from "../../types/agent";
import { WorkflowSummaryHero } from "./WorkflowSummaryHero";

function makeStep(overrides: Partial<AgentStep> = {}): AgentStep {
  return {
    step_id: "s1",
    agent_id: "planning",
    status: "completed",
    confidence: { score: 0.9, reasoning: "" },
    evidence: [
      { kind: "tool_call", reference: "a", summary: "a" },
      { kind: "graph_traversal", reference: "b", summary: "b" },
    ],
    result: { repositories_consulted: ["order-service", "payment-service"] },
    prompt_version: "1.0",
    output_ref: null,
    error_message: null,
    latency_ms: 1000,
    created_at: null,
    completed_at: null,
    ...overrides,
  };
}

const workflow: WorkflowDetail = {
  workflow_id: "wf-1",
  title: "Implement JWT authentication",
  workflow_type: "legacy_sdlc",
  current_stage: "completed",
  status: "completed",
  stages: [
    { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
    { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
  ],
  runs: [],
  created_at: "2026-01-01T10:00:00Z",
  updated_at: "2026-01-01T10:08:21Z",
  approved_by: null,
};

describe("WorkflowSummaryHero", () => {
  it("shows the workflow title and completion state", () => {
    render(<WorkflowSummaryHero workflow={workflow} steps={[makeStep()]} />);
    expect(screen.getByText("Implement JWT authentication")).toBeInTheDocument();
    expect(screen.getByText("Workflow Complete")).toBeInTheDocument();
  });

  it("computes duration from the real created_at/updated_at span", () => {
    render(<WorkflowSummaryHero workflow={workflow} steps={[makeStep()]} />);
    expect(screen.getByText("8m 21s")).toBeInTheDocument();
  });

  it("sums real evidence counts across all steps", () => {
    render(
      <WorkflowSummaryHero workflow={workflow} steps={[makeStep(), makeStep({ step_id: "s2" })]} />,
    );
    expect(screen.getByText("4 facts")).toBeInTheDocument();
  });

  it("dedupes repositories_consulted across steps", () => {
    render(
      <WorkflowSummaryHero workflow={workflow} steps={[makeStep(), makeStep({ step_id: "s2" })]} />,
    );
    // Same two repos reported by both steps -> still just 2, not 4.
    const label = screen.getByText("Repositories");
    expect(label.previousElementSibling).toHaveTextContent("2");
  });

  it("averages confidence across steps that reported a score", () => {
    render(
      <WorkflowSummaryHero
        workflow={workflow}
        steps={[
          makeStep({ confidence: { score: 0.8, reasoning: "" } }),
          makeStep({ step_id: "s2", confidence: { score: 1.0, reasoning: "" } }),
        ]}
      />,
    );
    expect(screen.getByText("90%")).toBeInTheDocument();
  });

  it("shows a placeholder when no step reported a confidence score", () => {
    render(
      <WorkflowSummaryHero
        workflow={workflow}
        steps={[makeStep({ confidence: { score: null, reasoning: "" } })]}
      />,
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
