"""Unit tests for app.agents.api_intelligence.renderers — deterministic,
non-LLM projections of an ApiIntelligenceResult into every export format."""

from __future__ import annotations

import json

import pytest
import yaml

from app.agents.api_intelligence.renderers import (
    curl_for_endpoint,
    render_html_dashboard,
    render_json,
    render_markdown_summary,
    render_openapi_yaml,
    render_postman_collection,
)
from app.agents.api_intelligence.schemas import (
    ApiEndpoint,
    ApiIntelligenceResult,
    ApiIntelligenceScores,
    ApiParameter,
    SecurityFinding,
)


@pytest.fixture
def sample_result() -> ApiIntelligenceResult:
    return ApiIntelligenceResult(
        repository_full_name="acme/widgets",
        executive_summary="A widgets API.",
        base_urls=["https://api.acme.com"],
        endpoints=[
            ApiEndpoint(
                method="POST",
                path="/v1/widgets",
                base_url="https://api.acme.com",
                description="Create a widget",
                parameters=[
                    ApiParameter(name="name", location="body", type="string", required=True, description="Name")
                ],
                request_example='{"name": "Gizmo"}',
                response_example='{"id": "1"}',
                status_codes=["201", "400"],
                authentication_required=True,
                owner="platform-team",
                version="v1",
                source_file="api.md",
            )
        ],
        security_findings=[
            SecurityFinding(
                category="rate_limiting",
                severity="high",
                title="No rate limiting",
                description="Not documented.",
                why_it_matters="Abuse risk.",
                recommendation="Add rate limits.",
                confidence=0.7,
            )
        ],
        scores=ApiIntelligenceScores(
            documentation_completeness=60,
            security_score=40,
            api_quality_score=70,
            readability_score=80,
            consistency_score=65,
            overall_readiness_score=58,
        ),
        missing_information=["No error schema documented."],
    )


def test_curl_includes_auth_header_and_body(sample_result: ApiIntelligenceResult) -> None:
    curl = curl_for_endpoint(sample_result.endpoints[0])
    assert "curl -X POST" in curl
    assert "https://api.acme.com/v1/widgets" in curl
    assert "Authorization: Bearer <token>" in curl
    assert '-d \'{"name": "Gizmo"}\'' in curl


def test_curl_omits_auth_header_when_not_required() -> None:
    endpoint = ApiEndpoint(method="GET", path="/v1/health", authentication_required=False)
    curl = curl_for_endpoint(endpoint)
    assert "Authorization" not in curl


def test_openapi_yaml_is_valid_and_grounded(sample_result: ApiIntelligenceResult) -> None:
    rendered = render_openapi_yaml(sample_result)
    parsed = yaml.safe_load(rendered)

    assert parsed["openapi"] == "3.0.3"
    assert parsed["servers"] == [{"url": "https://api.acme.com"}]
    assert "/v1/widgets" in parsed["paths"]
    assert "post" in parsed["paths"]["/v1/widgets"]
    assert parsed["components"]["securitySchemes"]["bearerAuth"]["type"] == "http"


def test_openapi_yaml_defaults_a_server_when_no_base_url_documented() -> None:
    result = ApiIntelligenceResult(repository_full_name="acme/widgets")
    parsed = yaml.safe_load(render_openapi_yaml(result))
    assert parsed["servers"] == [{"url": "https://api.example.com"}]
    assert parsed["paths"] == {}


def test_postman_collection_has_valid_schema_url_and_one_item_per_endpoint(
    sample_result: ApiIntelligenceResult,
) -> None:
    collection = render_postman_collection(sample_result)
    assert collection["info"]["schema"] == "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    assert len(collection["item"]) == 1
    item = collection["item"][0]
    assert item["request"]["method"] == "POST"
    assert item["request"]["body"]["raw"] == '{"name": "Gizmo"}'
    # Serializable — a malformed collection would fail this.
    json.dumps(collection)


def test_markdown_summary_includes_scores_and_findings(sample_result: ApiIntelligenceResult) -> None:
    md = render_markdown_summary(sample_result)
    assert "**Overall Readiness:** 58/100" in md
    assert "POST /v1/widgets" in md
    assert "No rate limiting" in md
    assert "No error schema documented." in md


def test_markdown_summary_handles_an_empty_result_without_hallucinating() -> None:
    result = ApiIntelligenceResult(repository_full_name="acme/empty")
    md = render_markdown_summary(result)
    assert "No endpoints were extracted" in md


def test_html_dashboard_is_self_contained_and_escapes_content() -> None:
    result = ApiIntelligenceResult(
        repository_full_name="acme/widgets",
        executive_summary="<script>alert(1)</script>",
    )
    rendered = render_html_dashboard(result)
    assert "<!doctype html>" in rendered.lower()
    assert "cdn." not in rendered.lower()
    assert '<script src=' not in rendered
    # The malicious payload must be escaped, not executed.
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_html_dashboard_renders_all_required_sections(sample_result: ApiIntelligenceResult) -> None:
    rendered = render_html_dashboard(sample_result)
    for section_id in (
        "summary", "landscape", "security-score", "risk-heatmap", "endpoints",
        "auth-flow", "status-matrix", "owasp", "missing", "relationships",
        "security-findings", "recommendations", "readiness",
    ):
        assert f'id="{section_id}"' in rendered


def test_render_json_round_trips(sample_result: ApiIntelligenceResult) -> None:
    body = render_json(sample_result)
    parsed = json.loads(body)
    assert parsed["repository_full_name"] == "acme/widgets"
    assert parsed["scores"]["overall_readiness_score"] == 58
