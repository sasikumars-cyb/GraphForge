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

3. Structured warning classification (`VerificationFinding`,
   `NON_BLOCKING_CATEGORIES`). Every `verification_warnings` entry any
   Planning/Development/Testing check below produces is also recorded as a
   `VerificationFinding` with a `category`, tagged at the point it's
   produced — never inferred later from its message text. Downstream
   readiness gates (Engineering Review, Documentation Planning) key their
   blocking decision off `VerificationFinding.blocking`, not off whether
   the warning list is merely non-empty: a real run found that an always-
   present informational disclaimer ("this is a test PLAN, not an
   execution") permanently made `readiness_status: "ready"` unreachable for
   every workflow, because presence-only gating cannot distinguish "nothing
   is wrong" from "something informational was said." `blocking` defaults
   to True for any category not explicitly listed in
   `NON_BLOCKING_CATEGORIES` — a new check that forgets to classify itself,
   or a category typo, fails closed (blocks) rather than silently passing.

4. Repository-scoped file-pair verification (`FilePathVerificationStatus`,
   `build_repository_scoped_evidence`, `verify_file_path_pair` — ADR 0027).
   A **separate** mechanism from (2) above, added for one specific reason
   `verify_claims` cannot serve: `verify_claims` is intentionally
   repository-agnostic (checks whether a string appears *anywhere* in this
   run's evidence), so it cannot tell a real file in the correct
   repository from the same-named real file in the *wrong* repository.
   This mechanism answers a narrower, relational question —
   "does this exact `(repository, file_path)` pair appear together in
   this run's own evidence" — via a single joint, repository-partitioned
   lookup, never two independently-true conditions ANDed together (ADR
   0027 §4.3, Invariant E). `verify_claims` is not modified, not reused,
   and not superseded by this addition; both run independently and
   neither check's result may substitute for or suppress the other's (ADR
   0027 §3, §7 case 23).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.agents.normalization import normalize_path, normalize_text, squash, tokenize

# ---------------------------------------------------------------------------
# -1. "Not yet indexed" self-annotation — a second, competing source of
#     truth this module must never let win.
# ---------------------------------------------------------------------------

# Development/Testing/Planning prompts instruct the LLM: "if a component
# isn't in the graph, say 'not yet indexed' rather than inventing it" — a
# reasonable instinct (don't fabricate a real-looking name/path), but it
# means the model sometimes writes that literal phrase *into* a field this
# module then treats as a factual claim to verify (`comp.name`,
# `RegressionTest.component`, ...). That claim always fails verification
# (the sentinel text obviously isn't real evidence) — correctly — but a
# frontend that renders the field's raw text unconditionally ends up
# displaying "order-service-python (not yet indexed)" for a component that
# *is* genuinely indexed, contradicting the run's own grounding banner.
#
# The fix is not to hide the phrase in CSS/string-filtering at render time
# — it's to never let it become part of the "name" in the first place.
# `strip_not_indexed_annotation` is the one place that recognizes the
# model's self-annotation and separates it from the real claim: callers
# use the returned clean text as the value to store/display, and
# `had_annotation` as an additional (not exclusive — see `verify_claims`
# below) signal that this specific field is unconfirmed.
_NOT_INDEXED_PATTERN = re.compile(
    r"""
    \s*
    (?:
        [-–—(]\s*not\s+yet\s+indexed\s*\)? |  # " - not yet indexed" / " (not yet indexed)"
        ^\s*not\s+yet\s+indexed\s*$                      # the whole field is just the sentinel
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def strip_not_indexed_annotation(text: str) -> tuple[str, bool]:
    """Split a model-written field into (clean_text, had_annotation).

    `had_annotation=True` means the model itself flagged this value as
    unconfirmed — a real signal, but never the final word: `verify_claims`
    on the *clean* text against this run's real evidence pool is what
    actually decides verified vs. unverified (a value the model doubted
    can still turn out to be real; this only ensures the doubt-text itself
    never becomes part of the displayed name/path).
    """
    if not text:
        return text, False
    cleaned = _NOT_INDEXED_PATTERN.sub("", text).strip()
    had_annotation = cleaned != text.strip()
    if not cleaned:
        # The whole field *was* the sentinel (e.g. file_path == "not yet
        # indexed") — nothing real to keep.
        return "", True
    return cleaned, had_annotation


# ---------------------------------------------------------------------------
# -0.5. One canonical "why is/isn't this grounded" state — UX audit P1.3/
#       P1.4.
# ---------------------------------------------------------------------------

#: "grounded" — the graph was reachable and had real data for this run.
#: "unavailable" — a genuine infrastructure failure (the graph service
#:   itself couldn't be reached); retrying later may fix it.
#: "not_indexed" — the graph was reachable but empty/has nothing relevant
#:   indexed yet (e.g. a greenfield idea, or a repo that hasn't been
#:   indexed); indexing a repository is the fix, not retrying.
GroundingStatus = Literal["grounded", "unavailable", "not_indexed"]


def grounding_status(graph_unavailable: bool, has_graph_data: bool) -> GroundingStatus:
    """Planning/Development/Testing each independently compute
    `graph_unavailable`/`has_graph_data` to pick their own `confidence_
    reasoning` prose (three real, distinct states — infra failure vs.
    genuinely-empty-graph vs. grounded). Until now that was the *only*
    place the distinction existed: the frontend's GroundingBanner received
    just `graph_context_used` (collapses "unavailable" and "not_indexed"
    into the same false state) and re-derived its own two-state banner
    text independently — which is how a genuine infrastructure failure
    ended up displayed as "expected for a new project," while the actual
    accurate explanation sat unused in `confidence_reasoning` next to it.
    This is the one place that maps the same two booleans to the same
    three-way answer every caller (backend prose, frontend banner) must
    agree on — never re-derive this classification separately.
    """
    if graph_unavailable:
        return "unavailable"
    if has_graph_data:
        return "grounded"
    return "not_indexed"


# ---------------------------------------------------------------------------
# 0. Structured warning classification
# ---------------------------------------------------------------------------

# The exhaustive set of categories that must NEVER block readiness — every
# category not listed here is treated as blocking, including one nobody has
# thought to classify yet (see module docstring point 3). This is
# deliberately an allowlist, not a denylist: a denylist would default new/
# unknown categories to non-blocking, exactly the silent-pass failure mode
# this mechanism exists to prevent.
NON_BLOCKING_CATEGORIES = frozenset(
    {
        # A statement of fact about the stage itself (e.g. "this is an
        # unexecuted test plan"), true on every run regardless of outcome —
        # never a claim checked against evidence and found wanting, so it
        # can never indicate a real problem with this particular run.
        "informational",
    }
)

# Every category any producer in this codebase currently assigns — kept
# here (rather than only inline at each call site) as the single place a
# reviewer can see the full taxonomy and confirm no blocking category was
# accidentally left out of the classification. Informative only; the
# runtime blocking decision is `category not in NON_BLOCKING_CATEGORIES`,
# not membership in this set.
BLOCKING_CATEGORIES = frozenset(
    {
        "repository_not_found",
        "repository_identity_mismatch",
        "component_not_found",
        "component_misattribution",
        "scope_ambiguity",
        # ADR 0027 §4.5 — a claimed file_path exists in this run's
        # evidence, but not paired with the claimed repository. Distinct
        # from, and mutually exclusive with, `component_not_found` (which
        # fires only when the path matches nothing anywhere) — see
        # `verify_file_path_pair` below for the check that assigns this.
        # Blocking by default already (not listed in
        # `NON_BLOCKING_CATEGORIES`); listed here for documentation
        # completeness only, per this set's own stated purpose.
        "component_repository_mismatch",
        # Assigned by the collector (see engineering_review/agent.py and
        # documentation_planning/agent.py) to any warning read back from a
        # stage result persisted before this classification existed, or
        # from a producer that sets `verification_warnings` without also
        # setting the matching `verification_findings` entry — fails
        # closed rather than silently dropping an unclassified warning
        # from the blocking decision.
        "unclassified_legacy",
    }
)


@dataclass(frozen=True)
class VerificationFinding:
    """One `verification_warnings` entry, classified at the point it's
    produced. `category` drives whether it can block readiness (see
    `NON_BLOCKING_CATEGORIES` above) — `blocking` is derived, never set
    independently, so a category and its blocking behavior can never drift
    apart from each other.
    """

    message: str
    category: str = "unclassified_legacy"

    @property
    def blocking(self) -> bool:
        return self.category not in NON_BLOCKING_CATEGORIES

    def to_dict(self) -> dict[str, str | bool]:
        """Plain-dict shape for storage in a stage result (see
        `PlanningResult.verification_findings` and its Development/Testing
        equivalents) — persisted stage results are already dicts end to
        end, so this avoids coupling this module to any one agent's
        pydantic schema."""
        return {"message": self.message, "category": self.category, "blocking": self.blocking}


def collect_verification_findings(
    stage_results: list[tuple[str, dict[str, Any] | None]],
) -> list[dict[str, str | bool]]:
    """Read every named stage's classified findings into one flat,
    label-prefixed list — the single implementation Engineering Review and
    Documentation Planning both call, replacing what used to be two
    near-identical `_collect_verification_warnings` copies that only ever
    read the unstructured `verification_warnings` text.

    `stage_results` is `[(label, result_dict_or_None), ...]` in the order
    the stages ran, e.g. `[("Planning", planning_result),
    ("Development", development_result), ("Testing", testing_result)]`.

    For each stage: prefers `result["verification_findings"]` (the
    classified form each producer now writes). Falls back to
    `result["verification_warnings"]` — present on every already-persisted
    workflow run from before this classification existed, and on any
    future producer that forgets to populate the structured field — with
    every entry classified `"unclassified_legacy"`, which is a blocking
    category (see `NON_BLOCKING_CATEGORIES`): unmigrated data blocks
    exactly as it always did, it is never silently dropped from the
    blocking decision just because it lacks the new metadata.
    """
    collected: list[dict[str, str | bool]] = []
    for label, result in stage_results:
        result = result or {}
        structured = result.get("verification_findings")
        if structured:
            for item in structured:
                collected.append(
                    {
                        "label": label,
                        "message": str(item.get("message", "")),
                        "category": str(item.get("category", "unclassified_legacy")),
                        "blocking": bool(item.get("blocking", True)),
                    }
                )
        else:
            for message in result.get("verification_warnings") or []:
                collected.append(
                    {
                        "label": label,
                        "message": message,
                        "category": "unclassified_legacy",
                        "blocking": True,
                    }
                )
    return collected


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

# Ordinary English words, written in caps for emphasis in ticket/requirement
# text ("This must be the ONLY change", "MUST NOT modify any other file"),
# match the same 2-6-uppercase-letter shape as a genuine tenant/business-unit
# code by pure coincidence. A real run flagged the literal word "ONLY" —
# from the ticket's own "This must be the ONLY change" — as an unmatched
# entity token, checked against an unrelated repository.
#
# Rather than hand-picking one offending word at a time as each new false
# positive is reported (an unbounded, always-behind stopword list), this is
# a representative set of common short English function/emphasis words —
# the vocabulary that actually shows up in *this* codebase's kind of ticket
# text (constraints, quantifiers, modals, conjunctions), not a general-
# purpose dictionary. Genuine tenant/business-unit codes (APC, GPC, PSEG,
# MPC) are not ordinary English words; this check exists to tell "written
# in caps for emphasis" apart from "looks like a business-unit code"
# without caring which specific word triggered it.
_COMMON_ENGLISH_WORDS = frozenset(
    {
        "A",
        "I",
        "AM",
        "AN",
        "AS",
        "AT",
        "BE",
        "BY",
        "DO",
        "GO",
        "IF",
        "IN",
        "IS",
        "IT",
        "MY",
        "NO",
        "OF",
        "ON",
        "OR",
        "SO",
        "TO",
        "UP",
        "US",
        "WE",
        "ALL",
        "ANY",
        "ARE",
        "BOTH",
        "BUT",
        "CAN",
        "DID",
        "DOES",
        "DONE",
        "EACH",
        "END",
        "FEW",
        "FOR",
        "FROM",
        "HAD",
        "HAS",
        "HAVE",
        "HER",
        "HIM",
        "HIS",
        "HOW",
        "INTO",
        "ITS",
        "JUST",
        "LESS",
        "MANY",
        "MAY",
        "MIGHT",
        "MORE",
        "MOST",
        "MUCH",
        "MUST",
        "NEVER",
        "NONE",
        "NOR",
        "NOT",
        "NOW",
        "OFF",
        "ONCE",
        "ONLY",
        "ONTO",
        "OUR",
        "OUT",
        "OVER",
        "OWN",
        "PER",
        "SAME",
        "SHALL",
        "SHOULD",
        "SINCE",
        "SOME",
        "SUCH",
        "THAN",
        "THAT",
        "THE",
        "THEIR",
        "THEM",
        "THEN",
        "THERE",
        "THESE",
        "THEY",
        "THIS",
        "THOSE",
        "THUS",
        "TOO",
        "UNTIL",
        "VERY",
        "WAS",
        "WERE",
        "WHAT",
        "WHEN",
        "WHERE",
        "WHICH",
        "WHILE",
        "WHO",
        "WHOSE",
        "WHY",
        "WILL",
        "WITH",
        "WITHIN",
        "WITHOUT",
        "WOULD",
        "YET",
        "YOU",
        "YOUR",
        "ABOVE",
        "AFTER",
        "AGAIN",
        "AGAINST",
        "ALSO",
        "ALWAYS",
        "AMONG",
        "BEFORE",
        "BEING",
        "BELOW",
        "BETWEEN",
        "DOING",
        "DOWN",
        "DURING",
        "EITHER",
        "EVERY",
        "HAVING",
        "HERE",
        "NEITHER",
        "OTHER",
        "TOGETHER",
        "UNDER",
        "UNLESS",
        "ACROSS",
        "AROUND",
        "BECAUSE",
        "BEHIND",
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
    return {
        t
        for t in tokens
        if _ACRONYM_SHAPE.match(t)
        and t not in _GENERIC_ACRONYM_STOPWORDS
        and t not in _COMMON_ENGLISH_WORDS
    }


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


def check_entity_mismatch(ticket_text: str, selected_repo_names: str | list[str]) -> str | None:
    """Warn when the ticket names an entity/tenant-shaped token that none
    of the *actually selected* target repositories' names contain.

    `selected_repo_names` accepts either a single repository name (kept for
    backward compatibility with existing callers) or a list — a multi-repo
    plan's token is only flagged when it's absent from *every* selected
    repository's name; present in any one of them is a match. Callers must
    pass the real target/selected repositories here, never an arbitrary
    top-ranked candidate from a broader survey — checking against a
    repository the plan was never actually going to touch produces a
    warning with nothing to do with the actual change (a real run flagged
    'ingestion-framework' this way while the objective named a completely
    different, explicitly selected repository).

    Returns None (no-op) whenever the ticket has no acronym-shaped token at
    all — this is the common case, and the check must never block or alter
    repository selection for an ordinary ticket. It only fires in the
    narrow situation this was built for: a ticket that does carry such a
    token, matched against repositories whose names carry none of them.

    Not a general "does this ticket match this repo" solver — see the
    module docstring for what this deliberately does not catch.
    """
    names = (
        [selected_repo_names] if isinstance(selected_repo_names, str) else list(selected_repo_names)
    )
    names = [n for n in names if n]
    if not names:
        return None
    ticket_tokens = _extract_acronym_tokens(ticket_text)
    if not ticket_tokens:
        return None
    repo_tokens: set[str] = set()
    for name in names:
        repo_tokens |= _name_tokens(name)
    unmatched = {t for t in ticket_tokens if t.lower() not in repo_tokens}
    if not unmatched:
        return None
    plural = "s" if len(unmatched) != 1 else ""
    names_str = ", ".join(f"'{n}'" for n in names)
    repo_word = "repository name" if len(names) == 1 else "repository names"
    return (
        f"Ticket references entity/tenant-shaped token{plural} "
        f"{', '.join(sorted(unmatched))} not found in the selected {repo_word} "
        f"{names_str}. Repository name similarity does not "
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


# ---------------------------------------------------------------------------
# 4. Repository-scoped file-pair verification (ADR 0027)
# ---------------------------------------------------------------------------

#: NOT_CHECKED = verification could not be performed because the required
#: evidence was unavailable (no repository-scoped evidence pool existed to
#: check against at all).
#: UNVERIFIED = verification was performed against available evidence, but
#: the exact (repository, file_path) pair was not found in it — this does
#: NOT mean the proposed change is invalid; it describes evidence state
#: only (ADR 0027 §4.4). A genuinely new file has no existing evidence to
#: match and is expected to be UNVERIFIED.
#: VERIFIED = the exact (repository, file_path) pair was deterministically
#: found in this run's own repository-scoped evidence.
FilePathVerificationStatus = Literal["not_checked", "verified", "unverified"]

NOT_CHECKED: FilePathVerificationStatus = "not_checked"
VERIFIED: FilePathVerificationStatus = "verified"
UNVERIFIED: FilePathVerificationStatus = "unverified"


def build_repository_scoped_evidence(
    components: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """repository -> the set of (normalized) file_paths this run's own
    tool-derived evidence (e.g. `ComponentDiscoveryTool`) actually
    returned for that specific repository.

    Callers must pass the *raw* per-component evidence dicts as returned
    by the discovery tool (each with its own `"repository"`/`"file_path"`
    keys intact) — never a pre-flattened pool. This is the one place in
    this module repository attribution is preserved rather than discarded
    (contrast with `build_evidence_pool`, which is deliberately flat and
    repository-agnostic for its own, different purpose).
    """
    by_repository: dict[str, set[str]] = {}
    for component in components:
        repository = component.get("repository")
        file_path = component.get("file_path")
        if not repository or not file_path:
            continue
        by_repository.setdefault(repository, set()).add(normalize_path(str(file_path)))
    return by_repository


def verify_file_path_pair(
    repository: str,
    file_path: str,
    evidence_by_repository: dict[str, set[str]],
) -> FilePathVerificationStatus:
    """The sole function permitted to produce `VERIFIED` for a Development
    component's file_path (ADR 0027 §4.2, Invariant F).

    A single joint containment test — `file_path in
    evidence_by_repository.get(repository, set())` — never two
    independently-true conditions ANDed together (Invariant E). Exact,
    normalized-path equality only: no fuzzy matching, no token overlap, no
    trailing-segment/bare-filename tolerance (unlike `_claim_supported`'s
    deliberately more permissive matching for its own, different purpose)
    — ADR 0027 explicitly excludes "filename alone" as a sufficient basis
    for VERIFIED.
    """
    if not evidence_by_repository:
        return NOT_CHECKED
    if not repository or not file_path:
        return UNVERIFIED
    normalized_path = normalize_path(file_path)
    if normalized_path in evidence_by_repository.get(repository, set()):
        return VERIFIED
    return UNVERIFIED


def file_path_exists_in_any_repository(
    file_path: str,
    evidence_by_repository: dict[str, set[str]],
) -> bool:
    """Whether `file_path` appears under *some* repository in the
    evidence, regardless of which one — used only to choose between the
    `component_not_found` and `component_repository_mismatch` diagnostic
    categories (ADR 0027 §4.5). Never consulted by `verify_file_path_pair`
    itself and never used to produce `VERIFIED` — existence in the wrong
    repository is exactly the case that must remain `UNVERIFIED`."""
    if not file_path:
        return False
    normalized_path = normalize_path(file_path)
    return any(normalized_path in paths for paths in evidence_by_repository.values())
