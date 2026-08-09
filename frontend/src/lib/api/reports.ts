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
  readiness: Readiness;
  generated_at: string;
}

export interface ConfidenceStagePoint {
  stage: string;
  label: string;
  confidence: number | null;
  delta_from_previous: number | null;
  dropped: boolean;
}

export interface ConfidenceSectionVM {
  availability: SectionAvailability;
  current: number | null;
  points: ConfidenceStagePoint[];
  summary_sentence: string;
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

export interface ContradictionsSectionVM {
  availability: SectionAvailability;
  synthesis_state: SynthesisRunState;
  items: ContradictionEntry[];
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

export interface OpenQuestionEntry {
  text: string;
  source_stage: string;
  is_blocking: boolean;
}

export interface NextActionsSectionVM {
  availability: SectionAvailability;
  questions: OpenQuestionEntry[];
}

export interface ReportViewModel {
  header: HeaderVM;
  confidence: ConfidenceSectionVM;
  timeline: TimelineSectionVM;
  knowledge: KnowledgeSectionVM;
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
