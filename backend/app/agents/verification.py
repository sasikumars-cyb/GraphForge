"""Generic, language-agnostic claim verification — shared by every agent.

Two independent checks live here, both deliberately blind to source code:

1. Entity/tenant mismatch detection (`check_entity_mismatch`). Repo
   selection (see app.agents.planning.tools.rank_repositories) scores
   candidates by name/component keyword overlap with the ticket text. That
   scoring can be fooled by two near-identical repositories that differ
   only by a short business-unit/tenant code baked into the name (e.g.
   "soco-gpc-c2m-rcs" vs. a ticket about "soco_apc_c2m_rcs" — GPC and APC
   are different Southern Company subsidiaries sharing near-identical
   pipeline code). This check extracts acronym-shaped tokens from the
   ticket text and the candidate repo name and flags it when the ticket
   names a token the repo name does not contain. It never opens a file or
   parses source — it only compares two strings — so it is identical for
   a Java, Python, or Scala repository.

2. Claim-vs-evidence verification (`verify_claims`). Every agent already
   gathers a real evidence pool this run via its own tool calls (indexed
   repository names, component names, file paths, Kafka topic names — all
   already language-normalized by the indexer into the same graph shape
   regardless of source language). This checks whether the LLM's specific
   claims (a `files_affected` entry, a component name, an ID) actually
   appear in that pool, rather than trusting free-generated text. Pure
   string membership — no source-code or domain knowledge required, so it
   applies identically to every agent and every repository language.

Neither check requires a schema migration or a new parser: (1) only reads
`Repository.name`/`full_name`, already indexed for every repository; (2)
only reads the observation data tools already return this run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 1. Entity / tenant mismatch detection
# ---------------------------------------------------------------------------

# 2-6 uppercase letters — the shape of a business-unit/tenant/provider code
# (APC, GPC, PSEG, ...). Split on non-alphanumeric boundaries (not `\b`,
# which treats "_" as a word character and would miss "APC" inside
# "Soco_C2M_APC_RCS") so ticket titles using underscores still tokenize.
_TOKEN_SPLIT_PATTERN = re.compile(r"[^a-zA-Z0-9]+")
_ACRONYM_SHAPE = re.compile(r"^[A-Z]{2,6}$")

# Generic acronyms that show up in tickets/specs for reasons unrelated to
# tenant identity — excluded so the check doesn't fire on every ticket that
# happens to mention an ID format or a protocol. Deliberately short and
# generic (not domain-specific to any one repo or customer).
_GENERIC_ACRONYM_STOPWORDS = frozenset({
    "ID", "IDS", "URL", "URI", "API", "APIS", "JSON", "XML", "CSV", "TSV",
    "SQL", "ETL", "UI", "UIS", "QA", "PR", "OK", "NA", "TBD", "FAQ",
    "HTTP", "HTTPS", "REST", "SDK", "CLI", "CPU", "GPU", "RAM", "AWS",
    "GCP", "IO", "OS", "DB", "PK", "FK", "UUID", "JWT", "SSO", "PII",
})


def _extract_acronym_tokens(text: str) -> set[str]:
    """Short, all-caps, acronym-shaped tokens from free text.

    Deliberately narrow (see module docstring's limitation notes): tuned
    for the acronym shape only, not full words like "Alabama Power".
    """
    tokens = _TOKEN_SPLIT_PATTERN.split(text)
    return {
        t for t in tokens
        if _ACRONYM_SHAPE.match(t) and t not in _GENERIC_ACRONYM_STOPWORDS
    }


def _name_tokens(name: str) -> set[str]:
    """Split a repository name into lowercase word tokens on any
    non-alphanumeric boundary — works the same for "ds-databricks-soco-gpc-
    c2m-rcs-dataingest" or "com.example.soco.gpc.service", no language
    assumption either way."""
    return {t for t in re.split(r"[^a-zA-Z0-9]+", name.lower()) if t}


def check_entity_mismatch(ticket_text: str, selected_repo_name: str) -> str | None:
    """Warn when the ticket names an entity/tenant-shaped token that the
    top-selected repository's own name does not contain.

    Returns None (no-op) whenever the ticket has no acronym-shaped token at
    all — this is the common case, and the check must never block or alter
    repository selection for an ordinary ticket. It only fires in the
    narrow situation this was built for: a ticket that does carry such a
    token, matched against a repo whose name carries none of them.

    Not a general "does this ticket match this repo" solver — see the
    module docstring for what this deliberately does not catch.
    """
    if not selected_repo_name:
        return None
    ticket_tokens = _extract_acronym_tokens(ticket_text)
    if not ticket_tokens:
        return None
    repo_tokens = _name_tokens(selected_repo_name)
    unmatched = {t for t in ticket_tokens if t.lower() not in repo_tokens}
    if not unmatched:
        return None
    plural = "s" if len(unmatched) != 1 else ""
    return (
        f"Ticket references entity/tenant-shaped token{plural} "
        f"{', '.join(sorted(unmatched))} not found in the selected repository "
        f"name '{selected_repo_name}'. Repository name similarity does not "
        "guarantee entity identity — verify this is the correct "
        "tenant/business-unit repository before proceeding."
    )


# ---------------------------------------------------------------------------
# 2. Claim-vs-evidence verification
# ---------------------------------------------------------------------------


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def build_evidence_pool(*groups: list[str]) -> set[str]:
    """Collect ground-truth strings this run's tool calls actually
    returned (repo names, component names, file paths, topic names, ...)
    into one normalized pool to check claims against."""
    pool: set[str] = set()
    for group in groups:
        for item in group:
            if item:
                pool.add(_normalize(str(item)))
    return pool


def _claim_supported(claim: str, evidence_pool: set[str]) -> bool:
    """A claim is supported if it matches an evidence string exactly, or is
    a substring/superstring of one — case- and whitespace-insensitive.

    Substring matching (not exact-only) tolerates the LLM citing a bare
    filename ("pipeline_config.py") when the evidence pool holds a full
    path ("soco_ingest/src/config/pipeline_config.py"), without requiring
    an exact path match. Pure string comparison — no parsing.
    """
    claim_n = _normalize(claim)
    if not claim_n:
        return True  # nothing to check
    for evidence in evidence_pool:
        if claim_n == evidence or claim_n in evidence or evidence in claim_n:
            return True
    return False


@dataclass(frozen=True)
class VerificationResult:
    """Which of a set of claims were actually backed by this run's own
    tool-returned evidence, and which were not."""

    verified: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    @property
    def all_verified(self) -> bool:
        return not self.unverified


def verify_claims(claims: list[str], evidence_pool: set[str]) -> VerificationResult:
    """Check each claim string against the evidence pool gathered this run.

    Generic string membership only — works identically regardless of what
    language or framework the underlying repository is written in, because
    it never looks at source code, only at what this run's own tools
    already returned.
    """
    verified: list[str] = []
    unverified: list[str] = []
    for claim in claims:
        if not claim:
            continue
        (verified if _claim_supported(claim, evidence_pool) else unverified).append(claim)
    return VerificationResult(verified=verified, unverified=unverified)
