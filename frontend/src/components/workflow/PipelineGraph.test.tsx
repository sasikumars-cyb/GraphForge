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

/** Every status at once — the pairs that share an icon in default mode
 *  (running/queued, partial/failed) are what compact mode has to separate. */
const allStatusStages: WorkflowStageInfo[] = [
  { stage: "context", label: "Context", status: "completed", run_id: "run-1" },
  { stage: "development", label: "Development", status: "running", run_id: "run-2" },
  { stage: "testing", label: "Testing", status: "queued", run_id: "run-3" },
  { stage: "docs", label: "Docs", status: "partial", run_id: "run-4" },
  { stage: "review", label: "Review", status: "failed", run_id: "run-5" },
  { stage: "deploy", label: "Deploy", status: "pending", run_id: null },
  { stage: "signoff", label: "Signoff", status: "awaiting_input", run_id: "run-6" },
];

/** The real production stage names that truncate hardest in compact mode. */
const longLabelStages: WorkflowStageInfo[] = [
  { stage: "context", label: "Context Discovery", status: "completed", run_id: "run-1" },
  { stage: "docs", label: "Documentation Planning", status: "pending", run_id: null },
  { stage: "review", label: "Engineering Review", status: "pending", run_id: null },
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

  it("names the pipeline generically by default", () => {
    renderGraph();
    expect(screen.getByRole("list", { name: "Workflow pipeline" })).toBeInTheDocument();
  });

  it("lets a caller identify which pipeline this is", () => {
    // Mission Control renders one per mission; without this every pipeline
    // and its stages were indistinguishable in the accessibility tree.
    renderGraph({ accessibleLabel: "Workflow pipeline for Fix the retry handler" });
    expect(
      screen.getByRole("list", { name: "Workflow pipeline for Fix the retry handler" }),
    ).toBeInTheDocument();
  });

  describe("compact mode", () => {
    // jsdom has no layout engine, so nothing here can verify that the row
    // actually fits its container — the live browser measurements are the
    // source of truth for that. What these cover is the behaviour compact
    // mode is responsible for: every stage still present, still operable,
    // and still fully described to assistive tech even though its visible
    // label is truncated to a few characters.

    it("renders every stage, in pipeline order", () => {
      renderGraph({ compact: true });
      const names = screen
        .getAllByRole("button")
        .map((b) => b.getAttribute("aria-label"));
      expect(names).toEqual([
        "Planning: Complete",
        "Development: Running…",
        "Testing: Queued",
        "Review: Queued",
      ]);
    });

    it("keeps the full stage label and status in the accessible name even though the visible label truncates", () => {
      renderGraph({ compact: true, stages: longLabelStages });
      // Visually this renders as "Docu…"; assistive tech must still get all of it.
      const node = screen.getByRole("button", { name: "Documentation Planning: Queued" });
      expect(node).toBeInTheDocument();
      expect(node.textContent).toBe("Documentation Planning");
    });

    it("exposes the full label and status as a tooltip so truncation is recoverable by pointer users", () => {
      renderGraph({ compact: true, stages: longLabelStages });
      expect(
        screen.getByRole("button", { name: "Documentation Planning: Queued" }),
      ).toHaveAttribute("title", "Documentation Planning: Queued");
    });

    it("does not set a tooltip in default mode, where nothing is truncated", () => {
      renderGraph();
      expect(screen.getByRole("button", { name: "Planning: Complete" })).not.toHaveAttribute(
        "title",
      );
    });

    it("still supports selecting a stage", async () => {
      const user = userEvent.setup();
      const onSelectStage = vi.fn();
      renderGraph({ compact: true, onSelectStage });
      await user.click(screen.getByRole("button", { name: "Planning: Complete" }));
      expect(onSelectStage).toHaveBeenCalledWith("run-1");
    });

    it("leaves stages with no run non-interactive", () => {
      renderGraph({ compact: true });
      expect(screen.getByRole("button", { name: "Testing: Queued" })).toBeDisabled();
    });

    // Compact mode drops the status sub-label, so the icon becomes the only
    // visual carrier of status. `running`/`queued` share an icon in default
    // mode, as do `partial`/`failed` — leaving them shared here would make
    // those pairs differ by colour alone (WCAG 2.1 SC 1.4.1).
    it("distinguishes every status by icon shape, not colour alone", () => {
      const { container } = renderGraph({ compact: true, stages: allStatusStages });
      const shapes = [...container.querySelectorAll("button")].map(
        (b) => b.querySelector("svg")?.getAttribute("class")?.match(/lucide-[a-z-]+/)?.[0] ?? null,
      );
      // every status renders a distinct silhouette
      expect(new Set(shapes).size).toBe(shapes.length);
      expect(shapes).not.toContain(null);
    });

    it("keeps running and queued visually separable", () => {
      const { container } = renderGraph({ compact: true, stages: allStatusStages });
      const iconOf = (name: string) =>
        screen.getByRole("button", { name }).querySelector("svg");
      const running = iconOf("Development: Running…");
      const queued = iconOf("Testing: Starting…");
      expect(running?.getAttribute("class")).not.toBe(queued?.getAttribute("class"));
      // motion is part of the distinction: only the running stage spins
      expect(running?.getAttribute("class")).toContain("animate-spin");
      expect(queued?.getAttribute("class")).not.toContain("animate-spin");
      expect(container).toBeTruthy();
    });

    it("keeps partial and failed visually separable", () => {
      renderGraph({ compact: true, stages: allStatusStages });
      const partial = screen
        .getByRole("button", { name: "Docs: Partial" })
        .querySelector("svg")
        ?.getAttribute("class");
      const failed = screen
        .getByRole("button", { name: "Review: Failed" })
        .querySelector("svg")
        ?.getAttribute("class");
      expect(partial).not.toBe(failed);
    });

    it("does not change the icons the workflow detail page has always shown", () => {
      // Default mode keeps its original (shared) icons — the compact-only
      // overrides must not leak into it.
      const { container } = renderGraph({ stages: allStatusStages });
      const cls = (name: string) =>
        screen.getByRole("button", { name }).querySelector("svg")?.getAttribute("class") ?? "";
      expect(cls("Development: Running…")).toContain("lucide-loader");
      expect(cls("Testing: Starting…")).toContain("lucide-loader");
      expect(cls("Docs: Partial")).toContain("lucide-circle-x");
      expect(cls("Review: Failed")).toContain("lucide-circle-x");
      expect(container).toBeTruthy();
    });
  });
});
