"""Shared deterministic normalization utilities.

Centralizes the case/separator/path canonicalization rules every
deterministic validator in this codebase needs before comparing two
strings that name "the same thing" written two different ways —
`app.agents.verification` (claim-vs-evidence matching) and
`app.agents.code_generation.verification` (repository/file-path
verification) both used a private copy of this logic before; this module
is the one place it lives now, per the "centralize normalization" design
goal — no AI, no fuzzy/semantic matching, just canonicalization and token
splitting, all O(n) in the length of the input string.

Every function here is a pure string transform: no I/O, no config, no
knowledge of any particular language, framework, or repository. That is
deliberate — this module is what keeps every consumer of it
language-independent, inexpensive, and repeatable (see Part 3's
requirements for the claim verifier: deterministic, safe to run on every
workflow stage, never dependent on another LLM).

What this module intentionally does NOT do (see its docstrings for why
each is unsupported, not just unimplemented):

- Cross-language file extension equivalence (`payment.py` == `payment.java`)
  — conflating two different real files in different languages is a
  correctness risk, not a normalization win. See `strip_known_extension`.
- Fuzzy/edit-distance or semantic similarity matching — explicitly out of
  scope per Part 9 ("avoid expensive fuzzy matching algorithms... avoid
  semantic similarity models").
- Equating an implementation file with its test file
  (`PaymentService` vs `PaymentServiceTest`) — a test file existing does
  not verify a claim about the implementation file.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Case + separator normalization
# ---------------------------------------------------------------------------

_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_TOKEN_BOUNDARY_RE = re.compile(r"[^a-z0-9]+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def normalize_text(s: str) -> str:
    """Case/whitespace-fold, and split camelCase boundaries with an
    underscore before lowercasing — not after.

    Lowercasing first and camelCase-splitting second (the order
    `tokenize` alone would produce if it received an already-normalized
    string) destroys the very information the split needs: "Transform
    ManifestParser" only has a detectable boundary while the "M" is still
    uppercase. Doing it here means both an exact-match comparison and
    `tokenize`'s output see "transform_manifest_parser" for a component
    genuinely named `TransformManifestParser`, instead of a single glued
    token nothing could ever match against.
    """
    spaced = _CAMEL_BOUNDARY_RE.sub(r"\1_\2", s)
    return re.sub(r"\s+", " ", spaced.strip().lower())


def tokenize(s: str) -> frozenset[str]:
    """Split into sub-word tokens on snake_case/dotted/slash/camelCase
    boundaries. Tokens under 3 characters are dropped — they are exactly
    the generic fragments (`id`, `db`, `is`) that make token-set
    containment gameable.

    This one function is what makes case, separator (`-`/`_`/`/`/`.`),
    and namespace normalization (Part 2) mostly fall out for free: any
    separator character is a token boundary, so `payment-service`,
    `payment_service`, `payment/service`, `payment.service`, and
    `PaymentService` all tokenize to the same `{"payment", "service"}`,
    and `com.company.payment.PaymentService` tokenizes to
    `{"com", "company", "payment", "service"}` — a superset a shorter
    claim's tokens can still be a subset of.
    """
    spaced = _CAMEL_BOUNDARY_RE.sub(r"\1_\2", s)
    return frozenset(t for t in _TOKEN_BOUNDARY_RE.split(spaced.lower()) if len(t) >= 3)


def squash(s: str) -> str:
    """Case- and separator-insensitive canonical key: strip every
    non-alphanumeric character and lowercase, WITHOUT camelCase splitting
    or a minimum token length.

    This is the tier `tokenize`-based containment cannot cover: a single
    glued word written with different casing or separators entirely —
    `PaymentService`, `paymentservice`, `PAYMENTSERVICE`, `payment-service`,
    `payment_service`, and `payment.service` all squash to
    `"paymentservice"`. `tokenize("paymentservice")` (no separators, no
    camelCase boundary because it's already all-lowercase) is a single
    11-character token that a two-token claim's tokens could never be a
    "subset" of the *other* direction either — `squash` is an exact,
    precise equality check (not containment), so it adds no false-positive
    risk: every letter and digit must match, in the same order, on both
    sides.
    """
    return _NON_ALNUM_RE.sub("", s.lower())


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------

_LEADING_DOT_SLASH_RE = re.compile(r"^(?:\./)+")
_DUP_SLASH_RE = re.compile(r"/{2,}")


def normalize_path(path: str) -> str:
    """Canonicalize a file path string before comparison: backslashes to
    forward slashes, a leading `./` (repeated or not) stripped, and
    duplicate slashes collapsed.

    Deliberately narrow — this is canonicalization, not path resolution:
    it does not resolve `..` segments (a claim containing one is a
    destination-safety problem `validate_file_operations` already rejects
    outright, not something to silently normalize away) and does not
    make relative paths absolute (there is no filesystem root to resolve
    against here; the whole point of this module is to stay I/O-free).
    """
    p = path.replace("\\", "/")
    p = _LEADING_DOT_SLASH_RE.sub("", p)
    p = _DUP_SLASH_RE.sub("/", p)
    return p


# ---------------------------------------------------------------------------
# Extension normalization — deliberately narrow, see module docstring
# ---------------------------------------------------------------------------

# Recognized source-code extensions this module knows how to strip for a
# same-language basename comparison (e.g. matching a bare class name claim
# against a file path evidence item). Cross-language extensions are never
# treated as equivalent to each other — see module docstring.
_KNOWN_EXTENSIONS = (
    ".py",
    ".java",
    ".kt",
    ".kts",
    ".scala",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rb",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
)


def strip_known_extension(path: str) -> str:
    """Strip one recognized trailing source-code extension, if present.
    Returns `path` unchanged otherwise — including when the extension
    isn't one this module recognizes, rather than guessing."""
    for ext in _KNOWN_EXTENSIONS:
        if path.endswith(ext):
            return path[: -len(ext)]
    return path
