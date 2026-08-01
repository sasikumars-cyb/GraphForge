"""Result shapes for the API Intelligence Agent (goal=analyze_api_intelligence).

`ApiIntelligenceResult` is the `AgentOutput.result` payload — read directly
by `ApiIntelligencePage.tsx` and by the export endpoints in
`app.api.v1.routers.api_intelligence` (OpenAPI/Postman/Markdown/HTML), which
re-render deterministically from a persisted run rather than recomputing
anything with the LLM.

Everything here is derived from Markdown documentation only — there is no
field anywhere for a source-code finding, a graph fact, or a "verified
against implementation" flag. That comparison is explicitly a *future*
capability (see the agent's own docstring on extension points), not
something Phase 1 fields should imply exists today.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
ParameterLocation = Literal["path", "query", "header", "body", "cookie"]
FindingSeverity = Literal["critical", "high", "medium", "low"]

# OWASP API Security Top 10 (2023) identifiers — a closed vocabulary so the
# HTML dashboard's "OWASP Coverage" section can render one row per category
# regardless of which ones this run actually found findings for, rather
# than only showing categories the LLM happened to mention.
SecurityCategory = Literal[
    "authentication",
    "authorization",
    "input_validation",
    "sensitive_data_exposure",
    "rate_limiting",
    "replay_protection",
    "https_usage",
    "token_handling",
    "secrets",
    "pii",
    "error_leakage",
    "owasp_api_top_10",
]


class ApiParameter(BaseModel):
    """One documented request parameter."""

    name: str = Field(min_length=1)
    location: ParameterLocation
    type: str = ""
    required: bool = False
    description: str = ""


class ApiEndpoint(BaseModel):
    """One documented API endpoint — the unit the "Endpoint Explorer" and
    OpenAPI/Postman exports are built from."""

    method: HttpMethod
    path: str = Field(min_length=1)
    base_url: str = ""
    description: str = ""
    parameters: list[ApiParameter] = Field(default_factory=list)
    request_example: str = ""
    response_example: str = ""
    status_codes: list[str] = Field(default_factory=list)
    authentication_required: bool = False
    owner: str = ""
    version: str = ""
    # Which Markdown file this endpoint was documented in — provenance,
    # same role as `DocumentationFinding.file_path`.
    source_file: str = ""


class SecurityFinding(BaseModel):
    """One security-review observation against the documentation."""

    category: SecurityCategory
    severity: FindingSeverity
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class ApiIntelligenceScores(BaseModel):
    """0-100 scores — each independently explainable, none a proxy for
    another (a well-documented API can still score low on security, and
    vice versa)."""

    documentation_completeness: int = Field(ge=0, le=100, default=0)
    security_score: int = Field(ge=0, le=100, default=0)
    api_quality_score: int = Field(ge=0, le=100, default=0)
    readability_score: int = Field(ge=0, le=100, default=0)
    consistency_score: int = Field(ge=0, le=100, default=0)
    overall_readiness_score: int = Field(ge=0, le=100, default=0)


class MarkdownFileSummary(BaseModel):
    """One Markdown file this run looked at."""

    path: str
    size_bytes: int
    heading_count: int = 0


class DocumentRelationship(BaseModel):
    """One deterministically-discovered edge between two Markdown files —
    computed from real internal links, never inferred by the LLM (see
    `discovery.discover_relationships`)."""

    from_file: str
    to_file: str
    relationship_type: Literal["links_to"] = "links_to"


class ApiIntelligenceResult(BaseModel):
    """The full result of one API Intelligence run."""

    repository_full_name: str
    executive_summary: str = ""

    # -- Extracted API surface ---------------------------------------------
    base_urls: list[str] = Field(default_factory=list)
    endpoints: list[ApiEndpoint] = Field(default_factory=list)
    authentication: str = ""
    authorization: str = ""
    rate_limits: str = ""
    pagination: str = ""
    versioning: str = ""
    dependencies: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    todos: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    # -- Security review ------------------------------------------------------
    security_findings: list[SecurityFinding] = Field(default_factory=list)

    # -- Scoring --------------------------------------------------------------
    scores: ApiIntelligenceScores = Field(default_factory=ApiIntelligenceScores)

    # -- What's missing, never hallucinated ------------------------------------
    missing_information: list[str] = Field(default_factory=list)

    # -- Deterministic, non-LLM provenance -------------------------------------
    files_reviewed: list[MarkdownFileSummary] = Field(default_factory=list)
    document_relationships: list[DocumentRelationship] = Field(default_factory=list)

    confidence_reasoning: str = ""
    prompt_version: str = "1.0"
