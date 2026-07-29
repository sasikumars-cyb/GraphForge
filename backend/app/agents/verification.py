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

from app.agents.normalization import normalize_path, normalize_text, squash, tokenize

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
_GENERIC_ACRONYM_STOPWORDS = frozenset(
    {
        "ID",
        "IDS",
        "URL",
        "URI",
        "API",
        "APIS",
        "JSON",
        "XML",
        "CSV",
        "TSV",
        "SQL",
        "ETL",
        "UI",
        "UIS",
        "QA",
        "PR",
        "OK",
        "NA",
        "TBD",
        "FAQ",
        "HTTP",
        "HTTPS",
        "REST",
        "SDK",
        "CLI",
        "CPU",
        "GPU",
        "RAM",
        "AWS",
        "GCP",
        "IO",
        "OS",
        "DB",
        "PK",
        "FK",
        "UUID",
        "JWT",
        "SSO",
        "PII",
    }
)


# The literal boilerplate `app.agents.prompt_utils.wrap_untrusted_content`
# fences fetched Jira/GitHub/Confluence content with — always a single
# line, always this shape. Stripped before acronym extraction so the
# wrapper's own scaffolding ("BEGIN", "END", "UNTRUSTED", "CONTENT", and
# the source name it upper-cases into the marker, e.g. "JIRA") is never
# mistaken for a tenant/entity code found *in* the content it's fencing.
# A real run flagged BEGIN/END/JIRA as unmatched entity tokens precisely
# because this wasn't stripped — false positives from GraphForge's own
# prompt, not from anything the ticket said.
_WRAPPER_MARKER_RE = re.compile(r"-{2,}\s*(?:BEGIN|END) UNTRUSTED [A-Z]+ CONTENT[^\n]*-{2,}")

# A ticket-ID-shaped reference ("PROT-5723", "NPT-6") always precedes a
# run number with a hyphen — acronym-shaped by coincidence, but it names
# the ticket, not a tenant or business unit. Stripped the same way as the
# wrapper markers, for the same reason: it's boilerplate this run itself
# introduced (the ticket key), not tenant identity signal from the ticket
# body.
_TICKET_KEY_RE = re.compile(r"\b[A-Z]{2,10}-\d+\b")


def _extract_acronym_tokens(text: str) -> set[str]:
    """Short, all-caps, acronym-shaped tokens from free text.

    Deliberately narrow (see module docstring's limitation notes): tuned
    for the acronym shape only, not full words like "Alabama Power".
    """
    cleaned = _TICKET_KEY_RE.sub(" ", _WRAPPER_MARKER_RE.sub(" ", text))
    tokens = _TOKEN_SPLIT_PATTERN.split(cleaned)
    return {t for t in tokens if _ACRONYM_SHAPE.match(t) and t not in _GENERIC_ACRONYM_STOPWORDS}


def _name_tokens(name: str) -> set[str]:
    """Split a repository name into lowercase word tokens on any
    non-alphanumeric boundary — works the same for "ds-databricks-soco-gpc-
    c2m-rcs-dataingest" or "com.example.soco.gpc.service", no language
    assumption either way."""
    return {t for t in re.split(r"[^a-zA-Z0-9]+", name.lower()) if t}


def _ordered_name_tokens(name: str) -> tuple[str, ...]:
    """Same split as `_name_tokens`, but positional — needed to find which
    token position varies across sibling repository names."""
    return tuple(t for t in re.split(r"[^a-zA-Z0-9]+", name.lower()) if t)


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


def find_unindexed_sibling_references(text: str, indexed_repo_names: list[str]) -> list[str]:
    """Tenant/business-unit codes mentioned in a plan's own narrative text
    that don't belong to any indexed repository, detected from the naming
    pattern of repositories that already exist as siblings — e.g. an
    indexed "...-soco-gpc-..." and "...-soco-apc-..." pair, differing only
    by a 3-letter tenant code, teaches this what that code's shape looks
    like at that position.

    This is distinct from `check_entity_mismatch`: that check compares the
    *ticket* against the *selected* repository. This one catches a repo
    the LLM's own output *names* — in a phase deliverable, a risk, the
    executive summary — that was never indexed and never reached
    `repository_usage` at all, so nothing else in verification ever sees
    it. A real run planned work against "MPC" this way: never selected,
    never scored, just asserted to exist in prose.

    Narrow by construction, like `check_entity_mismatch`: it only fires
    when the indexed set actually contains a sibling family to learn a
    pattern from (so a single indexed repo, or an unrelated set, produces
    no warnings at all), and only flags tokens matching that family's
    varying-slot code length — not every acronym in a paragraph.
    """
    if not indexed_repo_names:
        return []
    token_lists = [_ordered_name_tokens(n) for n in indexed_repo_names]
    slot_codes: dict[tuple[str, ...], set[str]] = {}
    for tokens in token_lists:
        for i in range(len(tokens)):
            shape = tokens[:i] + ("*",) + tokens[i + 1 :]
            slot_codes.setdefault(shape, set()).add(tokens[i])
    sibling_codes = {code for codes in slot_codes.values() if len(codes) >= 2 for code in codes}
    if not sibling_codes:
        return []  # no sibling family among the indexed repos — nothing to pattern-match
    code_lengths = {len(c) for c in sibling_codes}
    all_indexed_tokens = {t for tokens in token_lists for t in tokens}
    found = {
        tok
        for tok in _extract_acronym_tokens(text)
        if tok.lower() not in all_indexed_tokens and len(tok) in code_lengths
    }
    return sorted(found)


# ---------------------------------------------------------------------------
# 2. Claim-vs-evidence verification
# ---------------------------------------------------------------------------
#
# Case/separator/path canonicalization is centralized in
# app.agents.normalization — the same module app.agents.code_generation.
# verification uses for repository/file-path checks, so every deterministic
# validator in this codebase applies identical normalization rules rather
# than each keeping its own slightly-different copy.

_normalize = normalize_text  # local alias — kept so existing call sites/tests
_tokenize = tokenize  # in this module don't all need renaming.


def build_evidence_pool(*groups: list[str]) -> set[str]:
    """Collect ground-truth strings this run's tool calls actually
    returned (repo names, component names, file paths, topic names, ...)
    into one normalized pool to check claims against."""
    pool: set[str] = set()
    for group in groups:
        for item in group:
            if item:
                pool.add(normalize_text(normalize_path(str(item))))
    return pool


def _claim_supported(claim: str, evidence_pool: set[str]) -> bool:
    """A claim is supported if it matches an evidence string exactly (after
    path/case/separator normalization), is a path-segment-anchored match,
    its tokens are fully contained in a single evidence item's tokens, or
    its squashed (all-non-alphanumeric-stripped) form exactly equals a
    single evidence item's squashed form.

    This used to also accept `evidence in claim_n` — evidence as a bare
    substring anywhere inside the claim — which made verification close to
    vacuous: any evidence string short enough to appear inside a longer
    fabricated claim would "verify" it. That direction is exactly what let
    four components from three unrelated repositories (avangrid, pseg-nj)
    pass verification with zero warnings in a real run — each one shared
    enough incidental characters with *something* in the pooled evidence to
    match on a bare `in` check.

    Three directions survive, all anchored rather than raw substring:

    - `claim_n in evidence`, but only when `claim_n` is the evidence's
      trailing path segment (or dot-suffix) — tolerates the LLM citing a
      bare filename ("pipeline_config.py") against a full path
      ("soco_ingest/src/config/pipeline_config.py") without accepting an
      arbitrary fragment match. Both sides are path-normalized first (a
      leading "./", backslashes, duplicate slashes) so "./pipeline_config.py"
      and "pipeline_config.py" are treated identically — a real gap: a
      literal string comparison here previously rejected that pair as
      different claims.
    - Token-set containment: every token of the claim (snake_case/dotted/
      camelCase-split, 3+ chars) must appear among a single evidence item's
      tokens. Tolerates reordering and case/separator differences between
      how the LLM names something and how the indexer stored it, without
      letting one short generic word carry an otherwise-unrelated claim —
      a claim with only one token that itself doesn't already exact- or
      path-match gets no benefit from this path.
    - Squash equality: `PaymentService`, `paymentservice`, `PAYMENTSERVICE`,
      `payment-service`, `payment_service`, and `payment.service` all
      squash to the same `"paymentservice"` key. This is the one case
      token-containment cannot catch: a *single* glued word (no internal
      separator, no camelCase boundary once already lowercased) written
      with different casing or separators than the evidence. Squash
      equality requires every letter/digit to match, in order — it adds no
      false-positive risk beyond what containment already accepts.
    """
    claim_n = normalize_text(normalize_path(claim))
    if not claim_n:
        return True  # nothing to check
    claim_tokens = tokenize(claim_n)
    claim_squashed = squash(claim_n)
    for evidence in evidence_pool:
        if claim_n == evidence:
            return True
        if claim_n and (
            evidence.endswith("/" + claim_n)
            or evidence.endswith("\\" + claim_n)
            or evidence.endswith("." + claim_n)
        ):
            return True
        if len(claim_tokens) >= 2 and claim_tokens.issubset(tokenize(evidence)):
            return True
        if claim_squashed and claim_squashed == squash(evidence):
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
