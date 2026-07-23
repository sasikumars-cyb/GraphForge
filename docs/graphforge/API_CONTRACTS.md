# API_CONTRACTS.md — GraphForge

All endpoints are versioned under `/api/v1` (existing convention — FastAPI app already mounts
routers this way). This document adds new resource groups; it does not change existing contracts
for `/auth`, `/github`, `/repositories`, `/pull-requests/*` (see current OpenAPI schema for those
— unchanged by GraphForge).

> **Implementation status**: this document was written as a design spec before the Agent
> Orchestrator was built. **`## Agent Orchestrator API` is shipped** (`app/api/v1/routers/
> agent_runs.py`, `app/api/v1/routers/workflows.py`) — see that section for the corrected,
> as-built contract. **`## Knowledge Graph API`, `## Subject / Context API`, and
> `## Project API`, below, are NOT implemented** — no `/knowledge-graph`, `/subjects`, or
> `/projects` router exists in the codebase. They're kept here as forward-looking design
> notes, not a reference for existing endpoints — do not build against them as-is.

## Naming Standards

- Resource paths: plural, kebab-case (`/pull-requests`, `/knowledge-graph`, `/agent-runs`) —
  matches existing `pull-requests` precedent.
- Path params: `{id}` for the resource's own primary key (existing convention).
- Query params: `snake_case` (matches existing `per_page`/`sort` precedent from GitHub-facing code).
- Request/response models: `PascalCase` Pydantic classes, suffixed `Request`/`Response`
  (existing: `RunAIAnalysisRequest`, `AIAnalysisResultResponse`).
- Enums are closed vocabularies, never free text (existing precedent: `RiskLevel`,
  `urgency: Literal["blocking", "advisory"]`).

## Authentication

Unchanged: `Authorization: Bearer <access_token>` (existing JWT scheme via `get_current_user`
dependency). Every new router below uses the same `Depends(get_current_user)` +
ownership-check-via-join pattern already established in `ai_analysis.py`'s
`_get_owned_pull_request`.

## Pagination

New standard for list endpoints introduced by GraphForge (existing endpoints are unpaginated
because their result sets are small and per-repository; graph-scale endpoints are not):

```json
{
  "items": [ ... ],
  "page": 1,
  "page_size": 25,
  "total": 143,
  "has_more": true
}
```

Query params: `?page=1&page_size=25` (default `page_size=25`, max `100`). Cursor-based pagination
is deferred (see Roadmap) — offset pagination is sufficient while graph size stays within a
single organization's dataset.

## Filtering

Query-param filtering, closed vocabularies only. As-built, `GET /api/v1/agent-runs` accepts
`goal`, `status`, `subject_type`, and `subject_id` (there is no separate `agent_id` filter — a
Run doesn't carry one directly; filter by `goal`, which determines the agent, instead):

```
GET /api/v1/agent-runs?goal=review_pr&status=completed&subject_type=pull_request
GET /api/v1/agent-runs?subject_id=pr:a1b2c3d4
```

## Versioning

Path-based (`/v1`), matching the existing convention. A breaking change to a response shape ships
as `/v2` on the same resource; additive fields never require a version bump (existing precedent:
`release_coordination_plan` was added to `AIAnalysisResultResponse` without a version bump because
it was additive).

## Error Model

Unchanged — existing global `AppError` → JSON handler:

```json
{
  "error": {
    "code": "not_found",
    "message": "No AI analysis has been run for this pull request yet."
  }
}
```

| Status | error_code | Meaning |
|---|---|---|
| 400 | `validation_error` | Malformed request body (new for GraphForge — no 400 exists in ChangeGuard today; introduced for orchestrator run requests with invalid `goal`) |
| 401 | `unauthorized` | Missing/invalid auth, or integration not connected (existing) |
| 404 | `not_found` | Resource / analysis / subject not found (existing) |
| 422 | `*_not_indexed` / `unresolvable_subject` | Precondition not met (existing pattern: `repository_not_indexed`) |
| 502 | `*_api_error` | Upstream integration failure, not swallowed (existing pattern: `github_api_error`) |

## DTOs — Shared Types

```json
// Subject — canonical resolved entry point
{
  "subject_id": "pr:a1b2c3d4",
  "subject_type": "pull_request",
  "graph_node_ids": ["node-uuid-1"],
  "display_name": "Rename OrderCreated.total to totalCents"
}

// Evidence — attached to every agent claim
{
  "kind": "graph_traversal" | "tool_call" | "graph_fact",
  "reference": "node-uuid-1",
  "summary": "Traversed 2 downstream API dependents"
}

// Confidence
{
  "score": 0.88,
  "reasoning": "Diff and dependency graph agree on impacted component"
}
```

## Knowledge Graph API

```
GET /api/v1/knowledge-graph/search?q=order.cancelled&node_type=Topic
GET /api/v1/knowledge-graph/nodes/{node_id}
GET /api/v1/knowledge-graph/nodes/{node_id}/edges?direction=out&edge_type=DEPENDS_ON
```

Response for a node:

```json
{
  "id": "node-uuid-1",
  "node_type": "Component",
  "name": "OrderEventProducer",
  "repository_id": "repo-uuid",
  "properties": { "language": "java" },
  "owners": ["alice", "bob"],
  "last_seen_in_run": "run-uuid"
}
```

`GET /knowledge-graph/*` is read-only and public within the org (subject to auth) — this is the
generalized replacement for today's repository-scoped
`GET /pull-requests/{id}/analysis` dependency data, exposed org-wide instead of per-PR.

## Subject / Context API

```
POST /api/v1/subjects/resolve
```

Request:
```json
{ "reference": "ENG-421" }
```
or
```json
{ "reference": "https://github.com/acme/order-service/pull/14" }
```

Response: a `Subject` DTO (above). This is the HTTP-facing entry point to the Context Builder's
Entry Resolver stage — the same resolution the Orchestrator uses internally, exposed so the
frontend's global search/"jump to" bar can resolve free-text input before deciding what to render.

## Agent Orchestrator API

```
POST /api/v1/agent-runs
```

Request:
```json
{
  "subject_reference": "pr:a1b2c3d4",
  "goal": "review_pr",
  "model": "gpt-5"
}
```

Response (`202 Accepted` — run is async):
```json
{
  "run_id": "run-uuid",
  "status": "queued",
  "subject": { "subject_id": "pr:a1b2c3d4", "subject_type": "pull_request", "display_name": "..." },
  "goal": "review_pr"
}
```

(as-built `CreateRunResponse` does not include an `agents_selected` list — a run always
maps to exactly one agent via `goal`, so the response echoes `goal` instead.)

```
GET /api/v1/agent-runs/{run_id}
```

Response:
```json
{
  "run_id": "run-uuid",
  "goal": "review_pr",
  "status": "completed",
  "started_at": "2026-07-23T10:00:00Z",
  "completed_at": "2026-07-23T10:00:42Z",
  "steps": [
    {
      "step_id": "step-uuid",
      "agent_id": "review",
      "status": "completed",
      "confidence": { "score": 0.88, "reasoning": "..." },
      "evidence": [ { "kind": "graph_traversal", "reference": "node-uuid-1", "summary": "..." } ],
      "output_ref": "ai-analysis:a1b2c3d4"
    }
  ]
}
```

`output_ref` points to the existing domain resource the step wrote (e.g. the existing
`PullRequestAIAnalysis` row for the Review agent) rather than duplicating that payload inline —
the Orchestrator API is a run-tracking layer over existing domain APIs, not a replacement for them.

```
GET /api/v1/agent-runs?subject_id=pr:a1b2c3d4        # paginated, filterable — see Filtering
GET /api/v1/agent-runs/agents/manifests                # list AgentManifests (id, purpose, goals handled)
```

Also shipped, sequencing several agent runs into one SDLC lifecycle (not part of the original
design above — added afterwards, documented here rather than left drifting):

```
POST /api/v1/workflows                       # create a Workflow, run the planning stage
GET  /api/v1/workflows                       # list workflows (paginated)
GET  /api/v1/workflows/{workflow_id}         # workflow detail: stages + linked runs
POST /api/v1/workflows/{workflow_id}/continue  # run the next stage (development → testing → review)
```

A `Workflow` groups a fixed, linear sequence of `Run`s (`planning → development → testing →
review`, `app/services/workflow_service.py:STAGES`) — each stage's context is the original
request plus a plain-text summary of every prior completed stage's output
(`workflow_service.build_stage_context()`), not a structured/typed handoff.

`POST /pull-requests/{id}/ai-analysis`, `.../investigate`, and `.../publish-review` (existing,
unchanged) remain the direct, single-agent entry points for the Review agent specifically — they
become thin wrappers that internally call the same Orchestrator path with `goal="review_pr"`
pinned to the Review agent, preserving existing frontend contracts while gaining run tracking for
free.

## Project API (Jira/Confluence-resolved work items) — NOT IMPLEMENTED

Design-only. Depends on Jira/Confluence integration and on `requirement`/`architecture`/`release`
stages that don't exist in the shipped `Workflow` (see Agent Orchestrator API above, whose real
`STAGES` tuple is `planning, development, testing, review`). Nothing under `/api/v1/projects`
exists in the codebase today.

```
GET /api/v1/projects/{subject_id}          # a Story/Epic Subject with its resolved graph links
GET /api/v1/projects/{subject_id}/pipeline  # per-SDLC-stage agent run summary, for the Pipeline UI
```

Response for `.../pipeline`:
```json
{
  "subject_id": "jira:ENG-421",
  "stages": [
    { "stage": "requirement", "status": "completed", "run_id": "run-uuid-1" },
    { "stage": "planning", "status": "completed", "run_id": "run-uuid-2" },
    { "stage": "architecture", "status": "running", "run_id": "run-uuid-3" },
    { "stage": "development", "status": "not_started", "run_id": null },
    { "stage": "review", "status": "not_started", "run_id": null },
    { "stage": "testing", "status": "not_started", "run_id": null },
    { "stage": "release", "status": "not_started", "run_id": null }
  ]
}
```

## Repository API

Unchanged (`GET/POST /repositories`, `DELETE /repositories/{id}`, `POST /repositories/{id}/index`).
GraphForge adds one field to the existing repository response — additive, no version bump:

```json
{ "...": "existing fields", "graph_node_id": "node-uuid" }
```

linking the relational `Repository` row to its Knowledge Graph node.

## Review API

Unchanged: `POST /pull-requests/{id}/ai-analysis`, `GET /pull-requests/{id}/ai-analysis`,
`POST /pull-requests/{id}/investigate`, `POST /pull-requests/{id}/publish-review`. All become
Review-agent-specific conveniences over the Agent Orchestrator API (see above) without changing
their existing request/response shapes.

## Future APIs

```
POST /api/v1/integrations/jira/connect
POST /api/v1/integrations/confluence/connect
GET  /api/v1/incidents/{id}/context           # Monitoring agent, once shipped
GET  /api/v1/documentation/gaps                # Documentation agent, once shipped
```

Each follows the existing `IOAuthProvider`/`IKnowledgeSource` connect pattern — no new auth
paradigm introduced per integration.

## Status Codes Summary

| Code | Used for |
|---|---|
| 200 | Successful read / synchronous write |
| 202 | Accepted, async agent run queued |
| 400 | Invalid request body (new) |
| 401 | Auth failure / integration not connected |
| 404 | Resource/subject not found |
| 422 | Precondition not met (not indexed, unresolvable) |
| 502 | Upstream integration failure |
