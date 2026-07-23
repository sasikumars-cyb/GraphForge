import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AgentStep, WorkflowStageInfo } from "../../types/agent";
import { AgentActivityFeed } from "./AgentActivityFeed";

function makeStep(evidenceSummaries: string[]): AgentStep {
  return {
    step_id: "s1",
    agent_id: "planning",
    status: "completed",
    confidence: { score: 0.8, reasoning: "" },
    evidence: evidenceSummaries.map((summary, i) => ({
      kind: "tool_call" as const,
      reference: `tool-${i}`,
      summary,
    })),
    result: {},
    prompt_version: "1.0",
    output_ref: null,
    error_message: null,
    latency_ms: 500,
    created_at: null,
    completed_at: null,
  };
}

const stages: WorkflowStageInfo[] = [
  { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
  { stage: "development", label: "Development", status: "running", run_id: "run-2" },
  { stage: "testing", label: "Testing", status: "pending", run_id: null },
  { stage: "review", label: "Review", status: "pending", run_id: null },
];

describe("AgentActivityFeed", () => {
  it("shows real evidence lines for a completed stage", () => {
    const stepsByRunId = new Map([["run-1", makeStep(["Found 12 components."])]]);
    render(<AgentActivityFeed stages={stages} stepsByRunId={stepsByRunId} />);
    expect(screen.getByText("Found 12 components.")).toBeInTheDocument();
  });

  it("shows 'Waiting…' for a queued stage with no run yet", () => {
    render(<AgentActivityFeed stages={stages} stepsByRunId={new Map()} />);
    expect(screen.getAllByText("Waiting…")).toHaveLength(2);
  });

  it("shows a working indicator for the currently running stage", () => {
    const stepsByRunId = new Map([["run-2", makeStep([])]]);
    render(<AgentActivityFeed stages={stages} stepsByRunId={stepsByRunId} />);
    expect(screen.getByText("Starting up…")).toBeInTheDocument();
  });

  it("marks failed evidence with a failure marker instead of a checkmark", () => {
    const stepsByRunId = new Map([["run-1", makeStep(["FAILED: Neo4j unreachable."])]]);
    render(<AgentActivityFeed stages={stages} stepsByRunId={stepsByRunId} />);
    expect(screen.getByText("Neo4j unreachable.")).toBeInTheDocument();
    expect(screen.getByText("✕")).toBeInTheDocument();
  });
});
