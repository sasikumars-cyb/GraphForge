import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { WorkflowStageInfo } from "../../types/agent";
import { PipelineGraph } from "./PipelineGraph";

const stages: WorkflowStageInfo[] = [
  { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
  { stage: "development", label: "Development", status: "running", run_id: "run-2" },
  { stage: "testing", label: "Testing", status: "pending", run_id: null },
  { stage: "review", label: "Review", status: "pending", run_id: null },
];

function renderGraph(props: Partial<Parameters<typeof PipelineGraph>[0]> = {}) {
  return render(
    <MemoryRouter>
      <PipelineGraph stages={stages} selectedRunId={null} onSelectStage={vi.fn()} {...props} />
    </MemoryRouter>,
  );
}

describe("PipelineGraph", () => {
  it("renders every stage with its status label", () => {
    renderGraph();
    expect(screen.getByRole("button", { name: "Planning: Complete" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Development: Running…" })).toBeInTheDocument();
    expect(screen.getAllByText("Queued")).toHaveLength(2);
  });

  it("disables nodes that have no run yet", () => {
    renderGraph();
    expect(screen.getByRole("button", { name: "Testing: Queued" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Review: Queued" })).toBeDisabled();
  });

  it("calls onSelectStage with the run_id when a completed stage is clicked", async () => {
    const user = userEvent.setup();
    const onSelectStage = vi.fn();
    renderGraph({ onSelectStage });
    await user.click(screen.getByRole("button", { name: "Planning: Complete" }));
    expect(onSelectStage).toHaveBeenCalledWith("run-1");
  });

  it("marks the currently-selected stage's node distinctly", () => {
    renderGraph({ selectedRunId: "run-1" });
    const node = screen.getByRole("button", { name: "Planning: Complete" });
    expect(node.className).toContain("ring-accent-line");
  });
});
