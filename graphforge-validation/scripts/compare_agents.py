"""Validations 3 (Repository Understanding Agent), 4 (Dependency Query
Agent), and 5 (Impact Analysis Agent) — every check that requires
actually *running* a GraphForge agent (`POST /agent-runs`, real LLM
calls) rather than reading already-persisted state. See
`compare_relationships.py` for the graph/memory-only validations.

Narrative LLM fields (purpose, business capability, ...) are checked for
keyword presence, never exact text match — the RFC's "compare semantic
content, not exact ids" instruction extends naturally to "don't require
exact LLM wording."
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.client import GraphForgeClient, repo_subject_reference  # noqa: E402
from lib.results import CheckResult, ValidationSection, Verdict  # noqa: E402

GOAL_REPOSITORY_UNDERSTANDING = "analyze_repository_understanding"
GOAL_DEPENDENCY_QUERY = "analyze_dependency_query"
GOAL_IMPACT_ANALYSIS = "analyze_impact_analysis"


def _keywords_present(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


# ---------------------------------------------------------------------------
# Validation 3 — Repository Understanding Agent
# ---------------------------------------------------------------------------


async def validate_repository_understanding(
    client: GraphForgeClient, expected: dict[str, Any]
) -> ValidationSection:
    section = ValidationSection(3, "Repository Understanding Agent")
    name_to_id, _ = await client.get_repository_name_maps()

    for repo in expected.get("supported", []):
        name = repo["name"]
        repo_id = name_to_id.get(name)
        if repo_id is None:
            section.add(
                CheckResult(f"{name}: run agent", Verdict.MISSING, "Repository not tracked")
            )
            continue

        run = await client.run_agent_and_wait(
            repo_subject_reference(repo_id), GOAL_REPOSITORY_UNDERSTANDING
        )
        if run["status"] != "completed" or not run["steps"]:
            section.add(
                CheckResult(
                    f"{name}: agent run completed",
                    Verdict.FAIL,
                    detail=run.get("error_message") or f"status={run['status']}",
                )
            )
            continue
        section.add(CheckResult(f"{name}: agent run completed", Verdict.PASS))

        result = run["steps"][0]["result"]
        ru = repo.get("repository_understanding", {})

        # Java dependencies come back as full "group:artifact" coordinates
        # (RepositoryProfileService), Python ones as bare package names —
        # compare by the artifact's short name (the part after the last
        # ':') on both sides so this holds for either language.
        actual_deps = sorted({d.rsplit(":", 1)[-1] for d in result.get("dependencies", [])})
        expected_deps = sorted(repo.get("dependencies", []))
        verdict = Verdict.PASS if actual_deps == expected_deps else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: reported dependencies",
                verdict,
                expected=expected_deps,
                actual=actual_deps,
            )
        )

        actual_dbs = sorted(result.get("databases", []))
        expected_dbs = sorted(repo.get("databases", []))
        verdict = Verdict.PASS if actual_dbs == expected_dbs else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: reported databases", verdict, expected=expected_dbs, actual=actual_dbs
            )
        )

        narrative_text = " ".join(
            str(result.get(field, ""))
            for field in (
                "executive_summary",
                "repository_overview",
                "architecture_overview",
                "external_systems_summary",
                "dependency_summary",
            )
        )

        for field, keywords in (
            ("purpose", ru.get("purpose_keywords", [])),
            ("business_capability", ru.get("business_capability_keywords", [])),
            ("security", ru.get("security_keywords", [])),
        ):
            if not keywords:
                continue
            present = _keywords_present(narrative_text, keywords)
            verdict = Verdict.PASS if present else Verdict.FAIL
            section.add(
                CheckResult(
                    f"{name}: {field} keyword(s) present",
                    verdict,
                    expected=keywords,
                    actual=narrative_text[:200],
                )
            )

        sdk_usage = repo.get("sdk_usage", [])
        if sdk_usage:
            missing_sdks = [sdk for sdk in sdk_usage if sdk not in actual_deps]
            verdict = Verdict.PASS if not missing_sdks else Verdict.FAIL
            section.add(
                CheckResult(
                    f"{name}: SDK usage reported",
                    verdict,
                    expected=sdk_usage,
                    actual=actual_deps,
                )
            )

    return section


# ---------------------------------------------------------------------------
# Validation 4 — Dependency Query Agent
# ---------------------------------------------------------------------------


async def validate_dependency_query(
    client: GraphForgeClient, expected: dict[str, Any]
) -> ValidationSection:
    section = ValidationSection(4, "Dependency Query Agent")
    name_to_id, _ = await client.get_repository_name_maps()

    for repo in expected.get("repositories", []):
        name = repo["name"]
        repo_id = name_to_id.get(name)
        if repo_id is None:
            section.add(
                CheckResult(f"{name}: run agent", Verdict.MISSING, "Repository not tracked")
            )
            continue

        run = await client.run_agent_and_wait(repo_subject_reference(repo_id), GOAL_DEPENDENCY_QUERY)
        if run["status"] != "completed" or not run["steps"]:
            section.add(
                CheckResult(
                    f"{name}: agent run completed",
                    Verdict.FAIL,
                    detail=run.get("error_message") or f"status={run['status']}",
                )
            )
            continue
        section.add(CheckResult(f"{name}: agent run completed", Verdict.PASS))

        result = run["steps"][0]["result"]

        actual_direct = len(result.get("direct_dependencies", []))
        expected_direct = repo["direct_dependencies_count"]
        verdict = Verdict.PASS if actual_direct == expected_direct else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: direct_dependencies count",
                verdict,
                expected=expected_direct,
                actual=actual_direct,
            )
        )

        actual_consumers = len(result.get("downstream_consumers", []))
        expected_consumers = repo["downstream_consumers_count"]
        verdict = Verdict.PASS if actual_consumers == expected_consumers else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: downstream_consumers count",
                verdict,
                expected=expected_consumers,
                actual=actual_consumers,
            )
        )

        actual_breakdown = result.get("confidence_breakdown", {})
        expected_breakdown = repo["confidence_breakdown"]
        verdict = Verdict.PASS if actual_breakdown == expected_breakdown else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: confidence_breakdown",
                verdict,
                expected=expected_breakdown,
                actual=actual_breakdown,
            )
        )

    return section


# ---------------------------------------------------------------------------
# Validation 5 — Impact Analysis Agent
# ---------------------------------------------------------------------------


async def validate_impact_analysis(
    client: GraphForgeClient, expected: dict[str, Any]
) -> ValidationSection:
    section = ValidationSection(5, "Impact Analysis Agent")
    name_to_id, id_to_name = await client.get_repository_name_maps()

    for repo in expected.get("repositories", []):
        name = repo["name"]
        repo_id = name_to_id.get(name)
        if repo_id is None:
            section.add(
                CheckResult(f"{name}: run agent", Verdict.MISSING, "Repository not tracked")
            )
            continue

        run = await client.run_agent_and_wait(repo_subject_reference(repo_id), GOAL_IMPACT_ANALYSIS)
        if run["status"] != "completed" or not run["steps"]:
            section.add(
                CheckResult(
                    f"{name}: agent run completed",
                    Verdict.FAIL,
                    detail=run.get("error_message") or f"status={run['status']}",
                )
            )
            continue
        section.add(CheckResult(f"{name}: agent run completed", Verdict.PASS))

        result = run["steps"][0]["result"]

        actual_hops = result.get("max_hops")
        verdict = Verdict.PASS if actual_hops == repo["max_hops"] else Verdict.FAIL
        section.add(
            CheckResult(f"{name}: hop count", verdict, expected=repo["max_hops"], actual=actual_hops)
        )

        actual_direction = result.get("direction")
        verdict = Verdict.PASS if actual_direction == repo["direction"] else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: direction", verdict, expected=repo["direction"], actual=actual_direction
            )
        )

        # Node ids come back as "{repository_id}:repository" — translate
        # to short repo names for a human-readable, fixture-matchable comparison.
        raw_repos = result.get("directly_impacted_repositories", [])
        actual_repo_names = sorted(
            {id_to_name.get(node_id.split(":")[0], node_id) for node_id in raw_repos}
        )
        expected_repo_names = sorted(repo["impacted_repositories"])
        verdict = Verdict.PASS if actual_repo_names == expected_repo_names else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: blast radius affected repositories",
                verdict,
                expected=expected_repo_names,
                actual=actual_repo_names,
            )
        )

        actual_apis = len(result.get("indirectly_impacted_apis", []))
        verdict = Verdict.PASS if actual_apis == repo["impacted_apis_count"] else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: impacted APIs count",
                verdict,
                expected=repo["impacted_apis_count"],
                actual=actual_apis,
            )
        )

        actual_high_risk = len(result.get("high_risk_components", []))
        expected_high_risk = repo["impacted_databases_count"] + repo["impacted_queues_count"]
        verdict = Verdict.PASS if actual_high_risk == expected_high_risk else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: impacted databases+queues count",
                verdict,
                expected=expected_high_risk,
                actual=actual_high_risk,
            )
        )

        actual_confidence = result.get("confidence_summary", {})
        expected_confidence = repo["confidence_summary"]
        verdict = Verdict.PASS if actual_confidence == expected_confidence else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: confidence summary",
                verdict,
                expected=expected_confidence,
                actual=actual_confidence,
            )
        )

    return section
