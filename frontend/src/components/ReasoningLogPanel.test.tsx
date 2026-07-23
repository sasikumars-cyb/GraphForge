import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReasoningLogPanel } from "./ReasoningLogPanel";
import type { ReasoningStep } from "../types/analysis";

const TOOL_STEP: ReasoningStep = {
  step_number: 1,
  goal: "Determine whether this change touches the indexed architecture graph.",
  plan: "Always map changed files to graph nodes first.",
  tool_selected: "read_dependency_graph",
  observation: { tool_name: "read_dependency_graph", summary: "Matched 1 of 1 file(s)." },
  decision: "Proceeding to decide whether downstream traversal is warranted.",
};

const SKIPPED_STEP: ReasoningStep = {
  step_number: 2,
  goal: "Ground breaking-change analysis in the actual code change.",
  plan: "Risk is LOW - the node/dependency summary is already enough context.",
  tool_selected: null,
  observation: null,
  decision: "Skipped - low risk, the node summary is sufficient context.",
};

describe("ReasoningLogPanel", () => {
  it("renders one row per step with its goal, plan, and decision", () => {
    render(<ReasoningLogPanel steps={[TOOL_STEP]} />);
    expect(screen.getByText("Step 1")).toBeInTheDocument();
    expect(screen.getByText(TOOL_STEP.goal)).toBeInTheDocument();
    expect(screen.getByText(TOOL_STEP.plan)).toBeInTheDocument();
    expect(screen.getByText(TOOL_STEP.decision)).toBeInTheDocument();
  });

  it("shows the tool name as a badge when a tool was called", () => {
    render(<ReasoningLogPanel steps={[TOOL_STEP]} />);
    expect(screen.getByText("read_dependency_graph")).toBeInTheDocument();
  });

  it('shows a "Skipped" badge when no tool was selected', () => {
    render(<ReasoningLogPanel steps={[SKIPPED_STEP]} />);
    expect(screen.getByText("Skipped")).toBeInTheDocument();
  });

  it("renders the observation summary only when present", () => {
    const { rerender } = render(<ReasoningLogPanel steps={[TOOL_STEP]} />);
    expect(screen.getByText("Matched 1 of 1 file(s).")).toBeInTheDocument();

    rerender(<ReasoningLogPanel steps={[SKIPPED_STEP]} />);
    expect(screen.queryByText("Matched 1 of 1 file(s).")).not.toBeInTheDocument();
  });
});
