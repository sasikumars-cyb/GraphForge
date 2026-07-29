"""Tests for app.agents.normalization — the shared, deterministic
case/separator/path canonicalization used by claim verification
(app.agents.verification) and repository/file verification
(app.agents.code_generation.verification).
"""

from __future__ import annotations

from app.agents.normalization import (
    normalize_path,
    normalize_text,
    squash,
    strip_known_extension,
    tokenize,
)

# ---------------------------------------------------------------------------
# Case normalization (squash)
# ---------------------------------------------------------------------------


class TestSquashCaseNormalization:
    def test_camel_case_and_lowercase_and_uppercase_are_equivalent(self):
        assert squash("PaymentService") == squash("paymentservice") == squash("PAYMENTSERVICE")

    def test_squash_is_deterministic_and_repeatable(self):
        for _ in range(5):
            assert squash("PaymentService") == "paymentservice"


class TestSquashSeparatorNormalization:
    def test_hyphen_underscore_slash_dot_all_equivalent(self):
        variants = [
            "payment-service",
            "payment_service",
            "payment/service",
            "payment.service",
            "paymentservice",
        ]
        squashed = {squash(v) for v in variants}
        assert squashed == {"paymentservice"}

    def test_squash_is_precise_not_fuzzy(self):
        """Squash equality requires every letter/digit to match, in order
        — it must not equate genuinely different names."""
        assert squash("paymentservice") != squash("paymentservices")
        assert squash("paymentservice") != squash("paymentservicev2")


class TestTokenize:
    def test_separators_all_produce_same_tokens(self):
        expected = frozenset({"payment", "service"})
        assert tokenize("payment-service") == expected
        assert tokenize("payment_service") == expected
        assert tokenize("payment/service") == expected
        assert tokenize("payment.service") == expected
        assert tokenize("PaymentService") == expected

    def test_short_tokens_dropped(self):
        # "py"/"id"/"db" style short fragments must not survive — they are
        # exactly what makes containment gameable.
        assert "py" not in tokenize("payment_service.py")
        assert "id" not in tokenize("payment_id")

    def test_namespace_tokenizes_into_superset(self):
        namespace_tokens = tokenize("com.company.payment.PaymentService")
        assert namespace_tokens == frozenset({"com", "company", "payment", "service"})
        assert tokenize("PaymentService").issubset(namespace_tokens)
        assert tokenize("payment.PaymentService").issubset(namespace_tokens)


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------


class TestNormalizePath:
    def test_leading_dot_slash_stripped(self):
        assert normalize_path("./src/payment_service.py") == "src/payment_service.py"

    def test_repeated_leading_dot_slash_stripped(self):
        assert normalize_path("././src/x.py") == "src/x.py"

    def test_backslashes_normalized_to_forward_slashes(self):
        assert normalize_path("src\\payment_service.py") == "src/payment_service.py"

    def test_duplicate_slashes_collapsed(self):
        assert normalize_path("src//payment_service.py") == "src/payment_service.py"

    def test_bare_filename_unaffected(self):
        assert normalize_path("payment_service.py") == "payment_service.py"

    def test_does_not_resolve_parent_segments(self):
        """Deliberately narrow — see module docstring: a '..' segment is a
        destination-safety concern for the caller to reject outright, not
        something this function silently resolves away."""
        assert normalize_path("src/../payment_service.py") == "src/../payment_service.py"


# ---------------------------------------------------------------------------
# Extension normalization — deliberately narrow
# ---------------------------------------------------------------------------


class TestStripKnownExtension:
    def test_strips_recognized_extensions(self):
        assert strip_known_extension("payment.py") == "payment"
        assert strip_known_extension("Payment.java") == "Payment"
        assert strip_known_extension("payment.kt") == "payment"
        assert strip_known_extension("Payment.scala") == "Payment"

    def test_unrecognized_extension_left_unchanged(self):
        assert strip_known_extension("payment.xyz") == "payment.xyz"

    def test_no_extension_left_unchanged(self):
        assert strip_known_extension("payment") == "payment"


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


class TestNormalizeText:
    def test_camel_case_split_before_lowercasing(self):
        assert normalize_text("TransformManifestParser") == "transform_manifest_parser"

    def test_whitespace_folded(self):
        assert normalize_text("  Payment   Service  ") == "payment service"

    def test_repeatable(self):
        for _ in range(5):
            assert normalize_text("PaymentService") == "payment_service"
