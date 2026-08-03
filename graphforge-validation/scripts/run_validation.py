#!/usr/bin/env python3
"""GraphForge Regression Validation Framework — entry point.

Runs all ten validations from `docs/validation-guide.md` against a live
GraphForge instance (`GRAPHFORGE_API_URL`, default
`http://localhost:8000/api/v1`), writes a JSON results file and an HTML
report to `reports/`, prints a summary, and exits non-zero if anything
that isn't purely informational (Validation 9, Performance) failed.

Usage:
    cd graphforge-validation
    poetry run --directory ../backend python scripts/run_validation.py
    # or, from anywhere, with the backend's venv active:
    python scripts/run_validation.py
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import fixtures, memory  # noqa: E402
from lib.client import GraphForgeClient  # noqa: E402
from lib.config import load_config  # noqa: E402
from lib.results import CheckResult, ValidationSection, Verdict  # noqa: E402

import compare_agents  # noqa: E402
import compare_relationships  # noqa: E402
import generate_report  # noqa: E402

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"


# ---------------------------------------------------------------------------
# Validation 6 — Engineering Memory
# ---------------------------------------------------------------------------


async def validate_engineering_memory(
    client: GraphForgeClient, repo_expected: dict[str, Any]
) -> ValidationSection:
    section = ValidationSection(6, "Engineering Memory")
    name_to_id, _ = await client.get_repository_name_maps()

    for repo in repo_expected.get("supported", []):
        name = repo["name"]
        repo_id = name_to_id.get(name)
        if repo_id is None:
            section.add(CheckResult(f"{name}: memory", Verdict.MISSING, "Repository not tracked"))
            continue

        records = await memory.get_current_relationships(repo_id)

        section.add(
            CheckResult(
                f"{name}: relationships persisted",
                Verdict.PASS if records else Verdict.FAIL,
                actual=len(records),
            )
        )

        confidence_ok = all(r.confidence_state for r in records)
        section.add(
            CheckResult(
                f"{name}: confidence exists on every relationship",
                Verdict.PASS if confidence_ok else Verdict.FAIL,
            )
        )

        explanation_ok = all(r.explanation is not None for r in records)
        section.add(
            CheckResult(
                f"{name}: explanation exists on every relationship",
                Verdict.PASS if explanation_ok else Verdict.FAIL,
                actual=f"{sum(1 for r in records if r.explanation is not None)}/{len(records)}",
            )
        )

        provenance_ok = all(bool(r.provenance) for r in records)
        section.add(
            CheckResult(
                f"{name}: validator provenance exists on every relationship",
                Verdict.PASS if provenance_ok else Verdict.FAIL,
                actual=f"{sum(1 for r in records if r.provenance)}/{len(records)}",
            )
        )

        keys = [r.relationship_key for r in records]
        no_dupes = len(keys) == len(set(keys))
        section.add(
            CheckResult(
                f"{name}: no duplicate current relationships",
                Verdict.PASS if no_dupes else Verdict.FAIL,
                detail=f"{len(keys)} records, {len(set(keys))} distinct keys",
            )
        )

        # Append-only history: for a small sample, confirm the history is
        # a strictly increasing `sequence` and the "current" row is the
        # last entry of its own history (see app/models/knowledge_relationship.py).
        sample = records[:3]
        history_ok = True
        for record in sample:
            history = await memory.get_relationship_history(
                repo_id, record.relationship_type, record.source_entity, record.target_entity
            )
            sequences = [h.sequence for h in history]
            strictly_increasing = sequences == sorted(sequences) and len(set(sequences)) == len(
                sequences
            )
            current_is_latest = bool(history) and history[-1].sequence == record.sequence
            if not (strictly_increasing and current_is_latest):
                history_ok = False
        if sample:
            section.add(
                CheckResult(
                    f"{name}: append-only history maintained (sampled)",
                    Verdict.PASS if history_ok else Verdict.FAIL,
                    detail=f"sampled {len(sample)} relationship(s)",
                )
            )

    return section


# ---------------------------------------------------------------------------
# Validation 9 — Performance
# ---------------------------------------------------------------------------


async def measure_performance(
    client: GraphForgeClient, repo_expected: dict[str, Any], section_timings: dict[str, float]
) -> tuple[ValidationSection, list[dict[str, Any]]]:
    section = ValidationSection(9, "Performance")
    name_to_id, _ = await client.get_repository_name_maps()
    timing_table: list[dict[str, Any]] = []

    for repo in repo_expected.get("supported", []):
        name = repo["name"]
        repo_id = name_to_id.get(name)
        if repo_id is None:
            continue
        job = await client.get_latest_indexing_job(repo_id)
        if job is None or job["status"] != "completed":
            continue
        started = job.get("started_at")
        finished = job.get("finished_at")
        if not (started and finished):
            continue
        duration_s = (
            datetime.fromisoformat(finished) - datetime.fromisoformat(started)
        ).total_seconds()
        timing_table.append(
            {
                "repository": name,
                # Indexing time bundles clone + parse + graph build +
                # cross-repository relink — GraphForge's own
                # `IndexingJob` doesn't record sub-phase timestamps, so
                # "cross-repository linking time" and "materialization
                # time" from the RFC are NOT separately measurable via
                # this API today; see docs/validation-guide.md.
                "phase": "repository_indexing (includes cross-repo linking)",
                "duration_seconds": round(duration_s, 3),
            }
        )

    for repo in repo_expected.get("supported", []):
        name = repo["name"]
        repo_id = name_to_id.get(name)
        if repo_id is None:
            continue
        start = time.monotonic()
        await client.get_parity_report(repo_id)
        timing_table.append(
            {
                "repository": name,
                "phase": "parity_check",
                "duration_seconds": round(time.monotonic() - start, 3),
            }
        )

    for validation_name, duration_s in section_timings.items():
        timing_table.append(
            {
                "repository": "(all)",
                "phase": validation_name,
                "duration_seconds": round(duration_s, 3),
            }
        )

    section.add(
        CheckResult(
            "performance data collected",
            Verdict.PASS if timing_table else Verdict.FAIL,
            actual=f"{len(timing_table)} measurement(s)",
        )
    )
    return section, timing_table


# ---------------------------------------------------------------------------
# Validation 10 — Overall Score
# ---------------------------------------------------------------------------


def compute_overall_score(sections: list[ValidationSection]) -> dict[str, Any]:
    by_id = {s.validation_id: s for s in sections}

    def avg(*ids: int) -> float:
        rates = [by_id[i].pass_rate for i in ids if i in by_id and by_id[i].checks]
        return sum(rates) / len(rates) if rates else 1.0

    relationship_accuracy = avg(1, 2)
    agent_accuracy = avg(3, 4, 5)
    parity_score = avg(7)
    engineering_memory_score = avg(6)
    frontier_score = avg(8)
    performance_score = 1.0 if by_id.get(9) and by_id[9].overall == "PASS" else 0.5

    overall_health = sum(
        [
            relationship_accuracy,
            agent_accuracy,
            parity_score,
            engineering_memory_score,
            frontier_score,
            performance_score,
        ]
    ) / 6

    # Gating validations (everything except the purely informational
    # Performance section) must all be clean for an overall PASS —
    # health score alone can't paper over a hard FAIL in any of them.
    gating_ids = [1, 2, 3, 4, 5, 6, 7, 8]
    overall_pass = all(by_id[i].overall != "FAIL" for i in gating_ids if i in by_id)

    return {
        "relationship_accuracy": round(relationship_accuracy, 4),
        "agent_accuracy": round(agent_accuracy, 4),
        "parity_score": round(parity_score, 4),
        "engineering_memory_score": round(engineering_memory_score, 4),
        "frontier_score": round(frontier_score, 4),
        "performance_score": round(performance_score, 4),
        "overall_health_score": round(overall_health, 4),
        "overall_result": "PASS" if overall_pass else "FAIL",
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def main() -> int:
    config = load_config()
    repo_expected = fixtures.load_expected_repository_profiles()
    relationships_expected = fixtures.load_expected_relationships()
    dependency_query_expected = fixtures.load_expected_dependency_queries()
    impact_analysis_expected = fixtures.load_expected_impact_analysis()
    frontier_expected = fixtures.load_expected_frontier_hypotheses()

    run_started_at = datetime.now(UTC)
    section_timings: dict[str, float] = {}
    sections: list[ValidationSection] = []

    async with GraphForgeClient(config) as client:

        async def timed(label: str, coro: Any) -> ValidationSection:
            start = time.monotonic()
            result = await coro
            section_timings[label] = time.monotonic() - start
            return result

        print("Running Validation 1: Repository Graph...")
        sections.append(
            await timed(
                "validation_1_repository_graph",
                compare_relationships.validate_repository_graphs(client, repo_expected),
            )
        )

        print("Running Validation 2: Cross-Repository Relationships...")
        sections.append(
            await timed(
                "validation_2_cross_repository_relationships",
                compare_relationships.validate_cross_repository_relationships(
                    client, relationships_expected, repo_expected
                ),
            )
        )

        print("Running Validation 3: Repository Understanding Agent (live LLM calls)...")
        sections.append(
            await timed(
                "validation_3_repository_understanding_agent",
                compare_agents.validate_repository_understanding(client, repo_expected),
            )
        )

        print("Running Validation 4: Dependency Query Agent (live LLM calls)...")
        sections.append(
            await timed(
                "validation_4_dependency_query_agent",
                compare_agents.validate_dependency_query(client, dependency_query_expected),
            )
        )

        print("Running Validation 5: Impact Analysis Agent (live LLM calls)...")
        sections.append(
            await timed(
                "validation_5_impact_analysis_agent",
                compare_agents.validate_impact_analysis(client, impact_analysis_expected),
            )
        )

        print("Running Validation 6: Engineering Memory...")
        sections.append(
            await timed(
                "validation_6_engineering_memory",
                validate_engineering_memory(client, repo_expected),
            )
        )

        print("Running Validation 7: Parity...")
        sections.append(
            await timed("validation_7_parity", compare_relationships.validate_parity(client, repo_expected))
        )

        print("Running Validation 8: Frontier Generator...")
        sections.append(
            await timed(
                "validation_8_frontier_generator",
                compare_relationships.validate_frontier_hypotheses(client, frontier_expected),
            )
        )

        print("Running Validation 9: Performance...")
        performance_section, timing_table = await measure_performance(
            client, repo_expected, section_timings
        )
        sections.append(performance_section)

    overall = compute_overall_score(sections)
    section_10 = ValidationSection(10, "Overall Score")
    section_10.add(
        CheckResult(
            "overall result",
            Verdict.PASS if overall["overall_result"] == "PASS" else Verdict.FAIL,
            actual=overall,
        )
    )
    sections.append(section_10)

    run_finished_at = datetime.now(UTC)
    results = {
        "run_id": str(uuid.uuid4()),
        "started_at": run_started_at.isoformat(),
        "finished_at": run_finished_at.isoformat(),
        "duration_seconds": round((run_finished_at - run_started_at).total_seconds(), 2),
        "api_base_url": config.api_base_url,
        "sections": [s.to_dict() for s in sections],
        "timing_table": timing_table,
        "overall": overall,
    }

    REPORTS_DIR.mkdir(exist_ok=True)
    timestamp = run_started_at.strftime("%Y%m%dT%H%M%SZ")
    json_path = REPORTS_DIR / f"validation_results_{timestamp}.json"
    html_path = REPORTS_DIR / f"validation_report_{timestamp}.html"

    import json

    json_path.write_text(json.dumps(results, indent=2, default=str))
    (REPORTS_DIR / "latest.json").write_text(json.dumps(results, indent=2, default=str))

    generate_report.render(results, html_path)
    generate_report.render(results, REPORTS_DIR / "latest.html")

    print()
    print("=" * 72)
    for section in sections:
        marker = "PASS" if section.overall != "FAIL" else "FAIL"
        print(f"  [{marker:4}] Validation {section.validation_id}: {section.title} "
              f"({section.counts['PASS']}/{section.total} checks passed)")
    print("=" * 72)
    print(f"Overall Health Score: {overall['overall_health_score'] * 100:.1f}%")
    print(f"Overall Result:       {overall['overall_result']}")
    print()
    print(f"JSON results: {json_path}")
    print(f"HTML report:  {html_path}")

    return 0 if overall["overall_result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
