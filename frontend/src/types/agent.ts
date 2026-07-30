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
  kind: "graph_traversal" | "tool_call" | "graph_fact" | "llm_reasoning";
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
  | "queued"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "awaiting_input";

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

export interface ContextDiscoveryResult {
  original_request: string;
  enriched_text: string;
  resolved_references: ResolvedReferenceResult[];
  indexed_repositories: Record<string, unknown>[];
  graph_components: Record<string, unknown>[];
  graph_topics: Record<string, unknown>[];
  ranked_repository_names: string[];
  graph_context_text: string;
  graph_available: boolean;
  graph_has_data: boolean;
  additional_context_recommendation: AdditionalContextRecommendationResult | null;
  planning_metadata: Record<string, unknown>;
  prompt_version: string;
  // --- WorkingContext fields (reasoning-driven discovery) ---
  goal: string;
  assumptions: string[];
  unresolved_questions: ClarificationQuestionResult[];
  user_answers: Record<string, string>;
  confidence: number;
  readiness: ContextReadiness;
  blocking_reasons: string[];
  remediation_steps: string[];
  clarification_rounds: number;
  // --- Structured refinements: capability-specific confidence, generic
  // BlockingIssue, and a human-facing Discovery Summary — all derived from
  // the same WorkingContext the fields above are, not a second source of
  // truth. See app.context_pipeline.working_context on the backend.
  capability_confidence: CapabilityConfidence;
  blocking_issues: BlockingIssueResult[];
  discovery_summary: DiscoverySummary;
}

export type ContextReadiness = "READY" | "PARTIAL" | "BLOCKED";

export interface ClarificationQuestionResult {
  question_id: string;
  question: string;
  why: string;
  options: string[];
  blocking: boolean;
}

export interface CapabilityConfidence {
  work_item: number;
  repository: number;
  architecture: number;
  implementation_candidates: number;
  documentation: number;
}

export interface BlockingIssueResult {
  issue_id: string;
  type: string;
  severity: "blocking" | "warning";
  message: string;
  reason: string;
  recommended_action: string[];
  clarification_question: ClarificationQuestionResult | null;
  resolved: boolean;
}

export interface DiscoverySummaryItem {
  label: string;
  status: "ok" | "warning" | "error";
  detail: string;
}

export interface DiscoverySummary {
  items: DiscoverySummaryItem[];
  readiness: ContextReadiness;
  headline: string;
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
  options: string[];
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
}
