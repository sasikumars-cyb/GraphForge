"""Capability analysis for the Planning Agent.

Why this exists
---------------
The Planning Agent used to hand the LLM a repository inventory and then ask
for a plan. Because LLMs generate sequentially and anchor on whatever context
came first, an ETL brief reliably came back as "order-service + Kafka +
inventory-service" — the plan was shaped by what happened to be indexed
rather than by the business problem.

An earlier fix classified each brief into exactly one project type. That was
too rigid: real briefs are hybrids ("batch ingest, then publish an event"),
and one label forces a single architecture onto a mixed problem.

So this module detects *capabilities* instead — the things the solution has
to be able to do (file ingestion, validation, streaming, monitoring, ...).
Capabilities are multi-label, so a hybrid brief keeps all of its parts. The
dominant capabilities then imply an architecture pattern, and the pattern
supplies the layer backbone while the capabilities add cross-cutting layers.

The pipeline is: problem -> capabilities -> pattern -> architecture ->
required components -> repository search. Repository data enters only at the
last step, which is what keeps it from defining the architecture.

Cost
----
Pure Python: no LLM call, no network, no tokens. The playbook injected into
the prompt is *composed from the detected capabilities only*, so a simple
brief produces a shorter playbook than a complex one — typically smaller
than the fixed per-type playbook it replaces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    """One thing the solution must be able to do."""

    key: str
    label: str
    keywords: tuple[str, ...]
    # Repository-search terms: what a repo would contain if it already
    # implements this capability. Drives capability-driven reuse ranking.
    search_terms: tuple[str, ...] = ()
    # Cross-cutting architecture layer this capability adds, if any.
    layer: str = ""
    # Risk focus this capability introduces.
    risk: str = ""


@dataclass(frozen=True)
class ArchitecturePattern:
    """The structural shape implied by a set of capabilities."""

    key: str
    label: str
    layers: tuple[str, ...]
    flow: str


@dataclass(frozen=True)
class PlanningProfile:
    """Result of capability analysis — everything the prompt needs."""

    pattern: ArchitecturePattern
    capabilities: tuple[Capability, ...] = ()
    # Significant identifiers/nouns pulled straight from the brief itself
    # (field names, function names, entity names) — see `extract_key_terms`.
    # Kept separate from `capabilities` because these say nothing about
    # architecture (they must never shape `playbook()`'s layers), only about
    # which already-indexed code is worth showing the model.
    ticket_terms: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return self.pattern.key

    @property
    def label(self) -> str:
        return self.pattern.label

    @property
    def capability_labels(self) -> list[str]:
        return [c.label for c in self.capabilities]

    @property
    def search_terms(self) -> list[str]:
        """Flat list of repository-search terms: the brief's own vocabulary
        first, then the fixed capability vocabulary.

        The capability terms alone ("loader", "validator", "reader", ...)
        describe architecture *shapes* — they can find a repo that does
        file ingestion in general, but they say nothing about which
        specific field, function, or entity the brief is actually about.
        A ticket that names `realservicepointno` or `rate_attribute`
        directly should be able to surface the exact component that
        contains those names, not just "some ingestion-shaped repo" — so
        `ticket_terms` (extracted from the raw brief) are folded in here
        too, ticket-specific terms first since they're the more precise
        signal. Used to rank repositories/components by what the request
        actually needs, rather than a blunt project-type label.
        """
        terms: list[str] = []
        for t in self.ticket_terms:
            if t not in terms:
                terms.append(t)
        for cap in self.capabilities:
            for t in cap.search_terms:
                if t not in terms:
                    terms.append(t)
        return terms

    def playbook(self) -> str:
        """Compose the minimum architecture guidance for this brief.

        Only detected capabilities contribute, so the injected text scales
        with the complexity of the request instead of being a fixed block.
        """
        lines = [f"Architecture layers: {' -> '.join(self.pattern.layers)}."]

        cross = [c.layer for c in self.capabilities if c.layer]
        if cross:
            # dict.fromkeys preserves order while removing duplicates
            lines.append(f"Also model: {', '.join(dict.fromkeys(cross))}.")

        lines.append(f"Data flow: {self.pattern.flow}.")

        risks = [c.risk for c in self.capabilities if c.risk]
        if risks:
            lines.append(f"Risk focus: {'; '.join(dict.fromkeys(risks))}.")

        # The single most important anti-bias instruction: without it the
        # model reaches for whatever messaging/service vocabulary it saw in
        # the repository inventory.
        lines.append(
            "Use only the layers above. Do not introduce messaging, "
            "microservices, or ETL zones that these capabilities do not imply."
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Capability registry
# ---------------------------------------------------------------------------

_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="file_ingestion",
        label="File Ingestion",
        keywords=("csv", "file", "vendor file", "sftp", "parquet", "upload", "flat file"),
        search_terms=("reader", "loader", "parser", "ingest"),
        layer="a Landing Zone for raw arrivals",
        risk="late, duplicate, or partial file delivery",
    ),
    Capability(
        key="batch_processing",
        label="Batch Processing",
        keywords=(
            "batch",
            "nightly",
            "scheduled",
            "cron",
            "bulk",
            "spark",
            "airflow",
            "etl",
            "elt",
        ),
        search_terms=("job", "scheduler", "pipeline", "spark"),
        risk="idempotency on retry and partial-failure recovery",
    ),
    Capability(
        key="streaming",
        label="Streaming",
        keywords=(
            "streaming",
            "real-time",
            "realtime",
            "kafka",
            "event-driven",
            "event driven",
            "pubsub",
            "pub/sub",
            "kinesis",
            "consumer",
        ),
        search_terms=("consumer", "producer", "listener", "topic"),
        layer="a Dead Letter Queue for unprocessable events",
        risk="message ordering, consumer lag, and poison messages",
    ),
    Capability(
        key="validation",
        label="Validation",
        keywords=("validat", "schema check", "data quality", "cleanse", "quarantine", "verify"),
        search_terms=("validator", "validation", "schema", "constraint"),
        layer="a Validation & Quarantine step",
        risk="silent rejection of valid records",
    ),
    Capability(
        key="schema_evolution",
        label="Schema Evolution",
        keywords=(
            "schema evolution",
            "schema change",
            "schema registry",
            "backward compat",
            "versioned schema",
        ),
        search_terms=("schema", "registry", "migration", "avro"),
        risk="upstream schema drift breaking downstream consumers",
    ),
    Capability(
        key="warehouse_load",
        label="Warehouse Loading",
        keywords=("warehouse", "bigquery", "snowflake", "redshift", "curated", "data lake", "dbt"),
        search_terms=("writer", "sink", "loader", "warehouse"),
        layer="Raw and Curated zones ahead of the warehouse",
        risk="load cost and partition skew at volume",
    ),
    Capability(
        key="analytics",
        label="Analytics & Reporting",
        keywords=(
            "report",
            "dashboard",
            "analytics",
            "kpi",
            "metric",
            "looker",
            "tableau",
            "aggregat",
        ),
        search_terms=("aggregat", "metric", "report"),
        layer="an Analytics & Reporting layer",
        risk="metric definition drift and refresh latency",
    ),
    Capability(
        key="api",
        label="API Surface",
        keywords=("api", "endpoint", "rest", "openapi", "graphql", "crud", "grpc"),
        search_terms=("controller", "resource", "endpoint", "handler"),
        layer="an API edge with authentication",
        risk="versioning and backward compatibility for existing clients",
    ),
    Capability(
        key="service_topology",
        label="Service Topology",
        keywords=(
            "microservice",
            "bounded context",
            "service mesh",
            "saga",
            "domain-driven",
            "inter-service",
        ),
        search_terms=("service", "client", "gateway"),
        risk="distributed transactions and cascading failure",
    ),
    Capability(
        key="persistence",
        label="Persistence",
        keywords=("database", "postgres", "mysql", "mongo", "store", "persist", "repository layer"),
        search_terms=("repository", "entity", "dao", "store"),
        layer="a persistence layer",
        risk="data model migration and referential integrity",
    ),
    Capability(
        key="frontend",
        label="User Interface",
        keywords=(
            "frontend",
            "ui",
            "web app",
            "react",
            "angular",
            "vue",
            "page",
            "screen",
            "dashboard ui",
        ),
        search_terms=("component", "page", "view"),
        layer="a frontend served via CDN/cache",
        risk="load performance and deployment rollback",
    ),
    Capability(
        key="model_training",
        label="Model Training",
        keywords=(
            "machine learning",
            "model training",
            "feature store",
            "inference",
            "mlops",
            "model registry",
        ),
        search_terms=("model", "feature", "training"),
        layer="a Feature Store and Model Registry",
        risk="train/serve skew and data drift",
    ),
    Capability(
        key="notifications",
        label="Notifications",
        keywords=("notification", "alert", "email", "sms", "notify", "push"),
        search_terms=("notification", "mailer", "alert"),
        layer="a notification dispatch path",
        risk="duplicate or missed notifications on retry",
    ),
    Capability(
        key="monitoring",
        label="Monitoring",
        keywords=("monitor", "observab", "metrics", "logging", "audit", "trace", "sla"),
        search_terms=("monitor", "metric", "audit", "log"),
        layer="an observability and audit layer",
        risk="blind spots in failure detection",
    ),
    Capability(
        key="migration",
        label="Data Migration",
        keywords=(
            "migrat",
            "cutover",
            "legacy",
            "backfill",
            "reconcil",
            "decommission",
            "dual write",
        ),
        search_terms=("migration", "backfill", "reconcil"),
        layer="a reconciliation and rollback path",
        risk="data loss and reconciliation gaps at cutover",
    ),
    Capability(
        key="security",
        label="Security & Access",
        keywords=("auth", "oauth", "permission", "rbac", "encrypt", "pii", "gdpr", "sensitive"),
        search_terms=("auth", "security", "token", "permission"),
        risk="unauthorised access to sensitive data",
    ),
)

# ---------------------------------------------------------------------------
# Architecture patterns — the layer backbone a capability mix implies
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, ArchitecturePattern] = {
    "etl_batch": ArchitecturePattern(
        key="etl_batch",
        label="Batch ETL / Data Platform",
        layers=(
            "Source Systems",
            "Landing",
            "Validation",
            "Raw",
            "Transformation",
            "Curated",
            "Warehouse",
            "Analytics",
        ),
        flow=(
            "file arrival -> object storage -> orchestrator -> compute "
            "-> validation -> transformation -> warehouse -> BI"
        ),
    ),
    "streaming": ArchitecturePattern(
        key="streaming",
        label="Streaming / Event-Driven",
        layers=("Producers", "Topics", "Stream Processing", "State Store", "Sinks", "Consumers"),
        flow="producer -> topic -> consumer group -> processor -> state/sink -> downstream",
    ),
    "microservices": ArchitecturePattern(
        key="microservices",
        label="Microservices Platform",
        layers=(
            "API Gateway",
            "Services",
            "Integration",
            "Per-Service Data Stores",
            "Observability",
        ),
        flow="client -> gateway -> owning service -> datastore -> async events",
    ),
    "api_service": ArchitecturePattern(
        key="api_service",
        label="API Service",
        layers=("Client", "API Edge", "Controller", "Service", "Persistence"),
        flow="request -> auth -> validation -> handler -> data access -> response",
    ),
    "web_app": ArchitecturePattern(
        key="web_app",
        label="Web Application",
        layers=("Frontend", "API / BFF", "Application Services", "Data Store", "Cache / CDN"),
        flow="browser -> edge -> API -> service -> datastore -> response",
    ),
    "ml_pipeline": ArchitecturePattern(
        key="ml_pipeline",
        label="ML Pipeline",
        layers=(
            "Data Sources",
            "Feature Engineering",
            "Feature Store",
            "Training",
            "Model Registry",
            "Serving",
            "Monitoring",
        ),
        flow=(
            "raw data -> features -> training -> evaluation -> registry "
            "-> inference -> drift monitoring"
        ),
    ),
    "analytics": ArchitecturePattern(
        key="analytics",
        label="Analytics / Reporting",
        layers=("Source Data", "Modeling", "Semantic Layer", "Aggregations", "Dashboards"),
        flow="warehouse tables -> transformations -> metric definitions -> aggregates -> BI tool",
    ),
    "migration": ArchitecturePattern(
        key="migration",
        label="Data Migration",
        layers=(
            "Source System",
            "Extraction",
            "Staging",
            "Mapping & Reconciliation",
            "Target System",
            "Cutover",
        ),
        flow="snapshot -> extract -> stage -> map -> load -> reconcile -> cutover",
    ),
    "generic": ArchitecturePattern(
        key="generic",
        label="General Software Solution",
        layers=("Entry Point", "Processing", "Storage", "Consumption"),
        flow="derive the operational steps directly from the brief",
    ),
}

# Which capabilities vote for which pattern. Ordered most-specific first so a
# tie resolves toward the more opinionated architecture.
_PATTERN_VOTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ml_pipeline", ("model_training",)),
    ("migration", ("migration",)),
    ("etl_batch", ("file_ingestion", "batch_processing", "warehouse_load", "schema_evolution")),
    ("streaming", ("streaming",)),
    ("web_app", ("frontend",)),
    ("microservices", ("service_topology",)),
    ("analytics", ("analytics",)),
    ("api_service", ("api", "persistence")),
)

_MULTIWORD_WEIGHT = 3
_SINGLEWORD_WEIGHT = 1
# A capability needs real support in the text, not one incidental word.
_DETECTION_THRESHOLD = 1


def _matches(keyword: str, text: str) -> bool:
    """Word-boundary-anchored keyword match.

    Anchoring the *start* only is deliberate: several keywords are stems
    ("validat", "migrat", "aggregat") that must still match "validation" or
    "migrated". Without the leading boundary, short keywords match inside
    unrelated words — "ui" fires on "build", "api" on "rapid" — which
    silently attaches capabilities the brief never asked for.
    """
    return re.search(r"\b" + re.escape(keyword), text) is not None


def detect_capabilities(task_description: str) -> tuple[Capability, ...]:
    """Detect every capability the brief calls for — multi-label by design.

    A hybrid brief ("batch ingest then publish an event") keeps both its
    batch and streaming capabilities rather than being forced into one box.
    """
    text = (task_description or "").lower()
    if not text.strip():
        return ()

    found: list[tuple[int, Capability]] = []
    for cap in _CAPABILITIES:
        score = 0
        for kw in cap.keywords:
            if _matches(kw, text):
                score += _MULTIWORD_WEIGHT if " " in kw else _SINGLEWORD_WEIGHT
        if score >= _DETECTION_THRESHOLD:
            found.append((score, cap))

    # Strongest signal first so the playbook leads with what matters most.
    found.sort(key=lambda pair: pair[0], reverse=True)
    return tuple(cap for _, cap in found)


def derive_pattern(capabilities: tuple[Capability, ...]) -> ArchitecturePattern:
    """Pick the architecture backbone implied by the detected capabilities."""
    if not capabilities:
        return _PATTERNS["generic"]

    keys = {c.key for c in capabilities}
    best_key = "generic"
    best_score = 0
    for pattern_key, voters in _PATTERN_VOTES:
        score = len(keys & set(voters))
        # Strictly greater keeps the earlier (more specific) pattern on ties.
        if score > best_score:
            best_score = score
            best_key = pattern_key

    return _PATTERNS[best_key]


_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{3,}")

# Locators — URLs and absolute filesystem paths — name *where something
# lives*, never *what the work is about*. They are also the densest source
# of 4+ character tokens in a typical brief, so left in place they crowd
# out the domain vocabulary twice over: once by consuming the `max_terms`
# budget, and again by contributing tokens so rare that `_term_weights`
# scores them as the most discriminating terms in the run.
#
# Both effects were observed together on a real brief. "Prepare
# implementation plan for https://<host>/browse/PROT-5723 / The repo is
# already in my local /home/<user>/git_repositories/..." spent 16 of its
# 25 term slots on the URL and the path, pushing the ticket's actual
# subject ("manifest") to position 23; and the four surviving locator
# tokens then out-scored it, because a token matching one component
# scores 1/(1+1) while "manifest" — matching 108 — scored 1/(1+108).
# The top-ranked components for that run were consequently a Jira-comment
# helper and three path-manipulation utilities, none of which had
# anything to do with the ticket.
#
# Stripping locators wholesale is safe in a way that stopwording their
# tokens individually is not: the noise is per-user and per-host
# (usernames, directory names, ticket-tracker domains), so no fixed word
# list can anticipate it. Any identifier that genuinely matters will also
# appear in the prose — briefs name the thing they are about.
#
# Only *absolute* paths are stripped (leading `/`, `~`, or a drive
# letter). A repo-relative path is the opposite of noise: a brief that
# says the bug is in `soco_ingest/src/parsers/manifest_parser.py` has
# handed over the single most useful retrieval term in the whole
# document, and that must survive intact.
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+|\bwww\.\S+", re.IGNORECASE)
# The lookbehind is what makes "absolute only" actually hold. Without it
# the regex still matches the *tail* of a relative path — given
# `soco_ingest/src/parsers/manifest_parser.py` it would match from the
# first slash onward and strip everything but `soco_ingest`, throwing
# away the filename that was the whole point of citing the path.
_FS_PATH_RE = re.compile(r"(?<![\w.\-@+])(?:[A-Za-z]:|~)?[/\\](?:[\w.\-@+]+[/\\]){2,}[\w.\-@+]*")


def strip_locators(text: str) -> str:
    """Remove URLs and absolute filesystem paths from free text, leaving a
    space so surrounding words don't fuse. See `_URL_RE`/`_FS_PATH_RE` for
    why locators are dropped rather than down-weighted."""
    return _FS_PATH_RE.sub(" ", _URL_RE.sub(" ", text))

# Generic English function words, plus the app's own fixed prompt-wrapper
# vocabulary (see app.agents.prompt_utils.wrap_untrusted_content), plus the
# handful of nouns/verbs so common across *any* bug report or work item
# ("create a record", "process the file", "check the value") that they
# carry no domain signal on their own. None of this is specific to any one
# ticket's subject matter — it's the same fixed list for every brief.
#
# Why exclude ticket-boilerplate nouns at all, when `_term_weights` already
# downweights whatever turns out to be common? Because that weighting is
# computed per-run over whatever the traversal happens to return — often a
# few hundred components. In a sample that small, a generic word can look
# artificially "rare" (and so score as if it were specific) purely because
# only one or two components happen to contain it, not because it actually
# means anything. Stopwording removes the words this applies to categorically
# rather than leaving it to per-run sample noise.
_GENERIC_STOPWORDS = frozenset(
    {
        "this",
        "that",
        "these",
        "those",
        "with",
        "from",
        "into",
        "when",
        "where",
        "which",
        "should",
        "would",
        "could",
        "must",
        "also",
        "being",
        "been",
        "have",
        "has",
        "had",
        "will",
        "shall",
        "does",
        "done",
        "after",
        "before",
        "during",
        "while",
        "about",
        "then",
        "than",
        "only",
        "even",
        "both",
        "either",
        "neither",
        "each",
        "every",
        "some",
        "such",
        "other",
        "another",
        "more",
        "most",
        "less",
        "least",
        "very",
        "just",
        "still",
        "again",
        "once",
        "here",
        "there",
        "what",
        "whose",
        "whom",
        "the",
        "and",
        "for",
        "are",
        "was",
        "were",
        "not",
        "but",
        "can",
        "its",
        "our",
        "your",
        "their",
        "his",
        "her",
        "they",
        "them",
        "you",
        "we",
        "begin",
        "end",
        "untrusted",
        "content",
        "data",
        "follow",
        "any",
        "instructions",
        "found",
        "below",
        "phrased",
        "commands",
        "prepare",
        "implementation",
        "plan",
        "ticket",
        "issue",
        "task",
        "create",
        "created",
        "creates",
        "creating",
        "input",
        "inputs",
        "output",
        "outputs",
        "value",
        "values",
        "record",
        "records",
        "process",
        "processed",
        "processing",
        "result",
        "results",
        "system",
        "error",
        "errors",
        "please",
        "note",
        "connect",
        "verify",
        "verified",
        "check",
        "checking",
        "via",
        "case",
        "cases",
        "having",
        "getting",
        # Locator vocabulary that survives `strip_locators` because it was
        # written as prose rather than inside a URL or path — "the Jira
        # ticket", "the repo is already in my local checkout". These name
        # the tools and places the work is *described* in, never the work.
        "http",
        "https",
        "atlassian",
        "jira",
        "browse",
        "github",
        "gitlab",
        "bitbucket",
        "repo",
        "repos",
        "repository",
        "repositories",
        "branch",
        "local",
        "folder",
        "directory",
        "already",
        "attached",
        "link",
        "above",
    }
)


def extract_key_terms(text: str, max_terms: int = 40) -> tuple[str, ...]:
    """Pull the significant identifiers/nouns out of free text — field
    names, function names, entity names — so they can be matched against
    the Knowledge Graph's own component names.

    Deliberately dumb and generic (regex + stopword filter, no NLP model,
    no per-ticket tuning): any word/identifier of 4+ characters that isn't
    a common function word or prompt-boilerplate token is kept, in the
    order it first appears. This is what lets a brief that names a specific
    field ("realservicepointno") or function ("rate_attribute") surface the
    exact module/function that contains it, instead of relying entirely on
    the fixed capability-keyword vocabulary in `_CAPABILITIES`, which
    describes architecture shapes ("loader", "validator") and has no way to
    know about any one codebase's actual identifiers.
    """
    if not text:
        return ()
    seen: list[str] = []
    for match in _TOKEN_RE.finditer(strip_locators(text).lower()):
        token = match.group(0)
        if token in _GENERIC_STOPWORDS or token in seen:
            continue
        seen.append(token)
        if len(seen) >= max_terms:
            break
    return tuple(seen)


def analyse(task_description: str) -> PlanningProfile:
    """Full capability analysis for one brief.

    This is the entry point the agent uses: problem in, capabilities and
    architecture pattern out, before any repository data is touched.
    """
    capabilities = detect_capabilities(task_description)
    ticket_terms = extract_key_terms(task_description or "")
    return PlanningProfile(
        pattern=derive_pattern(capabilities),
        capabilities=capabilities,
        ticket_terms=ticket_terms,
    )


def pattern_for_key(key: str) -> ArchitecturePattern:
    """Resolve a pattern key back to its definition — used to honour the
    `architecture_pattern` the LLM echoes back. Unknown keys fall back to
    generic rather than inventing a shape."""
    return _PATTERNS.get(key, _PATTERNS["generic"])


def known_pattern_keys() -> list[str]:
    """All valid architecture_pattern values, for the prompt's enum hint."""
    return list(_PATTERNS)
