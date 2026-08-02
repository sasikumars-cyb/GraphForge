"""ADR 0018 — evidence-semantic validators for the Frontier Hypothesis
Generator's capability vocabulary (`OWNS_DATABASE`, `CONTAINS_CACHING`,
...). Deliberately NOT one class per language or framework — there is no
`SpringValidator`/`JavaValidator`/`PythonValidator` here, and there never
should be. One generic, reusable `EvidenceKeywordValidator` class,
configured by evidence *kind* (not by language) and instantiated once per
evidence domain (manifest, documentation, configuration, dependency) —
the same "reusable plugin instances, not an if-chain per case" pattern
`CROSS_REPO_LINK_RULES`/`DETERMINISTIC_STRUCTURAL_VALIDATORS` already use
in this codebase.

What it does, and why this counts as independent re-derivation, not
tautology: a validator checks whether the hypothesis's OWN cited evidence
(`item.id in hypothesis.evidence_refs` — never evidence the hypothesis
didn't cite, same discipline every existing validator already follows)
contains a keyword from a small, fixed, per-relationship-type keyword
table — deterministic substring matching against raw evidence text, the
same "narrow, explainable pattern match" precedent
`app.context_pipeline.reasoning.curation`'s `_REUSE_NAME_RE` and
`app.indexer.classification`'s `is_test` detection already use elsewhere
in this codebase, applied here to evidence text instead of a component
name. The keyword table names TECHNOLOGIES (`postgres`, `kafka`, `redis`,
`jwt`, `launchdarkly`, ...), never PROGRAMMING LANGUAGES — a Python
`requirements.txt` mentioning `psycopg2` and a Java `pom.xml` mentioning
`postgresql` both match the same `OWNS_DATABASE` entry, through the same
code path, because the keyword table (data) grows, not the validator
(code). This is the "self-improving without hardcoded language
validators" mechanism the platform asked for: recognizing a new
technology is a new keyword-table entry, never a new class.

Reliability tier is fixed at `_HEURISTIC_TIER` (1) for every instance,
regardless of evidence domain — keyword/substring matching against
unstructured-ish text is inherently heuristic (the same tier
`cross_repo.py`'s `DependencyCoordinateValidator` already uses for a
comparable "name similarity, not a literal annotation" signal), never the
deterministic tier (3) reserved for a literal, structurally-guaranteed
match. These validators only ever return `confirms` or `no_signal`, never
`contradicts` — the absence of a keyword in a possibly-incomplete
README/manifest sample is not proof a capability doesn't exist, the same
conservative discipline every other validator in this codebase already
follows.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.contracts.validation import KnowledgeValidator, ValidationResult

_HEURISTIC_TIER = 1

# Technology keywords, never language names — see this module's own
# docstring. Extending recognition to a new technology (e.g. a new
# feature-flag vendor) is one more string in the relevant tuple, not a
# new class or a pipeline change.
CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "OWNS_DATABASE": (
        "postgres",
        "postgresql",
        "mysql",
        "mariadb",
        "mongodb",
        "oracle",
        "sqlserver",
        "cassandra",
        "dynamodb",
        "sqlite",
        "jdbc",
        "sqlalchemy",
        "hibernate",
    ),
    "OWNS_MESSAGE_QUEUE": ("kafka", "rabbitmq", "amqp", "sqs", "activemq", "pulsar", "nats"),
    "OWNS_EVENT_SCHEMA": ("avro", "protobuf", "asyncapi", "cloudevents", "schema-registry"),
    "OWNS_API": ("openapi", "swagger", "graphql", "grpc"),
    "CONTAINS_INFRASTRUCTURE": (
        "dockerfile",
        "docker-compose",
        "terraform",
        "kubernetes",
        "helm",
        "ansible",
    ),
    "CONTAINS_BATCH_JOB": ("batch", "airflow", "spark-submit"),
    "CONTAINS_SCHEDULED_JOB": ("cron", "scheduler", "quartz", "celery-beat"),
    "INTEGRATES_WITH_CLOUD_SERVICE": (
        "aws-sdk",
        "boto3",
        "google-cloud",
        "azure-sdk",
        "lambda",
        "cloudfunctions",
    ),
    "CONTAINS_FEATURE_FLAG": (
        "launchdarkly",
        "unleash",
        "flagsmith",
        "feature-flag",
        "featureflag",
    ),
    "CONTAINS_AUTHENTICATION": ("oauth", "openid", "jwt", "saml"),
    "CONTAINS_AUTHORIZATION": ("rbac", "casbin", "opa"),
    "CONTAINS_CACHING": ("redis", "memcached", "ehcache", "caffeine"),
    "CONTAINS_EXTERNAL_API": ("okhttp", "retrofit", "axios", "webclient"),
}


def _contains_any_keyword(raw_value: str, keywords: tuple[str, ...]) -> bool:
    text = raw_value.lower()
    return any(keyword in text for keyword in keywords)


class EvidenceKeywordValidator(KnowledgeValidator):
    """One reusable validator, configured per evidence domain. Advertises
    its capability via plain, inspectable constructor state — no interface
    change to `KnowledgeValidator` needed, so this adds zero risk to any
    existing validator (deterministic or cross-repo): `applies_to` (which
    relationship types), `evidence_kind_prefixes` (which evidence kinds it
    reads), and `reliability_tier`/`source_type` (its confidence
    contribution) are all plain attributes, readable by any caller that
    wants to introspect the registry without touching `validate()` itself.
    """

    def __init__(
        self,
        *,
        name: str,
        evidence_kind_prefixes: tuple[str, ...],
        source_type: str,
        keyword_map: dict[str, tuple[str, ...]] = CAPABILITY_KEYWORDS,
        reliability_tier: int = _HEURISTIC_TIER,
    ) -> None:
        self.name = name
        self.applies_to = frozenset(keyword_map)
        self.evidence_kind_prefixes = evidence_kind_prefixes
        self.source_type = source_type
        self.reliability_tier = reliability_tier
        self._keyword_map = keyword_map

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        keywords = self._keyword_map.get(hypothesis.relationship_type, ())
        candidates = [
            item
            for item in pack.items
            if item.id in hypothesis.evidence_refs
            and item.kind.startswith(self.evidence_kind_prefixes)
        ]
        matched = [item for item in candidates if _contains_any_keyword(item.raw_value, keywords)]

        identity = GeneratorIdentity(kind="deterministic", name=self.name, version="1.0.0")
        provenance = Provenance(
            generator=identity,
            produced_at=datetime.now(UTC),
            pack_id=pack.id,
            pack_version=pack.schema_version,
            run_id=pack.id,
        )
        if matched:
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="confirms",
                evidence_used=tuple(item.id for item in matched),
                source_type=self.source_type,
                evidence_reliability_tier=self.reliability_tier,
                explanation=(
                    f"Cited {self.source_type} evidence contains a keyword associated with "
                    f"{hypothesis.relationship_type}."
                ),
                provenance=provenance,
            )
        return ValidationResult(
            hypothesis_id=hypothesis.id,
            validator_name=self.name,
            verdict="no_signal",
            evidence_used=(),
            source_type=self.source_type,
            evidence_reliability_tier=0,
            explanation=(
                f"No cited {self.source_type} evidence contained a recognized keyword for "
                f"{hypothesis.relationship_type}."
            ),
            provenance=provenance,
        )


manifest_validator = EvidenceKeywordValidator(
    name="manifest_keyword_validator",
    evidence_kind_prefixes=("repository_manifest",),
    source_type="repository_manifest",
)

documentation_validator = EvidenceKeywordValidator(
    name="documentation_keyword_validator",
    evidence_kind_prefixes=("repository_readme", "repository_architecture_doc"),
    source_type="repository_documentation",
)

configuration_validator = EvidenceKeywordValidator(
    name="configuration_keyword_validator",
    evidence_kind_prefixes=("repository_config",),
    source_type="repository_configuration",
)

dependency_validator = EvidenceKeywordValidator(
    name="dependency_keyword_validator",
    evidence_kind_prefixes=("graph_node:MavenDependency", "graph_node:PythonDependency"),
    source_type="dependency_declaration",
)

EVIDENCE_KEYWORD_VALIDATORS: tuple[KnowledgeValidator, ...] = (
    manifest_validator,
    documentation_validator,
    configuration_validator,
    dependency_validator,
)
