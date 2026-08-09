# ADR 0025: Phase 3 — Hypothesis ↔ Verification Correlation

## Status

Proposed. Design only — **no implementation in this ADR or alongside it.**
Written in direct response to the known limitation ADR 0024 §7/§16
documents and leaves open. Revised once already, after a design-review
round found the first draft's repository-only matching insufficient to
prevent false correlation (§2a, §4's corrected Option A, §8) — corrected
in place rather than superseded, following the same discipline ADR 0024
used for its own post-QA corrections. Do not begin implementation until
this document is reviewed and a recommendation below is explicitly
approved.

## 1. Problem statement

Confirmed in Report V2 Phase 2's final QA pass, and proven (not assumed)
against the real pipeline in `test_report_view_model.py::
TestRealPipelineNeverCorrelatesHypothesisVerification`:

**A hypothesis's `verification_status` is `NOT_CHECKED` in production
today, always, with no exception** — not because verification rarely
happens to overlap with a hypothesis, but because no code path anywhere
connects the two, even when they plainly describe the same real-world
claim. `map_knowledge_ledger_rows` (ADR 0024's Phase 1 dependency,
unchanged since) hardcodes `verification_status=None` on every
hypothesis-sourced `LedgerRow` it builds, by explicit, documented design:
*"a hypothesis is reasoning, never a code-run check."*

The result: the two-axis Knowledge Ledger's own stated purpose —
representing `SUPPORTED+VERIFIED`, `SUPPORTED+UNVERIFIED`, and every other
real combination of synthesis belief and deterministic confirmation — is
only half-realized. The synthesis axis is real and populated. The
verification axis is real and populated. They have simply never been the
same row.

**What "done" means for Phase 3:** at least one real, honest path exists
by which a hypothesis can end up `VERIFIED` or `UNVERIFIED` instead of
permanently `NOT_CHECKED`, without violating any commitment already made
in ADR 0024 or the Phase 1 decision report — specifically: no new LLM
call added to manufacture a link that doesn't already exist, no fabricated
evidence↔hypothesis relationship, no confidence-based guess dressed up as
a deterministic check.

## 2. Why this is hard — the actual constraint, traced through real code

Not a rendering problem (Phase 2 already renders the correlation
correctly if it existed — proven in
`test_verification_status_correlated_from_ledger_by_position`). The
constraint is that **nothing downstream of Context Discovery currently
knows a hypothesis exists at all.**

Traced through the real prompt-construction and verification call sites:

- `Hypothesis.description` (`understanding.py`) is free-text prose, e.g.
  *"The change bumps a timeout in the agent-runtime application code
  (API route or repository summary agent)..."* — no structured field
  identifies what repository, component, or claim-type it's actually
  about.
- Planning's own prompt is built from `engineering_understanding` (the
  flat, already-synthesized `EngineeringUnderstanding` DTO) — `context_
  discovery.schemas.ContextDiscoveryResult`'s own docstring is explicit
  that `investigation_workspace` (which carries `hypotheses`) *"must
  never reach Planning."* This is a deliberate Phase 1 boundary, not an
  oversight — Planning's prompt was kept free of unresolved, competing,
  possibly-rejected explanations so it always argues from the settled
  conclusion, never from the scratch work behind it.
- `VerificationFinding` (`app.agents.verification`) — the type behind
  every `verification_findings[]` entry across Planning, Development, and
  Testing — is `{message: str, category: str}`. No claim ID, no
  hypothesis reference, no structured subject. It is produced entirely
  independently, by code that has never seen a hypothesis.
- Proven directly: a hypothesis and a `verification_findings[]` entry
  with **byte-identical claim text** still produce two separate,
  uncorrelated `LedgerRow`s — there is no correlation of any kind today,
  text-based or otherwise (`test_even_with_matching_verification_
  findings_text_no_correlation_occurs`).

So the real design question is not "how do we match two lists" — it's
**where, if anywhere, does a stable reference get created**, and does
creating it require crossing the Planning-never-sees-hypotheses boundary
Phase 1 deliberately built.

## 2a. What a `VerificationFinding` actually checks — traced precisely (added after review)

A second review round asked a sharper question than §2 answered: even
with a shared scope identifier (e.g. the same repository), does a
verification finding actually confirm or refute the *specific assertion*
a hypothesis makes? Traced through every real call site — `app.agents.
verification.verify_claims`/`check_entity_mismatch`, and every place
Planning (`planning/agent.py:930-1070`), Development (`development/
agent.py:455-540`), and Testing (`testing/agent.py:563-650`) call them —
the answer is precise and has a consequence for the design that §4's
original Option A did not account for.

**Q1 — What does a `VerificationFinding` represent today?**
Exactly one thing, in every real producer: *"does a specific named
entity (a repository, a file path, or a component name) that something
else claimed actually appear in this run's own indexed evidence, and if
so, is it attributed to the repository it was claimed for."* Nothing
else. `VerificationFinding.category` is always one of `repository_not_
found`, `repository_identity_mismatch`, `component_not_found`,
`component_misattribution`, `scope_ambiguity`, `informational`, or
`unclassified_legacy` — every one of those is an existence-or-
attribution question about a named thing, never a statement about
system behavior.

**Q2 — What entities/claims does Planning/Development/Testing actually
verify?** Read every call site directly:
- Planning: `usage.files_affected` (file paths, per repository) and
  `planning_result.affected_components` (component names) — both
  checked via `verification.verify_claims(claims, evidence_pool)`
  against this run's own indexed file/component pool
  (`planning/agent.py:953,1025`). Also repository-name entity mismatches
  via `check_entity_mismatch` (`planning/agent.py:699`), and test-class-
  named-as-production-code via `check_test_used_as_production`
  (`planning/agent.py:1004`).
- Development: `[r.name for r in plan.repositories]` and `[comp.name,
  comp.file_path]` — same shape, same `verify_claims` call
  (`development/agent.py:528,536`).
- Testing: `test_plan.affected_repositories` and a component-claims list
  — same shape again (`testing/agent.py:569,648`).

Every single one is **"does this named repository/file/component exist
and is it correctly attributed"** — never "is this assertion about
behavior, causation, timing, or ownership-reasoning true." There is no
verification call site anywhere in this codebase that evaluates a
behavioral or causal claim. `verify_claims`'s own docstring says this
directly: *"Generic string membership only... it never looks at source
code, only at what this run's own tools already returned."*

**This directly answers the motivating example.** A hypothesis like *"
Concurrent ingestion runs may race and both write an active record"* is
a causal/behavioral claim. A finding like *"No evidence of concurrent
ingestion runs was found"* does not, and structurally cannot, come from
any real producer in this codebase today — nothing here evaluates
concurrency behavior. If such a finding ever existed, correlating it to
that hypothesis by repository alone would be exactly the false
correlation the review is warning against: same scope, unrelated claim
types, no shared entity being checked.

**Q3 — Can an existing finding be deterministically associated with a
hypothesis using repository, exact claim text, file/path, component,
category, or a combination?**
- **Repository alone: no.** Confirmed by the example above — shared
  scope proves nothing about shared claim.
- **Exact claim text: only in the degenerate case** where a hypothesis's
  entire assertion already *is* an entity name (e.g. a hypothesis whose
  `description` is reducible to "the handler is `payment_service.py`")
  — vanishingly rare for prose hypotheses, and not something to build a
  design around.
- **File/path or component name: meaningfully better than repository,
  but insufficient alone.** Two different, unrelated hypotheses about
  the *same file* (one behavioral, one about ownership) would both
  incorrectly correlate to any finding checking that file's existence,
  under a scope-only rule.
- **Verification category: not a correlation key at all** — it says
  *what kind* of check ran, not *which claim* it was checking.
  Orthogonal information, useful for display, useless for correlation.
- **Any combination of the above, without a claim-type check: still
  insufficient**, for the same reason repository-alone is insufficient
  — matching scope (however precisely) never proves the finding is
  evaluating the hypothesis's actual assertion, because scope and claim
  are different axes.

**The real, missing filter is claim-type compatibility, not scope
precision.** Verification only ever checks existence/attribution claims.
A hypothesis can only ever be honestly correlated to a verification
finding when **the hypothesis's own claim is itself an existence/
attribution claim about a named repository, file, or component** — the
same claim type `verify_claims` already evaluates for everything else.
For any hypothesis that is causal, behavioral, predictive, or reasoning
about *why* rather than *what/where* — which is most of them, including
every real example captured in this project's own QA fixtures (*"is
only referenced by 3 legacy call sites, all already migrated,"* *"is
still reachable via a dynamic plugin loader"*) — **no identifier
combination, however exact, can ever correlate it to a real verification
finding, because no real verification finding checks that type of
claim.** This is not a matching-precision gap to close with a better
key; it is a claim-type boundary that must gate correlation before any
key comparison happens at all. §4 revises Option A accordingly.

**Q4 — What information is available at the moment a `VerificationFinding`
is created that could establish the relationship deterministically?**
At every real call site, the code has: the specific claim string being
checked (a file path, a component name, a repository name), the
repository/scope context it's being checked within, and which existing,
already-structured source list it came from (`files_affected`,
`affected_components`, `repositories[].name`). It does **not** have, and
cannot derive, any reference to which hypothesis (if any) that claim
originated from — because, per §2, Planning/Development/Testing never
receive hypotheses at all. A stamped `subject_entity` (kind + exact
name) is available to attach at creation time; a hypothesis reference is
not, without crossing the boundary §2 identifies. This is the precise
technical fact behind Q5's answer, below.

## 3. One existing precedent worth reusing the shape of, not the mechanism

`ComponentWarning` (`app.agents.component_grounding`) already solves a
structurally similar problem inside a single stage: it carries a `claim:
str` field — the literal text of a plan's claim — checked deterministically
against real indexed components **at the point the claim is made**, not
matched retroactively against a separate list produced elsewhere. It's
still prose (`claim` is a string, not an ID), but the check happens
where the claim originates, with the real data in hand, instead of two
independent lists being reconciled after the fact by a third piece of
code that has no ground truth about whether they mean the same thing.

`KnowledgeGap`'s `status: "claimed" | "verified" | "refuted"`
(`reasoning/memory.py`) is a second, adjacent precedent: a human's answer
is recorded as a claim, and a *later* investigation cycle can
corroborate or refute it — but this works because the claim and its
correlated verification both live inside the same reasoning engine, with
the same `gap_id` carried across cycles. Neither precedent is a queue
of the "text-similarity matching" kind this ADR recommends against in
§4 (Option B).

## 4. Design options considered

### Option A — Claim-type-gated structured `subject_entity`, matched by exact key at render time (recommended, revised after review)

**Corrected from the first draft of this ADR**, which proposed matching
on `subject_repository` alone. §2a's review found that repository-only
matching is not sufficient to prevent false correlation — two unrelated
claims about the same repository (the review's own example: a race-
condition hypothesis and an unrelated "no concurrency evidence found"
finding, both nominally about the same repository) must never correlate,
and repository-only matching cannot tell them apart. The corrected
design adds the missing filter §2a identifies: **claim-type
compatibility gates correlation before any key comparison happens.**

Add an **optional**, structured field to the existing `Hypothesis`
model, filled by the **same existing synthesis LLM call** — but only
ever populated when the hypothesis's own claim genuinely is an
existence/attribution claim (the same claim type `verify_claims`
checks), never for a causal/behavioral/predictive hypothesis:

```python
class Hypothesis(BaseModel):
    description: str = ""
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    status: HypothesisStatus = "unknown"
    # NEW — optional, structured, exact-match only, and ONLY set when
    # `description` itself asserts the existence/location/attribution of
    # a specific named entity (never for a causal, behavioral, or
    # predictive claim — see §2a/§8). `kind` and `name` are both required
    # together; either both are present or the field is None.
    subject_entity: HypothesisSubjectEntity | None = None

class HypothesisSubjectEntity(BaseModel):
    kind: Literal["repository", "file", "component"]
    name: str  # exact, e.g. a real file path or component name this run indexed
```

Correlation happens **deterministically, in `map_knowledge_ledger_rows`
or a sibling function**, as an *exact match on both `kind` and `name`*
against the same already-structured source lists Planning/Development/
Testing's own verification already reads — `repository_usage[].name`
(kind=`repository`), `usage.files_affected`/`comp.file_path`
(kind=`file`), `affected_components`/`comp.name` (kind=`component`).
Matching against the *source arrays*, not a new field bolted onto
`VerificationFinding`, is deliberate: it means the correlation uses
exactly the same evidence Planning/Development/Testing's own
`verify_claims` call already checked that entity against, not a
second, independently-populated copy that could drift from it. No
fuzzy matching, no similarity score, no LLM call on the correlation
side. A hypothesis whose `subject_entity` is `None` (the common case —
most hypotheses are not existence/attribution claims and correctly stay
unset) or doesn't exactly match anything real gets `NOT_CHECKED`,
exactly as today.

**Why this doesn't need a new "possibly correlated" state:** once
gated by claim type, the match key is a real, structured,
already-authoritative identifier compared against the same evidence
Planning's own deterministic check already used — not a probabilistic
text comparison. A match either is or isn't, and it only ever fires for
the class of claim where "verified" already has a precise, real meaning
identical to what `verify_claims` checks for everything else. See §8 for
the exact activation condition this reasoning produces.

### Option B — Retroactive prose/semantic matching (rejected)

Compare `Hypothesis.description` against `verification_findings[].
message` after the fact — via token-overlap/Jaccard similarity
(deterministic but unreliable prose matching) or an LLM semantic-
similarity call (a new LLM call, and one whose output would be exactly
the *"LLM-generated verification claim"* both ADR 0024 and this
project's own explicit instructions rule out). Rejected on two
independent grounds:

1. Any similarity-based match is inherently probabilistic. Rendering it
   as a confident `VERIFIED`/`UNVERIFIED` would be a fabricated
   confidence dressed as a deterministic check — the precise failure
   mode this whole initiative exists to prevent. Doing it honestly would
   require inventing a fifth state ("possibly the same claim,
   confidence: 62%") which is a bigger contract change than the problem
   justifies, for a much weaker guarantee than Option A's exact match.
2. An LLM-based version reintroduces a new LLM call specifically to
   decide what counts as verification — the one thing Phase 1/2 both
   went out of their way to keep out of the report path.

### Option C — Stable `hypothesis_id`, threaded forward, downstream verification stamps `verifies_hypothesis_id`/`contradicts_hypothesis_id` (compared in detail below; not recommended for Phase 3)

**Q5 — Can a stable hypothesis identifier be carried forward from
Context Discovery into the downstream verification flow without
reopening the *entire* Planning boundary?** Yes, precisely — and this is
worth stating exactly, because "the boundary" is not one indivisible
wall. What Phase 1 actually forbids is `investigation_workspace`
(unresolved, competing, possibly-rejected reasoning) reaching Planning's
**LLM prompt** — so its conclusions stay argued from the settled
`engineering_understanding`, not from live scratch work. Nothing in that
boundary forbids a narrow, code-only, non-prompt channel: Context
Discovery could assign every hypothesis a stable, deterministic
`hypothesis_id` (e.g. derived from `iteration` + list index — no
randomness needed, since `reasoning_summary` is already rebuilt fresh
each run) and persist a small side-table of `{subject_entity: hypothesis_
id}` pairs (only for the claim-type-compatible subset — same gate as
Option A) that Planning/Development/Testing's *deterministic
verification code* — never their LLM prompts — could consult when
producing a finding for that exact entity. That is a real, narrower
boundary crossing than Option C's original framing implied, and Q5's
honest answer is **yes, technically possible without the full reopening
originally described** — but see the comparison below for why it's
still not recommended for Phase 3.

**Design comparison, as requested:**

| | **Option A** — exact-key match, computed at render time | **Option C** — stable ID, stamped at verification time |
|---|---|---|
| **Correctness** | Correct once claim-type-gated (§2a) — matches only existence/attribution hypotheses against the same evidence Planning already checked them against. | Equally correct **only if** the ID-stamping code uses the identical claim-type-gated matching rule as Option A. If instead the *LLM* (Planning's synthesis call) self-reports which hypothesis a claim addresses, that is an LLM-generated verification claim — forbidden outright, not just discouraged. |
| **Determinism** | Fully deterministic; one function, one place. | Fully deterministic **only if** implemented as pure code (same caveat as Correctness). |
| **Traceability** | Recomputed at render time from two independently-produced structured fields — always re-derivable, but not persisted as an explicit "this check was performed because of this hypothesis" record. | Better: a `verifies_hypothesis_id` persisted directly on the finding at the moment it's created is a first-class, explicit, permanent record — survives even if the matching rule changes later. Real, genuine advantage. |
| **Schema changes** | `Hypothesis.subject_entity` only. Verification agents' own code and schemas are untouched — matching reads their *existing* structured arrays. | `Hypothesis.hypothesis_id` (new) + `Hypothesis.subject_entity` (still needed to compute the match) + `VerificationFinding.verifies_hypothesis_id`/`contradicts_hypothesis_id` (new) + a side-table/lookup mechanism to carry IDs into three separate agents. Larger surface. |
| **Coupling between Context Discovery and Planning** | None — Planning/Development/Testing code is untouched; all correlation logic lives in report-generation's own `map_knowledge_ledger_rows`. | Real, new coupling — Planning/Development/Testing's *verification code* (not prompt) must receive and consult Context Discovery's hypothesis data, a dependency that does not exist today in any form. Confirmed possible (Q5) but not free. |
| **Backward compatibility** | Fully additive; old data simply has `subject_entity=None`. | Also additive, but spread across two models and three agents instead of one — more places a future change can drift out of sync with each other. |
| **Failure modes** | Single point of correlation logic — one function to audit, one place the claim-type gate can be forgotten. | Same false-correlation risk as Option A if the gate is skipped, **plus** a new class of bug: three independent implementations (Planning/Development/Testing) each computing the same match could drift or disagree with each other over time. |
| **Can it ever falsely claim VERIFIED?** | Only if implemented without the claim-type gate from §2a/§8 — same risk as Option C, not higher or lower on its own. | Identical risk profile to Option A *if* gated identically; strictly worse in practice because the gate must be correctly applied in three separate places instead of one. |

**Why Option A remains the recommendation despite Option C's real
traceability advantage:** the advantage is genuine, but it does not
outweigh tripling the surface area where the one rule that actually
matters — never correlate outside the claim-type gate — has to be
correctly applied. Option C is not rejected outright the way Option B
is; it is deferred, and worth revisiting specifically if Option A's
render-time recomputation ever proves insufficient in practice (for
example, if the source arrays it matches against stop being available
at render time for some reason they are not today).

### Option D — Do nothing; document and leave `NOT_CHECKED` as the permanent state

A legitimate choice, not a strawman. If the product decision is that
"hypothesis" and "verification" are simply different domains that should
never visually merge — reasoning about *what* is true vs. checking *a
specific claim already decided elsewhere* — then Option A's added
schema field and matching logic is complexity spent on a combination
that may not be worth representing. Included so the recommendation in
§10 is a choice among real alternatives, not the only path considered.

## 5. Why Option A is the recommendation

- **No new LLM call.** The existing synthesis call already has the
  information; this asks it to also structure one already-present fact.
- **No fabricated relationship.** The correlation is an exact match, on
  both entity kind and name, against a real, already-authoritative
  identifier Planning/Development/Testing's own verification already
  checked — not inferred, not scored, not guessed, and gated so it never
  fires for a claim type verification doesn't check (§2a/§8).
- **Strictly additive.** `subject_entity: HypothesisSubjectEntity | None
  = None` defaults to `None`; every existing hypothesis, real or
  fixture, continues to work unchanged. No existing test in the Phase
  1/2 suites should need to change because of this field's addition
  (verified by inspection of `Hypothesis.model_dump()` call sites — new
  optional fields don't break `model_validate()` on old data, and every
  `HypothesisEntry`/`HypothesisVM` construction site already reads
  fields by name, not positionally).
- **Respects the Planning boundary.** Nothing about Option A requires
  Planning, Development, or Testing to become aware of hypotheses —
  correlation happens entirely on the read/report side, using
  identifiers those stages already produce for their own reasons.
- **Honestly bounded.** Most hypotheses will legitimately have
  `subject_entity=None` — a hypothesis about *why* something behaves a
  certain way (the majority of real hypotheses, per §2a's own examples)
  is not an existence/attribution claim at all, and correlating it to
  anything would be dishonest regardless of matching precision. Phase 3
  does not chase closing that gap to zero; it only removes the current,
  structural impossibility for the narrower set of hypotheses where a
  real, claim-type-compatible match exists.

## 6. What Option A does NOT attempt

- Does not correlate hypotheses to verification data by anything other
  than an exact match on both `kind` and `name` of a structured
  identifier, gated by claim-type compatibility (§8) — never by scope
  overlap alone.
- Does not attempt to give every hypothesis a `subject_entity` — most
  will stay `None`, and that's correct, not a shortfall (§5).
- Does not correlate a hypothesis to *any* verification finding whose
  own check was not evaluating that exact entity's existence/
  attribution — a shared repository, shared file, or shared component
  name is necessary but never sufficient on its own if the underlying
  checks are of different claim types (there is no such case in
  practice today, since every real verification check is existence/
  attribution-shaped — but the rule is stated so a future verification
  check of a different shape doesn't silently start false-correlating).
- Does not touch `SynthesisStatus`, `VerificationStatus`, or
  `SynthesisRunState` — no enum gets a new value, no existing value's
  meaning changes.
- Does not produce, invent, or backfill `INFERRED`. That remains a
  separate, undecided question (ADR 0024 §16) with no bearing on this
  correlation work.
- Does not change `map_hypotheses`, `map_contradictions`, or any
  existing Phase 1/2 contract's return shape — only `map_knowledge_
  ledger_rows`'s internal correlation logic and `Hypothesis`'s schema.

## 7. Data model changes (proposed, not implemented)

```python
# app/context_pipeline/reasoning/understanding.py
class HypothesisSubjectEntity(BaseModel):
    kind: Literal["repository", "file", "component"]
    name: str

class Hypothesis(BaseModel):
    ...
    subject_entity: HypothesisSubjectEntity | None = None  # NEW, optional
```

`app.agents.verification`/`VerificationFinding` is **not** changed. This
is a deliberate correction from the first draft, which proposed a
parallel `subject_repository` field there too — matching against a
second, independently-populated copy risks the copy drifting from the
real evidence pool it's supposedly describing. Instead, correlation
reads the same structured arrays Planning/Development/Testing already
produce and already checked their own claims against:
`repository_usage[].name` (kind=`repository`), `usage.files_affected`/
`comp.file_path` (kind=`file`), `affected_components`/`comp.name`
(kind=`component`).

`map_knowledge_ledger_rows` gains a correlation pass, added after the
existing hypothesis-only and verification-only row construction
(unchanged): for each hypothesis row whose source `Hypothesis` has a
non-`None` `subject_entity`, look up whether that exact `(kind, name)`
pair appears in the same stage bundle's own structured source array —
not in another `LedgerRow`'s free-text `claim`/`message`, which stays
exactly as uncorrelated as it is today. Only on an exact match does the
hypothesis row's `verification_status` get set, using the same
`map_verification_status_from_repo_usage`/`map_verification_status_
from_finding` logic that already exists for every other row. Contradiction
rows are out of scope — nothing in this ADR proposes correlating
contradictions to verification (a contradiction is already a two-sided
claim about evidence, not a single checkable assertion, and extending
this reasoning to contradictions is a separate design question if it's
wanted at all).

## 8. The precise condition for `SUPPORTED+VERIFIED` vs `SUPPORTED+UNVERIFIED`

This is the operative rule — everything above exists to justify it, and
no implementation may deviate from it.

**GraphForge may render a hypothesis's `verification_status` as anything
other than `NOT_CHECKED` if and only if ALL of the following hold:**

1. **Claim-type compatibility.** The hypothesis's own claim is an
   existence/location/attribution assertion about one specific named
   repository, file, or component — the same claim type `verify_claims`
   evaluates for everything else in this codebase. A causal, behavioral,
   predictive, or "why"-shaped hypothesis fails this test unconditionally,
   with no exception, regardless of what else is true about it.
2. **Structured subject, not prose.** The hypothesis carries a
   `subject_entity: {kind, name}` set by the synthesis call as a
   discrete field — never derived from parsing `description`'s prose
   after the fact.
3. **Exact key match against the real evidence Planning/Development/
   Testing already checked.** `(kind, name)` matches, byte-for-byte
   (after only the same path/case normalization `verify_claims` itself
   already applies — no new normalization heuristic), an entry in the
   *same run's* own structured source array for that entity kind — not
   a different run, not a semantically-similar name, not a fuzzy or
   token-overlap match.
4. **The match is against the specific check, not the specific stage.**
   A hypothesis with `subject_entity={kind: "file", name: "x.py"}`
   correlates only against a check that specifically evaluated
   `x.py`'s existence/attribution — never against some other, unrelated
   finding that merely happens to share the same stage or repository.

**If any one of these fails — including simply because the hypothesis
is not an existence/attribution claim — the correct, permanent state is
`NOT_CHECKED`.** This is not a fallback for missing data; it is the
honest answer for the majority of real hypotheses, which are not claims
verification can evaluate at all.

**Explicitly, GraphForge must never infer `VERIFIED` (or `UNVERIFIED`)
merely because:**
- the repository matches, without the entity kind/name also matching
  exactly;
- the wording or phrasing is similar between the hypothesis and a
  finding's message;
- the verification finding is "related" to the same file in a general
  sense (e.g. the file was touched by the same stage) without being a
  check *of that exact file's existence/attribution*;
- an LLM — synthesis, report generation, or any other call — asserts
  that the two are related, in any form, at any confidence;
- the hypothesis and finding were produced in the same run, stage, or
  time window.

None of these establish a checkable relationship; all of them are the
scope-only or similarity-based reasoning §2a and this review round
found insufficient.

**Addendum — conflicting signals for the same exact entity resolve to
the negative, never averaged or upgraded.** Not previously stated; added
by this review round's case 9 (§9a). Two independent checks (e.g. one
per stage) can legitimately both apply to the same `(kind, name)` — one
reporting the entity present/correct, another reporting it absent or
misattributed. Consistent with this codebase's existing fail-closed
convention (`usage.verified = name_indexed and files_check.all_
verified` — a single failed sub-check taints the whole result;
`NON_BLOCKING_CATEGORIES` is an allowlist, so an unrecognized category
defaults to blocking, not passing), the correlation pass must apply the
same rule: **if any matching check for that exact entity is negative,
the resulting `verification_status` is `UNVERIFIED`, regardless of how
many other matching checks were positive.** A hypothesis is never shown
as confidently `VERIFIED` on the strength of one passing check while a
second, equally real check for the same entity failed.

## 9. Worked example: Hypothesis → verification request → verification finding → correlation → final status

**A hypothesis that CAN be correlated** (existence/attribution claim):

```
1. Synthesis call produces:
   Hypothesis(
     description="The handler is defined in agent-runtime's
                  app/api/routes.py.",
     status="supported", confidence=0.8,
     subject_entity=HypothesisSubjectEntity(kind="file",
                                             name="app/api/routes.py"),
   )
   — a location/existence claim; subject_entity is legitimately set.

2. Planning, independently and for its own reasons, records:
   PlanningResult.repository_usage = [
     RepositoryUsage(name="agent-runtime",
                      files_affected=["app/api/routes.py", ...], ...)
   ]
   verification.verify_claims(["app/api/routes.py", ...], evidence_pool)
   → "app/api/routes.py" IS in this run's indexed evidence pool
   → files_check.all_verified is True for this file
   → usage.verified = True

3. map_knowledge_ledger_rows (Phase 3's new correlation pass):
   hypothesis.subject_entity = {kind: "file", name: "app/api/routes.py"}
   Planning's repository_usage[0].files_affected contains
     "app/api/routes.py" (exact match, kind=file)
   → correlation succeeds

4. Final status: SUPPORTED + VERIFIED
   (SynthesisStatus.SUPPORTED from the hypothesis's own `status`;
    VerificationStatus.VERIFIED from the matched, real, deterministic
    files_check outcome — two independently-produced, both-real signals,
    now on the same row because they were checking the identical entity.)
```

**A hypothesis that MUST stay `NOT_CHECKED`** (the review's own example
— behavioral claim, fails condition 1 in §8 regardless of any scope
overlap):

```
1. Synthesis call produces:
   Hypothesis(
     description="Concurrent ingestion runs may race and both write
                  an active record.",
     status="unknown", confidence=0.4,
     subject_entity=None,  # correctly unset — this is a causal claim,
                           # not an existence/attribution claim; no
                           # honest subject_entity exists for it
   )

2. Suppose Testing separately records, in the same repository:
   VerificationFinding(message="No evidence of concurrent ingestion
                                 runs was found.",
                        category="informational")
   — note this exact finding shape does not occur in this codebase
   today (§2a) — included only to show the correlation pass correctly
   refuses it even if it did.

3. map_knowledge_ledger_rows:
   hypothesis.subject_entity is None → condition 2 (§8) fails
   → no lookup is even attempted; the finding above is never consulted

4. Final status: SUPPORTED axis unaffected (still whatever `status` the
   hypothesis has); VERIFICATION axis: NOT_CHECKED — permanently, not
   provisionally. Rendered as two badges: e.g. "Synthesis: Unknown" /
   "Verification: Not checked" — never implying a race condition was
   checked and ruled out, because nothing checked it.
```

## 9a. False Positive Matrix (added after review)

**The invariant this whole design exists to guarantee:** `VERIFIED` (or
`UNVERIFIED`) is possible *only* when the deterministic verification
contract explicitly covers the hypothesis's claim type **and** the
exact structured entity matches — §8's four conditions plus the
conflicting-signal addendum, applied together, with no exception.
Every case below traces to exactly one of those conditions failing or
holding; none introduce a new rule beyond what §8 already states,
except case 9, which is where this review round's addendum (end of §8)
comes from.

| # | Case | Resulting status | Why (§8 condition) |
|---|---|---|---|
| 1 | Same repository, different claim | `NOT_CHECKED` | Condition 1 fails: the hypothesis's actual assertion is not an existence/attribution claim *about that repository* — it merely occurs within it. `subject_entity` is never set for it, so no lookup is attempted. Repository identity alone (§2a) is scope, not claim — the two are different axes. |
| 2 | Same file, different claim | `NOT_CHECKED` | Same as case 1, at file granularity. The correlation is never "this hypothesis is about file X" — it is specifically "this hypothesis's entire claim reduces to file X's existence/attribution." A behavioral or causal hypothesis that happens to name or concern a file still fails condition 1 and never gets `subject_entity={kind: "file", ...}` for it. |
| 3 | Same component, different claim | `NOT_CHECKED` | Same as cases 1–2, at component granularity. |
| 4 | Same entity, exact existence/attribution claim | `VERIFIED` or `UNVERIFIED` (whichever the real check found) | The only case where all of §8's conditions hold: claim-type compatible (1), structured (2), exact key match against the real check (3), and the match is against the specific check of that exact entity (4). This is the sole positive path — see §9's first worked example. |
| 5 | Similar wording, different entity | `NOT_CHECKED` | Condition 3 fails outright: matching is exact-key only. No fuzzy, token-overlap, or similarity comparison exists anywhere in this design (§4 rejects that mechanism entirely as Option B) — a near-miss name never satisfies an exact match, regardless of how close the wording is. |
| 6 | Exact wording, but unsupported claim type | `NOT_CHECKED` | Condition 1 fails and is checked *before* any text comparison — even if `description` contains an entity name verbatim, `subject_entity` is only ever set when the hypothesis's overall claim is itself an existence/attribution assertion. Verbatim text containment is irrelevant; classified claim type is the only gate that matters here. |
| 7 | Verification finding exists but does not correspond to the hypothesis | `NOT_CHECKED` | Conditions 3 and 4 fail: a real finding may exist in the same stage bundle, but if its `(kind, name)` doesn't exactly match the hypothesis's `subject_entity`, or it checked a different entity than the one named, no correlation occurs. Presence of *some* finding is never sufficient. |
| 8 | Hypothesis has no `subject_entity` | `NOT_CHECKED` | Condition 2 fails immediately — this is the default, most common case (§5); no lookup is even attempted (§9's second worked example). Not a fallback for missing data; the honest state for any hypothesis that isn't an existence/attribution claim. |
| 9 | Verification produces both positive and negative findings (for the same exact entity) | `UNVERIFIED` | New rule, stated at the end of §8: conflicting signals for the same `(kind, name)` resolve to the negative, fail-closed — consistent with `usage.verified`'s existing all-must-pass logic and `NON_BLOCKING_CATEGORIES`'s allowlist-not-denylist convention elsewhere in this codebase. Never averaged, never let one passing check mask a real failing one. |
| 10 | Verification machinery is unavailable/failed (stage didn't run, no structured source array exists for that entity) | `NOT_CHECKED` | No structured array to match against means condition 3 has nothing to compare — the same outcome as no finding existing at all. Explicitly **not** treated as "assume passed" or "assume failed": absence of a check is absence of information, and `NOT_CHECKED` — not `UNVERIFIED` — is the state that means "nothing evaluated this," reserved specifically so a stage outage or an unrun check can never be misread as an active negative finding. |

## 10. Recommendation

**Proceed with Option A**, scoped exactly as §5–§8 describe, as Phase
3's implementation target — pending review of this document. Option C
(the stable-ID design) is compared in detail in §4 and deferred, not
rejected outright, for a possible future revisit if Option A's
render-time recomputation proves insufficient in practice. Option B is
rejected outright, not deferred — it should not be revisited without a
materially different honesty argument than the one this ADR rejects it
on.

## 11. Testing strategy (for when implementation is approved)

Same discipline as Phases 1–2 — real pipeline, not only hand-built
fixtures:

1. Unit tests on the new correlation pass in `map_knowledge_ledger_rows`:
   a claim-type-compatible `subject_entity` that exactly matches a real
   source-array entry produces a correlated row; a `None` `subject_
   entity`, or one that doesn't exactly match, produces `NOT_CHECKED`,
   unchanged from today.
2. **The exact negative case this review round raised**, as its own
   named test: a hypothesis and a verification finding that share a
   repository (or even a file) but are not the same claim type/entity
   check must never correlate — asserted directly against §9's second
   worked example, not just against a synthetic near-miss.
3. A regression test proving the existing Phase 1/2 suites are
   unaffected by the new optional field (old fixtures/persisted data
   with no `subject_entity` key still `model_validate()` cleanly).
4. A real-workflow test: rerun a live investigation whose synthesis call
   is prompted to also emit `subject_entity` for claim-type-compatible
   hypotheses, confirm at least one hypothesis ends up genuinely
   `VERIFIED`/`UNVERIFIED` end-to-end (`reasoning_summary` → ledger →
   view model → API → rendered card), the same trace discipline used to
   close the Phase 2 QA gap.
5. A negative test proving no fuzzy/semantic matching creeps in — e.g. a
   hypothesis and a finding about similar-but-different file/component
   names must never correlate.
6. One test per row of §9a's False Positive Matrix — each of the ten
   cases asserted directly, not just implied by the tests above, so the
   matrix itself stays the executable spec, not just documentation.
7. A conflicting-signals test (§9a case 9): two checks for the same
   exact entity, one positive and one negative, must resolve to
   `UNVERIFIED` — never averaged, never resolved by which check ran
   first or last.
8. An unavailable-verification test (§9a case 10): a claim-type-
   compatible `subject_entity` with no corresponding stage bundle (or an
   empty/absent structured source array) must resolve to `NOT_CHECKED`,
   never `UNVERIFIED` — absence of a check must never be misread as an
   active negative finding.

## 12. Open questions for review before implementation

1. Is `subject_entity` at repository/file/component granularity the
   right starting scope, or should any of the three be deferred to
   narrow the first implementation further?
2. Does the synthesis prompt change (asking for one new structured
   field, gated by the claim-type rule in §8) need its own smaller
   review/approval separate from this ADR, given it touches `_SYSTEM_
   PROMPT` in `understanding.py` — a prompt already carrying real
   product-behavior weight, and one that must reliably distinguish
   existence claims from behavioral ones to populate `subject_entity`
   correctly?
3. Is a backfill for pre-Phase-3 persisted hypotheses in scope, or does
   old data simply stay `NOT_CHECKED` forever (which seems correct and
   acceptable, but should be an explicit choice, not a default)?
4. Is Option C (the stable-ID design, §4) worth scoping as an explicit
   future phase now, or should it stay an unscoped "revisit if Option A
   proves insufficient" note as it is here?
5. Should §8's claim-type gate (condition 1) be enforced only by the
   synthesis prompt's own discipline, or should a second, deterministic
   sanity check exist (e.g. rejecting a `subject_entity` if `description`
   contains no token matching `name`) as defense against a prompt
   regression silently starting to set `subject_entity` on behavioral
   hypotheses?

## 13. Explicit non-goals (restated for anyone reading only this section)

- No implementation in or alongside this ADR.
- No new LLM call anywhere in this design.
- No fuzzy, semantic, or LLM-based evidence↔hypothesis matching.
- No correlation based on repository match, wording similarity, same-
  file relatedness, or any LLM assertion of relatedness — §8's four
  conditions are the only path to anything other than `NOT_CHECKED`.
- No new `SynthesisStatus`/`VerificationStatus`/`SynthesisRunState`
  value.
- No attempt to produce `INFERRED`.
- No change to any already-shipped Phase 1/2 contract's return shape.
- No change to `VerificationFinding`'s schema (corrected from the first
  draft — see §7).
