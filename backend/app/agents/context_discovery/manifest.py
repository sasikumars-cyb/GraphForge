"""Context Discovery Agent manifest — registered with the Orchestrator at
startup, exactly like every other agent (see app/agents/setup.py)."""

from app.agents._contract import AgentManifest

CONTEXT_DISCOVERY_MANIFEST = AgentManifest(
    agent_id="context_discovery",
    purpose=(
        "Given a free-text engineering request, discover the context a plan needs: "
        "detected Jira/Confluence/GitHub references and their resolved content, plus "
        "the Knowledge Graph's indexed repositories, components, and topics. Answers "
        "'what exists?' — never 'what should be built?'."
    ),
    goals=frozenset({"discover_context"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="standard",
    max_graph_hops=2,
    output_schema_name="ContextDiscoveryResult",
)
