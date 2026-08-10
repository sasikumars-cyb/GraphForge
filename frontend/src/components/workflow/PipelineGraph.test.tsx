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

  describe("compact mode", () => {
    // Regression coverage for the Active Missions overflow bug: the default
    // layout's stage nodes are `flex-1` with no `min-w-0`, so flexbox
    // refuses to shrink them below their label's intrinsic text width —
    // with 6 real stages that overflowed a half-width Mission Control card
    // by 250-450px at every breakpoint tested (only reachable via the
    // page's own invisible `overflow-x: auto`). `min-w-0` on every node in
    // the flex chain is what actually fixes it: it's what lets the row's
    // total width stay pinned to its container regardless of label length.
    // These assertions fail if a future edit repeats the omission.
    it("marks every stage node shrinkable so the row can never exceed its container", () => {
      renderGraph({ compact: true });
      for (const stage of stages) {
        const node = screen.getByRole("button", { name: new RegExp(`^${stage.label}:`) });
        expect(node.className).toContain("min-w-0");
        expect(node.parentElement?.className).toContain("min-w-0");
      }
    });

    it("still exposes every stage with its full accessible name", () => {
      renderGraph({ compact: true });
      expect(screen.getByRole("button", { name: "Planning: Complete" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Development: Running…" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Testing: Queued" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Review: Queued" })).toBeInTheDocument();
    });

    it("still supports selecting a stage", async () => {
      const user = userEvent.setup();
      const onSelectStage = vi.fn();
      renderGraph({ compact: true, onSelectStage });
      await user.click(screen.getByRole("button", { name: "Planning: Complete" }));
      expect(onSelectStage).toHaveBeenCalledWith("run-1");
    });

    it("does not affect the default (non-compact) layout", () => {
      renderGraph();
      const node = screen.getByRole("button", { name: "Planning: Complete" });
      expect(node.className).not.toContain("min-w-0");
    });
  });
});
