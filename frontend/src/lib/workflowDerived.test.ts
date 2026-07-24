import { describe, expect, it } from "vitest";
import type { AgentStep, Evidence, WorkflowDetail, WorkflowStageInfo } from "../types/agent";
import {
  buildExecutionLog,
  buildWorkflowTimeline,
  computeElapsedMs,
  deriveArtifactCounts,
  deriveWorkflowState,
  estimateRemainingMs,
  evidenceToActivityLines,
  formatDuration,
  nextStageOf,
  progressFraction,
  resultRepositories,
  resultSummary,
  stageLabel,
  workflowStatusDisplay,
} from "./workflowDerived";

function makeStep(overrides: Partial<AgentStep> = {}): AgentStep {
  return {
    step_id: "step-1",
    agent_id: "planning",
    status: "completed",
    confidence: { score: 0.8, reasoning: "grounded" },
    evidence: [],
    result: {},
    prompt_version: "1.0",
    output_ref: null,
    error_message: null,
    latency_ms: 1000,
    created_at: "2026-01-01T10:00:00Z",
    completed_at: "2026-01-01T10:00:05Z",
    ...overrides,
  };
}

describe("nextStageOf", () => {
  const legacyStages = [
    { stage: "planning" },
    { stage: "development" },
    { stage: "testing" },
    { stage: "review" },
  ];
  const planningStages = [
    { stage: "planning" },
    { stage: "development" },
    { stage: "testing" },
    { stage: "engineering_review" },
  ];

  it("returns the next stage in a legacy sequence", () => {
    expect(nextStageOf("planning", legacyStages)).toBe("development");
    expect(nextStageOf("development", legacyStages)).toBe("testing");
    expect(nextStageOf("testing", legacyStages)).toBe("review");
  });

  it("returns the next stage in a planning sequence", () => {
    expect(nextStageOf("testing", planningStages)).toBe("engineering_review");
  });

  it("returns null after the last stage", () => {
    expect(nextStageOf("review", legacyStages)).toBeNull();
    expect(nextStageOf("engineering_review", planningStages)).toBeNull();
  });

  it("returns null for an unknown stage", () => {
    expect(nextStageOf("not-a-stage", legacyStages)).toBeNull();
  });

  it("returns null when no stages array provided", () => {
    expect(nextStageOf("planning")).toBeNull();
  });
});

describe("stageLabel", () => {
  it("strips the 'Agent' suffix from known stages", () => {
    expect(stageLabel("planning")).toBe("Planning");
    expect(stageLabel("development")).toBe("Development");
  });

  it("falls back to the raw stage id for unknown stages", () => {
    expect(stageLabel("mystery")).toBe("mystery");
  });
});

describe("formatDuration", () => {
  it("formats sub-minute durations as seconds only", () => {
    expect(formatDuration(45_000)).toBe("45s");
  });

  it("formats multi-minute durations as minutes and seconds", () => {
    expect(formatDuration(125_000)).toBe("2m 5s");
  });

  it("clamps negative durations to zero", () => {
    expect(formatDuration(-500)).toBe("0s");
  });

  it("formats multi-hour durations as hours and minutes (a stale or long-running workflow)", () => {
    expect(formatDuration(3 * 3600_000 + 12 * 60_000)).toBe("3h 12m");
  });
});

describe("computeElapsedMs", () => {
  it("uses updated_at once the workflow is done", () => {
    const ms = computeElapsedMs("2026-01-01T10:00:00Z", "2026-01-01T10:05:00Z", true, Date.now());
    expect(ms).toBe(5 * 60 * 1000);
  });

  it("uses the current time while the workflow is still running", () => {
    const now = new Date("2026-01-01T10:00:30Z").getTime();
    const ms = computeElapsedMs("2026-01-01T10:00:00Z", "2026-01-01T10:00:00Z", false, now);
    expect(ms).toBe(30_000);
  });
});

describe("estimateRemainingMs", () => {
  it("returns null with no completed stage durations", () => {
    expect(estimateRemainingMs([], 2)).toBeNull();
  });

  it("returns null when there are no remaining stages", () => {
    expect(estimateRemainingMs([makeStep({ latency_ms: 1000 })], 0)).toBeNull();
  });

  it("multiplies the average real duration by remaining stage count", () => {
    const steps = [makeStep({ latency_ms: 1000 }), makeStep({ latency_ms: 3000 })];
    expect(estimateRemainingMs(steps, 2)).toBe(4000); // avg 2000 * 2
  });
});

describe("progressFraction", () => {
  const stages: WorkflowStageInfo[] = [
    { stage: "planning", label: "Planning", status: "completed", run_id: "r1" },
    { stage: "development", label: "Development", status: "completed", run_id: "r2" },
    { stage: "testing", label: "Testing", status: "running", run_id: "r3" },
    { stage: "review", label: "Review", status: "pending", run_id: null },
  ];

  it("computes completed / total", () => {
    expect(progressFraction(stages)).toBe(0.5);
  });

  it("returns 0 for an empty stage list", () => {
    expect(progressFraction([])).toBe(0);
  });
});

describe("evidenceToActivityLines", () => {
  it("marks failed evidence and strips the FAILED: prefix", () => {
    const evidence: Evidence[] = [
      { kind: "tool_call", reference: "get_repos", summary: "Found 3 repositories." },
      { kind: "graph_traversal", reference: "traverse", summary: "FAILED: Neo4j unreachable." },
    ];
    const lines = evidenceToActivityLines(evidence);
    expect(lines[0]).toMatchObject({ text: "Found 3 repositories.", done: true, failed: false });
    expect(lines[1]).toMatchObject({ text: "Neo4j unreachable.", done: false, failed: true });
  });

  it("never invents text beyond the evidence summary", () => {
    const evidence: Evidence[] = [
      { kind: "llm_reasoning", reference: "llm", summary: "Synthesized a 3-step plan." },
    ];
    const [line] = evidenceToActivityLines(evidence);
    expect(line.text).toBe("Synthesized a 3-step plan.");
  });
});

describe("buildExecutionLog", () => {
  it("anchors start/end lines to the step's real timestamps", () => {
    const step = makeStep({
      evidence: [{ kind: "tool_call", reference: "x", summary: "Did a thing." }],
    });
    const lines = buildExecutionLog(step, "Planning Agent");

    expect(lines[0].text).toBe("Planning Agent started");
    expect(lines[lines.length - 1].text).toBe("Planning Agent stage completed");
    // Start/end times must reflect the real created_at/completed_at, not
    // an arbitrary interpolation artifact.
    expect(lines[0].time).not.toBe("—");
  });

  it("labels the completion line as failed for a failed step", () => {
    const step = makeStep({ status: "failed", confidence: { score: null, reasoning: "" } });
    const lines = buildExecutionLog(step, "Testing Agent");
    expect(lines[lines.length - 1].text).toBe("Testing Agent failed");
  });

  it("includes a confidence line only when a score was actually reported", () => {
    const withScore = buildExecutionLog(makeStep(), "Planning Agent");
    expect(withScore.some((l) => l.text.startsWith("Confidence calculated"))).toBe(true);

    const withoutScore = buildExecutionLog(
      makeStep({ confidence: { score: null, reasoning: "" } }),
      "Planning Agent",
    );
    expect(withoutScore.some((l) => l.text.startsWith("Confidence calculated"))).toBe(false);
  });
});

describe("deriveArtifactCounts", () => {
  it("counts only non-empty real list fields for the given stage", () => {
    const counts = deriveArtifactCounts("planning", {
      implementation_steps: [{ order: 1 }, { order: 2 }],
      affected_components: [],
      risk_considerations: ["risk one"],
    });
    expect(counts).toEqual([
      { label: "Implementation steps", count: 2 },
      { label: "Risks identified", count: 1 },
    ]);
  });

  it("returns an empty list for an unknown stage", () => {
    expect(deriveArtifactCounts("unknown", { anything: [1, 2, 3] })).toEqual([]);
  });
});

describe("resultSummary / resultRepositories", () => {
  it("reads executive_summary when present", () => {
    expect(resultSummary({ executive_summary: "A plan." })).toBe("A plan.");
  });

  it("returns an empty string when missing or the wrong type", () => {
    expect(resultSummary({})).toBe("");
    expect(resultSummary(undefined)).toBe("");
    expect(resultSummary({ executive_summary: 42 })).toBe("");
  });

  it("reads repositories_consulted as a real string array", () => {
    expect(resultRepositories({ repositories_consulted: ["order-service"] })).toEqual([
      "order-service",
    ]);
  });

  it("returns an empty array when missing", () => {
    expect(resultRepositories({})).toEqual([]);
    expect(resultRepositories(undefined)).toEqual([]);
  });
});

describe("buildWorkflowTimeline", () => {
  const planningStep = makeStep({
    agent_id: "planning",
    evidence: [{ kind: "tool_call", reference: "x", summary: "Found 3 repositories." }],
    created_at: "2026-01-01T10:00:00Z",
    completed_at: "2026-01-01T10:00:10Z",
  });
  const developmentStep = makeStep({
    agent_id: "development",
    evidence: [{ kind: "graph_traversal", reference: "y", summary: "Traced 2 dependencies." }],
    created_at: "2026-01-01T10:00:10Z",
    completed_at: "2026-01-01T10:00:30Z",
  });
  const stages: WorkflowStageInfo[] = [
    { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
    { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
    { stage: "testing", label: "Testing", status: "pending", run_id: null },
  ];
  const stepsByRunId = new Map([
    ["run-1", planningStep],
    ["run-2", developmentStep],
  ]);

  it("merges events from every stage with a step into one chronological timeline", () => {
    const timeline = buildWorkflowTimeline(stages, stepsByRunId);
    const timestamps = timeline.map((e) => e.atMs);
    expect(timestamps).toEqual([...timestamps].sort((a, b) => a - b));
    expect(timeline.some((e) => e.stage === "planning")).toBe(true);
    expect(timeline.some((e) => e.stage === "development")).toBe(true);
  });

  it("never invents text beyond each stage's real events", () => {
    const timeline = buildWorkflowTimeline(stages, stepsByRunId);
    expect(timeline.some((e) => e.text === "Found 3 repositories.")).toBe(true);
    expect(timeline.some((e) => e.text === "Traced 2 dependencies.")).toBe(true);
  });

  it("skips stages with no run yet, without throwing", () => {
    const timeline = buildWorkflowTimeline(stages, stepsByRunId);
    expect(timeline.every((e) => e.stage !== "testing")).toBe(true);
  });

  it("returns an empty timeline when no stage has a matching step", () => {
    expect(buildWorkflowTimeline(stages, new Map())).toEqual([]);
  });

  it("tags each event with its real agent label", () => {
    const timeline = buildWorkflowTimeline(stages, stepsByRunId);
    const planningEvent = timeline.find((e) => e.stage === "planning");
    expect(planningEvent?.agentLabel).toBe("Planning Agent");
  });
});

describe("deriveWorkflowState", () => {
  function makeWorkflow(overrides: Partial<WorkflowDetail> = {}): WorkflowDetail {
    return {
      workflow_id: "wf-1",
      title: "Add rate limiting",
      workflow_type: "planning",
      current_stage: "testing",
      status: "in_progress",
      stages: [
        { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
        { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
        { stage: "testing", label: "Testing", status: "pending", run_id: null },
        { stage: "review", label: "Review", status: "pending", run_id: null },
      ],
      runs: [],
      created_at: "2026-01-01T10:00:00Z",
      updated_at: "2026-01-01T10:00:00Z",
      ...overrides,
    };
  }

  it("is 'awaiting_approval' when the current stage hasn't been attempted yet", () => {
    const state = deriveWorkflowState(makeWorkflow());
    expect(state.phase).toBe("awaiting_approval");
    expect(state.currentStageInfo?.stage).toBe("testing");
    expect(state.lastCompletedStage?.stage).toBe("development");
  });

  it("is 'running' when the current stage's run is in flight", () => {
    const state = deriveWorkflowState(
      makeWorkflow({
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
          { stage: "testing", label: "Testing", status: "running", run_id: "run-3" },
          { stage: "review", label: "Review", status: "pending", run_id: null },
        ],
      }),
    );
    expect(state.phase).toBe("running");
  });

  it("is 'failed' when the current stage's run failed — never 'awaiting_approval'", () => {
    // Regression case: current_stage does NOT advance past a failed stage
    // (advance_workflow only moves forward on a completed run), so the
    // approval banner used to keep saying "approve to start Testing" even
    // though Testing had already failed.
    const state = deriveWorkflowState(
      makeWorkflow({
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
          { stage: "testing", label: "Testing", status: "failed", run_id: "run-3" },
          { stage: "review", label: "Review", status: "pending", run_id: null },
        ],
      }),
    );
    expect(state.phase).toBe("failed");
    expect(state.currentStageInfo?.status).toBe("failed");
  });

  it("is 'completed' once the workflow itself is marked completed, regardless of stage status", () => {
    const state = deriveWorkflowState(
      makeWorkflow({
        status: "completed",
        current_stage: "completed",
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
          { stage: "testing", label: "Testing", status: "completed", run_id: "run-3" },
          { stage: "review", label: "Review", status: "completed", run_id: "run-4" },
        ],
      }),
    );
    expect(state.phase).toBe("completed");
  });

  it("finds the failed stage's currentStageInfo even after a later successful retry re-ran it", () => {
    // A retry creates a *second* run for the same stage; _build_stages on the
    // backend keeps only the latest run per stage, so by the time the
    // frontend sees it, a successfully retried stage looks "completed" again
    // — this just documents that deriveWorkflowState trusts stages[] as-is.
    const state = deriveWorkflowState(
      makeWorkflow({
        current_stage: "review",
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
          { stage: "testing", label: "Testing", status: "completed", run_id: "run-3b" },
          { stage: "review", label: "Review", status: "pending", run_id: null },
        ],
      }),
    );
    expect(state.phase).toBe("awaiting_approval");
    expect(state.lastCompletedStage?.stage).toBe("testing");
  });

  it("is 'blueprint_approval' — never the per-stage 'awaiting_approval' — once a Planning workflow finishes its last stage", () => {
    // The real bug this distinction exists to prevent: without a separate
    // phase, this state would fall through to the same "awaiting_approval"
    // a mid-sequence stage uses, and the per-stage ApprovalGateBanner would
    // render "approve to start X" with no X left to start.
    const state = deriveWorkflowState(
      makeWorkflow({
        status: "awaiting_approval",
        current_stage: "engineering_review",
        stages: [
          { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
          { stage: "development", label: "Development", status: "completed", run_id: "run-2" },
          { stage: "testing", label: "Testing", status: "completed", run_id: "run-3" },
          {
            stage: "engineering_review",
            label: "Engineering Review",
            status: "completed",
            run_id: "run-4",
          },
        ],
      }),
    );
    expect(state.phase).toBe("blueprint_approval");
  });

  it("stays 'blueprint_approval' for both terminal decisions (approved/rejected)", () => {
    const approved = deriveWorkflowState(makeWorkflow({ status: "approved" }));
    const rejected = deriveWorkflowState(makeWorkflow({ status: "rejected" }));
    expect(approved.phase).toBe("blueprint_approval");
    expect(rejected.phase).toBe("blueprint_approval");
  });
});

describe("workflowStatusDisplay", () => {
  it("distinguishes the three real statuses behind 'blueprint_approval', unlike a plain phase lookup", () => {
    expect(workflowStatusDisplay({ status: "awaiting_approval" }, "blueprint_approval")).toEqual({
      label: "Awaiting Approval",
      tone: "warning",
    });
    expect(workflowStatusDisplay({ status: "approved" }, "blueprint_approval")).toEqual({
      label: "Approved",
      tone: "success",
    });
    expect(workflowStatusDisplay({ status: "rejected" }, "blueprint_approval")).toEqual({
      label: "Rejected",
      tone: "danger",
    });
  });

  it("falls back to the plain phase lookup for every other phase", () => {
    expect(workflowStatusDisplay({ status: "completed" }, "completed")).toEqual({
      label: "Completed",
      tone: "success",
    });
    expect(workflowStatusDisplay({ status: "in_progress" }, "failed")).toEqual({
      label: "Failed",
      tone: "danger",
    });
  });
});
