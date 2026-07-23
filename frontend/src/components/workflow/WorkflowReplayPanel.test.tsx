import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentStep, WorkflowStageInfo } from "../../types/agent";
import { WorkflowReplayPanel } from "./WorkflowReplayPanel";

function makeStep(overrides: Partial<AgentStep> = {}): AgentStep {
  return {
    step_id: "s1",
    agent_id: "planning",
    status: "completed",
    confidence: { score: 0.9, reasoning: "" },
    evidence: [{ kind: "tool_call", reference: "a", summary: "Found 3 repositories." }],
    result: {},
    prompt_version: "1.0",
    output_ref: null,
    error_message: null,
    latency_ms: 10_000,
    created_at: "2026-01-01T10:00:00Z",
    completed_at: "2026-01-01T10:00:10Z",
    ...overrides,
  };
}

const stages: WorkflowStageInfo[] = [
  { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
  { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
];

const planningStep = makeStep();
const developmentStep = makeStep({
  step_id: "s2",
  agent_id: "development",
  evidence: [{ kind: "graph_traversal", reference: "b", summary: "Traced 2 dependencies." }],
  created_at: "2026-01-01T10:00:10Z",
  completed_at: "2026-01-01T10:00:30Z",
});

const stepsByRunId = new Map([
  ["run-1", planningStep],
  ["run-2", developmentStep],
]);

describe("WorkflowReplayPanel", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows a locked message when no stage has a completed step yet", () => {
    render(<WorkflowReplayPanel stages={stages} stepsByRunId={new Map()} />);
    expect(screen.getByText(/nothing to play back yet/)).toBeInTheDocument();
  });

  it("reveals the first real event at the start position", () => {
    render(<WorkflowReplayPanel stages={stages} stepsByRunId={stepsByRunId} />);
    expect(screen.getByRole("slider", { name: "Replay position" })).toBeInTheDocument();
    expect(screen.getAllByText("Planning Agent started").length).toBeGreaterThan(0);
    // The development stage hasn't happened yet at the start position.
    expect(screen.queryByText("Traced 2 dependencies.")).not.toBeInTheDocument();
  });

  it("reveals later real events as playback advances", () => {
    render(<WorkflowReplayPanel stages={stages} stepsByRunId={stepsByRunId} />);

    fireEvent.click(screen.getByRole("button", { name: "Play replay" }));
    act(() => {
      vi.advanceTimersByTime(5000); // 5s wall-clock * 20x default speed = 100s of workflow time
    });

    expect(screen.getAllByText("Traced 2 dependencies.").length).toBeGreaterThan(0);
  });

  it("scrubbing to a specific position reveals only events up to that point", () => {
    render(<WorkflowReplayPanel stages={stages} stepsByRunId={stepsByRunId} />);
    const slider = screen.getByRole("slider", { name: "Replay position" });

    // Seek to a point still inside the planning stage.
    fireEvent.change(slider, { target: { value: new Date("2026-01-01T10:00:05Z").getTime() } });
    expect(screen.getAllByText("Found 3 repositories.").length).toBeGreaterThan(0);
    expect(screen.queryByText("Traced 2 dependencies.")).not.toBeInTheDocument();

    // Seek to the very end.
    fireEvent.change(slider, { target: { value: new Date("2026-01-01T10:00:30Z").getTime() } });
    expect(screen.getAllByText("Traced 2 dependencies.").length).toBeGreaterThan(0);
  });

  it("lets the user change playback speed", () => {
    render(<WorkflowReplayPanel stages={stages} stepsByRunId={stepsByRunId} />);

    const fastButton = screen.getByRole("button", { name: "60x" });
    fireEvent.click(fastButton);
    expect(fastButton).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "20x" })).toHaveAttribute("aria-pressed", "false");
  });

  it("restarts playback back to the beginning", () => {
    render(<WorkflowReplayPanel stages={stages} stepsByRunId={stepsByRunId} />);

    fireEvent.click(screen.getByRole("button", { name: "Play replay" }));
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getAllByText("Traced 2 dependencies.").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Restart from the beginning" }));
    expect(screen.queryByText("Traced 2 dependencies.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play replay" })).toBeInTheDocument();
  });
});
