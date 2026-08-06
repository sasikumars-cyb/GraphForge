"""Response schemas for the in-app Reports/metrics dashboard.

Mirrors the sections produced by `scripts/generate_report.py` (the original
standalone HTML report), so the live `/reports` page can reach full parity
with that script's output without running it.
"""

from pydantic import BaseModel


class MetricsOverview(BaseModel):
    total_workflows: int
    completed_workflows: int
    completed_runs: int
    total_llm_calls: int
    total_tokens: int
    total_cost_usd: float
    avg_latency_ms: float
    indexed_repositories: int
    total_graph_nodes: int
    total_graph_edges: int


class CostByDayPoint(BaseModel):
    day: str
    cost_usd: float
    tokens: int


class ProviderCost(BaseModel):
    provider: str
    calls: int
    cost_usd: float
    tokens: int


class StageCost(BaseModel):
    stage: str
    calls: int
    cost_usd: float
    tokens: int


class ModelUsage(BaseModel):
    model: str
    provider: str
    calls: int
    cost_usd: float


class RunStageOutcome(BaseModel):
    stage: str
    total: int
    succeeded: int
    failed: int


class RepositoryComponentCount(BaseModel):
    repository_id: str
    name: str
    components: int


class WorkflowSummary(BaseModel):
    id: str
    title: str
    status: str
    current_stage: str
    workflow_type: str
    created_at: str
    updated_at: str
    cost_usd: float
    tokens: int


class MetricsReportResponse(BaseModel):
    scope: str  # "user" | "global"
    generated_at: str
    window_days: int
    overview: MetricsOverview
    cost_by_day: list[CostByDayPoint]
    cost_by_provider: list[ProviderCost]
    cost_by_stage: list[StageCost]
    model_usage: list[ModelUsage]
    run_success_by_stage: list[RunStageOutcome]
    repository_components: list[RepositoryComponentCount]
    recent_workflows: list[WorkflowSummary]


class WorkflowStageLLMUsage(BaseModel):
    """One workflow's LLM consumption for a single stage (Planning,
    Development, Testing, ...) - the per-stage breakdown Frontier Agent
    users need to see which stage actually drove cost/latency, not just
    the workflow's total."""

    stage: str
    models: list[str]
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    avg_latency_ms: float


class WorkflowLLMUsageResponse(BaseModel):
    workflow_id: str
    workflow_title: str
    stages: list[WorkflowStageLLMUsage]
