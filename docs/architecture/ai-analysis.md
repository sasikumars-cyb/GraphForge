# AI-Enriched Analysis — Phase 8

## Overview

Phase 8 adds an AI layer on top of Phase 7's deterministic pull request impact analysis. Given a pull request whose graph-based impact has already been computed, the AI module enriches it with:

- **Executive summary** — natural-language explanation of the change's impact
- **Breaking changes** — identified contract/schema/API breakages with severity and confidence
- **Migration advice** — actionable guidance for downstream consumers
- **Suggested reviewers** — team members with relevant expertise
- **Regression tests** — recommended test scenarios to validate the change

The AI layer never replaces or re-implements deterministic analysis. It strictly consumes Phase 7's output and adds interpretive value.

---

## Architecture

```
Client (POST /pull-requests/{id}/ai-analysis)
│
▼
FastAPI Router (ai_analysis.py)
│  • JWT authentication
│  • Ownership validation
│  • Dependency resolution
│
▼
AIAnalysisService
│  • Ensures deterministic analysis exists
│  • Orchestrates the full pipeline
│
├──▶ ImpactAnalysisEngine (reused from Phase 7)
│       • Runs deterministic analysis if missing
│
▼
ContextBuilder
│  • Assembles bounded AIContext from:
│    - Repository metadata
│    - Pull request metadata
│    - Deterministic analysis results
│
▼
PromptBuilder
│  • Loads Markdown template from app/ai/prompts/
│  • Extracts version from YAML front-matter
│  • Renders {{ variable }} placeholders
│
▼
ILLMProvider (interface)
│
▼
OpenAIProvider (concrete implementation)
│  • Calls OpenAI Chat Completions API
│  • Requests JSON mode
│  • Validates response against AIAnalysisResult schema
│
▼
Persistence (PullRequestAIAnalysis table)
│  • Upserts result (replaces prior analysis)
│
▼
REST API Response
```

### Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| `ai_analysis.py` (router) | HTTP concerns only: auth, ownership, DI, response serialization |
| `AIAnalysisService` | Orchestration: coordinate deterministic check → context → provider → persist |
| `ContextBuilder` | Assemble bounded, typed context from multiple data sources |
| `PromptBuilder` | Template loading, version extraction, variable rendering |
| `ILLMProvider` | Abstract contract for any LLM backend |
| `OpenAIProvider` | Concrete: HTTP call to OpenAI, JSON parsing, Pydantic validation |
| `PullRequestAIAnalysis` | ORM model for persisted AI results |
| `AIAnalysisResult` | Pydantic schema defining the LLM's expected output structure |

---

## AI Request Flow

### `POST /pull-requests/{id}/ai-analysis`

Complete execution sequence:

```
1. Request received
2. JWT token extracted and validated (get_current_user)
3. Pull request looked up with ownership check:
   SELECT pull_requests.*
   FROM pull_requests
   JOIN repositories ON pull_requests.repository_id = repositories.id
   WHERE pull_requests.id = :id AND repositories.user_id = :user_id
4. AIAnalysisService.analyze(pull_request_id) called
5. Service checks for existing deterministic analysis:
   SELECT * FROM pull_request_analyses WHERE pull_request_id = :id
6. If missing → ImpactAnalysisEngine.analyze_pull_request(id)
   (runs the full Phase 7 pipeline: fetch changed files, map to graph, traverse, classify risk)
7. ContextBuilder assembles AIContext:
   - with_repository(name, owner, default_branch)
   - with_pull_request(title, number, head_ref, base_ref)
   - with_analysis_from_persisted(deterministic_analysis)
   - .build() → frozen AIContext dataclass
8. AIContext.to_prompt_variables() → dict[str, str]
9. PromptBuilder.load("impact_analysis.md")
10. PromptBuilder.extract_version() → "1.0"
11. PromptBuilder.render(variables) → rendered prompt string
12. OpenAIProvider calls POST https://api.openai.com/v1/chat/completions
    - model: gpt-4o (configurable)
    - temperature: 0.2 (configurable)
    - max_tokens: 4096 (configurable)
    - response_format: {"type": "json_object"}
13. Response JSON parsed and validated against AIAnalysisResult
14. Result persisted to pull_request_ai_analyses table (upsert)
15. AIAnalysisResult returned to router
16. Router serializes to AIAnalysisResultResponse and returns HTTP 200
```

---

## REST API

### `POST /pull-requests/{id}/ai-analysis`

**Purpose:** Run AI-enriched impact analysis for a pull request.

**Authentication:** Bearer token (JWT) required.

**Authorization:** User must own the repository containing the pull request.

**Request:** No body required. The pull request ID is in the URL path.

**Response (200 OK):**

```json
{
  "executive_summary": "This PR modifies the OrderEventProducer, changing the Kafka topic name. This is a breaking change that will affect all downstream consumers.",
  "breaking_changes": [
    {
      "component": "OrderEventProducer",
      "description": "Kafka topic name changed from 'order-created' to 'order.created.v2'",
      "severity": "high",
      "confidence": {
        "score": 0.92,
        "reasoning": "Topic constant is a literal string rename with no migration path"
      }
    }
  ],
  "migration_advice": [
    {
      "component": "OrderEventProducer",
      "advice": "Update all consumers to subscribe to the new topic name. Consider a transition period where both topics are active.",
      "priority": "high"
    }
  ],
  "suggested_reviewers": [
    {
      "reviewer": "alice",
      "reason": "Primary owner of the messaging infrastructure based on commit history",
      "confidence": {
        "score": 0.85,
        "reasoning": "70% of commits to this package in the last 6 months"
      }
    }
  ],
  "regression_tests": [
    {
      "component": "OrderEventProducer",
      "test_description": "Verify event delivery succeeds on the new topic name",
      "priority": "high",
      "confidence": {
        "score": 0.88,
        "reasoning": "Critical path - message delivery failure would cause data loss"
      }
    }
  ],
  "confidence": {
    "score": 0.88,
    "reasoning": "High confidence - clear breaking change with well-defined impact boundary"
  },
  "prompt_version": "1.0"
}
```

**Status Codes:**

| Code | Condition |
|------|-----------|
| 200 | Analysis completed successfully |
| 401 | Missing or invalid JWT token |
| 404 | Pull request not found or not owned by the authenticated user |
| 501 | Configured AI provider is not implemented |
| 503 | AI provider not configured (e.g., missing API key) |
| 500 | AI provider error (timeout, rate limit, malformed response) |

---

### `GET /pull-requests/{id}/ai-analysis`

**Purpose:** Retrieve the most recent AI analysis for a pull request.

**Authentication:** Bearer token (JWT) required.

**Authorization:** User must own the repository containing the pull request.

**Request:** No body. Pull request ID in the URL path.

**Response (200 OK):**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "pull_request_id": "f0e1d2c3-b4a5-6789-0abc-def123456789",
  "executive_summary": "This PR modifies the OrderEventProducer...",
  "breaking_changes": [...],
  "migration_advice": [...],
  "suggested_reviewers": [...],
  "regression_tests": [...],
  "confidence_score": 0.88,
  "confidence_reasoning": "High confidence - clear breaking change",
  "prompt_version": "1.0",
  "analyzed_at": "2026-07-22T12:34:56.789Z"
}
```

**Status Codes:**

| Code | Condition |
|------|-----------|
| 200 | Analysis found and returned |
| 401 | Missing or invalid JWT token |
| 404 | Pull request not found, not owned by user, or no AI analysis has been run |

---

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AI_PROVIDER` | No | `openai` | LLM provider backend (`openai`, `claude`, `gemini`, `ollama`) |
| `OPENAI_API_KEY` | Yes (if `AI_PROVIDER=openai`) | — | OpenAI API key for Chat Completions |
| `OPENAI_MODEL` | No | `gpt-4o` | Model to use for analysis |
| `OPENAI_TEMPERATURE` | No | `0.2` | Sampling temperature (lower = more deterministic) |
| `OPENAI_MAX_TOKENS` | No | `4096` | Maximum tokens in the completion response |

### Provider Selection

The `AI_PROVIDER` environment variable selects which LLM backend to use. The factory (`app/ai/providers/factory.py`) maps this to a concrete provider:

- `openai` — Fully implemented. Uses OpenAI Chat Completions API with JSON mode.
- `claude` / `anthropic` — Returns HTTP 501 (not yet implemented).
- `gemini` — Returns HTTP 501 (not yet implemented).
- `ollama` — Returns HTTP 501 (not yet implemented).

If `OPENAI_API_KEY` is not set when `AI_PROVIDER=openai`, the factory returns HTTP 503 with error code `ai_provider_not_configured`.

---

## Project Structure

```
app/ai/
├── __init__.py
├── interfaces/
│   ├── __init__.py
│   └── llm_provider.py          # ILLMProvider ABC
├── providers/
│   ├── __init__.py
│   ├── factory.py                # create_llm_provider() factory function
│   └── openai_provider.py        # OpenAIProvider implementation
├── services/
│   ├── __init__.py
│   ├── ai_analysis_service.py    # Orchestration service
│   ├── context_builder.py        # AIContext assembly (ContextBuilder)
│   └── prompt_builder.py         # Template loading + rendering
├── schemas/
│   ├── __init__.py
│   └── analysis_result.py        # AIAnalysisResult Pydantic schema
├── prompts/
│   ├── __init__.py
│   ├── impact_analysis.md        # Main analysis prompt template
│   ├── regression_tests.md       # Regression test prompt template
│   └── reviewer.md               # Reviewer suggestion prompt template
└── models/
    └── __init__.py
```

### Package Responsibilities

| Package | Responsibility |
|---------|---------------|
| `interfaces/` | Abstract contracts (ABCs) for the AI layer. Only `ILLMProvider` today. |
| `providers/` | Concrete LLM provider implementations + factory. Each provider handles HTTP communication, response parsing, and error translation. |
| `services/` | Business logic: `AIAnalysisService` (orchestration), `ContextBuilder` (data assembly), `PromptBuilder` (template rendering). |
| `schemas/` | Pydantic v2 models defining the structured LLM output contract (`AIAnalysisResult` and its nested types). |
| `prompts/` | Markdown templates with YAML front-matter. Versioned independently of code. |
| `models/` | Reserved for future AI-specific SQLAlchemy models (current AI persistence model lives in `app/models/pull_request_ai_analysis.py` alongside other ORM models). |

### Supporting files outside `app/ai/`

| File | Purpose |
|------|---------|
| `app/models/pull_request_ai_analysis.py` | SQLAlchemy ORM model for the `pull_request_ai_analyses` table |
| `app/schemas/ai_analysis.py` | API response schemas (`AIAnalysisResponse`, `AIAnalysisResultResponse`) |
| `app/api/v1/routers/ai_analysis.py` | FastAPI router (POST + GET endpoints) |
| `alembic/versions/a1b2c3d4e5f6_*.py` | Database migration creating the AI analyses table |

---

## Testing

### Unit Tests

Located in `tests/unit/ai/`:

| File | Coverage |
|------|----------|
| `test_context_builder.py` | ContextBuilder assembly, `to_prompt_variables()`, edge cases |
| `test_prompt_builder.py` | Template loading, YAML parsing, variable rendering |
| `test_provider_factory.py` | Factory routing, missing key handling, unsupported providers |
| `test_ai_analysis_service.py` | Orchestration with mocked DB + mocked provider |

**What is mocked:** Database session (`AsyncSession`), LLM provider (`ILLMProvider`), `ImpactAnalysisEngine`.

**Infrastructure required:** None. All unit tests run without Postgres, Neo4j, or network access.

### Integration Tests

| File | Coverage |
|------|----------|
| `tests/integration/test_openai_provider.py` | OpenAI HTTP round-trip (mock server via httpx transport), timeout, auth errors, rate limiting, malformed responses |
| `tests/integration/test_ai_analysis_api.py` | Full API round-trip: auth → ownership → service → persist → response |

**What is mocked in API tests:** `create_llm_provider` (returns a mock provider), `GitHubVersionControlProvider.list_changed_files` (avoids real GitHub calls).

**Infrastructure required for API tests:** PostgreSQL + Neo4j (same as `test_pull_requests_api.py`).

### Running Tests

```bash
cd backend

# Format check
python3 -m black --check .

# Lint
python3 -m ruff check .

# Type check
python3 -m mypy app/

# Unit tests only (no infrastructure needed)
python3 -m pytest tests/unit/ -x -q

# Integration tests - OpenAI provider (no infrastructure needed)
python3 -m pytest tests/integration/test_openai_provider.py -x -q

# Integration tests - API (requires Postgres + Neo4j)
python3 -m pytest tests/integration/test_ai_analysis_api.py -x -v

# All tests
python3 -m pytest -x -q
```

---

## Design Decisions

### 1. Provider Abstraction (`ILLMProvider`)

**Why:** Decouples business logic from any specific LLM vendor. `AIAnalysisService` depends only on the interface; swapping OpenAI for Claude requires zero changes to orchestration, context building, or persistence.

### 2. ContextBuilder (Bounded Context Assembly)

**Why:** Controls exactly what data reaches the LLM. Prevents accidental exposure of sensitive fields (user emails, API keys, internal IDs). Makes the prompt's data contract explicit and independently testable. Enables token budgeting in future without touching service logic.

### 3. PromptBuilder (Template-Based Prompts)

**Why:** Separates prompt engineering from application code. Templates are `.md` files that can be reviewed, diffed, and versioned in PRs by non-engineers. YAML front-matter carries a version string persisted with every analysis for reproducibility.

### 4. AIAnalysisService Orchestration

**Why:** Single point of coordination for the analysis pipeline. Ensures deterministic analysis always exists before AI enrichment runs. Keeps the router thin (no business logic) and keeps provider/persistence concerns behind the service boundary.

### 5. Structured JSON Responses

**Why:** Using OpenAI's JSON mode (`response_format: json_object`) combined with Pydantic validation ensures the LLM output is machine-parseable and schema-conformant. Malformed responses are caught immediately rather than corrupting downstream data.

### 6. Pydantic Validation at Every Boundary

**Why:** `AIAnalysisResult` validates LLM output; `AIAnalysisResponse`/`AIAnalysisResultResponse` validate API output. This double validation ensures internal schema evolution doesn't accidentally break the public API contract.

### 7. Separate Persistence Model

**Why:** `PullRequestAIAnalysis` is distinct from `PullRequestAnalysis` (Phase 7). AI results evolve at LLM speed (prompt changes, model upgrades); deterministic results are stable. Separate tables allow independent schema evolution, independent retention policies, and clear data lineage.

### 8. Provider Factory

**Why:** Centralizes provider instantiation logic (configuration reading, API key validation, constructor wiring) in one function. Routes and services never import `OpenAIProvider` directly — they call `create_llm_provider()` and get back an `ILLMProvider`. This is the single place to add a new provider without touching any consumer.

---

## Known Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| OpenAI is the only implemented provider | No fallback if OpenAI is unavailable | Factory raises 501 for others; adding a provider is one file + one factory case |
| No retry/backoff on provider failures | Transient errors surface immediately to users | Single-attempt is acceptable for MVP; retry is a future enhancement |
| No streaming responses | Full latency before any response | OpenAI calls are typically 5-15s; acceptable for async workflows |
| No GitHub comment publishing | Users must check the UI for results | Keeps the pipeline decoupled from GitHub API availability |
| No response caching | Every POST triggers a fresh LLM call | Acceptable for on-demand analysis; caching adds staleness risk |
| No token budgeting | Large contexts may exceed model limits | `max_tokens` caps output; input overflow will get a provider error |
| Single prompt per analysis | Cannot specialize prompts per repository type | Template system supports multiple files; routing is future work |
| No cost tracking | No visibility into API spend | Logging captures each call; metering is future work |
| Synchronous execution | Long-running LLM calls block the request | 60s timeout prevents indefinite hangs; background execution is future work |

---

## Future Enhancements

### Provider Ecosystem
- **Claude/Anthropic provider** — Implement `AnthropicProvider` with Messages API
- **Gemini provider** — Implement `GeminiProvider` with Google AI API
- **Ollama provider** — Implement `OllamaProvider` for local/self-hosted models
- **Provider fallback chain** — Try primary provider, fall back to secondary on failure

### Reliability & Performance
- **Retry with exponential backoff** — Configurable retry on 429/503/timeout
- **Token budgeting** — Truncate context intelligently to fit within model limits
- **Response caching** — Cache AI results keyed on (pull_request_id, deterministic_analysis_hash, prompt_version)
- **Async background execution** — Run analysis as a background job with status polling (similar to indexing)
- **Streaming responses** — Return partial results as they're generated

### Prompt Engineering
- **Prompt version management** — Route to different prompt versions based on configuration
- **Prompt A/B testing** — Compare outputs across prompt variants
- **Multi-turn refinement** — Follow up on initial analysis with targeted questions
- **Repository-type-specific prompts** — Different templates for microservices vs. monoliths

### Integration & Visibility
- **GitHub PR comments** — Publish analysis summary as a PR comment
- **Slack notifications** — Alert reviewers when AI analysis identifies breaking changes
- **AI analysis history** — Store all past analyses (not just the latest) for trend tracking
- **Cost tracking** — Log token usage and estimated cost per analysis
- **Diff-aware context** — Include actual code diffs (not just file paths) in the context

### Quality
- **Confidence calibration** — Compare AI confidence scores to actual outcomes over time
- **Human feedback loop** — Allow users to rate AI suggestions for future fine-tuning
- **Hallucination detection** — Cross-reference AI claims against the actual graph data
