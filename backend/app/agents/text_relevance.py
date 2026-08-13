"""Token-overlap relevance scoring, shared by any agent that ranks
free-text or identifier-like items against a set of search terms.

Extracted from `app.agents.planning.tools`, which used this to rank
Components/Repositories against a planning brief — the algorithm itself
has nothing planning- or component-specific about it (only each caller's
own "what text does this item get matched on" function does), so
`app.agents.testing.tools`' TestRailCoverageTool reuses it unchanged
rather than re-implementing the same tokenization/IDF-weighting logic a
second time.
"""

from __future__ import annotations

import math
import re

_SEGMENT_BOUNDARY_RE = re.compile(r"[^a-zA-Z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_MIN_TOKEN_LENGTH = 3

# RFC-0017 — a segment this short is exempted from both camelCase-boundary
# splitting and the minimum-length filter below when its *shape* (not its
# specific spelling) marks it as a short code rather than an ordinary word:
# a digit anywhere in it ("S3"), or more than one uppercase letter ("TnT",
# "UDW", "API"). Deliberately shape-based and length-bounded, not a name
# lookup — it fires for any acronym-shaped 2-5 character segment, never a
# specific one. Below this length, splitting "TnT" the normal way (camel-
# boundary insert, then a 3-character minimum) leaves "tn"/"t", both too
# short to survive — the acronym vanishes entirely even though it may be
# the single most specific term in the text it came from.
_MIN_ACRONYM_LENGTH = 2
_MAX_ACRONYM_LENGTH = 5

# RFC-0017 — conservative regular-plural stripping only, never a general
# stemmer: derivationally related but distinct words (validate/validation/
# validator) are deliberately left untouched — merging those would be a
# much larger, riskier claim than "schema and schemas are the same word."
# The skip-suffixes below are the common English shapes where a trailing
# "s" is NOT a plural marker (class/status/basis/chaos), guarding against
# exactly the over-stripping failure mode ("process" -> "proces") a naive
# `word.rstrip("s")` would produce.
_NOT_A_PLURAL_SUFFIXES = ("ss", "us", "is", "os")
_MIN_LENGTH_TO_STRIP_PLURAL = 5


def _is_acronym_shaped(segment: str) -> bool:
    if not (_MIN_ACRONYM_LENGTH <= len(segment) <= _MAX_ACRONYM_LENGTH):
        return False
    has_digit = any(c.isdigit() for c in segment)
    upper_count = sum(1 for c in segment if c.isupper())
    return has_digit or upper_count > 1


def _normalize_plural(word: str) -> str:
    """Strip a regular English plural marker, conservatively. Only two
    shapes are handled — trailing "ies" (`policies` -> `policy`) and a
    plain trailing "s" outside the `_NOT_A_PLURAL_SUFFIXES` guard — because
    those are the only two shapes safe to apply without a real dictionary.
    This is not a complete stemmer and does not try to be one: irregular
    plurals, and non-plural words that merely end in "s" in a shape this
    guard doesn't catch (e.g. "always"), are known, accepted gaps — see
    RFC-0017's write-up for why a full stemmer/NLP dependency was not used.
    """
    if word.endswith("ies") and len(word) > _MIN_LENGTH_TO_STRIP_PLURAL:
        return word[:-3] + "y"
    if (
        word.endswith("s")
        and not word.endswith(_NOT_A_PLURAL_SUFFIXES)
        and len(word) > _MIN_LENGTH_TO_STRIP_PLURAL
    ):
        return word[:-1]
    return word


def tokenize(text: str) -> frozenset[str]:
    """Split an identifier, file path, title, or free-text string into its
    individual sub-words: `snake_case`, `dotted.paths`, `slash/separated`,
    and `camelCase` are all token breaks — the same tokenization a code
    search engine applies to identifiers.

    This is what an item's text and a search term are compared through,
    rather than raw substring containment. Substring containment treats
    "process" as a hit inside "test_already_**process**ed_file" and
    "page" as a hit inside "**page**ination" — real words that happen to
    contain the query as a fragment, not an actual match. Token equality
    doesn't make that mistake, and it composes correctly for multi-word
    terms too: "rate_attribute" tokenizes to {"rate", "attribute"}, which
    overlaps *both* words of `transform_rate_attribute`'s {"transform",
    "rate", "attribute"} — two-token overlap naturally outscores any
    single incidental one-token match without needing separate phrase
    handling.

    RFC-0017 — each `[^a-zA-Z0-9]+`-delimited segment is handled one of
    two ways before the result: an acronym-shaped segment (`_is_acronym_
    shaped`) is kept whole, lowercased, bypassing both camelCase-splitting
    and the length minimum (so "TnT", "S3", "UDW" survive as single
    tokens); every other segment goes through the original camelCase-
    boundary split, is lowercased, has a conservative plural suffix
    stripped (`_normalize_plural`), and is kept only if still >=
    `_MIN_TOKEN_LENGTH` characters — unchanged from before for ordinary
    words.
    """
    tokens: set[str] = set()
    for segment in _SEGMENT_BOUNDARY_RE.split(text):
        if not segment:
            continue
        if _is_acronym_shaped(segment):
            tokens.add(segment.lower())
            continue
        spaced = _CAMEL_BOUNDARY_RE.sub(r"\1_\2", segment)
        for sub in spaced.lower().split("_"):
            sub = _normalize_plural(sub)
            if len(sub) >= _MIN_TOKEN_LENGTH:
                tokens.add(sub)
    return frozenset(tokens)


def relevance(text: str, terms: list[str], weights: dict[str, float] | None = None) -> float:
    """How much this text matches the search terms, by token overlap —
    flat count of matching tokens, or (when `weights` is given) the sum of
    each matched token's weight. See `term_weights` for where the weights
    come from."""
    text_tokens = tokenize(text)
    term_tokens = tokenize(" ".join(terms))
    matched = text_tokens & term_tokens
    if weights is None:
        return float(len(matched))
    return sum(weights.get(t, 1.0) for t in matched)


def term_weights(terms: list[str], texts: list[str]) -> dict[str, float]:
    """Inverse-document-frequency weight for each search-term token over
    `texts` — the match-text of every item in the pool being ranked
    (e.g. `[match_text(c) for c in components]`, or a TestCase title per
    case). A token that matches almost every item is nearly worthless as
    a discriminator; a token that matches one or two is exactly what
    should decide the ranking.

    This is what lets `terms` mix two very different kinds of vocabulary
    safely: a fixed, deliberately-generic capability-keyword list and
    free-text terms pulled straight out of a brief (deliberately
    specific, e.g. a field or function name mentioned once). A flat
    "one match = one point" count lets the generic terms drown out the
    specific ones just by matching more often; weighting by rarity fixes
    that without needing to hand-classify which list a term came from,
    and self-calibrates to whatever this item pool's own vocabulary is —
    no fixed thresholds, no stopword tuning per domain.

    The falloff is logarithmic, not linear. A raw `1/(1+df)` is far too
    steep to use as a ranking weight — see the caller-facing discussion
    of this in git history for `app.agents.planning.tools`; the ~54x
    penalty a linear falloff produced for genuinely on-topic-but-common
    terms is why this uses `1/(1+log1p(df))` instead, which compresses
    that gap to ~3x so rarity breaks ties rather than overruling
    topicality outright.
    """
    term_tokens = tokenize(" ".join(terms))
    if not term_tokens:
        return {}
    token_sets = [tokenize(t) for t in texts]
    weights: dict[str, float] = {}
    for t in term_tokens:
        df = sum(1 for ts in token_sets if t in ts)
        # A term matching zero items in this pool wasn't measured as rare
        # — it wasn't measured at all, and log1p(0)==0 would otherwise
        # land it at the maximum possible weight, 1.0. Pin it to 0.0
        # explicitly rather than relying on `relevance`'s `.get(t, 1.0)`
        # fallback to ever agree — an absent key still resolves to 1.0
        # there, so leaving df=0 terms out of this dict is not the same
        # fix as this (matters when `weights` is later used to score text
        # — e.g. a bare repository name — that never went into the df
        # count and could still contain a df=0 term).
        weights[t] = 0.0 if df == 0 else 1.0 / (1 + math.log1p(df))
    return weights
