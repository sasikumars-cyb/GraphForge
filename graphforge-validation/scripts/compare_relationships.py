"""Validations 1 (Repository Graph), 2 (Cross-Repository Relationships),
7 (Parity), and 8 (Frontier Generator) — everything that compares
GraphForge's *graph and Engineering Memory state* against
`validation/expected_*.yaml`, as opposed to `compare_agents.py`'s
live agent-run comparisons.

Every function takes an already-authenticated `GraphForgeClient` and
returns a `lib.results.ValidationSection` — `run_validation.py` is the
only place these get orchestrated, scored, and written to `reports/`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import memory  # noqa: E402
from lib.client import GraphForgeClient  # noqa: E402
from lib.results import CheckResult, ValidationSection, Verdict  # noqa: E402


# ---------------------------------------------------------------------------
# Validation 1 — Repository Graph
# ---------------------------------------------------------------------------


def _graph_facts(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = graph["nodes"]
    edges = graph["edges"]
    kafka_topics = sorted(
        {
            n["properties"].get("name")
            for n in nodes
            if "KafkaTopic" in n["labels"] and n["properties"].get("name")
        }
    )
    feign_clients = sorted(
        {
            n["properties"].get("name")
            for n in nodes
            if "FeignClient" in n["labels"] and n["properties"].get("name")
        }
    )
    dependencies = sorted(
        {
            n["properties"].get("name") or n["properties"].get("artifact_id")
            for n in nodes
            if ("PythonDependency" in n["labels"] or "MavenDependency" in n["labels"])
        }
        - {None}
    )
    endpoint_count = sum(1 for n in nodes if "Endpoint" in n["labels"])
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "endpoint_count": endpoint_count,
        "dependency_count": len(dependencies),
        "dependencies": dependencies,
        "kafka_topics": kafka_topics,
        "feign_clients": feign_clients,
    }


async def validate_repository_graphs(
    client: GraphForgeClient, expected: dict[str, Any]
) -> ValidationSection:
    section = ValidationSection(1, "Repository Graph")
    name_to_id, _ = await client.get_repository_name_maps()

    for repo in expected.get("supported", []):
        name = repo["name"]
        repo_id = name_to_id.get(name)
        if repo_id is None:
            section.add(
                CheckResult(
                    f"{name}: tracked in GraphForge", Verdict.MISSING, "Repository not tracked"
                )
            )
            continue
        section.add(CheckResult(f"{name}: tracked in GraphForge", Verdict.PASS))

        graph = await client.get_repository_graph(repo_id)
        facts = _graph_facts(graph)

        for field in ("node_count", "edge_count", "endpoint_count", "dependency_count"):
            expected_value = repo[field]
            actual_value = facts[field]
            verdict = Verdict.PASS if actual_value == expected_value else Verdict.FAIL
            section.add(
                CheckResult(
                    f"{name}: {field}",
                    verdict,
                    expected=expected_value,
                    actual=actual_value,
                )
            )

        for field in ("dependencies", "kafka_topics", "feign_clients"):
            expected_value = sorted(repo.get(field, []))
            actual_value = facts[field]
            verdict = Verdict.PASS if actual_value == expected_value else Verdict.FAIL
            section.add(
                CheckResult(
                    f"{name}: {field}",
                    verdict,
                    expected=expected_value,
                    actual=actual_value,
                )
            )

    for repo in expected.get("unsupported", []):
        name = repo["name"]
        repo_id = name_to_id.get(name)
        if repo_id is None:
            section.add(
                CheckResult(
                    f"{name}: tracked in GraphForge", Verdict.MISSING, "Repository not tracked"
                )
            )
            continue
        job = await client.get_latest_indexing_job(repo_id)
        if job is None:
            section.add(
                CheckResult(f"{name}: indexing job exists", Verdict.MISSING, "Never indexed")
            )
            continue
        status_ok = job["status"] == repo["expected_status"]
        error_ok = repo["error_contains"] in (job.get("error_message") or "")
        verdict = Verdict.PASS if (status_ok and error_ok) else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: still correctly unsupported",
                verdict,
                expected=f"status={repo['expected_status']!r} containing {repo['error_contains']!r}",
                actual=f"status={job['status']!r}: {job.get('error_message')!r}",
            )
        )

    return section


# ---------------------------------------------------------------------------
# Validation 2 — Cross-Repository Relationships
# ---------------------------------------------------------------------------


async def validate_cross_repository_relationships(
    client: GraphForgeClient, expected: dict[str, Any], repo_expected: dict[str, Any]
) -> ValidationSection:
    section = ValidationSection(2, "Cross-Repository Relationships")
    name_to_id, id_to_name = await client.get_repository_name_maps()

    raw_edges = await client.get_cross_repository_edges()
    actual_edges: set[tuple[str, str, str]] = set()
    for edge in raw_edges:
        source_repo_id = edge["source_id"].split(":")[0]
        target_repo_id = edge["target_id"].split(":")[0]
        source_name = id_to_name.get(source_repo_id)
        target_name = id_to_name.get(target_repo_id)
        if source_name and target_name:
            actual_edges.add((edge["type"], source_name, target_name))

    expected_edges = {
        (e["type"], e["source"], e["target"]) for e in expected.get("cross_repository_edges", [])
    }

    for e in sorted(expected_edges):
        verdict = Verdict.PASS if e in actual_edges else Verdict.MISSING
        section.add(CheckResult(f"cross-repo edge: {e[1]} -{e[0]}-> {e[2]}", verdict))

    # Anything actual that isn't in the expected set AND isn't already
    # accounted for by a known-absent entry (which by definition
    # shouldn't be present) is a genuinely new, unreviewed relationship.
    # Scoped to this suite's own repos only — the same GraphForge account
    # may track other, unrelated repositories (pre-existing fixtures from
    # earlier work) whose edges are none of this framework's business.
    known_absent = {
        (e["type"], e["source"], e["target"])
        for e in expected.get("known_absent_cross_repository_edges", [])
    }
    suite_names = {repo["name"] for repo in repo_expected.get("supported", [])} | {
        repo["name"] for repo in repo_expected.get("unsupported", [])
    }
    for e in sorted(actual_edges - expected_edges):
        if e[1] not in suite_names or e[2] not in suite_names:
            continue  # edge touches a repo outside this suite (e.g. a pre-existing fixture repo)
        section.add(
            CheckResult(
                f"unexpected cross-repo edge: {e[1]} -{e[0]}-> {e[2]}",
                Verdict.UNEXPECTED,
                "Present in GraphForge but not in expected_relationships.yaml — review and add it",
            )
        )

    for e in sorted(known_absent):
        verdict = Verdict.PASS if e not in actual_edges else Verdict.FAIL
        detail = (
            "still absent, as expected"
            if verdict == Verdict.PASS
            else "now present — the documented gap may have closed; update the fixture"
        )
        section.add(CheckResult(f"known-absent edge: {e[1]} -{e[0]}-> {e[2]}", verdict, detail))

    # Within-repository edge-type counts (EXPOSES/PRODUCES_TO/CONSUMES_FROM/DEPENDS_ON/CONTAINS/...)
    expected_within = expected.get("within_repository_edge_types", {})
    for repo in repo_expected.get("supported", []):
        name = repo["name"]
        if name not in expected_within:
            continue
        repo_id = name_to_id.get(name)
        if repo_id is None:
            continue
        graph = await client.get_repository_graph(repo_id)
        actual_counts: dict[str, int] = {}
        for edge in graph["edges"]:
            actual_counts[edge["type"]] = actual_counts.get(edge["type"], 0) + 1
        for edge_type, expected_count in expected_within[name].items():
            actual_count = actual_counts.get(edge_type, 0)
            verdict = Verdict.PASS if actual_count == expected_count else Verdict.FAIL
            section.add(
                CheckResult(
                    f"{name}: {edge_type} edge count",
                    verdict,
                    expected=expected_count,
                    actual=actual_count,
                )
            )

    return section


# ---------------------------------------------------------------------------
# Validation 7 — Parity
# ---------------------------------------------------------------------------


async def validate_parity(
    client: GraphForgeClient, repo_expected: dict[str, Any], min_similarity: float = 99.0
) -> ValidationSection:
    section = ValidationSection(7, "Parity")
    name_to_id, _ = await client.get_repository_name_maps()

    for repo in repo_expected.get("supported", []):
        name = repo["name"]
        repo_id = name_to_id.get(name)
        if repo_id is None:
            section.add(CheckResult(f"{name}: parity", Verdict.MISSING, "Repository not tracked"))
            continue

        report = await client.get_parity_report(repo_id)
        similarity = report["similarity_percentage"]
        verdict = Verdict.PASS if similarity >= min_similarity else Verdict.FAIL
        detail_parts = []
        if report["missing_nodes"]:
            detail_parts.append(f"{len(report['missing_nodes'])} missing node(s)")
        if report["unexpected_nodes"]:
            detail_parts.append(f"{len(report['unexpected_nodes'])} unexpected node(s)")
        if report["missing_edges"]:
            detail_parts.append(f"{len(report['missing_edges'])} missing edge(s)")
        if report["unexpected_edges"]:
            detail_parts.append(f"{len(report['unexpected_edges'])} unexpected edge(s)")
        if report["duplicate_nodes"] or report["duplicate_edges"]:
            detail_parts.append("duplicate entities present")
        section.add(
            CheckResult(
                f"{name}: parity similarity >= {min_similarity}%",
                verdict,
                detail="; ".join(detail_parts) or "identical",
                expected=f">= {min_similarity}%",
                actual=f"{similarity}%",
            )
        )

    return section


# ---------------------------------------------------------------------------
# Validation 8 — Frontier Generator
# ---------------------------------------------------------------------------

_STATES = ("verified", "highly_likely", "likely", "candidate", "rejected", "conflicting")


async def validate_frontier_hypotheses(
    client: GraphForgeClient, expected: dict[str, Any]
) -> ValidationSection:
    section = ValidationSection(8, "Frontier Generator")
    name_to_id, _ = await client.get_repository_name_maps()

    for repo in expected.get("repositories", []):
        name = repo["name"]
        repo_id = name_to_id.get(name)
        if repo_id is None:
            section.add(
                CheckResult(f"{name}: hypotheses", Verdict.MISSING, "Repository not tracked")
            )
            continue

        records = await memory.get_current_relationships(repo_id)
        counts = dict.fromkeys(_STATES, 0)
        for record in records:
            if record.confidence_state in counts:
                counts[record.confidence_state] += 1

        generated = len(records)
        verdict = Verdict.PASS if generated == repo["generated"] else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: generated hypotheses",
                verdict,
                expected=repo["generated"],
                actual=generated,
            )
        )

        validated_expected = repo["generated"] - repo["candidate"]
        validated_actual = generated - counts["candidate"]
        verdict = Verdict.PASS if validated_actual == validated_expected else Verdict.FAIL
        section.add(
            CheckResult(
                f"{name}: validated hypotheses",
                verdict,
                expected=validated_expected,
                actual=validated_actual,
            )
        )

        for state in _STATES:
            expected_count = repo[state]
            actual_count = counts[state]
            verdict = Verdict.PASS if actual_count == expected_count else Verdict.FAIL
            section.add(
                CheckResult(
                    f"{name}: {state} hypotheses",
                    verdict,
                    expected=expected_count,
                    actual=actual_count,
                )
            )

    return section
