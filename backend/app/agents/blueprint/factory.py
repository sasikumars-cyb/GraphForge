"""BlueprintFactory — transforms structured agent output into BlueprintArtifacts.

Pure Python, no LLM calls. Each agent generates its structured result first;
the factory deterministically converts those structured fields into visual
diagram objects. This separation means:
- Diagram data is always consistent with the structured result
- No second LLM call required
- Factory can be unit-tested with synthetic PlanningResult fixtures

To add support for a new agent (Development, Testing, Engineering Review):
1. Add a static method `from_X_result(result: XResult) -> BlueprintArtifact`
2. Register it in that agent's run() after the LLM parse succeeds
3. Add a frontend renderer for any new DiagramType it introduces
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.agents.blueprint.models import (
    BlueprintArtifact,
    Diagram,
    DiagramEdge,
    DiagramLayout,
    DiagramNode,
    DiagramType,
)

if TYPE_CHECKING:
    from app.agents.development.schemas import DevelopmentPlan
    from app.agents.planning.schemas import PlanningResult


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    """Stable, filesystem-safe ID fragment from arbitrary text."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


def _truncate_at_word(text: str, limit: int) -> str:
    """Truncate to at most `limit` characters without cutting a word in
    half, and mark that it happened with an ellipsis.

    A bare `text[:limit]` slice — the previous implementation — has no
    idea where a word ends, so a deliverable one character over the limit
    reads as finished ("...from job run 7") when it was actually cut off
    mid-sentence, with no visual sign anything is missing. Rewinding to
    the last whitespace before the cut, and appending "…" only when a cut
    actually happened, keeps both the truncation point and the fact that
    truncation occurred honest.
    """
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",.;: ")
    return f"{cut}…" if cut else f"{text[:limit].rstrip()}…"


def _risk_level(text: str) -> str:
    """Heuristic severity for a risk string — keyword scan, no LLM."""
    t = text.lower()
    if any(w in t for w in ("critical", "severe", "breach", "security", "data loss")):
        return "critical"
    if any(w in t for w in ("high", "major", "significant", "failure")):
        return "high"
    if any(w in t for w in ("medium", "moderate", "possible", "performance")):
        return "medium"
    return "low"


def _stars(n: int) -> str:
    """Return a star-rating string with filled and empty stars (1-5)."""
    n = max(1, min(5, n))
    return "★" * n + "☆" * (5 - n)


def _risk_severity(likelihood: str, impact: str) -> str:
    """Compute severity from likelihood × impact matrix."""
    impact_score = {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(impact.lower(), 2)
    likelihood_score = {"low": 1, "medium": 2, "high": 3}.get(likelihood.lower(), 2)
    combined = impact_score + likelihood_score
    if combined >= 6:
        return "critical"
    if combined >= 5:
        return "high"
    if combined >= 3:
        return "medium"
    return "low"


_LAYER_TYPE_TO_NODE_TYPE: dict[str, str] = {
    "source": "input",
    "ingestion": "input",
    "processing": "component",
    "transformation": "component",
    "storage": "phase",
    "consumption": "output",
    "monitoring": "default",
}

_FLOW_STEP_TYPE_TO_NODE_TYPE: dict[str, str] = {
    "source": "input",
    "process": "component",
    "storage": "phase",
    "destination": "output",
}

# ---------------------------------------------------------------------------
# Quality enrichment: expand sparse LLM output to minimum viable diagrams
# ---------------------------------------------------------------------------

_STEP_TYPE_PRIORITY: dict[str, int] = {
    "source": 0,
    "process": 1,
    "storage": 2,
    "destination": 3,
}
_STANDARD_FLOW_STAGES: list[dict[str, Any]] = [
    {"name": "Data Source", "step_type": "source", "technology": "", "order": 0},
    {"name": "Validation", "step_type": "process", "technology": "", "order": 0},
    {"name": "Transformation", "step_type": "process", "technology": "", "order": 0},
    {"name": "Data Store", "step_type": "storage", "technology": "", "order": 0},
    {"name": "Delivery", "step_type": "destination", "technology": "", "order": 0},
]

_LAYER_TYPE_PRIORITY: dict[str, int] = {
    "source": 0,
    "ingestion": 1,
    "processing": 2,
    "transformation": 2,
    "storage": 3,
    "consumption": 4,
    "monitoring": 5,
}
_STANDARD_ARCH_LAYERS: list[dict[str, Any]] = [
    {
        "name": "Data Source",
        "layer_type": "source",
        "description": "External data origins",
        "order": 0,
    },
    {
        "name": "Ingestion",
        "layer_type": "ingestion",
        "description": "Data collection and landing",
        "order": 0,
    },
    {
        "name": "Processing",
        "layer_type": "processing",
        "description": "Business logic and transformation",
        "order": 0,
    },
    {
        "name": "Storage",
        "layer_type": "storage",
        "description": "Persistent data layer",
        "order": 0,
    },
    {
        "name": "Consumption",
        "layer_type": "consumption",
        "description": "APIs and downstream consumers",
        "order": 0,
    },
]


def _expand_flow_stages(steps: list[dict[str, Any]], min_count: int = 4) -> list[dict[str, Any]]:
    """Expand a sparse flow to at least min_count stages using standard pipeline types."""
    if len(steps) >= min_count:
        return steps
    covered = {s.get("step_type", "process") for s in steps}
    result = list(steps)
    for stage in _STANDARD_FLOW_STAGES:
        if len(result) >= min_count:
            break
        if stage["step_type"] not in covered:
            result.append(dict(stage))
            covered.add(stage["step_type"])
    result.sort(
        key=lambda s: (
            _STEP_TYPE_PRIORITY.get(s.get("step_type", "process"), 1),
            int(s.get("order", 99)),
        )
    )
    for i, s in enumerate(result):
        result[i] = {**s, "order": i + 1}
    return result


def _expand_arch_layers(layers: list[dict[str, Any]], min_count: int = 3) -> list[dict[str, Any]]:
    """Expand sparse architecture layers to at least min_count layers."""
    if len(layers) >= min_count:
        return layers
    covered = {entry.get("layer_type", "processing") for entry in layers}
    result = list(layers)
    for layer in _STANDARD_ARCH_LAYERS:
        if len(result) >= min_count:
            break
        if layer["layer_type"] not in covered:
            result.append(dict(layer))
            covered.add(layer["layer_type"])
    result.sort(
        key=lambda entry: (
            _LAYER_TYPE_PRIORITY.get(entry.get("layer_type", "processing"), 2),
            int(entry.get("order", 99)),
        )
    )
    for i, entry in enumerate(result):
        result[i] = {**entry, "order": i + 1}
    return result


def _validate_edges(nodes: list[DiagramNode], edges: list[DiagramEdge]) -> list[DiagramEdge]:
    """Remove edges that reference node IDs not present in the node list."""
    node_ids = {n.id for n in nodes}
    return [e for e in edges if e.source in node_ids and e.target in node_ids]


# ---------------------------------------------------------------------------
# Ground-truth diagram builder — built directly from the Knowledge Graph
# traversal, not from anything the LLM wrote. See BlueprintFactory's
# `from_planning_result` docstring for why this exists and is ordered first.
# ---------------------------------------------------------------------------


def _build_grounded_architecture(
    components: list[dict[str, Any]],
    repository_name: str,
    verified_names: set[str],
    verified_file_paths: set[str],
) -> Diagram | None:
    """The real package/file layout of the top-matched repository, built
    straight from this run's own `traverse_architecture_graph` tool result —
    no LLM involved, no architecture template.

    `_build_solution_architecture` below draws whatever layers the LLM
    wrote into `architecture_layers` — usually a plausible generic ETL/API
    shape, since that field is guided by a fixed pattern template
    (`app.agents.planning.classifier._PATTERNS`) rather than the indexed
    code. That diagram is worth keeping for the ~30-40% of a plan that is
    genuinely new work with nothing to point at yet, but it is a narrative,
    not a fact, and presenting it first invites reading it as one.

    This diagram only contains what the Knowledge Graph actually returned
    for `repository_name` this run: every directory node is a real folder,
    every file node is a real indexed file. Nodes are marked `has_affected`
    only when they matched a claim `app.agents.verification.verify_claims`
    already confirmed against this run's own evidence — so a reader can
    tell, from the diagram alone, which part of the plan is grounded.
    """
    by_repo = [
        c for c in components if c.get("repository") == repository_name and c.get("file_path")
    ]
    if not by_repo:
        return None

    def _is_affected(c: dict[str, Any]) -> bool:
        name = c.get("name", "")
        path = c.get("file_path", "")
        short_name = name.rsplit(".", 1)[-1] if name else ""
        if name in verified_names or short_name in verified_names:
            return True
        return any(
            path == p or path.endswith(f"/{p}") or p.endswith(f"/{path}")
            for p in verified_file_paths
            if p
        )

    by_dir: dict[str, list[dict[str, Any]]] = {}
    for c in by_repo:
        file_path = c["file_path"]
        dir_path = file_path.rsplit("/", 1)[0] if "/" in file_path else "(root)"
        by_dir.setdefault(dir_path, []).append(c)

    nodes = [
        DiagramNode(
            id="repo_root",
            label=repository_name,
            type="input",
            properties={"affected_component": f"{len(by_repo)} indexed component(s)"},
            metadata={"kind": "repository"},
        )
    ]
    edges: list[DiagramEdge] = []
    seen_files: set[str] = set()

    for dir_path, comps_in_dir in sorted(by_dir.items()):
        has_affected = any(_is_affected(c) for c in comps_in_dir)
        dir_id = f"dir_{_slug(dir_path)}"
        detail = f"{len(comps_in_dir)} component(s) indexed here"
        if has_affected:
            detail += " — contains affected file(s) below"
        nodes.append(
            DiagramNode(
                id=dir_id,
                label=dir_path,
                # A directory is a structural container, not a risk — it was
                # previously typed "risk" (orange/red, the same styling a
                # genuinely risky *file* node gets) merely for holding one,
                # which mislabels plain folders like `scripts` or
                # `tests/unittest` as themselves risky. The actual affected
                # *file* nodes below (type="risk") already carry that
                # signal; a directory just needs to say it contains one.
                type="component",
                properties={"affected_component": detail},
                metadata={"kind": "directory", "has_affected": has_affected},
            )
        )
        edges.append(DiagramEdge(id=f"root_to_{dir_id}", source="repo_root", target=dir_id))

        for c in comps_in_dir:
            if not _is_affected(c):
                continue
            file_path = c["file_path"]
            if file_path in seen_files:
                continue
            seen_files.add(file_path)
            file_id = f"file_{_slug(file_path)}"
            nodes.append(
                DiagramNode(
                    id=file_id,
                    label=file_path.rsplit("/", 1)[-1],
                    type="risk",
                    properties={"affected_component": c.get("name", "")},
                    metadata={
                        "kind": "file",
                        "file_path": file_path,
                        "component_type": c.get("type", ""),
                    },
                )
            )
            edges.append(
                DiagramEdge(id=f"{dir_id}_to_{file_id}", source=dir_id, target=file_id, type="risk")
            )

    if len(nodes) <= 1:
        return None

    edges = _validate_edges(nodes, edges)
    return Diagram(
        id="grounded_architecture",
        title="Indexed Codebase Structure (Ground Truth)",
        description=(
            f"The real, indexed package layout of {repository_name} — every node here "
            "exists in the Knowledge Graph right now. Highlighted nodes are the files "
            "this plan's specific claims were verified against."
        ),
        type=DiagramType.DEPENDENCY,
        nodes=nodes,
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={
            "section": "Architecture",
            "why": (
                "Grounds the plan in the repository as actually indexed, not a generated template"
            ),
            "repository": repository_name,
            "grounded": True,
        },
    )


# ---------------------------------------------------------------------------
# New Principal Architect-level diagram builders
# ---------------------------------------------------------------------------


def _build_solution_architecture(layers: list[dict[str, Any]]) -> Diagram | None:
    """Hero diagram: conceptual solution layers from source to consumption."""
    if not layers:
        return None
    expanded = _expand_arch_layers(layers, min_count=3)
    sorted_layers = sorted(expanded, key=lambda layer: int(layer.get("order", 0)))
    nodes = [
        DiagramNode(
            id=f"layer_{i}",
            label=layer["name"],
            type=_LAYER_TYPE_TO_NODE_TYPE.get(layer.get("layer_type", "processing"), "component"),
            properties={"affected_component": layer.get("description", "")},
            metadata={
                "layer_type": layer.get("layer_type", "processing"),
                "order": layer.get("order", i + 1),
            },
        )
        for i, layer in enumerate(sorted_layers)
        if layer.get("name")
    ]
    if not nodes:
        return None
    edges = _validate_edges(
        nodes,
        [
            DiagramEdge(id=f"layer_{i}_to_{i + 1}", source=f"layer_{i}", target=f"layer_{i + 1}")
            for i in range(len(nodes) - 1)
        ],
    )
    return Diagram(
        id="solution_architecture",
        title="Solution Architecture (Conceptual)",
        description=(
            "The LLM's proposed high-level layers for this brief — a narrative shape, "
            "not indexed code. See 'Indexed Codebase Structure' for what actually exists."
        ),
        type=DiagramType.ARCHITECTURE,
        nodes=nodes,
        edges=edges,
        layout=DiagramLayout(direction="TB"),
        metadata={
            "section": "Architecture",
            "why": "Shows the conceptual solution layers from data source to consumption",
            "layer_count": len(nodes),
            "grounded": False,
        },
    )


def _build_data_flow(steps: list[dict[str, Any]]) -> Diagram | None:
    """End-to-end operational flow: how data moves through the system."""
    if not steps:
        return None
    expanded = _expand_flow_stages(steps, min_count=4)
    sorted_steps = sorted(expanded, key=lambda s: int(s.get("order", 0)))
    nodes = [
        DiagramNode(
            id=f"flow_{i}",
            label=s["name"],
            type=_FLOW_STEP_TYPE_TO_NODE_TYPE.get(s.get("step_type", "process"), "component"),
            properties={"affected_component": s.get("technology", "")},
            metadata={"step_type": s.get("step_type", "process"), "order": s.get("order", i + 1)},
        )
        for i, s in enumerate(sorted_steps)
        if s.get("name")
    ]
    if len(nodes) < 2:
        return None
    edges = _validate_edges(
        nodes,
        [
            DiagramEdge(
                id=f"flow_{i}_to_{i + 1}",
                source=f"flow_{i}",
                target=f"flow_{i + 1}",
                type="data_flow",
            )
            for i in range(len(nodes) - 1)
        ],
    )
    return Diagram(
        id="data_flow",
        title="End-to-End Data Flow",
        description="Operational data movement from ingestion to consumption",
        type=DiagramType.FLOW,
        nodes=nodes,
        edges=edges,
        layout=DiagramLayout(direction="TB"),
        metadata={
            "section": "Data Flow",
            "why": "Shows how data moves through the system operationally, step by step",
        },
    )


def _build_repository_reuse(repo_usage: list[dict[str, Any]]) -> Diagram | None:
    """Repository Reuse Blueprint: existing repos + WHY each is recommended."""
    if not repo_usage:
        return None
    valid = [r for r in repo_usage if r.get("name")]
    if not valid:
        return None

    # Filter out repos with no meaningful data — name alone is not sufficient
    # to justify a diagram node (e.g. {"name": "foundation"} with no components
    # or reason produces a "New System → foundation" diagram that communicates nothing).
    meaningful = [
        r
        for r in valid
        if r.get("reusable_components")
        or r.get("reason")
        or int(r.get("estimated_reuse_pct", 0) or 0) > 0
    ]
    if not meaningful:
        return None
    valid = meaningful

    repo_nodes = []
    for r in valid:
        reuse_pct = int(r.get("estimated_reuse_pct", 0) or 0)
        components = r.get("reusable_components", [])
        files_affected = r.get("files_affected", [])
        # `verified` (set deterministically in agent.py's verification
        # block, never LLM-free-generated — see RepositoryUsage.verified)
        # used to reach this diagram nowhere at all: a repository whose
        # every files_affected claim was fabricated rendered identically to
        # one that checked out, distinguished only by the star count, which
        # measures ranking relevance, not evidence. `verify_note` is folded
        # into the same sub-line the frontend already renders
        # (`properties.affected_component`) rather than a new property key,
        # since this diagram has no per-node detail panel for a new field
        # to appear in — a reader only ever sees what's on the node itself.
        verified = bool(r.get("verified", False))
        if verified:
            verify_note = "✓ verified"
        elif files_affected:
            verify_note = f"⚠ unverified — {len(files_affected)} file(s) unconfirmed"
        else:
            verify_note = "⚠ unverified"
        # The sub-label carries the reuse decision at a glance: what we get
        # back and how much of it. Falls back to the component list alone for
        # v2 results that predate estimated_reuse_pct.
        detail = ", ".join(components)[:70]
        if reuse_pct > 0:
            detail = f"{reuse_pct}% reuse · {detail}" if detail else f"{reuse_pct}% reuse"
        detail = f"{detail} — {verify_note}" if detail else verify_note
        repo_nodes.append(
            DiagramNode(
                id=f"repo_{_slug(r['name'])}",
                label=f"{_stars(int(r.get('stars', 3)))} {r['name']}",
                type="input",
                properties={"affected_component": detail},
                metadata={
                    "stars": r.get("stars", 3),
                    "purpose": r.get("purpose", ""),
                    "reason": r.get("reason", ""),
                    "relationship": r.get("relationship", "reuse"),
                    "estimated_reuse_pct": reuse_pct,
                    "confidence": r.get("confidence", "medium"),
                    "reusable_components": components,
                    "files_affected": files_affected,
                    "alternatives": r.get("alternatives", []),
                    "verified": verified,
                },
            )
        )
    system_node = DiagramNode(id="new_system", label="New System", type="output")
    edges = [
        DiagramEdge(
            id=f"repo_{_slug(r['name'])}_provides",
            source=f"repo_{_slug(r['name'])}",
            target="new_system",
            label=r.get("relationship", "reuse"),
        )
        for r in valid
    ]
    return Diagram(
        id="repository_reuse",
        title="Repository Reuse Blueprint",
        description=(
            "Existing repositories contributing to this solution — the GraphForge advantage"
        ),
        type=DiagramType.DEPENDENCY,
        nodes=[*repo_nodes, system_node],
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={
            "section": "Repository Analysis",
            "why": (
                "Shows which repositories are recommended and exactly why — "
                "grounded in real graph analysis"
            ),
            "repo_count": len(valid),
        },
    )


def _build_data_model(entities: list[dict[str, Any]]) -> Diagram | None:
    """ER diagram: business domain entities and their relationships."""
    if not entities:
        return None
    valid = [e for e in entities if e.get("name")]
    if not valid:
        return None

    entity_ids: dict[str, str] = {e["name"]: f"entity_{_slug(e['name'])}" for e in valid}
    nodes: list[DiagramNode] = [
        DiagramNode(
            id=entity_ids[e["name"]],
            label=e["name"],
            type="entity",
            properties={
                "affected_component": ", ".join(e.get("key_attributes", [])[:4]),
            },
            metadata={"attributes": e.get("key_attributes", [])},
        )
        for e in valid
    ]
    edges: list[DiagramEdge] = []
    seen_edge_ids: set[str] = set()
    for e in valid:
        source_id = entity_ids[e["name"]]
        for rel_str in e.get("relationships", []):
            parts = rel_str.strip().split(None, 1)
            if len(parts) < 2:
                continue
            verb, target_name = parts[0], parts[1]
            target_id = entity_ids.get(target_name)
            if not target_id:
                # Auto-create any entity referenced in a relationship but absent
                # from the LLM-provided list — prevents dangling relationships
                # that would leave the source entity as an isolated island.
                #
                # These are ghost nodes: `target_name` is copied verbatim from
                # a free-text relationship string ("has_many OrderItems"), so
                # it's whatever the LLM happened to type there — no key
                # attributes, no independent declaration, sometimes not even
                # the same name as the concept it's standing in for (a real
                # run produced entities like "ManifestEntries" this way,
                # never once declared as an actual entity). `synthesized:
                # True` was already recorded in metadata but nothing on the
                # frontend reads node metadata for this diagram type, so
                # every ghost rendered pixel-identical to a real declared
                # entity. Marking it in the label and the always-rendered
                # `affected_component` sub-line is what actually makes it
                # visible without needing a detail panel this diagram
                # doesn't have.
                new_id = f"entity_{_slug(target_name)}"
                if not _slug(target_name):
                    continue
                entity_ids[target_name] = new_id
                nodes.append(
                    DiagramNode(
                        id=new_id,
                        label=f"{target_name} (inferred)",
                        type="entity",
                        properties={
                            "affected_component": (
                                "Not declared as an entity — inferred from a "
                                "relationship reference only"
                            )
                        },
                        metadata={"attributes": [], "synthesized": True},
                    )
                )
                target_id = new_id
            edge_id = f"{source_id}__{target_id}__{_slug(verb)}"
            if edge_id in seen_edge_ids:
                continue
            seen_edge_ids.add(edge_id)
            edges.append(
                DiagramEdge(
                    id=edge_id,
                    source=source_id,
                    target=target_id,
                    label=verb.replace("_", " "),
                )
            )
    # A single isolated entity with no edges communicates nothing — skip it.
    if len(nodes) < 2 or not edges:
        return None
    return Diagram(
        id="data_model",
        title="Data Model",
        description="Business entities and their domain relationships",
        type=DiagramType.ER,
        nodes=nodes,
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={
            "section": "Data Model",
            "why": "Domain model — the business entities this system creates, manages, and queries",
            "entity_count": len(nodes),
        },
    )


def _build_implementation_roadmap(phases: list[dict[str, Any]]) -> Diagram | None:
    """Timeline: engineering phases with expandable deliverables."""
    if not phases:
        return None
    valid = [p for p in phases if p.get("name")]
    if not valid:
        return None
    sorted_phases = sorted(valid, key=lambda p: int(p.get("order", 0)))
    nodes = [
        DiagramNode(
            id=f"phase_{p.get('order', i + 1)}",
            label=p["name"],
            type="phase",
            properties={
                "steps": [_truncate_at_word(d, 80) for d in p.get("deliverables", [])],
                "description": f"{len(p.get('deliverables', []))} deliverable(s)",
            },
        )
        for i, p in enumerate(sorted_phases)
    ]
    edges = [
        DiagramEdge(
            id=(
                f"phase_{sorted_phases[i].get('order', i + 1)}"
                f"_to_{sorted_phases[i + 1].get('order', i + 2)}"
            ),
            source=f"phase_{sorted_phases[i].get('order', i + 1)}",
            target=f"phase_{sorted_phases[i + 1].get('order', i + 2)}",
        )
        for i in range(len(sorted_phases) - 1)
    ]
    return Diagram(
        id="implementation_roadmap",
        title="Implementation Roadmap",
        description=(
            "Engineering phases from foundation to production — "
            "click a phase to expand deliverables"
        ),
        type=DiagramType.TIMELINE,
        nodes=nodes,
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={
            "section": "Implementation Plan",
            "why": "Engineering phases from foundation through to production deployment",
            "total_phases": len(nodes),
        },
    )


def _build_risk_matrix(risks: list[dict[str, Any]]) -> Diagram | None:
    """Risk & Complexity Matrix: structured risks with likelihood/impact/mitigation."""
    if not risks:
        return None
    valid = [r for r in risks if r.get("description")]
    if not valid:
        return None

    nodes: list[DiagramNode] = []
    for i, r in enumerate(valid):
        likelihood = r.get("likelihood", "medium")
        impact = r.get("impact", "medium")
        mitigation = r.get("mitigation", "")
        severity = _risk_severity(likelihood, impact)
        category = str(r.get("category", "") or "")
        evidence = r.get("evidence", "")
        # The label is the risk statement alone — category-prefixed so it
        # reads as classified rather than generic. Likelihood, impact,
        # mitigation, and evidence used to be flattened into this same
        # string with " — " separators, producing one run-on paragraph per
        # risk with no visual distinction between what happened, how
        # likely it is, and what to do about it — the opposite of a
        # matrix. Those fields already exist as their own `metadata`
        # entries below; RiskHeatmapRenderer renders them as separate
        # structured rows instead of re-flattening them here.
        label = (
            f"[{category.replace('_', ' ')}] {r['description']}" if category else r["description"]
        )
        nodes.append(
            DiagramNode(
                id=f"risk_{i}",
                label=_truncate_at_word(label, 140),
                type="risk",
                metadata={
                    "severity": severity,
                    "likelihood": likelihood,
                    "impact": impact,
                    "category": category,
                    "mitigation": mitigation,
                    "evidence": evidence,
                    "confidence": r.get("confidence", "medium"),
                },
            )
        )

    nodes_sorted = sorted(
        nodes,
        key=lambda n: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
            str(n.metadata.get("severity", "low")), 3
        ),
    )
    return Diagram(
        id="risk_matrix",
        title="Risk & Complexity Matrix",
        description=(
            "Architectural risks assessed by likelihood, impact, and mitigation readiness"
        ),
        type=DiagramType.RISK_HEATMAP,
        nodes=nodes_sorted,
        edges=[],
        layout=DiagramLayout(direction="TB", algorithm="grid"),
        metadata={
            "section": "Risks",
            "why": (
                "Surfaces architectural risks with evidence-backed mitigations "
                "before implementation begins"
            ),
            "risk_count": len(valid),
        },
    )


# ---------------------------------------------------------------------------
# Fallback builders — used when new LLM fields are absent
# (keeps old Planning Blueprint behaviour for legacy stored results)
# ---------------------------------------------------------------------------


def _build_implementation_flow(steps: list[dict[str, Any]]) -> Diagram:
    """Ordered linear flow of implementation steps (top-to-bottom)."""
    nodes = [
        DiagramNode(
            id=f"step_{s['order']}",
            label=_truncate_at_word(s["description"], 70),
            type=(
                "default"
                if 0 < s["order"] < len(steps)
                else ("input" if s["order"] == 1 else "output")
            ),
            properties={
                "order": s["order"],
                "affected_component": s.get("affected_component", ""),
                "risk_note": s.get("risk_note", ""),
            },
        )
        for s in steps
    ]
    edges = [
        DiagramEdge(
            id=f"step_{steps[i]['order']}_to_{steps[i + 1]['order']}",
            source=f"step_{steps[i]['order']}",
            target=f"step_{steps[i + 1]['order']}",
        )
        for i in range(len(steps) - 1)
    ]
    return Diagram(
        id="implementation_flow",
        title="Implementation Flow",
        description="Ordered implementation steps",
        type=DiagramType.FLOW,
        nodes=nodes,
        edges=edges,
        layout=DiagramLayout(direction="TB"),
    )


def _build_repository_impact(repositories: list[str], components: list[str]) -> Diagram:
    """Dependency diagram: repositories and their affected components."""
    repo_nodes = [DiagramNode(id=f"repo_{_slug(r)}", label=r, type="input") for r in repositories]
    comp_nodes = [
        DiagramNode(id=f"comp_{_slug(c)}", label=c, type="component") for c in components[:8]
    ]
    edges: list[DiagramEdge] = []
    if repositories and components:
        for i, r in enumerate(repositories):
            n = max(1, len(components) // len(repositories))
            for c in components[i * n : (i + 1) * n]:
                edges.append(
                    DiagramEdge(
                        id=f"repo_{_slug(r)}_has_{_slug(c)}",
                        source=f"repo_{_slug(r)}",
                        target=f"comp_{_slug(c)}",
                        label="contains",
                        type="dependency",
                    )
                )
    return Diagram(
        id="repository_impact",
        title="Repository Impact",
        description="Repositories and the components they contribute to this change",
        type=DiagramType.DEPENDENCY,
        nodes=[*repo_nodes, *comp_nodes],
        edges=edges,
        layout=DiagramLayout(direction="LR"),
    )


def _build_kafka_flow(topics: list[str], components: list[str]) -> Diagram:
    """Messaging flow: components and the Kafka topics they interact with."""
    topic_nodes = [DiagramNode(id=f"topic_{_slug(t)}", label=t, type="topic") for t in topics[:6]]
    comp_nodes = [
        DiagramNode(id=f"comp_{_slug(c)}", label=c, type="component") for c in components[:6]
    ]
    edges = [
        DiagramEdge(
            id=f"{_slug(c)}_to_{_slug(t)}",
            source=f"comp_{_slug(c)}",
            target=f"topic_{_slug(t)}",
            label="interacts",
            type="data_flow",
        )
        for c in components[:4]
        for t in topics[:3]
    ]
    return Diagram(
        id="kafka_flow",
        title="Messaging Flow",
        description="Kafka topics involved in this change",
        type=DiagramType.DEPENDENCY,
        nodes=[*comp_nodes, *topic_nodes],
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={
            "note": (
                "Edge direction is placeholder until Development stage produces "
                "explicit producer/consumer mappings."
            )
        },
    )


def _build_risk_heatmap(risks: list[str]) -> Diagram:
    """Risk heatmap: one node per risk string, styled by heuristic severity."""
    nodes = [
        DiagramNode(
            id=f"risk_{i}",
            label=_truncate_at_word(risk, 100),
            type="risk",
            metadata={"severity": _risk_level(risk), "index": i},
        )
        for i, risk in enumerate(risks)
    ]
    return Diagram(
        id="risk_heatmap",
        title="Risk Assessment",
        description="Risk considerations identified during planning, ordered by severity",
        type=DiagramType.RISK_HEATMAP,
        nodes=sorted(
            nodes,
            key=lambda n: {"critical": 0, "high": 1, "medium": 2, "low": 3}[n.metadata["severity"]],
        ),
        edges=[],
        layout=DiagramLayout(direction="TB", algorithm="grid"),
        metadata={"risk_count": len(risks)},
    )


def _build_implementation_timeline(steps: list[dict[str, Any]]) -> Diagram:
    """Horizontal timeline grouping implementation steps into phases."""
    PHASE_SIZE = 3
    phases = []
    for i in range(0, len(steps), PHASE_SIZE):
        chunk = steps[i : i + PHASE_SIZE]
        order = i // PHASE_SIZE + 1
        phases.append(
            DiagramNode(
                id=f"phase_{order}",
                label=f"Phase {order}",
                type="phase",
                properties={
                    "steps": [_truncate_at_word(s.get("description", ""), 60) for s in chunk],
                    "components": list(
                        {
                            s.get("affected_component", "")
                            for s in chunk
                            if s.get("affected_component")
                        }
                    ),
                },
            )
        )
    edges = [
        DiagramEdge(
            id=f"phase_{i + 1}_to_{i + 2}", source=f"phase_{i + 1}", target=f"phase_{i + 2}"
        )
        for i in range(len(phases) - 1)
    ]
    return Diagram(
        id="implementation_timeline",
        title="Implementation Timeline",
        description="Implementation phases derived from planning steps",
        type=DiagramType.TIMELINE,
        nodes=phases,
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={"total_steps": len(steps), "phase_count": len(phases)},
    )


# ---------------------------------------------------------------------------
# Development Agent diagram builders
# ---------------------------------------------------------------------------

_COMP_TYPE_TO_LAYER: dict[str, str] = {
    "controller": "API Layer",
    "restcontroller": "API Layer",
    "feignclient": "Integration",
    "service": "Service Layer",
    "serviceimpl": "Service Layer",
    "repository": "Data Layer",
    "entity": "Data Layer",
    "listener": "Integration",
    "producer": "Integration",
    "consumer": "Integration",
    "config": "Infrastructure",
    "configuration": "Infrastructure",
}


def _infer_layer(component_type: str) -> str:
    return _COMP_TYPE_TO_LAYER.get(component_type.lower().replace(" ", ""), "Service Layer")


def _build_dev_overview(
    repos: list[dict[str, Any]], components: list[dict[str, Any]]
) -> Diagram | None:
    """What is changing: repositories and component types affected."""
    if not repos and not components:
        return None

    # Infer unique layers from component types
    layers: dict[str, list[str]] = {}
    for c in components[:12]:
        layer = _infer_layer(c.get("component_type", ""))
        layers.setdefault(layer, []).append(c.get("name", ""))

    repo_nodes = [
        DiagramNode(
            id=f"repo_{_slug(r['name'])}",
            label=r["name"],
            type="input",
            properties={"affected_component": _truncate_at_word(r.get("reason", ""), 60)},
        )
        for r in repos[:8]
        if r.get("name")
    ]
    layer_nodes = [
        DiagramNode(
            id=f"layer_{_slug(layer)}",
            label=layer,
            type="component",
            properties={"affected_component": ", ".join(comps[:3])},
        )
        for layer, comps in layers.items()
    ]
    impl_node = DiagramNode(id="implementation", label="Implementation", type="output")

    edges: list[DiagramEdge] = []
    # repo → layer (if component repo matches)
    for r in repos[:8]:
        if not r.get("name"):
            continue
        r_id = f"repo_{_slug(r['name'])}"
        added_layers: set[str] = set()
        for c in components:
            if c.get("repository", "") == r.get("name", ""):
                lyr = _infer_layer(c.get("component_type", ""))
                lyr_id = f"layer_{_slug(lyr)}"
                if lyr_id not in added_layers and lyr in layers:
                    edges.append(
                        DiagramEdge(
                            id=f"{r_id}_to_{lyr_id}", source=r_id, target=lyr_id, label="changes"
                        )
                    )
                    added_layers.add(lyr_id)

    # layer → implementation
    for layer in layers:
        lyr_id = f"layer_{_slug(layer)}"
        edges.append(
            DiagramEdge(
                id=f"{lyr_id}_to_impl", source=lyr_id, target="implementation", label="impacts"
            )
        )

    return Diagram(
        id="dev_overview",
        title="Implementation Overview",
        description="Repositories and code layers affected by this change",
        type=DiagramType.ARCHITECTURE,
        nodes=[*repo_nodes, *layer_nodes, impl_node],
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={
            "section": "Implementation Overview",
            "why": (
                "Shows what is changing — which repositories and architectural layers are involved"
            ),
        },
    )


def _build_file_modification_map(components: list[dict[str, Any]]) -> Diagram | None:
    """Repo → component map showing what files/classes change where."""
    if not components:
        return None

    repos: dict[str, list[dict[str, Any]]] = {}
    for c in components:
        repo = c.get("repository", "Unknown")
        repos.setdefault(repo, []).append(c)

    nodes: list[DiagramNode] = []
    edges: list[DiagramEdge] = []

    for repo, comps in repos.items():
        repo_id = f"repo_{_slug(repo)}"
        nodes.append(DiagramNode(id=repo_id, label=repo, type="input"))
        for c in comps[:6]:
            comp_id = f"comp_{_slug(c.get('name', ''))}_{_slug(repo)}"
            subtitle = c.get("file_path", "") or c.get("component_type", "")
            nodes.append(
                DiagramNode(
                    id=comp_id,
                    label=c.get("name", "")[:40],
                    type="component",
                    properties={"affected_component": subtitle[:60] if subtitle else ""},
                )
            )
            edges.append(
                DiagramEdge(
                    id=f"{repo_id}_to_{comp_id}",
                    source=repo_id,
                    target=comp_id,
                    label=c.get("component_type", "")[:20],
                )
            )

    if not nodes:
        return None

    return Diagram(
        id="file_modification_map",
        title="File Modification Map",
        description="Components changing, grouped by repository",
        type=DiagramType.DEPENDENCY,
        nodes=nodes,
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={
            "section": "File Modifications",
            "why": "Shows exactly which files/classes change and in which repositories",
        },
    )


def _build_component_dependency_graph(dependencies: list[dict[str, Any]]) -> Diagram | None:
    """Component dependency graph from the graph traversal results."""
    if not dependencies:
        return None

    # Collect unique component names
    comp_names: set[str] = set()
    for d in dependencies:
        if d.get("source"):
            comp_names.add(d["source"])
        if d.get("target"):
            comp_names.add(d["target"])

    if not comp_names:
        return None

    nodes = [
        DiagramNode(id=f"dep_{_slug(name)}", label=name[:40], type="component")
        for name in comp_names
    ]
    edges = [
        DiagramEdge(
            id=f"dep_{_slug(d.get('source', ''))}_to_{_slug(d.get('target', ''))}",
            source=f"dep_{_slug(d['source'])}",
            target=f"dep_{_slug(d['target'])}",
            label=d.get("relationship", "")[:20],
            type="dependency",
        )
        for d in dependencies
        if d.get("source") and d.get("target")
    ]

    return Diagram(
        id="component_dependency_graph",
        title="Component Dependency Graph",
        description="How affected components depend on each other",
        type=DiagramType.DEPENDENCY,
        nodes=nodes,
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={
            "section": "Component Dependencies",
            "why": "Shows cross-component coupling that must be managed during implementation",
        },
    )


def _build_dev_implementation_flow(phases: list[dict[str, Any]]) -> Diagram | None:
    """Implementation phases as a clickable timeline."""
    if not phases:
        return None
    sorted_phases = sorted(phases, key=lambda p: int(p.get("order", 0)))
    nodes = [
        DiagramNode(
            id=f"devphase_{p.get('order', i + 1)}",
            label=p.get("title", f"Phase {i + 1}")[:40],
            type="phase",
            properties={
                "steps": p.get("affected_components", [])[:5],
                "description": _truncate_at_word(p.get("description", "") or "", 60),
            },
            metadata={"complexity": p.get("estimated_complexity", "")},
        )
        for i, p in enumerate(sorted_phases)
        if p.get("title")
    ]
    if not nodes:
        return None
    edges = [
        DiagramEdge(
            id=(
                f"devphase_{sorted_phases[i].get('order', i + 1)}"
                f"_to_{sorted_phases[i + 1].get('order', i + 2)}"
            ),
            source=f"devphase_{sorted_phases[i].get('order', i + 1)}",
            target=f"devphase_{sorted_phases[i + 1].get('order', i + 2)}",
        )
        for i in range(len(sorted_phases) - 1)
    ]
    return Diagram(
        id="dev_implementation_flow",
        title="Implementation Flow",
        description="Engineering phases — click each to see affected components",
        type=DiagramType.TIMELINE,
        nodes=nodes,
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={
            "section": "Implementation Flow",
            "why": "Engineering phases from planning through to deployment",
        },
    )


def _build_code_impact(components: list[dict[str, Any]]) -> Diagram | None:
    """Impact across architectural layers: API, Service, Data, Integration."""
    if not components:
        return None

    layers: dict[str, list[str]] = {}
    for c in components:
        layer = _infer_layer(c.get("component_type", ""))
        layers.setdefault(layer, []).append(c.get("name", ""))

    system_node = DiagramNode(id="system", label="System Impact", type="output")
    layer_nodes = [
        DiagramNode(
            id=f"impact_{_slug(layer)}",
            label=layer,
            type="input",
            properties={
                "affected_component": f"{len(comps)} component(s): " + ", ".join(comps[:2])
            },
        )
        for layer, comps in layers.items()
    ]
    if not layer_nodes:
        return None
    edges = [
        DiagramEdge(
            id=f"impact_{_slug(layer)}_to_system",
            source=f"impact_{_slug(layer)}",
            target="system",
            label=f"{len(comps)} change(s)",
        )
        for layer, comps in layers.items()
    ]
    return Diagram(
        id="code_impact",
        title="Code Impact",
        description="Impact across architectural layers",
        type=DiagramType.ARCHITECTURE,
        nodes=[*layer_nodes, system_node],
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={
            "section": "Code Impact",
            "why": "Shows which architectural layers are touched and how much",
        },
    )


def _build_test_strategy(
    components: list[dict[str, Any]], dependencies: list[dict[str, Any]]
) -> Diagram | None:
    """Test strategy derived from component types and dependency complexity."""
    if not components:
        return None

    unit_targets = [
        c.get("name", "")
        for c in components
        if _infer_layer(c.get("component_type", "")) == "Service Layer"
    ]
    integration_targets = [
        c.get("name", "")
        for c in components
        if _infer_layer(c.get("component_type", "")) in ("API Layer", "Integration")
    ]
    has_cross_repo = any(d.get("risk_note") for d in dependencies)

    phases: list[dict[str, Any]] = []
    if unit_targets:
        phases.append(
            {
                "name": "Unit Tests",
                "steps": unit_targets[:5],
                "description": f"{len(unit_targets)} service(s)",
            }
        )
    if integration_targets:
        phases.append(
            {
                "name": "Integration Tests",
                "steps": integration_targets[:5],
                "description": f"{len(integration_targets)} endpoint(s)",
            }
        )
    if has_cross_repo:
        cross = [
            f"{d.get('source', '')} → {d.get('target', '')}"
            for d in dependencies
            if d.get("risk_note")
        ]
        phases.append(
            {
                "name": "Contract Tests",
                "steps": cross[:4],
                "description": f"{len(cross)} cross-service flow(s)",
            }
        )
    phases.append(
        {
            "name": "Regression Tests",
            "steps": [c.get("name", "") for c in components[:5]],
            "description": f"All {len(components)} changed component(s)",
        }
    )

    nodes = [
        DiagramNode(
            id=f"test_{i}",
            label=p["name"],
            type="phase",
            properties={"steps": p["steps"], "description": p["description"]},
        )
        for i, p in enumerate(phases)
    ]
    edges = [
        DiagramEdge(id=f"test_{i}_to_{i + 1}", source=f"test_{i}", target=f"test_{i + 1}")
        for i in range(len(nodes) - 1)
    ]
    return Diagram(
        id="test_strategy",
        title="Test Strategy",
        description=(
            "Testing approach across unit, integration, and regression layers — "
            "click a phase to expand"
        ),
        type=DiagramType.TIMELINE,
        nodes=nodes,
        edges=edges,
        layout=DiagramLayout(direction="LR"),
        metadata={
            "section": "Test Strategy",
            "why": (
                "Testing approach derived from the component types and "
                "cross-service dependencies involved"
            ),
        },
    )


def _build_dev_risk_matrix(risks: list[dict[str, Any]]) -> Diagram | None:
    """Risk matrix from Development Agent's Risk objects."""
    if not risks:
        return None
    valid = [r for r in risks if r.get("description")]
    if not valid:
        return None

    nodes: list[DiagramNode] = []
    for i, r in enumerate(valid):
        severity = r.get("severity", "medium").lower()
        if severity not in ("critical", "high", "medium", "low"):
            severity = _risk_level(r["description"])
        mitigation = r.get("mitigation", "")
        label_parts = [r["description"]]
        if r.get("affected_component"):
            label_parts.append(f"Affects: {r['affected_component']}")
        if mitigation:
            label_parts.append(f"Mitigation: {mitigation}")
        nodes.append(
            DiagramNode(
                id=f"devrisk_{i}",
                label=" — ".join(label_parts),
                type="risk",
                metadata={"severity": severity, "mitigation": mitigation},
            )
        )

    nodes_sorted = sorted(
        nodes,
        key=lambda n: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
            str(n.metadata.get("severity", "low")), 3
        ),
    )
    return Diagram(
        id="dev_risk_matrix",
        title="Implementation Risks",
        description="Technical risks identified during change analysis",
        type=DiagramType.RISK_HEATMAP,
        nodes=nodes_sorted,
        edges=[],
        layout=DiagramLayout(direction="TB", algorithm="grid"),
        metadata={
            "section": "Implementation Risks",
            "why": (
                "Technical debt, breaking changes, and migration risks surfaced before "
                "implementation"
            ),
        },
    )


def _require_grounded_flag(diagrams: list[Diagram]) -> list[Diagram]:
    """Every diagram must carry an explicit `grounded` bool in its
    metadata, so the frontend can chip a diagram as "illustrative" without
    depending on each of the ~20 builder functions above remembering to
    set it themselves.

    Only two builders (`_build_grounded_architecture`,
    `_build_solution_architecture`) ever set this explicitly; the rest —
    including "End-to-End Data Flow" and "Data Model", which are 100%
    LLM narrative with no graph traversal behind them at all — silently
    carried no flag, which the frontend would have had to treat as
    "ungrounded" anyway (`Boolean(undefined)` is falsy) but with no
    record of that being deliberate rather than an oversight in whichever
    builder produced it. This backstop makes the absence explicit and
    fails closed: any diagram a builder doesn't positively mark `True`
    reads as `False`, never as unmarked-therefore-trustworthy.
    """
    for d in diagrams:
        d.metadata.setdefault("grounded", False)
    return diagrams


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


class BlueprintFactory:
    """Converts structured agent output into a BlueprintArtifact.

    Each static method corresponds to one agent stage. Future agents add a
    method here; nothing else in the framework changes.

    Planning blueprint strategy (v2):
    - Prefer new architect-level fields (architecture_layers, data_flow, etc.)
      produced by the updated Planning prompt — these generate Principal
      Architect-style diagrams.
    - Fall back to legacy builders from existing fields so old stored results
      and test fixtures continue to produce diagrams without a re-run.
    """

    @staticmethod
    def from_planning_result(
        result: PlanningResult,
        graph_components: list[dict[str, Any]] | None = None,
        top_repository: str | None = None,
        verified_affected_names: list[str] | None = None,
        verified_file_paths: list[str] | None = None,
    ) -> BlueprintArtifact:
        diagrams: list[Diagram] = []
        steps_raw = [s.model_dump() for s in result.implementation_steps]

        # 0. Indexed Codebase Structure — ground truth, built directly from
        # this run's own graph traversal (see `_build_grounded_architecture`).
        # Ordered first, deliberately: this is what the hackathon's visual
        # focus is about — leading with the LLM's conceptual architecture
        # invites reading a narrative as a fact. Silently skipped (returns
        # None) whenever no repository was actually matched/traversed, so
        # callers that don't pass graph data get the same output as before.
        if graph_components and top_repository:
            grounded = _build_grounded_architecture(
                graph_components,
                top_repository,
                set(verified_affected_names or []),
                set(verified_file_paths or []),
            )
            if grounded:
                diagrams.append(grounded)

        # 1. Solution Architecture (hero diagram — "what are we building?")
        arch_layers_raw = [layer.model_dump() for layer in result.architecture_layers]
        if arch_layers_raw:
            d = _build_solution_architecture(arch_layers_raw)
            if d:
                diagrams.append(d)

        # 2. End-to-End Data Flow
        data_flow_raw = [s.model_dump() for s in result.data_flow]
        if data_flow_raw:
            d = _build_data_flow(data_flow_raw)
            if d:
                diagrams.append(d)
        elif result.kafka_topics_involved and result.affected_components:
            diagrams.append(
                _build_kafka_flow(result.kafka_topics_involved, result.affected_components)
            )
        elif steps_raw:
            diagrams.append(_build_implementation_flow(steps_raw))

        # 3. Data Model / ER Diagram (business entities, no fallback)
        entities_raw = [e.model_dump() for e in result.data_entities]
        if entities_raw:
            d = _build_data_model(entities_raw)
            if d:
                diagrams.append(d)

        # 4. Repository Reuse Blueprint (GraphForge differentiator).
        # Deliberately ordered after the architecture and data model: repository
        # intelligence supports the design, it does not define it. Leading with
        # it made blueprints read as dependency reports rather than solution
        # designs.
        repo_usage_raw = [r.model_dump() for r in result.repository_usage]
        if repo_usage_raw:
            d = _build_repository_reuse(repo_usage_raw)
            if d:
                diagrams.append(d)
        elif result.repositories_consulted:
            diagrams.append(
                _build_repository_impact(result.repositories_consulted, result.affected_components)
            )

        # 5. Implementation Roadmap
        phases_raw = [p.model_dump() for p in result.implementation_phases]
        if phases_raw:
            d = _build_implementation_roadmap(phases_raw)
            if d:
                diagrams.append(d)
        elif len(steps_raw) > 2:
            diagrams.append(_build_implementation_timeline(steps_raw))

        # 6. Risk & Complexity Matrix
        risks_raw = [r.model_dump() for r in result.risks]
        if risks_raw:
            d = _build_risk_matrix(risks_raw)
            if d:
                diagrams.append(d)
        elif result.risk_considerations:
            diagrams.append(_build_risk_heatmap(result.risk_considerations))

        return BlueprintArtifact(
            agent_id="planning",
            stage="planning",
            diagrams=_require_grounded_flag(diagrams),
        )

    @staticmethod
    def from_development_result(result: DevelopmentPlan) -> BlueprintArtifact:
        diagrams: list[Diagram] = []

        repos_raw = [r.model_dump() for r in result.repositories]
        comps_raw = [c.model_dump() for c in result.components]
        deps_raw = [d.model_dump() for d in result.dependencies]
        phases_raw = [p.model_dump() for p in result.implementation_phases]
        risks_raw = [r.model_dump() for r in result.risks]

        if repos_raw or comps_raw:
            d = _build_dev_overview(repos_raw, comps_raw)
            if d:
                diagrams.append(d)

        if comps_raw:
            d = _build_file_modification_map(comps_raw)
            if d:
                diagrams.append(d)

        if deps_raw:
            d = _build_component_dependency_graph(deps_raw)
            if d:
                diagrams.append(d)

        if phases_raw:
            d = _build_dev_implementation_flow(phases_raw)
            if d:
                diagrams.append(d)

        if comps_raw:
            d = _build_code_impact(comps_raw)
            if d:
                diagrams.append(d)

        if comps_raw:
            d = _build_test_strategy(comps_raw, deps_raw)
            if d:
                diagrams.append(d)

        if risks_raw:
            d = _build_dev_risk_matrix(risks_raw)
            if d:
                diagrams.append(d)

        return BlueprintArtifact(
            agent_id="development",
            stage="development",
            diagrams=_require_grounded_flag(diagrams),
        )
