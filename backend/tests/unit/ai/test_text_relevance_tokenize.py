"""RFC-0017 — generic tokenizer improvements: acronym/short-identifier
preservation and conservative singular/plural normalization.

Every test here is deliberately shape-based, not name-based: no test
relies on the literal string "TnT", "Avangrid", or any other PROT-5764
vocabulary being special-cased anywhere in `tokenize()` — several
unrelated synthetic acronym shapes are used specifically to prove the
mechanism is generic. See `docs/benchmarks/context-discovery/` for the
live benchmark this was built to unblock.
"""

from __future__ import annotations

from app.agents.text_relevance import tokenize

# ---------------------------------------------------------------------------
# Acronym / short-identifier preservation
# ---------------------------------------------------------------------------


def test_acronym_shaped_identifiers_survive_as_whole_tokens() -> None:
    """Several unrelated, synthetic short mixed-case/alphanumeric
    identifiers — none of them real-world acronyms, none hardcoded into
    `tokenize()` — must all survive as their own token. This is the
    generic shape rule ("a digit, or more than one uppercase letter, in a
    2-5 character run") the live benchmark's `TnT`/`S3`/`UDW`/`API` needed,
    proven here on shapes that could never have been special-cased."""
    for identifier, expected in [
        ("XoQ", "xoq"),
        ("Q7", "q7"),
        ("ZzP", "zzp"),
        ("M4x", "m4x"),
        ("WkV", "wkv"),
    ]:
        assert expected in tokenize(identifier), f"{identifier!r} should tokenize to include {expected!r}"


def test_acronym_shaped_identifier_is_not_shredded_by_camelcase_splitting() -> None:
    """The specific failure mode: a short mixed-case run has an internal
    lowercase-then-uppercase transition, which camelCase-boundary
    splitting would normally cut — for an ordinary word that's correct
    (`getUserId` -> `get`/`user`/`id`), but for a short acronym-shaped run
    it destroys the only token that carries any meaning at all. Uses a
    synthetic 3-character shape, not any real acronym."""
    tokens = tokenize("_emit_XoQ_internal_error")
    assert "xoq" in tokens
    # The old (pre-RFC-0017) behavior fragmented it into pieces this short:
    assert "xo" not in tokens
    assert "q" not in tokens


def test_acronym_shape_requires_digit_or_multiple_uppercase() -> None:
    """Not every short capitalized fragment qualifies — an ordinary
    single-capital abbreviation like "Id" or "Ok" must NOT be exempted
    from the normal length filter; only genuinely acronym-shaped runs
    (digit present, or more than one uppercase letter) do. Guards against
    the shape rule over-firing on ordinary short words."""
    assert tokenize("Id") == frozenset()
    assert tokenize("Ok") == frozenset()


def test_long_identifiers_are_unaffected_by_the_acronym_shape_rule() -> None:
    """The acronym-shape exemption is length-bounded (2-5 characters) so
    it can never swallow an ordinary longer identifier just because it
    happens to contain a digit or two capitals somewhere."""
    tokens = tokenize("generate2FactorAuthCode")
    assert "generate2factorauthcode" not in tokens  # not kept whole
    # Pre-existing camelCase-boundary behavior (unchanged by RFC-0017): a
    # digit only splits from a *following* uppercase letter, not from the
    # word before it, so "generate2" stays fused — this test only asserts
    # the acronym-shape rule didn't additionally swallow the whole
    # 24-character identifier, not that digit/letter splitting improved.
    assert {"factor", "auth", "code"} <= tokens


# ---------------------------------------------------------------------------
# Conservative singular/plural normalization
# ---------------------------------------------------------------------------


def test_singular_plural_pairs_normalize_to_the_same_token() -> None:
    pairs = [
        ("schema", "schemas"),
        ("event", "events"),
        ("pipeline", "pipelines"),
        ("policy", "policies"),
        ("category", "categories"),
    ]
    for singular, plural in pairs:
        assert tokenize(singular) == tokenize(plural), f"{singular!r} vs {plural!r}"


def test_plural_normalization_does_not_merge_unrelated_words() -> None:
    """Singular/plural stripping must not accidentally make two genuinely
    different concepts collide into the same token."""
    assert tokenize("schema") != tokenize("scheme")
    assert tokenize("event") != tokenize("even")


# ---------------------------------------------------------------------------
# Identifier form equivalence — snake_case / camelCase / PascalCase / kebab-case
# ---------------------------------------------------------------------------


def test_identifier_forms_all_normalize_to_the_same_token_set() -> None:
    forms = [
        "validate_schema",
        "validate_schemas",
        "validateSchemas",
        "ValidateSchema",
        "validate-schema",
    ]
    expected = frozenset({"validate", "schema"})
    for form in forms:
        assert tokenize(form) == expected, f"{form!r} -> {sorted(tokenize(form))}"


def test_compound_identifier_forms_all_normalize_to_the_same_token_set() -> None:
    forms = ["event_owner", "eventOwner", "EventOwner", "event-owner"]
    expected = frozenset({"event", "owner"})
    for form in forms:
        assert tokenize(form) == expected, f"{form!r} -> {sorted(tokenize(form))}"


# ---------------------------------------------------------------------------
# No over-aggressive suffix stripping
# ---------------------------------------------------------------------------


def test_words_ending_in_double_s_or_common_non_plural_suffixes_are_untouched() -> None:
    """The regular-plural stripper must not fire on words where a trailing
    "s" is not a plural marker at all — the exact failure mode a naive
    `word.rstrip("s")` would produce."""
    for word in ("process", "status", "basis", "class", "access", "success", "focus"):
        tokens = tokenize(word)
        assert word in tokens, f"{word!r} was incorrectly stripped -> {sorted(tokens)}"


def test_short_words_are_never_stripped_regardless_of_trailing_s() -> None:
    """The plural stripper only applies above a minimum length — short
    words like "gas" or "bus" must not be mangled into "ga"/"bu"."""
    for word in ("gas", "bus"):
        tokens = tokenize(word)
        assert word in tokens


def test_derivationally_related_but_distinct_words_are_not_merged() -> None:
    """RFC-0017 is explicitly NOT a stemmer: validate/validation/validator
    are different words and must stay different tokens — only regular
    plural forms of the *same* word are normalized."""
    assert tokenize("validate") != tokenize("validation")
    assert tokenize("validation") != tokenize("validator")
