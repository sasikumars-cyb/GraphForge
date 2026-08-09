import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { WorkflowHeader } from "./WorkflowHeader";
import type { AgentStep, WorkflowDetail } from "../../types/agent";
import type { WorkflowPhase } from "../../lib/workflowDerived";

function makeWorkflow(overrides: Partial<WorkflowDetail> = {}): WorkflowDetail {
  return {
    workflow_id: "wf-1",
    title: "Add rate limiting",
    original_prompt: "Add rate limiting to the public API",
    workflow_type: "planning",
    current_stage: "engineering_review",
    status: "in_progress",
    stages: [
      { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
      {
        stage: "engineering_review",
        label: "Engineering Review",
        status: "completed",
        run_id: "run-2",
      },
    ],
    runs: [],
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-01T10:05:00Z",
    approved_by: null,
    version: 1,
    parent_workflow_id: null,
    refinement_note: null,
    ...overrides,
  };
}

function renderHeader(workflow: WorkflowDetail, phase: WorkflowPhase, steps: AgentStep[] = []) {
  return render(
    <MemoryRouter>
      <WorkflowHeader workflow={workflow} completedSteps={steps} phase={phase} />
    </MemoryRouter>,
  );
}

describe("WorkflowHeader — Est. remaining / duration freeze on every terminal state", () => {
  it("never shows 'calculating…' once the blueprint is approved", () => {
    renderHeader(makeWorkflow({ status: "approved" }), "blueprint_approval");
    expect(screen.queryByText(/calculating/)).not.toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("never shows 'calculating…' once the blueprint is rejected", () => {
    renderHeader(makeWorkflow({ status: "rejected" }), "blueprint_approval");
    expect(screen.queryByText(/calculating/)).not.toBeInTheDocument();
  });

  it("never shows 'calculating…' once a stage has failed", () => {
    renderHeader(
      makeWorkflow({
        status: "in_progress",
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          { stage: "development", label: "Development", status: "failed", run_id: "run-2" },
        ],
      }),
      "failed",
    );
    expect(screen.queryByText(/calculating/)).not.toBeInTheDocument();
  });

  it("still shows 'calculating…' while a stage is genuinely still awaiting/running", () => {
    renderHeader(
      makeWorkflow({
        status: "in_progress",
        stages: [{ stage: "planning", label: "Planning", status: "running", run_id: "run-1" }],
      }),
      "running",
    );
    expect(screen.getByText(/calculating/)).toBeInTheDocument();
  });
});

describe("WorkflowHeader — P0-2 regression: awaiting-approval must never read as active work", () => {
  it("shows 'Awaiting Approval', not 'In Progress', for the exact PROT-5754 reproduction", () => {
    // Context Discovery just completed (READY, 92%); current_stage has
    // already advanced to "planning" (the /continue dispatch target), but
    // no Planning run has been created yet — status stays "in_progress"
    // the whole time a Planning-type workflow sits at this gate. The bug:
    // this state used to render "In Progress" with a climbing duration,
    // identical to a stage an agent was genuinely executing.
    renderHeader(
      makeWorkflow({
        title: "Update Rate Associations for SOCO C2M",
        current_stage: "planning",
        status: "in_progress",
        stages: [
          {
            stage: "context_discovery",
            label: "Context Discovery",
            status: "completed",
            run_id: "run-1",
          },
          { stage: "planning", label: "Planning", status: "pending", run_id: null },
        ],
      }),
      "awaiting_approval",
    );
    expect(screen.getByText("Awaiting Approval")).toBeInTheDocument();
    expect(screen.queryByText("In Progress")).not.toBeInTheDocument();
  });
});
