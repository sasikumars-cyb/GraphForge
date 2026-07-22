# ADR 0009: AI-enriched pull request analysis

## Status
Accepted

## Context
The ask: given a pull request whose deterministic impact analysis (Phase 7) has already identified which components, APIs, and topics are impacted, enrich that signal with AI-generated insights — an executive summary, breaking-change detection, migration advice, reviewer suggestions, and regression test recommendations — using an LLM. The AI layer sits strictly *on top of* the deterministic engine; it never replaces or re-implements any of Phase 7's graph-based analysis.

The architecture must support multiple LLM providers (OpenAI today, Claude/Gemini/Ollama in future) without changing business logic, and must keep prompt templates separate from code so they can be versioned, reviewed, and iterated on independently of application releases.

## Decisions

**Provider abstraction via `ILLMProvider`.** A single-method interface (`analyze(context: AIContext) -> AIAnalysisResult`) is the only contract between the orchestration layer and any LLM backend. `OpenAIProvider` is the sole implementation today; adding Claude or Gemini means one new file in `app/ai/providers/` and one new case in the factory — zero changes to `AIAnalysisService`, `ContextBuilder`, or the router.

**Factory-based provider instantiation.** `create_llm_provider(settings)` reads `AI_PROVIDER` from configuration and returns the matching concrete implementation. Unsupported providers raise `UnsupportedProviderError` (HTTP 501) immediately — no silent fallback, no runtime guessing.

**ContextBuilder — bounded, typed context assembly.** Rather than passing raw ORM models or unstructured dicts to the LLM layer, `ContextBuilder` assembles a frozen `AIContext` dataclass with exactly the fields the prompt needs. This bounds what the LLM sees (no accidental PII leakage, no unbounded token consumption) and makes the context contract explicit and testable. `AIContext.to_prompt_variables()` produces the `dict[str, str]` that `PromptBuilder.render()` consumes.

**PromptBuilder — Markdown templates with YAML front-matter.** Prompt templates live as `.md` files in `app/ai/prompts/`, with a `version` field in YAML front-matter. `PromptBuilder` loads, extracts the version, renders `{{ variable }}` placeholders, and returns the rendered string + version. This keeps prompt engineering separate from Python code, enables diffing prompt changes in PRs, and attaches a version string to every persisted AI analysis for reproducibility.

**Structured JSON responses via Pydantic.** The OpenAI provider requests `response_format={"type": "json_object"}` and validates the response against `AIAnalysisResult` (Pydantic v2). If the LLM returns malformed JSON or fails validation, the provider raises `AIProviderResponseError` — never passes garbage downstream.

**AIAnalysisService orchestration — thin, sequential.** The service's `analyze()` method is a linear pipeline: ensure deterministic analysis exists → build context → call provider → persist → return. No concurrency, no branching, no retry. This keeps the orchestration trivially readable and testable; retry/backoff is a documented future enhancement, not a first-iteration concern.

**Separate persistence model.** `PullRequestAIAnalysis` is a distinct table from `PullRequestAnalysis` (Phase 7's deterministic results). One-to-one with the pull request (`pull_request_id` is unique), replaced on re-run — same "always replace, never accumulate history" philosophy as Phase 7. JSON columns store the structured sub-results (breaking changes, migration advice, etc.) because their schema is LLM-output-shaped and may evolve faster than a normalized relational model would allow.

**Idempotent upsert on re-analysis.** Re-running `POST /pull-requests/{id}/ai-analysis` replaces the existing row rather than creating a second. The `UNIQUE(pull_request_id)` constraint guarantees this at the database level; the service layer checks for an existing row and updates in place.

**Router mirrors Phase 7's pattern exactly.** Same prefix (`/pull-requests`), same `_get_owned_pull_request` ownership query (join PullRequest→Repository→check `user_id`), same auth dependency, same `NotFoundError` for missing resources. The AI router is a separate module for phase isolation but is otherwise indistinguishable in structure from the deterministic router.

**No GitHub comment publishing.** The AI analysis is persisted and returned via the REST API only. Publishing to GitHub PR comments is a documented future enhancement — keeping it out avoids coupling the analysis pipeline to GitHub's API availability and rate limits.

**Error hierarchy for provider failures.** `AIProviderError` (base), `AIProviderTimeoutError`, `AIProviderAuthError`, `AIProviderRateLimitError`, `AIProviderResponseError` — each carries enough context for logging and future HTTP-status mapping without leaking API keys or raw responses to clients. Currently surfaced as generic 500s by the centralized error handler; more specific codes (429, 502, 504) are a hardening-phase improvement.

## Scope boundaries (explicitly not built)
- Retry/backoff on provider failures — the orchestration is single-attempt.
- Token budgeting or context truncation — `ContextBuilder` assembles everything available; no overflow protection beyond the provider's own `max_tokens`.
- Streaming responses — the provider waits for the full completion before returning.
- Response caching — every `POST` triggers a fresh LLM call.
- Prompt A/B testing or version routing — only one prompt template per type, selected implicitly.
- Cost tracking or usage metering.
- Background/async execution — the full pipeline runs synchronously in the request cycle.

## Verification strategy
- `tests/unit/ai/` — ContextBuilder, PromptBuilder, provider factory, and AIAnalysisService (with mocked DB and mocked provider) — 108 unit tests total across all phases.
- `tests/integration/test_openai_provider.py` — real HTTP round-trip against a mock OpenAI-compatible server (httpx transport swap), covering success, timeout, auth failure, rate limiting, and malformed response.
- `tests/integration/test_ai_analysis_api.py` — full API round-trip (auth → ownership → service → persist → response) with the LLM provider mocked, covering 404-before-run, happy-path POST+GET, cross-user ownership denial, and missing auth.
- All providers, services, and schemas pass `mypy --strict`-equivalent checks, `black`, and `ruff`.

## Consequences
- AI analysis quality is bounded by what the deterministic engine provides — if Phase 7 misses an impact (e.g., due to file-path-only granularity), the AI layer cannot independently discover it.
- A single LLM call per analysis means the prompt must be comprehensive enough to elicit all insight categories in one shot — no multi-turn refinement.
- The "always replace" persistence model means there is no history of how AI analysis evolved across re-runs — same trade-off Phase 7 accepted.
- Without retry/backoff, transient OpenAI outages surface immediately as user-visible errors rather than being absorbed — acceptable for an MVP but not for production.
