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
  model: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  steps: AgentStep[];
}

export interface RunListItem {
  run_id: string;
  goal: string;
  status: RunStatus;
  subject: Subject;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  confidence_score: number | null;
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

export interface PlanningResult {
  executive_summary: string;
  implementation_steps: PlanningStep[];
  affected_components: string[];
  kafka_topics_involved: string[];
  risk_considerations: string[];
  graph_context_used: boolean;
  repositories_consulted?: string[];
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
