"""Context Discovery Agent manifest — registered with the Orchestrator at
startup, exactly like every other agent (see app/agents/setup.py)."""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM, DEPENDENCY_NEO4J

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
    # Per-repository call budget (see app.graph.hop_budget: it counts
    # IGraphRepository reads per repository, not hops). Each graph query this
    # agent makes costs 2 reads per repository — one Component label read, one
    # KafkaTopic read.
    #
    # Unlike the fixed pipeline this replaced, the reasoning engine may query
    # the graph more than once in a single run: a broad survey to find which
    # repositories exist, then a traversal scoped to the one it identified, and
    # — on a resumed run — a query verifying a repository the human named. At
    # the old budget of 2 the *second* query tripped the limit and surfaced as
    # "the architecture graph could not be read", which then told the user to
    # check their Neo4j connection for what was really the agent hitting its own
    # ceiling.
    #
    # 6 covers three queries per repository, which is the engine's real ceiling:
    # `attempted()` dedupe means each distinct action runs at most once, and
    # there are three graph action kinds (survey / scope / verify).
    max_graph_hops=6,
    output_schema_name="ContextDiscoveryResult",
    # ADR 0011, OD-3 — LLM (this agent's own reasoning), Neo4j (max_graph_hops
    # > 0, above).
    required_dependencies=frozenset({DEPENDENCY_LLM, DEPENDENCY_NEO4J}),
)
