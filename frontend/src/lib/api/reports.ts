/**
 * API functions for the Workflow Reports endpoints — the reports generated
 * when a Planning workflow's blueprint is approved.
 *
 * Report V2 Phase 2 (ADR 0024): `view_model` is now the authoritative,
 * deterministic representation the Reports page renders through real
 * components (see components/report/) — every type below mirrors
 * `app.agents.report_generation.view_model.ReportViewModel` field-for-field
 * (the backend's `to_json_dict` is a straight `dataclasses.asdict`, so the
 * two shapes are kept in sync by hand, not generated). `html_content`
 * remains only as a fallback for a report generated before this field
 * existed.
 */

import { apiFetch } from "./client";

export interface ReportSummary {
  id: string;
  workflow_id: string;
  workflow_title: string;
  title: string;
  /** The complete request the user actually submitted (the workflow's
   * `original_prompt`) — what the report is an answer to, and what the
   * Reports list leads with. `title`/`workflow_title` are short
   * AI-generated labels for the same thing. Empty when the report's
   * workflow no longer exists. */
  request: string;
  status: "pending" | "completed" | "failed";
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

// --- Report V2 view model types (ADR 0024 §5) ------------------------------

export type Availability = "available" | "degraded" | "unavailable";

export interface SectionAvailability {
  status: Availability;
  reason: string | null;
}

export type Readiness = "ready" | "needs_revision" | "not_ready" | "unknown";

/** Per-claim reasoning belief — never conflated with `SynthesisRunState`
 * (whether reasoning execution itself succeeded) or `VerificationStatus`
 * (whether a deterministic check confirmed it). See ADR 0024 §7. */
export type SynthesisStatus = "supported" | "inferred" | "contradicted" | "unknown";

/** Whether reasoning execution succeeded, at the whole Hypotheses/
 * Contradictions section's scope. ADR 0024 §11 is the source of truth for
 * what each value means and the exact copy each one renders as. */
export type SynthesisRunState = "not_run" | "failed" | "completed_empty" | "completed";

export type VerificationStatus = "verified" | "unverified" | "not_checked";

export interface HeaderVM {
  question: string;
  workflow_title: string;
  repository: string | null;
  /** The readiness the whole document renders — Engineering Review's own
   * verdict, downgraded by the backend when open blocking items contradict
   * it. Every section reads this one value. */
  readiness: Readiness;
  /** Engineering Review's raw verdict, kept for traceability — differs from
   * `readiness` only when the backend downgraded it. */
  reported_readiness: Readiness;
  generated_at: string;
}

export interface ConfidenceStagePoint {
  stage: string;
  label: string;
  confidence: number | null;
  delta_from_previous: number | null;
  dropped: boolean;
}

/** The two confidence numbers, never merged. `overall` is confidence that
 * the issue is understood well enough to implement; `top_hypothesis_
 * confidence` is confidence in one specific unproven hypothesis. They
 * routinely differ, and `divergence_note` (set by the backend only when the
 * gap is material) is what says so instead of leaving two bare percentages
 * that read as a bug. */
export interface ConfidenceBreakdownVM {
  overall: number | null;
  overall_label: string;
  overall_basis: string;
  top_hypothesis_confidence: number | null;
  top_hypothesis_statement: string | null;
  top_hypothesis_label: string;
  divergence_note: string | null;
}

export interface ConfidenceSectionVM {
  availability: SectionAvailability;
  current: number | null;
  points: ConfidenceStagePoint[];
  summary_sentence: string;
  breakdown: ConfidenceBreakdownVM;
}

/** Something the investigation actually established — never a hypothesis,
 * at any confidence. Only verified evidence and verified Knowledge Ledger
 * rows produce one of these. */
export interface ConfirmedFinding {
  statement: string;
  source_stage: string;
  source_field: string;
  evidence_summary: string | null;
}

export interface FindingsSectionVM {
  availability: SectionAvailability;
  items: ConfirmedFinding[];
  truncated_count: number;
}

export interface ReviewOutcomeVM {
  availability: SectionAvailability;
  readiness: Readiness;
  reported_readiness: Readiness;
  outcome_label: string;
  outcome_statement: string;
  reasons: string[];
  recommendation: string;
  blocking_count: number;
  advisory_count: number;
}

export interface TimelineStep {
  cycle: number;
  provider: string;
  action: string;
  outcome: string;
  summary: string;
  intent: string;
}

export interface TimelineSectionVM {
  availability: SectionAvailability;
  steps: TimelineStep[];
  truncated_count: number;
}

export interface KnowledgeSectionVM {
  availability: SectionAvailability;
  known: string[];
  known_truncated_count: number;
  unknown: string[];
  unknown_truncated_count: number;
}

/** ADR 0025 §7 — a hypothesis's structured, exact-match-only claim
 * subject. `null` for the vast majority of real hypotheses (only set
 * when the hypothesis's own claim is itself an existence/location/
 * attribution assertion — never for a causal/behavioral one). Not
 * rendered directly anywhere today; present for contract fidelity with
 * the backend and any future debugging/display need. */
export type SubjectEntityKind = "repository" | "file" | "component";

export interface SubjectEntity {
  kind: SubjectEntityKind;
  name: string;
}

export interface HypothesisEntry {
  statement: string;
  status: SynthesisStatus;
  confidence: number;
  supporting_evidence: string[];
  contradicting_evidence: string[];
  subject_entity?: SubjectEntity | null;
}

export interface HypothesisVM {
  entry: HypothesisEntry;
  /** `null` renders as "Not checked" — no correlated Knowledge Ledger row
   * exists for this claim. Never inferred from confidence or status. */
  verification_status: VerificationStatus | null;
}

export interface HypothesesSectionVM {
  availability: SectionAvailability;
  synthesis_state: SynthesisRunState;
  items: HypothesisVM[];
  truncated_count: number;
}

export interface ContradictionEntry {
  statement: string;
  evidence_for: string[];
  evidence_against: string[];
  resolved: boolean;
  resolution_note: string;
}

/** A contradiction plus what it does to the conclusion and what would
 * settle it — all derived deterministically by the backend. An unresolved
 * contradiction is always `is_blocking`, and appears in `next_actions` as a
 * blocking item, so the two sections can never disagree. */
export interface ContradictionVM {
  entry: ContradictionEntry;
  is_blocking: boolean;
  impact: string;
  required_resolution: string;
}

export interface ContradictionsSectionVM {
  availability: SectionAvailability;
  synthesis_state: SynthesisRunState;
  items: ContradictionVM[];
}

export interface EvidenceCategoryCount {
  kind: string;
  count: number;
}

export interface EvidenceSectionVM {
  availability: SectionAvailability;
  categories: EvidenceCategoryCount[];
  total: number;
}

export type OpenItemKind = "blocking_issue" | "unresolved_contradiction" | "knowledge_gap";

export interface OpenQuestionEntry {
  text: string;
  source_stage: string;
  is_blocking: boolean;
  kind: OpenItemKind;
}

export interface NextActionsSectionVM {
  availability: SectionAvailability;
  questions: OpenQuestionEntry[];
  /** Counted once, by the backend, from `questions` itself. Never re-filter
   * the list to produce your own counts — that is exactly how one section
   * came to say "one blocking item" while another said "0 blocking". */
  blocking_count: number;
  advisory_count: number;
}

export interface ReportViewModel {
  header: HeaderVM;
  review_outcome: ReviewOutcomeVM;
  confidence: ConfidenceSectionVM;
  timeline: TimelineSectionVM;
  knowledge: KnowledgeSectionVM;
  findings: FindingsSectionVM;
  hypotheses: HypothesesSectionVM;
  contradictions: ContradictionsSectionVM;
  evidence: EvidenceSectionVM;
  next_actions: NextActionsSectionVM;
  /** The one LLM-authored field in the whole model — narrates what's
   * already decided above, never adds a fact absent from it. `null` when
   * the summary call wasn't attempted or failed. */
  executive_summary: string | null;
}

export interface ReportDetail extends ReportSummary {
  html_content: string | null;
  view_model: ReportViewModel | null;
}

/**
 * Whether a stored `view_model` carries the post–Engineering Review
 * document's sections.
 *
 * `workflow_reports.view_model` is a JSON column written at generation
 * time and never migrated, so a report generated before the review-outcome
 * sections existed deserializes into this same `ReportViewModel` type
 * while actually missing `review_outcome` and `findings`. Rendering that
 * through `ReportView` would throw on the first property access. This is
 * the one place that check lives — callers fall back to `html_content`,
 * exactly as they already do for a report that predates `view_model`
 * entirely.
 */
export function isCurrentViewModel(model: ReportViewModel | null): model is ReportViewModel {
  return Boolean(model && model.review_outcome && model.findings && model.confidence?.breakdown);
}

export function listReports(token: string, signal?: AbortSignal): Promise<ReportSummary[]> {
  return apiFetch<ReportSummary[]>("/reports", { token, signal });
}

export function getReport(
  token: string,
  reportId: string,
  signal?: AbortSignal,
): Promise<ReportDetail> {
  return apiFetch<ReportDetail>(`/reports/${encodeURIComponent(reportId)}`, { token, signal });
}

/** Deletes the generated report only — the workflow behind it, its stage
 * runs, and their evidence trails all survive, and the same workflow can
 * be reported on again. (Deleting the investigation itself is
 * `deleteWorkflow`, a different and much larger action.) */
export function deleteReport(token: string, reportId: string): Promise<void> {
  return apiFetch<void>(`/reports/${encodeURIComponent(reportId)}`, {
    token,
    method: "DELETE",
  });
}
