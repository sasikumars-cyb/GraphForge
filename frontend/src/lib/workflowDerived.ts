/**
 * Pure derivation helpers for the Workflow Command Center.
 *
 * Everything here reads data the API already returns (WorkflowDetail +
 * RunDetail/AgentStep) and reshapes it for a richer execution view — no
 * new endpoints, no fabricated facts. Where real data must be approximated
 * (e.g. spreading evidence timestamps between a step's real start/end),
 * that approximation is called out in the function's doc comment.
 */

import type { AgentStep, Evidence, WorkflowDetail, WorkflowStageInfo } from "../types/agent";

type EventKind = Evidence["kind"] | "lifecycle";

export const STAGE_ORDER = ["planning", "development", "testing", "review"] as const;

export const STAGE_AGENT_LABEL: Record<string, string> = {
  planning: "Planning Agent",
  development: "Development Agent",
  testing: "Testing Agent",
  review: "Review Agent",
};

/** The stage that consumes this stage's output, per the real chaining
 * `workflow_service.build_stage_context()` performs on the backend — this
 * is not a UI invention, it mirrors the actual STAGES sequence. */
export function nextStageOf(stage: string): string | null {
  const idx = STAGE_ORDER.indexOf(stage as (typeof STAGE_ORDER)[number]);
  if (idx === -1 || idx + 1 >= STAGE_ORDER.length) return null;
  return STAGE_ORDER[idx + 1];
}

export function stageLabel(stage: string): string {
  return STAGE_AGENT_LABEL[stage]?.replace(" Agent", "") ?? stage;
}

// ---------------------------------------------------------------------------
// Single source of truth for "what is currently true about this workflow" —
// every component that used to read workflow.status / current_stage /
// stages[] independently (and could disagree with each other, e.g. a failed
// stage still showing an "approve to continue" banner) now derives from this
// one function instead.
// ---------------------------------------------------------------------------

export type WorkflowPhase = "running" | "awaiting_approval" | "failed" | "completed";

export interface WorkflowUiState {
  phase: WorkflowPhase;
  /** The stage matching workflow.current_stage — null only if the backend
   * ever reports a current_stage outside the known STAGE_ORDER. */
  currentStageInfo: WorkflowStageInfo | null;
  lastCompletedStage: WorkflowStageInfo | null;
}

export function deriveWorkflowState(workflow: WorkflowDetail): WorkflowUiState {
  const currentStageInfo = workflow.stages.find((s) => s.stage === workflow.current_stage) ?? null;
  const lastCompletedStage =
    [...workflow.stages].reverse().find((s) => s.status === "completed") ?? null;

  let phase: WorkflowPhase;
  if (workflow.status === "completed") {
    phase = "completed";
  } else if (currentStageInfo?.status === "failed") {
    phase = "failed";
  } else if (currentStageInfo?.status === "running") {
    phase = "running";
  } else {
    // current stage exists but hasn't been attempted yet ("pending") —
    // awaiting an explicit approve/continue action, the only way any stage
    // ever starts in this workflow engine.
    phase = "awaiting_approval";
  }

  return { phase, currentStageInfo, lastCompletedStage };
}

// ---------------------------------------------------------------------------
// Duration / progress
// ---------------------------------------------------------------------------

export function formatDuration(ms: number): string {
  if (ms < 0) ms = 0;
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${seconds}s`;
}

/** Elapsed time since the workflow started, or its total run time once
 * completed — computed from real created_at/updated_at timestamps. */
export function computeElapsedMs(
  createdAt: string,
  updatedAt: string,
  isDone: boolean,
  now: number,
): number {
  const start = new Date(createdAt).getTime();
  const end = isDone ? new Date(updatedAt).getTime() : now;
  return Math.max(0, end - start);
}

/** Rough remaining-time estimate: average real duration of completed
 * stages, multiplied by the number of stages not yet started. Deliberately
 * simple and labeled as an estimate in the UI — never presented as exact. */
export function estimateRemainingMs(
  steps: AgentStep[],
  remainingStageCount: number,
): number | null {
  const durations = steps.filter((s) => s.latency_ms != null).map((s) => s.latency_ms as number);
  if (durations.length === 0 || remainingStageCount <= 0) return null;
  const avg = durations.reduce((a, b) => a + b, 0) / durations.length;
  return avg * remainingStageCount;
}

export function progressFraction(stages: WorkflowStageInfo[]): number {
  if (stages.length === 0) return 0;
  const completed = stages.filter((s) => s.status === "completed").length;
  return completed / stages.length;
}

// ---------------------------------------------------------------------------
// Activity feed — real evidence, paraphrased as short present-tense lines
// ---------------------------------------------------------------------------

export interface ActivityLine {
  key: string;
  text: string;
  done: boolean;
  failed: boolean;
}

/** Turns a stage's real Evidence entries into short activity-feed lines.
 * The wording is tightened for a feed ("Found 12 components" instead of
 * the full sentence) but never adds a claim the evidence doesn't contain —
 * each line is still that evidence entry's own summary, just trimmed. */
export function evidenceToActivityLines(evidence: Evidence[]): ActivityLine[] {
  return evidence.map((ev, i) => {
    const failed = ev.summary.startsWith("FAILED:");
    const text = failed ? ev.summary.replace(/^FAILED:\s*/, "") : ev.summary;
    return { key: `${ev.kind}-${i}`, text, done: !failed, failed };
  });
}

// ---------------------------------------------------------------------------
// Execution log lines — step-level timestamps are real; evidence-level
// timestamps are linearly interpolated between the step's real start and
// end (we don't have per-evidence timestamps from the API) and shown
// without false sub-second precision to avoid implying more accuracy than
// the source data supports.
// ---------------------------------------------------------------------------

export interface LogLine {
  key: string;
  time: string;
  text: string;
  kind: Evidence["kind"] | "lifecycle";
}

interface RawEvent {
  key: string;
  /** Real epoch ms — either a true step timestamp, or linearly interpolated
   * between a step's real start/end for per-evidence events (see module
   * doc comment). Never fabricated outside that one documented case. */
  atMs: number | null;
  text: string;
  kind: EventKind;
}

/** The shared, timestamped event sequence for a single step — every other
 * log/timeline view (ExecutionLogPanel, the Workflow Replay feature) is a
 * projection of this one list, so the "start/evidence/confidence/end"
 * ordering and interpolation logic lives in exactly one place. */
function buildStepEvents(step: AgentStep, agentLabel: string): RawEvent[] {
  const events: RawEvent[] = [];
  const start = step.created_at ? new Date(step.created_at) : null;
  const end = step.completed_at ? new Date(step.completed_at) : null;

  if (start) {
    events.push({
      key: "start",
      atMs: start.getTime(),
      text: `${agentLabel} started`,
      kind: "lifecycle",
    });
  }

  const span = start && end ? end.getTime() - start.getTime() : 0;
  const n = Math.max(step.evidence.length, 1);
  step.evidence.forEach((ev, i) => {
    const at = start && span > 0 ? start.getTime() + (span * (i + 1)) / (n + 1) : start?.getTime();
    events.push({ key: `ev-${i}`, atMs: at ?? null, text: ev.summary, kind: ev.kind });
  });

  if (step.confidence.score !== null) {
    events.push({
      key: "confidence",
      atMs: end ? end.getTime() : (start?.getTime() ?? null),
      text: `Confidence calculated: ${Math.round(step.confidence.score * 100)}%`,
      kind: "lifecycle",
    });
  }

  if (end) {
    const label =
      step.status === "failed" ? `${agentLabel} failed` : `${agentLabel} stage completed`;
    events.push({ key: "end", atMs: end.getTime(), text: label, kind: "lifecycle" });
  }

  return events;
}

export function buildExecutionLog(step: AgentStep, agentLabel: string): LogLine[] {
  return buildStepEvents(step, agentLabel).map((ev) => ({
    key: ev.key,
    time: ev.atMs !== null ? formatClock(new Date(ev.atMs)) : "—",
    text: ev.text,
    kind: ev.kind,
  }));
}

function formatClock(d: Date): string {
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

// ---------------------------------------------------------------------------
// Workflow Replay — a single chronological timeline stitched from every
// stage's real events, so the whole run can be scrubbed/played back like a
// flight recorder. No new data: this merges the exact same per-step events
// buildExecutionLog renders, just across all stages instead of one.
// ---------------------------------------------------------------------------

export interface TimelineEvent {
  key: string;
  atMs: number;
  stage: string;
  agentLabel: string;
  text: string;
  kind: EventKind;
}

export function buildWorkflowTimeline(
  stages: WorkflowStageInfo[],
  stepsByRunId: Map<string, AgentStep>,
): TimelineEvent[] {
  const events: TimelineEvent[] = [];
  for (const stage of stages) {
    if (!stage.run_id) continue;
    const step = stepsByRunId.get(stage.run_id);
    if (!step) continue;
    const agentLabel = STAGE_AGENT_LABEL[stage.stage] ?? stage.label;
    for (const ev of buildStepEvents(step, agentLabel)) {
      if (ev.atMs === null) continue;
      events.push({
        key: `${stage.stage}-${ev.key}`,
        atMs: ev.atMs,
        stage: stage.stage,
        agentLabel,
        text: ev.text,
        kind: ev.kind,
      });
    }
  }
  return events.sort((a, b) => a.atMs - b.atMs);
}

// ---------------------------------------------------------------------------
// Artifacts produced — generic, driven by each agent's real result shape
// ---------------------------------------------------------------------------

interface ArtifactField {
  key: string;
  label: string;
}

const ARTIFACT_FIELDS: Record<string, ArtifactField[]> = {
  planning: [
    { key: "implementation_steps", label: "Implementation steps" },
    { key: "affected_components", label: "Affected components" },
    { key: "risk_considerations", label: "Risks identified" },
  ],
  development: [
    { key: "implementation_phases", label: "Implementation phases" },
    { key: "components", label: "Affected components" },
    { key: "reusable_implementations", label: "Reusable implementations" },
    { key: "risks", label: "Risks identified" },
  ],
  testing: [
    { key: "regression_tests", label: "Regression tests" },
    { key: "integration_tests", label: "Integration tests" },
    { key: "edge_cases", label: "Edge cases" },
  ],
  review: [
    { key: "breaking_changes", label: "Breaking changes" },
    { key: "suggested_reviewers", label: "Suggested reviewers" },
    { key: "regression_tests", label: "Regression tests" },
    { key: "migration_advice", label: "Migration notes" },
  ],
};

export interface ArtifactCount {
  label: string;
  count: number;
}

/** Counts real list-valued fields already present in a step's result —
 * e.g. "4 implementation steps" — using each agent's actual known result
 * shape (see types/agent.ts). Empty lists are omitted, not padded. */
export function deriveArtifactCounts(
  stage: string,
  result: Record<string, unknown>,
): ArtifactCount[] {
  const fields = ARTIFACT_FIELDS[stage] ?? [];
  const counts: ArtifactCount[] = [];
  for (const field of fields) {
    const value = result[field.key];
    if (Array.isArray(value) && value.length > 0) {
      counts.push({ label: field.label, count: value.length });
    }
  }
  return counts;
}

export function resultSummary(result: Record<string, unknown> | undefined): string {
  if (!result) return "";
  return typeof result.executive_summary === "string" ? result.executive_summary : "";
}

export function resultRepositories(result: Record<string, unknown> | undefined): string[] {
  if (!result) return [];
  const value = result.repositories_consulted;
  return Array.isArray(value) ? (value as string[]) : [];
}
