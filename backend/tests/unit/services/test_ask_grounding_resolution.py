"""Repository resolution safety rails (C-1).

The audit found `resolve_repository` taking the argmax of an IDF-weighted
token-overlap score with no floor, no margin and no notion of a
discriminating term. In a real 67-repository account, six repositories
tied at an identical score on the single generic token "service", and the
winner was decided by database row order — so "What breaks if I change
the payment service?" produced a confident, evidence-badged impact
assessment for `ds-databricks-oe-incentive-data-backup-service-dataingest`,
a repository the user had never mentioned.

Every test here runs against the pure `_resolve` over a hand-built
repository list — no database, no graph, no LLM — so each rule is pinned
independently of any account's data.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.services import ask_grounding as ag


def _repo(name: str, owner: str = "acme") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, f"{owner}/{name}"),
        name=name,
        full_name=f"{owner}/{name}",
    )


# A pool shaped like the real account that produced the audit finding:
# many repositories sharing the generic tokens "service"/"data".
POOL = [
    _repo("bcs-data-service"),
    _repo("bcs-data-service-python"),
    _repo("bcs-data-service-python-delete"),
    _repo("bcs-data-migration-service"),
    _repo("ds-databricks-oe-incentive-data-backup-service-dataingest"),
    _repo("bcs-batch-download-service"),
    _repo("notification-dispatcher"),
    _repo("telemetry-collector"),
    _repo("billing-reconciler"),
]

# The same pool plus a repository the word "payment" genuinely names —
# used to prove the rails reject *guesses*, not legitimate matches.
POOL_WITH_PAYMENTS = [*POOL, _repo("payments-ledger")]


class TestExactMatch:
    def test_exact_repository_name_resolves(self):
        r = ag._resolve("What breaks if I change bcs-batch-download-service?", POOL)
        assert r.status == "resolved"
        assert r.repository.name == "bcs-batch-download-service"
        assert r.reason == "exact_name_match"

    def test_case_and_punctuation_differences_still_resolve(self):
        for question in (
            "impact of BCS Batch Download Service",
            "impact of bcs_batch_download_service",
            "impact of BCS-BATCH-DOWNLOAD-SERVICE?",
        ):
            r = ag._resolve(question, POOL)
            assert r.status == "resolved", question
            assert r.repository.name == "bcs-batch-download-service", question

    def test_full_name_with_owner_resolves(self):
        r = ag._resolve("what depends on acme/payments-ledger", POOL_WITH_PAYMENTS)
        assert r.status == "resolved"
        assert r.repository.name == "payments-ledger"

    def test_most_specific_name_wins_over_its_own_prefix(self):
        """`bcs-data-service` is a token-subset of `bcs-data-service-python`,
        so a naive subset test reports both and turns an unambiguous
        question into a clarification."""
        r = ag._resolve("impact of bcs-data-service-python", POOL)
        assert r.status == "resolved"
        assert r.repository.name == "bcs-data-service-python"

    def test_exact_match_beats_the_score_thresholds(self):
        """A repository whose name is entirely generic tokens is still
        resolvable when the user types it outright — thresholds guard
        guessing, not explicit naming."""
        pool = [*POOL, _repo("data-service")]
        r = ag._resolve("impact of data-service", pool)
        assert r.status == "resolved"
        assert r.repository.name == "data-service"


class TestGenericTermsNeverResolve:
    def test_a_real_discriminating_token_still_resolves(self):
        """The rails reject guesses, not genuine matches: when the account
        actually holds a payments repository, "the payment service" means
        it, and resolution must still succeed."""
        r = ag._resolve("What breaks if I change the payment service?", POOL_WITH_PAYMENTS)
        assert r.status == "resolved"
        assert r.repository.name == "payments-ledger"

    def test_generic_term_only_is_ambiguous_not_a_guess(self):
        """The audit's headline case: no repository in the account has
        anything to do with payments, so the only token that matched
        anything was the generic word "service"."""
        r = ag._resolve("What breaks if I change the payment service?", POOL)
        assert r.status == "ambiguous"
        assert r.repository is None
        assert r.reason == "only_generic_terms_matched"

    def test_nonsense_plus_generic_term_never_resolves(self):
        """Explicit audit regression: this must NOT select a repository."""
        r = ag._resolve("zzzz impact of qqqq service", POOL)
        assert r.status != "resolved"
        assert r.repository is None

    def test_nonexistent_repository_name_does_not_fall_back_to_a_real_one(self):
        r = ag._resolve(
            "What is the impact of changing the totally-nonexistent-service-xyz repository?",
            POOL,
        )
        assert r.status != "resolved"
        assert r.repository is None

    def test_every_listed_generic_token_is_non_discriminating(self):
        for token in ("service", "data", "system", "pipeline", "application", "repository"):
            assert token in ag._GENERIC_TOKENS, token


class TestTiesAndMargins:
    def test_a_multi_way_score_tie_is_ambiguous(self):
        """Four repositories share `bcs`+`data`; nothing separates them."""
        pool = [
            _repo("bcs-data-alpha"),
            _repo("bcs-data-beta"),
            _repo("bcs-data-gamma"),
            _repo("unrelated-thing"),
        ]
        r = ag._resolve("what breaks in bcs data", pool)
        assert r.status == "ambiguous"
        assert r.repository is None

    def test_a_near_tie_within_the_margin_is_ambiguous(self):
        pool = [_repo("telemetry-collector"), _repo("telemetry-collector-v2")]
        r = ag._resolve("impact on telemetry", pool)
        assert r.status == "ambiguous"
        assert r.repository is None

    def test_resolution_is_deterministic_regardless_of_input_order(self):
        """The old implementation broke ties by iteration (DB row) order."""
        question = "What breaks if I change the payment service?"
        first = ag._resolve(question, POOL)
        second = ag._resolve(question, list(reversed(POOL)))
        assert first.status == second.status
        assert [c.full_name for c in first.candidates] == [c.full_name for c in second.candidates]

    def test_a_clear_unique_winner_still_resolves(self):
        r = ag._resolve("what breaks if we change notification-dispatcher", POOL)
        assert r.status == "resolved"
        assert r.repository.name == "notification-dispatcher"


class TestNoMatch:
    def test_no_repositories_indexed(self):
        r = ag._resolve("impact of anything", [])
        assert r.status == "no_match"
        assert r.reason == "no_repositories_indexed"

    def test_question_with_no_usable_terms(self):
        r = ag._resolve("?? !! ??", POOL)
        assert r.status == "no_match"
        assert r.repository is None

    def test_completely_unrelated_question(self):
        r = ag._resolve("what breaks if we change the espresso machine", POOL)
        assert r.status == "no_match"
        assert r.repository is None
        assert r.reason == "below_minimum_confidence"


class TestCandidatesAreOffered:
    def test_ambiguity_returns_candidates_to_choose_from(self):
        r = ag._resolve("What breaks if I change the payment service?", POOL)
        assert r.candidates, "an ambiguous result must offer something to pick"
        assert len(r.candidates) <= ag._MAX_CANDIDATES
        assert all(c.repository_id and c.full_name for c in r.candidates)

    def test_candidates_are_ordered_by_score(self):
        r = ag._resolve("What breaks if I change the payment service?", POOL)
        scores = [c.score for c in r.candidates]
        assert scores == sorted(scores, reverse=True)
