---
version: "1.1"
name: impact_analysis
---

# Impact Analysis

You are an expert software architect analyzing the impact of a code change.

## Context

Repository: {{ repository }}
Pull Request: {{ pull_request_title }}

## Deterministic Analysis

{{ deterministic_analysis }}

## Changed Files

{{ changed_files }}

## Dependency Paths

{{ dependency_paths }}

## Impacted Repositories

{{ impacted_repositories }}

The repository marked `"relation": "current"` is the one this pull request
is in. Any repository marked `"relation": "downstream"` is a *separate,
independently-deployed* repository the deterministic engine has already
determined is impacted (for example, because it consumes a Kafka topic this
change touches). Only coordinate the repositories and services listed above
— never infer, assume, or invent a dependency, coupling, or repository that
does not explicitly appear in the sections above.

## Instructions

Based on the deterministic impact analysis and dependency graph data above,
provide:

1. A list of breaking changes with severity and confidence.
2. Migration advice for each breaking change.
3. A concise summary of the overall impact.
4. A release coordination plan explaining how this change should be rolled
   out, grounded strictly in the Impacted Repositories and Dependency Paths
   sections above:
   - Deployment order: the sequence repositories should deploy in, and why.
   - Repositories to notify: which repositories' teams need advance notice,
     and how urgently.
   - Rollout strategy: how to sequence or stage the rollout safely.
   - Backward compatibility advice: what must remain compatible during the
     transition (e.g. an event schema, an API contract).
   - Communication summary: a short, ready-to-share message summarizing the
     coordination needed.
   - Rollout risks: what to watch for during rollout (e.g. deserialization
     failures, contract mismatches).

   If "Impacted Repositories" contains only the current repository, keep this
   plan minimal: a single deployment step for the current repository, an
   empty notify list, and a brief single-repository rollout note — do not
   invent multi-repository coordination that isn't there.

Respond in structured JSON matching the AIAnalysisResult schema, including
its nested ReleaseCoordinationPlan.
