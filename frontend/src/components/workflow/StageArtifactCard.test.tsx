import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AgentStep } from "../../types/agent";
import { StageArtifactCard } from "./StageArtifactCard";

function makeStep(overrides: Partial<AgentStep> = {}): AgentStep {
  return {
    step_id: "s1",
    agent_id: "planning",
    status: "completed",
    confidence: { score: 0.85, reasoning: "Grounded in real graph data." },
    evidence: [
      { kind: "tool_call", reference: "a", summary: "a" },
      { kind: "graph_traversal", reference: "b", summary: "b" },
    ],
    result: {
      executive_summary: "A plan to add JWT auth.",
      implementation_steps: [{ order: 1, description: "Add filter" }],
      affected_components: ["AuthService"],
    },
    prompt_version: "1.0",
    output_ref: null,
    error_message: null,
    latency_ms: 4200,
    created_at: null,
    completed_at: null,
    ...overrides,
  };
}

describe("StageArtifactCard", () => {
  it("shows the real executive summary", () => {
    render(<StageArtifactCard stage="planning" step={makeStep()} />);
    expect(screen.getByText("A plan to add JWT auth.")).toBeInTheDocument();
  });

  it("shows real evidence count and execution time", () => {
    render(<StageArtifactCard stage="planning" step={makeStep()} />);
    expect(screen.getByText("2 items")).toBeInTheDocument();
    expect(screen.getByText("4.2s")).toBeInTheDocument();
  });

  it("shows counted artifacts from the stage's real result fields", () => {
    render(<StageArtifactCard stage="planning" step={makeStep()} />);
    expect(screen.getByText("1 Implementation steps")).toBeInTheDocument();
    expect(screen.getByText("1 Affected components")).toBeInTheDocument();
  });

  it("names the real next consumer of this stage's output", () => {
    render(<StageArtifactCard stage="planning" step={makeStep()} />);
    expect(screen.getByText("Development")).toBeInTheDocument();
  });

  it("labels the last stage's output as workflow output, not a fake next agent", () => {
    render(<StageArtifactCard stage="review" step={makeStep({ agent_id: "review" })} />);
    expect(screen.getByText("Workflow output")).toBeInTheDocument();
  });
});
