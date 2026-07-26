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
}

export interface Confidence {
  score: number | null;
  reasoning: string;
}

// --- Run DTOs ---

export type RunStatus = "queued" | "running" | "completed" | "partial" | "failed";

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
  prompt: string;
  raw_response: string;
  latency_ms: number | null;
}

export interface PlanningResult {
  executive_summary: string;
  implementation_steps: PlanningStep[];
  affected_components: string[];
  kafka_topics_involved: string[];
  risk_considerations: string[];
  graph_context_used: boolean;
  repositories_consulted?: string[];
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
}

// --- Workflow Types ---

export type WorkflowStage = "planning" | "development" | "testing" | "review";
export type WorkflowStatus =
  "in_progress" | "completed" | "awaiting_approval" | "approved" | "rejected";

export interface WorkflowStageInfo {
  stage: string;
  label: string;
  status: "completed" | "running" | "failed" | "pending";
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
