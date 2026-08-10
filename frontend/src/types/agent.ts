/**
 * Types for the Agent Framework API — mirrors the backend's agent_runs
 * router DTOs and _contract.py types.
 */

// --- Shared DTOs ---

export interface Subject {
  subject_id: string;
  subject_type: string;
  display_name: string;
}

export interface Evidence {
  /** "human_input" records something a person told the agent — deliberately
   * its own kind rather than being filed under tool_call/graph_fact, since the
   * evidence trail's whole job is saying where a claim came from. It never
   * counts as grounding. */
  kind: "graph_traversal" | "tool_call" | "graph_fact" | "llm_reasoning" | "human_input";
  reference: string;
  summary: string;
  /** What actually happened, distinct from `kind` — lets a UI distinguish
   * "found" from "connected but nothing relevant" from "not configured"
   * from "the call failed" without parsing `summary`'s free text. Absent
   * for evidence that doesn't need the distinction (e.g. graph traversals). */
  status?: "success" | "not_found" | "unavailable" | "failed" | null;
}

export interface Confidence {
  score: number | null;
  reasoning: string;
}

// --- Run DTOs ---

export type RunStatus =
  "queued" | "running" | "completed" | "partial" | "failed" | "awaiting_input";

export interface AgentStep {
  step_id: string;
  agent_id: string;
  status: string;
  confidence: Confidence;
  evidence: Evidence[];
  result: Record<string, unknown>;
  prompt_version: string;
  output_ref: string | null;
  error_message: string | null;
  latency_ms: number | null;
  created_at: string | null;
  completed_at: string | null;
  /** A human's correction, merged over `result` for downstream stages. `result`
   * itself stays the AI's unedited output, so the UI must read this to show the
   * corrected value — otherwise a saved correction appears to have been lost. */
  human_override?: Record<string, unknown> | null;
  overridden_at?: string | null;
}

export interface RunDetail {
  run_id: string;
  goal: string;
  status: RunStatus;
  subject: Subject;
  title: string | null;
  provider: string | null;
  user: string | null;
  repository: string | null;
  model: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  steps: AgentStep[];
  workflow_id: string | null;
  workflow_stage: string | null;
  previous_run_id: string | null;
}

export interface RunListItem {
  run_id: string;
  goal: string;
  status: RunStatus;
  subject: Subject;
  title: string | null;
  provider: string | null;
  user: string | null;
  repository: string | null;
  model: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  confidence_score: number | null;
  workflow_id: string | null;
  workflow_stage: string | null;
}

export interface RunListResponse {
  items: RunListItem[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
}

export interface CreateRunRequest {
  subject_reference: string;
  goal: string;
  model?: string;
  /** Optional: ground a standalone Development/Testing run in a prior
   * standalone Planning run's result. Omit for today's default behavior. */
  planning_run_id?: string;
}

export interface CreateRunResponse {
  run_id: string;
  status: string;
  subject: Subject;
  goal: string;
}

// --- Agent Manifest ---

export interface AgentManifest {
  agent_id: string;
  purpose: string;
  goals: string[];
  cost_class: string;
}

// --- Planning Result (agent-specific payload inside step.result) ---

export interface PlanningStep {
  order: number;
  description: string;
  affected_component?: string;
  risk_note?: string;
}

// Architect-level blueprint fields present in v2 agent output
export interface ArchitectureLayer {
  name: string;
  description?: string;
  layer_type?: string;
  order?: number;
}

export interface DataFlowStep {
  name: string;
  technology?: string;
  step_type?: string;
  order?: number;
}

export interface PlanningPhase {
  name: string;
  order?: number;
  deliverables?: string[];
}

export interface StructuredRisk {
  description: string;
  likelihood?: string;
  impact?: string;
  mitigation?: string;
  category?: string;
}

export interface LLMTrace {
  model: string;
  // Which provider actually served the request — may differ from the
  // configured default when a rate-limit fallback fired.
  provider: string;
  prompt: string;
  raw_response: string;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  // Rough estimate from a static per-model price table, not a real billing
  // figure — null when the model isn't in that table or usage wasn't
  // reported by the provider.
  estimated_cost_usd: number | null;
}

export interface PlanningResult {
  executive_summary: string;
  implementation_steps: PlanningStep[];
  affected_components: string[];
  kafka_topics_involved: string[];
  risk_considerations: string[];
  graph_context_used: boolean;
  repositories_consulted?: string[];
  // Deterministic, backend-computed list of claims in this result that
  // could not be matched against the run's own tool evidence (see
  // app.agents.verification). Produced by every planning-family agent and,
  // until now, displayed by none of them — a plan could show a green
  // "verified" badge beside a file path that exists nowhere in the graph.
  // Always render these; an unsurfaced caveat is the same as no caveat.
  verification_warnings?: string[];
  blueprint?: import("./blueprint").BlueprintArtifact | null;
  // v2 architect-level fields — optional, empty when agent version is older
  architecture_layers?: ArchitectureLayer[];
  data_flow?: DataFlowStep[];
  implementation_phases?: PlanningPhase[];
  risks?: StructuredRisk[];
  capabilities?: string[];
  project_type?: string;
  project_type_label?: string;
  llm_trace?: LLMTrace | null;
}

// --- Development Plan Result (agent-specific payload inside step.result) ---

export interface AffectedRepository {
  name: string;
  owner: string;
  reason: string;
}

export interface AffectedComponent {
  name: string;
  component_type: string;
  repository: string;
  file_path: string;
  change_description: string;
}

export interface PlanDependency {
  source: string;
  target: string;
  relationship: string;
  risk_note: string;
}

export interface ReusableImplementation {
  name: string;
  repository: string;
  reason: string;
}

export interface ImplementationPhase {
  order: number;
  title: string;
  description: string;
  affected_components: string[];
  estimated_complexity: string;
  depends_on_phases: number[];
}

export interface PlanRisk {
  description: string;
  severity: string;
  affected_component: string;
  mitigation: string;
}

export interface DevelopmentPlanResult {
  goal: string;
  executive_summary: string;
  repositories: AffectedRepository[];
  components: AffectedComponent[];
  dependencies: PlanDependency[];
  reusable_implementations: ReusableImplementation[];
  implementation_phases: ImplementationPhase[];
  risks: PlanRisk[];
  recommendations: string[];
  graph_context_used: boolean;
  repositories_consulted?: string[];
  // Deterministic, backend-computed list of claims in this result that
  // could not be matched against the run's own tool evidence (see
  // app.agents.verification). Produced by every planning-family agent and,
  // until now, displayed by none of them — a plan could show a green
  // "verified" badge beside a file path that exists nowhere in the graph.
  // Always render these; an unsurfaced caveat is the same as no caveat.
  verification_warnings?: string[];
  blueprint?: import("./blueprint").BlueprintArtifact | null;
}

// --- Test Plan Result (agent-specific payload inside step.result) ---

export interface TestScopeResult {
  in_scope: string[];
  out_of_scope: string[];
}

export interface RegressionTestResult {
  component: string;
  description: string;
  priority: string;
  automated: boolean;
}

export interface IntegrationTestResult {
  source_component: string;
  target_component: string;
  relationship: string;
  description: string;
  priority: string;
}

export interface EdgeCaseResult {
  description: string;
  component: string;
  severity: string;
  category: string;
}

export interface EnvironmentRequirementResult {
  name: string;
  description: string;
  services_required: string[];
}

export interface ExecutionPhaseResult {
  order: number;
  title: string;
  description: string;
  test_types: string[];
  depends_on_phases: number[];
}

export interface AutomationCandidateResult {
  description: string;
  component: string;
  test_type: string;
  reason: string;
}

export interface ManualValidationResult {
  description: string;
  component: string;
  reason: string;
}

export interface TestRiskResult {
  description: string;
  severity: string;
  affected_component: string;
  mitigation: string;
}

export interface TestPlanResult {
  goal: string;
  executive_summary: string;
  test_scope: TestScopeResult;
  affected_repositories: string[];
  affected_components: string[];
  regression_tests: RegressionTestResult[];
  integration_tests: IntegrationTestResult[];
  edge_cases: EdgeCaseResult[];
  environment_requirements: EnvironmentRequirementResult[];
  execution_order: ExecutionPhaseResult[];
  automation_candidates: AutomationCandidateResult[];
  manual_validations: ManualValidationResult[];
  risks: TestRiskResult[];
  recommendations: string[];
  graph_context_used: boolean;
  repositories_consulted?: string[];
  // Deterministic, backend-computed list of claims in this result that
  // could not be matched against the run's own tool evidence (see
  // app.agents.verification). Produced by every planning-family agent and,
  // until now, displayed by none of them — a plan could show a green
  // "verified" badge beside a file path that exists nowhere in the graph.
  // Always render these; an unsurfaced caveat is the same as no caveat.
  verification_warnings?: string[];
  blueprint?: import("./blueprint").BlueprintArtifact | null;
}

// --- Documentation Plan Result (agent-specific payload inside step.result) ---

export interface RequiredDocumentationUpdateResult {
  document: string;
  category: string;
  current_status: string;
  action: string;
  reason: string;
  priority: string;
  owner: string;
  estimated_effort: string;
  dependencies: string[];
}

export interface NewDocumentationItemResult {
  name: string;
  category: string;
  purpose: string;
  suggested_location: string;
  owner: string;
  priority: string;
  estimated_effort: string;
}

export interface ExistingDocumentationUpdateResult {
  file_path: string;
  sections_affected: string[];
  summary_of_changes: string;
}

export interface DocumentationRiskResult {
  description: string;
  severity: string;
}

export interface DocumentationChecklistItemResult {
  label: string;
  applicable: boolean;
}

export interface DocumentationPlanResult {
  goal: string;
  executive_summary: string;
  documentation_impact: string;
  impact_explanation: string;
  required_updates: RequiredDocumentationUpdateResult[];
  new_documentation: NewDocumentationItemResult[];
  existing_updates: ExistingDocumentationUpdateResult[];
  risks: DocumentationRiskResult[];
  recommendations: string[];
  release_notes_draft: string[];
  checklist: DocumentationChecklistItemResult[];
  // Deterministic verification_warnings carried forward from Planning/
  // Development/Testing (see app.agents.verification) — this agent runs
  // no tools of its own, same reasoning as TestPlanResult.verification_warnings.
  prior_verification_warnings?: string[];
}

// --- Context Discovery Result (agent-specific payload inside step.result) ---

export interface ResolvedReferenceResult {
  type: string;
  provider: string;
  confidence: number;
  raw_value: string;
  normalized_value: string;
}

export interface AdditionalContextRecommendationResult {
  should_search: boolean;
  capability: string | null;
  reasoning: string;
}

/** One repository Context Discovery has identified — either explicitly
 * named in the request (or a human's claim confirmed), or suggested via a
 * real cross-repository graph relationship. `reason` is always populated
 * for a suggested repository; `relationship` names the kind of graph edge
 * behind it (e.g. "CALLS_SERVICE", "SHARES_TOPIC") when known.
 *
 * ADR 0010 §2 — this is the canonical shape. `ContextDiscoveryResult.
 * repositories` is the only field anything ever writes directly (including
 * a human override — see `RepositorySelector.tsx`); every other
 * repository-shaped field below it is a read-only projection derived from
 * `repositories` on the backend (`reasoning.projection.
 * project_repositories`) and must never be read by new frontend code —
 * filter `repositories` itself instead (invariant I6). */
export interface RepositoryCandidate {
  name: string;
  source: "explicit" | "suggested";
  selected: boolean;
  reason: string;
  relationship?: string;
  /** Position in the relevance ranking, when one exists. */
  rank?: number | null;
  /** ADR 0010 §4 — populated only where a real graph_version signal
   * reached this candidate (a cross-repository relationship's target
   * version); honestly absent otherwise, never fabricated. */
  graph_version?: string | null;
  /** ADR 0010 (Theme E) — "structural" (a literal Feign target or Kafka
   * topic name) or "heuristic" (a dependency-name match) for a suggested-
   * via-relationship candidate; empty for every other source. */
  confidence?: string;
}

export interface ContextDiscoveryResult {
  // --- What Planning reads (derived views over the fact ledger) ---
  original_request: string;
  enriched_text: string;
  resolved_references: ResolvedReferenceResult[];
  indexed_repositories: Record<string, unknown>[];
  graph_components: Record<string, unknown>[];
  graph_topics: Record<string, unknown>[];
  /** The canonical repository model (ADR 0010 §2) — read/write this field
   * directly; every field below is a derived, read-only projection of it. */
  repositories: RepositoryCandidate[];
  /** @deprecated read-only projection of `repositories`, sorted by rank —
   * kept for Planning's pre-existing star-rating contract, not for new
   * frontend code. */
  ranked_repository_names: string[];
  /** @deprecated read-only projection of `repositories` (every name,
   * unfiltered) — kept for backward compatibility, not for new code. */
  implementation_candidates: string[];
  /** @deprecated read-only projection of `repositories` filtered to
   * `source === "explicit"` — filter `repositories` directly instead. */
  explicit_repositories: RepositoryCandidate[];
  /** @deprecated read-only projection of `repositories` filtered to
   * `source === "suggested"` — filter `repositories` directly instead. */
  suggested_repositories: RepositoryCandidate[];
  /** @deprecated read-only projection of `repositories` filtered to
   * `selected === true` — filter `repositories` directly instead. Never
   * reflects a human override (which always targets `repositories`
   * itself) unless this projection is recomputed, which the backend only
   * does once, at write time. */
  selected_repositories: RepositoryCandidate[];
  graph_context_text: string;
  graph_available: boolean;
  graph_has_data: boolean;
  planning_metadata: Record<string, unknown>;
  prompt_version: string;

  // --- Readiness verdict ---
  goal: string;
  readiness: ContextReadiness;
  /** Necessity-weighted mean of the per-capability scores, excluding
   * capabilities that don't apply to this request. Entirely evidence-derived —
   * no LLM self-report contributes to it. */
  confidence: number;
  capability_confidence: Record<string, number>;
  clarification_rounds: number;
  blocking_reasons: string[];
  remediation_steps: string[];
  assumptions: string[];
  user_answers: Record<string, string>;
  /** At most one entry: the single question discovery is waiting on. */
  unresolved_questions: ClarificationQuestionResult[];

  // --- The human-facing report + resumable engine state ---
  discovery_report: DiscoveryReport;
  working_memory: Record<string, unknown>;
}

export type ContextReadiness = "READY" | "PARTIAL" | "BLOCKED";

export interface ClarificationQuestionResult {
  question_id: string;
  question: string;
  why: string;
  /** Real candidate values only — never UI instructions. */
  options: string[];
  /** What discovery already tried before resorting to asking. */
  investigated: string[];
  blocking: boolean;
}

/** One line of the investigation as the user reads it. The engine states
 * intent before acting and reports what it observed after, so the reasoning
 * process is visible rather than only its final state. */
export interface TranscriptEntry {
  kind: "intent" | "observation" | "question" | "answer" | "conclusion";
  text: string;
  iteration: number;
  evidence_ids: string[];
}

/** One decomposable reason a capability's confidence is what it is. */
export interface ConfidenceSignal {
  label: string;
  satisfied: boolean;
  /** Populated when unsatisfied: what specifically is missing. */
  detail: string;
  /** Provenance — which investigations back this signal. */
  evidence_ids: string[];
}

export interface CapabilityBreakdown {
  capability: string;
  label: string;
  necessity: "required" | "recommended" | "not_applicable";
  score: number;
  satisfied: boolean;
  explanation: string;
  signals: ConfidenceSignal[];
}

export interface FindingItem {
  fact_id: string;
  subject: string;
  provider: string;
  /** False for a human answer that hasn't been corroborated yet. */
  verified: boolean;
  evidence: { evidence_id: string; summary: string; outcome: string } | null;
}

export interface FindingGroup {
  kind: string;
  items: FindingItem[];
  /** True count of facts of this kind. `items` is capped for readability, so
   * this is what the UI must show when reporting how much was found. */
  total: number;
}

export interface DiscoveryGap {
  gap_id: string;
  capability: string;
  summary: string;
  why: string;
  severity: "blocking" | "advisory";
  status: "open" | "claimed" | "verified" | "refuted" | "unresolvable";
  missing: string[];
  recommended_action: string[];
  resolution_note: string;
  user_claim: string | null;
}

export interface InvestigationStep {
  evidence_id: string;
  provider: string;
  action: string;
  outcome: "success" | "not_found" | "unavailable" | "failed";
  summary: string;
  intent: string;
  iteration: number;
}

export interface Interpretation {
  statement: string;
  kind: string;
  supporting_fact_ids: string[];
  withdrawn: boolean;
}

/** The human-facing report generated from the engine's working memory (see
 * reasoning/projection.build_discovery_report on the backend). */
export interface DiscoveryReport {
  readiness: ContextReadiness;
  confidence: number;
  headline: string;
  transcript: TranscriptEntry[];
  confidence_breakdown: CapabilityBreakdown[];
  findings: FindingGroup[];
  interpretations: Interpretation[];
  gaps: DiscoveryGap[];
  investigation: InvestigationStep[];
}

// --- Engineering Understanding DTO (RFC-003) ---

export interface RepositorySummaryDTO {
  primary: string;
  supporting: string[];
  ownership: string[];
}

export interface AreaClusterDTO {
  name: string;
  components: string[];
}

export interface UnknownItemDTO {
  category: "known" | "unknown" | "unavailable";
  description: string;
}

export interface PlanningFactorDTO {
  satisfied: boolean;
  description: string;
}

export interface PlanningAssessmentDTO {
  status: ContextReadiness;
  reasons: PlanningFactorDTO[];
}

export interface DebugBundleDTO {
  investigation_trail: Record<string, unknown>[];
  confidence_breakdown: Record<string, unknown>[];
  findings: Record<string, unknown>[];
  gaps: Record<string, unknown>[];
  transcript: Record<string, unknown>[];
  graph_components: Record<string, unknown>[];
  graph_topics: Record<string, unknown>[];
  repository_ranking: string[];
  capability_confidence: Record<string, number>;
  planning_metadata: Record<string, unknown>;
  working_memory: Record<string, unknown>;
  assumptions: string[];
  evidence_package_raw: Record<string, unknown>;
}

export interface EngineeringUnderstandingDTO {
  business_goal: string;
  current_situation: string;
  expected_outcome: string;
  repository_summary: RepositorySummaryDTO;
  architecture_summary: string;
  relevant_areas: AreaClusterDTO[];
  known_constraints: string[];
  missing_information: string[];
  unknowns: UnknownItemDTO[];
  evidence_summary: string[];
  recommendations: string[];
  planning_assessment: PlanningAssessmentDTO;
  confidence_explanation: string;
  documentation_status: string;
  next_step: string;
  debug_bundle: DebugBundleDTO | null;
}

// --- Workflow Types ---

export type WorkflowStage =
  | "context_discovery"
  | "planning"
  | "development"
  | "testing"
  | "documentation_planning"
  | "engineering_review"
  | "review";
export type WorkflowStatus =
  | "in_progress"
  | "completed"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "awaiting_clarification";

export interface PendingClarification {
  question_id: string;
  question: string;
  why: string;
  /** Real candidate values only (repository names the graph actually
   * contains). Remediation verbs are never options. */
  options: string[];
  /** What discovery already tried before resorting to asking, so the question
   * reads as a last resort rather than a first move. */
  investigated: string[];
}

export interface WorkflowStageInfo {
  stage: string;
  label: string;
  // Mirrors Run.status directly (see backend WorkflowStageResponse) — that
  // includes "queued" (Run row created, not yet picked up for execution)
  // and "partial" (agent finished with a degraded/incomplete result), not
  // just the 4 statuses this used to declare. Under-typing this let
  // "queued" silently fall through PipelineGraph's status→config lookup to
  // the same static, no-spinner treatment as an untouched future stage —
  // indistinguishable from "nothing has happened yet" even while the run
  // was genuinely in flight.
  status: "completed" | "running" | "queued" | "partial" | "failed" | "pending" | "awaiting_input";
  run_id: string | null;
}

export interface WorkflowRunItem {
  run_id: string;
  goal: string;
  status: string;
  workflow_stage: string | null;
  confidence_score: number | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface WorkflowDetail {
  workflow_id: string;
  title: string;
  // The complete, unmodified text the user submitted to create this
  // workflow — `title` is an AI-generated summary of this, not a
  // replacement for it.
  original_prompt: string;
  // "planning" is the only type creatable today; typed as string (not a
  // narrow union) since new types are added server-side over time and the
  // UI already has fallback handling for anything it doesn't recognize.
  workflow_type: string;
  current_stage: string;
  status: WorkflowStatus;
  stages: WorkflowStageInfo[];
  runs: WorkflowRunItem[];
  created_at: string;
  updated_at: string;
  // Resolved display name of whoever approved this blueprint — null if
  // never approved, or approved before this field existed.
  approved_by: string | null;
  // Version lineage — 1/null for anything not created via "Refine".
  version: number;
  parent_workflow_id: string | null;
  refinement_note: string | null;
  // The single question Context Discovery is waiting on, when
  // status === "awaiting_clarification". Null/undefined otherwise —
  // optional so existing test fixtures that predate this field don't all
  // need updating.
  pending_clarification?: PendingClarification | null;
}

export interface WorkflowListItem {
  workflow_id: string;
  title: string;
  workflow_type: string;
  current_stage: string;
  status: WorkflowStatus;
  stages: WorkflowStageInfo[];
  created_at: string;
  updated_at: string;
  approved_by: string | null;
  version: number;
  parent_workflow_id: string | null;
}

export interface WorkflowListResponse {
  items: WorkflowListItem[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
}

export interface CreateWorkflowRequest {
  title: string;
  model?: string;
  workflow_type?: string;
  // "Refine" — the workflow this one refines, plus the human's own note on
  // what to change. See NewWorkflowPage's parentId/refinementNote handling.
  parent_workflow_id?: string;
  refinement_note?: string;
}

export interface ContinueWorkflowResponse {
  workflow_id: string;
  run_id: string;
  stage: string;
  status: string;
}

export interface WorkflowApprovalResponse {
  workflow_id: string;
  status: string;
}

export interface OverrideStageResultRequest {
  // A partial correction, merged on top of the stage's own AgentStep.result
  // at read time (see get_stage_result on the backend) — only the fields a
  // human actually changed, never the whole result.
  override: Record<string, unknown>;
  // When true, triggers a fresh Context Discovery execution using the
  // selected repositories as explicit input — recomputing all investigation
  // results instead of just patching the repository list.
  rerun?: boolean;
}
