# ADR 0027: Development Component Verification Enforcement

## Status

Proposed. **Design only — no implementation, schema, or prompt change in
this ADR or alongside it.** Written in direct response to ADR 0026's P2
Finding #2 (re-scoped by the independent audit that followed it from an
abstract classification-scheme gap to one concrete code path). Revised
once already, after an independent skeptical design review (recorded
inline throughout §§4–12 below, not as a separate addendum, since the
review found the first draft's own wording could reintroduce the bug it
was meant to fix — see §5's Option B and §7 case 15). This document first
**independently re-verifies the underlying finding against the actual
code** (§1), then designs the smallest fix that closes it (§§2–13). Do
not begin implementation until this document is reviewed and a
recommendation is explicitly approved — same discipline ADR 0025 used.

## 1. Independent verification of the audit's claim

The audit's claim, restated precisely: a Development-stage component
whose `file_path` fails deterministic existence verification remains in
`plan.components`, is persisted, and is later read by Code Generation's
`_collect_known_file_paths()` as if it were confirmed ground truth,
letting a `modify`/`delete` operation at that path pass
`validate_file_operations()` with no violation.

Traced independently, function by function, against current source (not
re-reading the prior audit's own summary):

1. **`development/agent.py:155-163`** — `plan.components` is built
   directly from the LLM's parsed JSON (`c.get("file_path", "")`,
   `c.get("name", "")`, `c.get("repository", "")`). Confirmed: free-
   generated text, not tool output, at the point of construction.

2. **`development/agent.py:446-451`** — `evidence_pool` is built by
   `verification.build_evidence_pool(...)` from **flattened** lists:
   `[c.get("name") for c in components_obs.data["components"]]` and
   `[c.get("file_path") for c in components_obs.data["components"]]`
   (`components_obs` is this run's own real graph traversal via
   `ComponentDiscoveryTool` — `app/agents/development/tools.py:113-153`).
   `ComponentDiscoveryTool`'s own output *does* carry per-component
   repository attribution (`"repository": repo_name`, `tools.py:152`) —
   but `build_evidence_pool` discards it before the pool is built. The
   pool is one flat set of names/paths, with no per-repository
   partitioning.

3. **`development/agent.py:535-542`** —
   ```python
   for comp in plan.components:
       comp_check = verification.verify_claims([comp.name, comp.file_path], evidence_pool)
       for claim in comp_check.unverified:
           _warn(..., "component_not_found")
   ```
   Confirmed: `_warn()` (`agent.py:455-459`) only appends to
   `verification_warnings`/`verification_findings`. No line anywhere in
   this function removes, flags, or mutates the offending entry in
   `plan.components` itself. This is a real, confirmed asymmetry with the
   adjacent `_apply_test_grounding` block (`agent.py:477-510`), which
   *does* rewrite/drop entries from `plan.components` for the test-vs-
   production case, a few lines earlier in the same function.

4. **`development/agent.py:633`** — `result=plan.model_dump()`. Confirmed:
   the unfiltered `components` list, including any `file_path` that failed
   step 3, is what gets persisted as this run's `AgentStep.result`.

5. **`code_generation/verification.py:101-118`** —
   `_collect_known_file_paths()` reads `result.get("components", [])`
   directly from the persisted Development stage result and builds
   `by_repo[component["repository"]].add(component["file_path"])`, with
   no read of `verification_warnings`/`verification_findings` at all.

6. **`code_generation/verification.py:242-299`** —
   `validate_file_operations()`'s `modify`/`delete` branch
   (`normalize_path(path) not in known_normalized`) checks membership in
   exactly the tainted set built in step 5. Confirmed: a flagged-but-not-
   removed path is treated identically to a genuinely graph-confirmed one.

7. **`app/integrations/github.py:539-579`** — `create_commit()` builds Git
   Data API tree entries directly from `files` with no pre-check that a
   `modify`/`delete` path exists in the base tree; the trees endpoint
   merges entries into `base_tree` by path, so a "modify" at a path absent
   from the base tree is not rejected by the API shape. Live delete-of-
   nonexistent-path behavior was not empirically confirmed in this pass —
   flagged as unconfirmed by direct observation, not upgraded to
   certainty.

**Verdict: the audit's exact path is real, confirmed independently at
every step.** The root cause is not only "verified but not filtered" — it
is also that **`verify_claims` for components never checks repository
attribution at all**, because `build_evidence_pool` flattens away the one
piece of data (`ComponentDiscoveryTool`'s per-item `repository` field)
that would let it. This matters directly for §4 (what "verified" should
mean) and for adversarial case 2 below.

## 2. Root cause

Two independent, additive gaps, not one:

- **Gap A — enforcement.** `verify_claims`'s per-component result
  (`component_not_found`) is computed and classified but never applied
  back to `plan.components`. A check that runs and is discarded provides
  the *appearance* of verification without its effect.
- **Gap B — granularity.** Even where `verify_claims` does run, its
  evidence pool is repository-blind: a `file_path` is checked for
  existence anywhere among all repositories this run touched, not for
  existing **in the specific repository the component claims
  (`comp.repository`)**. A real file from the wrong repository currently
  passes `verify_claims` with zero warnings.

Both must be closed; closing only Gap A while leaving Gap B open would
still let case 2 (wrong-repository file) through as "verified."

## 3. Why `verify_claims()` alone, as it exists today, is insufficient

`verify_claims()` (`app/agents/verification.py:523+`) is a **generic,
repository-agnostic string-membership check** — by design (its own module
docstring: "identical for a Java, Python, or Scala repository," "never
looks at source code"). That design is correct for its original purpose
(does this string appear anywhere in this run's real evidence at all). It
was never designed to answer a **relational** question — "does this
specific `(repository, file_path)` pair correspond to a real indexed
component" — and nothing in `development/agent.py` currently asks it that
question.

**Unambiguous statement of the architecture** (tightened after review —
the prior wording, "used alongside it," was correct but loose enough to
misread as reusing `verify_claims` with different evidence):

- `verify_claims()` remains exactly what it is today: the existing,
  unmodified, generic, repository-agnostic evidence check. This ADR does
  not change its contract, its call sites, or what it's called with.
- The new repository/file verification (§4) is a **separate**,
  purpose-built, deterministic, repository-partitioned lookup — a
  different function, a different evidence structure
  (`dict[repository, set[file_path]]`, never a flat pool), computing a
  different result (`file_path_verification`, not a warning string).
- **The two checks serve different purposes and neither substitutes for
  the other.** `verify_claims` continues to run unmodified and continues
  to produce `component_not_found` warnings exactly as it does today
  (§4.5 below). The new check runs independently, in addition, and is
  the sole authority for `file_path_verification` and therefore for the
  Code Generation write gate. Neither check's result overrides,
  suppresses, or substitutes for the other's (§4.5, §7 case 23).

## 4. What "verified" must mean

### 4.1 Chosen granularity

Four candidate granularities were weighed: (1) file existence only, (2)
repository + file attribution, (3) component name + file attribution, (4)
all of the above.

**Chosen: (2), repository + file attribution — exact pair, evidence-
confirmed.** Reasoning:

- **(1) alone is insufficient** — it's what today's check effectively
  degrades to (Gap B); a real file from the wrong repository passes a
  (1)-only check trivially (case 2).
- **(2)** — `(comp.repository, comp.file_path)` matched as a single pair
  against `ComponentDiscoveryTool`'s own per-repository data — is the
  precise fact `_collect_known_file_paths()` downstream actually needs,
  since it already partitions by `repository`
  (`by_repo[repo].add(path)`, `code_generation/verification.py:117`).
- **(3)** — component *name* accuracy is a separate, already-solved
  problem (`check_test_used_as_production`); a component's `name` is
  never read by `_collect_known_file_paths()`/`validate_file_operations()`
  at all, so verifying it would not close any part of this trust gap and
  risks rejecting components whose path is correct but whose name is a
  legitimate paraphrase.
- **(4)** — over-scoped; would re-litigate component-naming grounding,
  which already has its own separate, working mechanism this ADR does
  not touch.

### 4.2 Precise definition — VERIFIED

> **VERIFIED** means: the exact `(repository, file_path)` pair was
> deterministically confirmed by repository-scoped evidence — i.e., this
> run's own `ComponentDiscoveryTool` (or the Context Discovery graph data
> this run reused, `development/agent.py:304-312`) actually returned this
> exact `file_path` **for this exact `repository`**.

**VERIFIED must never mean, and no implementation of this ADR may treat
any of the following as sufficient:**

- the filename exists somewhere (any repository, unscoped)
- the file exists, but in a *different* repository than claimed
- the component's *name* matches something, independent of its path
- wording/description similarity between claim and evidence
- the same file being referenced elsewhere in the run (a different
  component, a different stage) without its own independent evidence
- the LLM's own output asserting `"verified": true` or any equivalent
  self-report, in any field, at any stage

### 4.3 The lookup must be joint, not two ANDed checks

> **Invariant E (lookup shape).** The verification lookup MUST be a
> single, joint containment test:
> ```
> file_path in evidence_by_repository.get(repository, set())
> ```
> It must **not** be implemented as two independently-true conditions
> ANDed together:
> ```
> repository in repositories_consulted   AND   file_path in <global pool>
> ```
> The AND-of-two-independent-checks shape is a real, easy-to-write
> reimplementation of Gap B: repository A can be genuinely in scope,
> `file_path` X can genuinely exist (in repository B), and the AND would
> pass while the pair was never actually checked together. The evidence
> structure must be built as `dict[repository, set[file_path]]` (a
> repository-partitioned index), never a single flat `set[file_path]`
> checked against a separately-verified repository name.

### 4.4 The three-state model, and where it lives

Rather than a boolean, verification state is one of exactly three values,
**reusing the existing three-state verification vocabulary already used
elsewhere in this codebase** (`VerificationStatus` in
`app/agents/report_generation/contracts.py`: `VERIFIED` / `UNVERIFIED` /
`NOT_CHECKED`) rather than inventing a second one:

- **`NOT_CHECKED`** — verification **could not be performed** because the
  required evidence was unavailable: no repository-scoped evidence pool
  existed to check against at all (graph unavailable, or no
  `ComponentDiscoveryTool` data for this repository — case 9). This is
  strictly about evidence *availability*, never about whether a path was
  looked for and not found — see the distinction from `UNVERIFIED` below,
  which is precise and load-bearing, not a stylistic choice.
- **`UNVERIFIED`** — verification **was performed** against available
  evidence, but the exact `(repository, file_path)` pair was not found in
  it (case 1, case 2, case 5, case 6, case 22). This is the correct state
  whenever a repository-scoped evidence pool existed and was checked,
  regardless of *why* the pair didn't match — including the case where
  the pair doesn't match because the file is a legitimate proposed
  addition that doesn't exist yet (case 6) and the case where it doesn't
  match because the claim is wrong (case 22). Development has no signal
  (no `operation` field, Invariant H) to distinguish those two
  *reasons* — and critically, **it doesn't need one**, because:

  > **`UNVERIFIED` does not mean "the proposed change is invalid."** It
  > describes the state of verification/evidence only — that this exact
  > pair could not be deterministically confirmed against this run's
  > evidence — never a judgment that the component or its file_path is
  > wrong, unauthorized, or should not proceed. Whether an `UNVERIFIED`
  > pair is acceptable depends entirely on what operation is eventually
  > attempted against it (§4.7, §8 Invariant 2): a `create` may proceed
  > despite `UNVERIFIED` — a genuinely new file naturally has no existing
  > evidence to match, so `UNVERIFIED` is its normal, expected state, not
  > a red flag — while `modify`/`delete` require `VERIFIED` specifically
  > because they operate against a file that must already exist for the
  > operation to be meaningful.
- **`VERIFIED`** — the exact `(repository, file_path)` pair was
  deterministically found in this run's own repository-scoped evidence
  (case 7, case 8's positive form, case 21).

**Placement:** the three-state type is defined in `app/agents/verification.py`
— the module `development/agent.py` and `code_generation/verification.py`
**already both import from** (`from app.agents import verification`;
`from app.agents.verification import build_evidence_pool, verify_claims`)
— not in `app.agents.report_generation.contracts`, which `development`
does not and should not depend on (report_generation is a downstream
consumer of Development's output, not a peer Development should import
from). This is a deliberate placement decision to remove ambiguity for
an implementer: **do not** import from `report_generation` into
`development`; **do** mirror the same three literal string values
(`"verified"` / `"unverified"` / `"not_checked"`) so the *vocabulary* is
shared even though the *type definition* is not cross-imported across
that boundary.

The gate condition downstream is exactly one comparison:
`status == "verified"` — `NOT_CHECKED` and `UNVERIFIED` behave
identically at the gate (both excluded); the distinction exists for
diagnostics, not for a second code path.

### 4.5 Diagnostic findings — a distinct concept from verification state

`file_path_verification` (§4.4) is a **machine-consumed gate input**.
Whether a human ever sees *why* a component ended up `UNVERIFIED` is a
separate concept — a `VerificationFinding` (`app/agents/verification.py`,
already the mechanism `component_not_found` uses today,
`development/agent.py:530-534`/`537-542`). Both must exist, and they must
stay distinct:

| Diagnostic category | When it fires | Existing or new |
|---|---|---|
| `component_not_found` | The claimed `file_path` matches nothing in this run's evidence, in **any** repository this run touched (the existing, repository-blind `verify_claims` check, unchanged) | **Existing — unmodified** |
| **`component_repository_mismatch`** | The claimed `file_path` **does** exist in this run's evidence, but not paired with the claimed `repository` — i.e. the new joint lookup (§4.3) finds it under a different repository | **New** |
| *(none)* | The pair matches — `file_path_verification == VERIFIED` | No finding is raised; absence of a finding is not itself evidence of anything beyond "this specific check passed" |
| *(none, or an existing category if the framework already has one that fits — see below)* | No evidence pool was available to check against at all (`NOT_CHECKED` from unavailability, not from mismatch) | No new category is introduced for this case; `graph_unavailable`'s existing handling already covers the "verification could not run" condition at the run level (`agent.py`'s `graph_unavailable` branch and its own `confidence_reasoning`) — nothing further is added here |

`component_not_found` and `component_repository_mismatch` are **mutually
exclusive by construction**, because they're evaluated from disjoint
conditions: the first fires when the path matches nothing anywhere; the
second only evaluates once the path is confirmed to exist *somewhere*, so
a nonexistent path can never also be flagged as a mismatch, and a
mismatched path (which exists in evidence, just under another
repository) can never also be flagged as not-found. A given component
receives at most one of the two findings.

**Placement:** `component_repository_mismatch` is added to
`app.agents.verification.BLOCKING_CATEGORIES` (`app/agents/verification.py:81-95`)
— the same documentation set `component_not_found` is already listed in.
It is **not** added to `NON_BLOCKING_CATEGORIES` (`verification.py:59-68`);
per that module's own existing, unmodified rule, any category not listed
there defaults to blocking, so this addition is required for
documentation completeness, not to change runtime behavior — the
category is blocking the moment it's assigned, with or without the
`BLOCKING_CATEGORIES` listing.

### 4.6 Three layers — kept structurally distinct

Three different questions, three different mechanisms, deliberately never
merged into one:

| Layer | Question it answers | Possible results | Consumed by |
|---|---|---|---|
| **Verification state** (§4.4) | Is this exact `(repository, file_path)` pair deterministically confirmed? | `NOT_CHECKED` / `VERIFIED` / `UNVERIFIED` | Code Generation's write gate — **exclusively** |
| **Diagnostic finding** (§4.5) | Why was this pair unverifiable, if it was? | `component_not_found` / `component_repository_mismatch` / (none, if verified) | Engineering Review's readiness/blocking decision, and any human-facing display — **exclusively** |
| **Write gate** (`validate_file_operations`) | May this specific operation proceed? | allow / reject | The actual git write call |

The sequence for a mismatch, concretely:

```
file_path_verification = UNVERIFIED               (§4.4 — the new joint lookup)
         ↓
component_repository_mismatch finding recorded     (§4.5 — a VerificationFinding, blocking category)
         ↓
Engineering Review's blocking-findings check sees it (existing mechanism, unmodified — engineering_review/agent.py:497-509)
         ↓
Code Generation's validate_file_operations() excludes the path from "known" for modify/delete (§4.4's gate condition, unmodified by this section)
```

**Each arrow is a one-way, read-only relationship — never a shortcut
between non-adjacent layers:**

- Code Generation's write gate reads `file_path_verification` **only**.
  It must never inspect `verification_warnings`/`verification_findings`
  text or category names to decide allow/reject — the write gate has no
  business parsing diagnostic prose, and doing so would let a change to
  a message string silently change write behavior.
- Engineering Review's blocking decision reads `verification_findings`'
  `category`/`blocking` **only** (the existing mechanism, unmodified). It
  must never read `file_path_verification` directly — Engineering
  Review is a human-readiness signal about the whole blueprint, not a
  write gate, and has no reason to know about a machine-only field whose
  entire purpose is gating a downstream git operation.
- Neither layer may be inferred from the other's *absence*: no finding
  does not imply `VERIFIED` (a `NOT_CHECKED` component with no evidence
  pool also produces no finding, per §4.5's table); no blocking finding
  does not imply `UNVERIFIED` (a component could in principle have some
  other, unrelated blocking finding attached to it by another mechanism
  while its own file-pair status remains `VERIFIED`).

### 4.7 Engineering Review blocking behavior — explicit, including the CREATE question

**Decision: `component_repository_mismatch` is blocking for Engineering
Review's readiness determination unconditionally — not conditioned on
whether the component will eventually become a `create`, `modify`, or
`delete` operation.**

This requires explicit reasoning, because it is easy to misread the
"CREATE is unaffected" principle (Invariant 2) as applying here too. It
does not, for a structural reason: **`AffectedComponent` (Development's
own schema, `development/schemas.py:24-31`) has no `operation` field at
all.** Operation type (`create`/`modify`/`delete`) is a Code Generation
concept, decided one stage later, per proposed file
(`GeneratedFile.operation`, `code_generation/schemas.py`) — Development
cannot know, and must not guess, what a component will become. There is
therefore no signal available at the point `component_repository_mismatch`
is raised that could condition it on operation type without inventing one
— which would be exactly the kind of heuristic this ADR is told not to
introduce.

**Consequences, made explicit so Engineering Review can never be
mistaken for treating CREATE like MODIFY, or vice versa:**

- A component whose claimed `(repository, file_path)` doesn't pair up
  gets a blocking `component_repository_mismatch` finding **regardless of
  what it will later become** — even if it is only ever used as a
  `create` target downstream. This is a statement about the blueprint's
  identity claim, not a prediction about the eventual write.
- This blocking finding does **not**, by itself, prevent approval:
  `approve_workflow` (`workflow_service.py:577-588`) does not read
  `readiness_status` or any `VerificationFinding` at all — a human can
  approve a blueprint with this finding present, exactly as they can
  today for any other blocking category. This is existing, unmodified
  behavior, restated here only so it isn't assumed away.
- **The only place operation type is actually known, and the only place
  it actually changes the outcome, is Code Generation's write gate**
  (§4.6's third layer): if the same component is later used as a
  `create`, `validate_file_operations`'s known-path check never consults
  `file_path_verification` at all (Invariant 2, unconditional); if used
  as `modify`/`delete`, it does, and rejects.
- **This means a real, intended asymmetry exists between the two layers,
  by design**: Engineering Review may show a blocking finding for a
  component that Code Generation ultimately writes without any issue
  (because it turned out to be a `create`). This is not a bug to reconcile
  — Engineering Review's finding is honestly reporting "this identity
  claim could not be confirmed," which is true regardless of operation;
  Code Generation's gate is honestly reporting "this specific operation
  is safe or not," which does depend on operation. Collapsing the two to
  agree would require either suppressing a true finding (dishonest) or
  blocking a safe `create` (an unnecessary regression, exactly what
  Option B was chosen to avoid — §6, case 6).
- Code Generation's write gate remains the actual, independent backstop
  for `modify`/`delete` regardless of what Engineering Review found or
  what a human approved — this was already true before this ADR (§9) and
  is unchanged by it.

## 5. Options considered

### Option A — Drop invalid components in Development

When a component's `(repository, file_path)` fails the repository-scoped
check, remove it from `plan.components` before persistence. Code
Generation only ever sees components that survived.

- **Fail-closed:** Yes.
- **Correctness:** High for the modify/delete gate's purpose.
- **Repo/file scope safety:** Closes Gap A and, combined with the
  repository-scoped lookup (§4), Gap B too.
- **Backward compatibility:** Changes `plan.components`'s cardinality for
  any workflow with unverified entries — a real behavior change for
  every existing reader of `components` (frontend blueprint card,
  `format_development_block`), not only Code Generation.
- **Existing architecture fit:** Matches the precedent already in the
  same function (`_apply_test_grounding` already drops/replaces entries).
- **DB/schema impact:** None.
- **API/DTO impact:** None new.
- **Test complexity:** Low.
- **Risk of rejecting legitimate files:** **Option A's real weakness,
  directly implicating case 6.** A legitimately new file (proposed for
  `create`) has no matching entry in `ComponentDiscoveryTool`'s data by
  definition — it can never be "verified" by an existence check. Applied
  uniformly, Option A would silently drop every legitimate new-file
  component before Code Generation ever sees it, unless Development's
  schema also records intended operation per component (a Code
  Generation concept, one stage later) — which would require either
  inferring intent (a heuristic — disallowed) or accepting the
  regression.
- **Interaction with `verify_claims()`:** Reused, repository-scoped.
- **Interaction with `repositories_consulted`:** None — orthogonal.
- **Interaction with Code Generation verification:** Simplifies it, but
  only if the false-rejection risk above doesn't materialize.
- **Interaction with GitHub tree API:** None directly.

### Option B — Carry explicit per-component verification state

Add a **per-component** field, e.g. `AffectedComponent.file_path_verification:
Literal["not_checked", "verified", "unverified"]` (§4.4), set by the
repository-scoped, joint-lookup check (§4.3), **without** removing
anything from `plan.components`. `_collect_known_file_paths()` is changed
to include only entries where this value is `"verified"`.

**The field must live on the component itself, keyed implicitly by that
component's own `(repository, file_path)`.** A parallel structure
(e.g. a separate `verified_pairs` collection alongside `components`) is
not disallowed in principle, but **if used, it must be keyed by
`(repository, file_path)` tuples, never by `file_path` alone** — a flat,
path-only parallel structure (`verified_file_paths: set[str]`) is
**explicitly rejected** as a design option: it would let a path verified
under repository A silently "verify" a same-named path claimed under
repository B, reintroducing Gap B inside the fix meant to close it (see
case 15). The per-component embedded field is the simpler, lower-risk
shape and is the one this ADR recommends implementing; the keyed-tuple
alternative is documented here only to close off the unsafe variant, not
as an equally-preferred choice.

- **Fail-closed:** Yes, at the point that matters — while still
  preserving the full component list, verified-or-not, for anything that
  legitimately needs to see it (a human-facing blueprint view, a future
  audit).
- **Correctness:** High, and strictly more precise than Option A: it
  distinguishes `NOT_CHECKED`/`UNVERIFIED`/`VERIFIED` per-item instead of
  making dropped-or-present a binary the rest of the system can't
  recover from.
- **Repo/file scope safety:** Closes Gap A and Gap B identically to
  Option A, at the specific consuming boundary that matters
  (`_collect_known_file_paths`), without touching every other reader of
  `plan.components`.
- **Backward compatibility:** **Additive only.** Every existing consumer
  of `plan.components` keeps seeing the full list exactly as today; only
  `_collect_known_file_paths` changes behavior.
- **Existing architecture fit:** Matches ADR 0025's `subject_entity`
  precedent — an optional, narrowly-scoped structured field, read by
  exactly one new deterministic function.
- **DB/schema impact:** New key in the same JSON blob (`AgentStep.result`)
  — no migration. Old, already-persisted Development results simply lack
  the field (handled by a safe `NOT_CHECKED` default — case 10).
- **API/DTO impact:** One new optional field on `AffectedComponent` — not
  required to be surfaced in any DTO/frontend for this fix to function.
- **Test complexity:** Slightly higher than A; each piece independently
  testable.
- **Risk of rejecting legitimate files:** **Materially lower than Option
  A**, and correctly resolves case 6 *by construction*: an unverifiable-
  because-new-file component still exists in `plan.components`, carries
  `UNVERIFIED` (§4.4 — not `NOT_CHECKED`; verification was performed
  against available evidence and the pair simply wasn't in it), and is
  simply absent from the *verified* subset — `create` never consults
  that subset at all (§8, Invariant 2), so `UNVERIFIED`'s presence here
  is harmless by construction, not despite the label.
- **Interaction with `verify_claims()`:** Not reused directly for this
  specific check (§4.3 requires a joint, repository-partitioned lookup —
  a different shape than `verify_claims`'s flat membership test); the
  same deterministic evidence source (`ComponentDiscoveryTool`'s data)
  is reused, just indexed differently for this purpose.
- **Interaction with `repositories_consulted`:** None — orthogonal.
- **Interaction with Code Generation verification:**
  `_collect_known_file_paths` gains one extra filter condition
  (`status == "verified"`) — a small, local change to an already-existing
  function.
- **Interaction with GitHub tree API:** None directly.

### Option C — Re-verify at Code Generation

Do not trust Development's component list at all. At Code Generation
time, independently resolve each *proposed* `modify`/`delete` file path
against the repository's actual state before allowing the operation.

**Corrected reasoning — the `max_graph_hops=0` objection from the first
draft of this ADR was factually wrong and is retracted.**
`CODE_GENERATION_MANIFEST`'s `max_graph_hops=0` is documented, in the
same file, as scoped specifically to **Neo4j** ("ADR 0011, OD-3 — LLM
only; `max_graph_hops=0` means no Neo4j dependency,"
`code_generation/manifest.py:17-19`). It says nothing about, and does not
prevent, a GitHub API call. `GitHubApiClient.get_file_content()`
(`app/integrations/github.py:402-429`) already exists, is already used
elsewhere in this codebase for exactly this "does this file exist"
question (a repository's CODEOWNERS lookup), and would perform a
lightweight, per-path, live existence check **without touching Neo4j or
the architecture graph at all**. A live-tree-based Option C is
technically available and does not "reverse" `max_graph_hops=0`.

**Option C is still not recommended, for reasons independent of the graph
objection:**

- **New external runtime dependency at code-generation time.**
  `CodeGenerationAgent.run` today makes zero live external API calls (DB
  + LLM only, `agent.py`) — Option C would add GitHub as a hard runtime
  dependency of a stage that currently has none.
- **Latency/rate-limit cost.** One GitHub API call per `modify`/`delete`
  file, on every code-generation run, is a new per-file cost with no
  current analog in this stage, and GitHub's API rate limits become a
  new operational concern for a stage that previously had none.
- **New GitHub API failure modes.** What happens when the live check
  itself fails (network error, rate limit, transient 5xx)? No such
  question currently exists for this stage; Option C would need to
  define new fail-closed semantics for it (presumably: treat an
  unreachable check as `NOT_CHECKED`, i.e. reject `modify`/`delete` — but
  this is a new decision this ADR would have to make and test, not one
  that already exists anywhere in this codebase).
- **Token/context propagation.** `CodeGenerationAgent` does not currently
  receive a GitHub access token in its `AgentContext` — unlike
  `create_pull_request_agent.py`, which does. Threading credential access
  into a stage that has never needed it is a real scope expansion of that
  agent's trust surface, not a small addition.
- **New fail-closed semantics required, with no existing precedent in
  this stage** to build on, unlike Option B, which reuses the exact
  fail-closed pattern (`NOT_CHECKED` on missing evidence) already
  established at Development time.
- **Larger implementation/test surface** than Option B: live-call
  mocking, retry/timeout handling, and failure-mode tests are all new
  test categories this ADR's testing strategy (§10) would otherwise not
  need.
- **Staleness protection is Option C's one genuine advantage over B** —
  if a repository changes between Development and Code Generation, B
  still trusts a known-real-at-Development-time path that may no longer
  exist; C would not. This audit found no concrete evidence of that
  staleness scenario actually occurring in practice — a real but
  currently-hypothetical extension, not a confirmed gap.

### Option D — Defense in depth (B + C)

Strongest possible guarantee; also the most implementation, all of
Option C's new failure-mode surface *in addition to* Option B's, and a
new question neither B nor C alone has: if Development says `VERIFIED`
and a live Code Generation check disagrees, which wins, and does the
discrepancy need to be surfaced anywhere? Given the instruction not to
over-build past the smallest safe fix, and that Option B alone already
closes both Gap A and Gap B completely for the confirmed code path (§1),
Option D's marginal benefit (staleness protection) is unevidenced by this
audit as a currently-occurring problem.

## 6. Chosen option: **B — carry explicit per-component verification state**

Reasoning, weighed directly against the alternatives:

- Closes both Gap A and Gap B completely for the confirmed code path
  (§1), the same as A and C.
- Is the only option that gets case 6 (legitimate new files) right **by
  construction**, without a second design decision layered on top.
- Is strictly additive to the schema — no existing consumer of
  `plan.components` breaks, unlike Option A's silent-drop behavior
  change.
- Does not require Code Generation to take on a new external runtime
  dependency, new credential propagation, or new live-call failure
  modes, all of which Option C genuinely requires (§5's corrected
  reasoning) even though the original `max_graph_hops=0` objection to it
  was wrong.
- Reuses this codebase's existing deterministic evidence sources and
  existing three-state verification vocabulary, consistent with "do not
  introduce heuristics, do not use LLM judgment as the safety mechanism."
- Option D's added staleness protection is real but not evidenced by
  this audit as a currently-occurring problem, and can be considered
  later as a separate, explicitly-scoped addition if a future audit
  finds concrete evidence of stale-repository-state workflows.

**Rejected: A** (breaks legitimate new-file proposals by construction).
**Rejected for now: C and D** (real, non-graph-related costs — new
external dependency, latency, credential propagation, new failure-mode
surface — larger than the confirmed bug requires; the original
graph-hops-based rejection of C is corrected in §5 but the conclusion to
defer C does not change, for the reasons restated above).

## 7. Adversarial cases — reasoned through explicitly

1. **LLM invents a completely nonexistent file.** No `(repository,
   file_path)` match → `UNVERIFIED` → excluded from
   `_collect_known_file_paths`'s "known" set → `modify`/`delete` rejected.
2. **LLM names a real file from the wrong repository.** The joint,
   repository-partitioned lookup (§4.3) specifically requires the pair
   `(comp.repository, comp.file_path)` to match an entry
   `ComponentDiscoveryTool` returned *for that repository* — a real file
   under a different repository's tree does not satisfy that pair.
   **Full outcome, all four layers (§4.6):** verification state
   `UNVERIFIED`; diagnostic finding `component_repository_mismatch`
   (§4.5, not `component_not_found` — the path *does* exist, just not
   under this repository); blocking for Engineering Review: **yes**,
   unconditionally (§4.7); Code Generation: `modify`/`delete` rejected,
   `create` unaffected (Invariant 2). This is the case §4's
   repository-scoping exists to close, and the case this ADR's revision
   adds full diagnostic-layer treatment for.
3. **LLM names a real file that exists but is unrelated to the requested
   change.** Out of scope — existence/attribution verification cannot
   and should not judge *relevance*. Verifies as `VERIFIED` legitimately;
   whether it *should* be part of the blueprint is a quality/planning
   question, not a trust-boundary one.
4. **LLM names a test file as production code.** Unchanged — handled by
   the existing, separate `check_test_used_as_production` grounding.
   Additive alongside this ADR's fix, not a replacement.
5. **LLM names a directory instead of a file.** No matching file-shaped
   evidence entry → `UNVERIFIED` → excluded, same as case 1.
6. **LLM proposes a new file that legitimately does not exist yet.**
   **`UNVERIFIED`** — not `NOT_CHECKED` (corrected; an earlier draft of
   this ADR stated `NOT_CHECKED` here, which contradicted §4.4's own
   formal definition and case 22's identical scenario; there is no signal
   available to Development — no `operation` field, Invariant H — to
   distinguish "legitimately new" from "wrong claim" at the point
   verification state is computed, so both mechanically produce the same
   state). This is correct and harmless precisely because of §4.4's
   `UNVERIFIED`-does-not-mean-invalid clarification: `UNVERIFIED` is only
   ever consulted for `modify`/`delete` gating (Invariant 2, §8), never
   for `create` — a proposed new file being `UNVERIFIED` is its normal,
   expected state, not evidence of a problem. The component still appears
   in `plan.components` and still reaches Code Generation's prompt
   context. **Pre-existing behavior, not introduced by this ADR:** the
   existing, unmodified `verify_claims` check (`agent.py:535-542`)
   already runs against every component today, new-file proposals
   included, and already produces a blocking `component_not_found`
   finding for a path with no matching evidence — this was true before
   this ADR and remains true after it; this ADR adds
   `file_path_verification`/`component_repository_mismatch` alongside it,
   it does not change what `component_not_found` already does or when it
   already fires.
7. **LLM proposes modifying an existing file.** Requires `VERIFIED` under
   the new gate.
8. **LLM proposes deleting an existing file.** Same treatment as modify —
   `validate_file_operations` already applies its known-path check
   identically to both operations.
9. **Development verification fails** (graph/tool call errors).
   `NOT_CHECKED` for every component that had no evidence pool to check
   against — fail closed, consistent with this codebase's standing
   philosophy.
10. **Verification is unavailable** (old, already-persisted Development
    results predating this field). Defaults to `NOT_CHECKED` — a
    Pydantic field default, not inferred — old results present zero
    verified paths to Code Generation. Fails closed, not open.
11. **Multiple components contain a mix of verified and unverified
    paths.** Handled per-item — `_collect_known_file_paths` filters at
    the individual component level.
12. **A component has a verified name but unverified `file_path`** (or
    vice versa). `name` verification is out of scope for this fix
    (already handled by test-grounding); the new status tracks
    `file_path`-and-`repository` attribution specifically.
13. **Code Generation proposes a path that Development never mentioned at
    all.** Unchanged: absent from `known_file_paths[repository]`
    entirely — `modify`/`delete` rejected exactly as today.
14. **A malicious/adversarial prompt tries to make the LLM invent a
    trusted path.** Structurally immune: the trust decision never
    depends on what the LLM *says* — only on whether
    `ComponentDiscoveryTool`'s independently-collected evidence (not
    influenced by the same prompt) contains a matching pair.
15. **A parallel, path-only verified-set implementation is used instead
    of a per-component field.** `verified_file_paths = {"X"}` — a
    component claiming `(repository=B, file_path=X)` would incorrectly
    read as verified if `X` was actually verified under repository A.
    **Explicitly rejected as a design shape in §5** — this case exists to
    make the rejection testable, not merely stated.
16. **The LLM's own JSON output includes a `"verified": true` (or any
    verification-shaped) key on a component.** Must be silently ignored,
    structurally, not merely by convention — see Invariant A, §8.
17. **A Development-stage human override attempts to set or change a
    component's verification state** (e.g. `{"components": [{...,
    "file_path_verification": "verified"}]}` via `override_stage_result`).
    Must be rejected outright — see Invariant B, §8.
18. **A component's `repository` or `file_path` changes after
    verification was computed** (a hypothetical future code path, not
    present today). The previously-computed verification state must be
    treated as invalid for the new values — see Invariants C/D, §8.
19. **Code Generation reconstructs the same `(repository, file_path)`
    pair independently** (e.g. the Code Generation LLM's own output
    happens to name the same repository and path Development already
    marked `UNVERIFIED`). Repetition by a second, independent LLM call
    is not new deterministic evidence — the status must remain whatever
    Development's own evidence-backed computation produced; Code
    Generation has no mechanism (and must not gain one) to upgrade an
    `UNVERIFIED`/`NOT_CHECKED` component to `VERIFIED` by re-asserting it.
20. **A verified component passes through Code Generation's prompt
    context unchanged.** Its `VERIFIED` status must survive to
    `_collect_known_file_paths` unchanged — this is the positive control
    case (§10 includes this as an explicit regression test, not only the
    negative cases).
21. **Inverse of case 2 — repository A contains `X`; the component
    correctly claims repository A + `X`.** Full outcome: verification
    state `VERIFIED`; **no** `component_repository_mismatch` finding (and
    no `component_not_found` — the pair matched); Engineering Review sees
    no blocking finding from this mechanism; `modify`/`delete` allowed,
    subject to every other existing gate (repository authorization, path
    safety) unaffected by this ADR.
22. **`X` does not exist anywhere, in any repository this run touched**
    (restated precisely, as case 1, to confirm existing behavior is
    preserved rather than replaced). Verification state: `UNVERIFIED`
    (evidence for the claimed repository was available and checked; it
    simply didn't contain this path — per §4.4 this is `UNVERIFIED`, not
    `NOT_CHECKED`, since a check *did* run). **Now explicitly the same
    verification-state outcome as case 6** — both are "evidence was
    available, this path wasn't in it" — the two cases differ only in
    *why* a human might expect the mismatch (a genuine new-file proposal
    vs. a wrong claim), not in the state itself; §4.4's clarification
    that `UNVERIFIED` doesn't mean "invalid" is what makes this
    consistency safe rather than alarming. Diagnostic finding:
    `component_not_found` — the existing, unmodified category, **not**
    `component_repository_mismatch` (§4.5's mutual-exclusivity: a path
    that exists nowhere can never be flagged as mismatched, only as
    not-found), and — as case 6 now states explicitly — this specific
    diagnostic firing for a not-found path is **pre-existing behavior**,
    not introduced by this ADR. Code Generation: `modify`/`delete`
    blocked, `create` unaffected. No new semantics are introduced for
    this case — this ADR's fix is that this outcome is now *enforced* at
    the write gate, not that the diagnostic behavior changes.
23. **The old, repository-blind `verify_claims` check's silence must
    never suppress the new mismatch finding.** Concretely: repository A
    genuinely contains `X` (so the flat evidence pool `verify_claims`
    checks against contains `X`, and `verify_claims([comp.name,
    comp.file_path], evidence_pool)` finds no problem — no
    `component_not_found` warning is produced); the component claims
    repository B + `X`. The new joint, repository-partitioned lookup
    (§4.3) is a **separate, independently-run mechanism** (§3) that does
    not consult or depend on `verify_claims`'s result — it must still
    correctly compute `UNVERIFIED` and still emit
    `component_repository_mismatch`. The old check passing is not
    evidence of anything the new check checks, and must never be treated
    as such by an implementation that (incorrectly) short-circuits the
    new check when the old one already succeeded.

## 8. Invariants

> **Invariant 1 (modify/delete).** An LLM-generated file path may
> influence a `modify` or `delete` operation only after deterministic,
> repository-scoped verification establishes that the `(repository,
> file_path)` pair matches evidence this run's own tool traversal
> actually returned for that specific repository, per §4.3's joint-lookup
> requirement.

> **Invariant 2 (create).** A `create` operation must never require its
> target path to already exist, and must never be gated by verification
> status at all — including when that status is `UNVERIFIED` (§4.4:
> `UNVERIFIED` describes the evidence state, not the validity of the
> proposed change; a new file naturally has no existing evidence to
> match, so `UNVERIFIED` is its expected, harmless state for `create`).
> Creation of a genuinely new file is a legitimate, expected outcome of a
> Development/Code Generation blueprint.

> **Invariant A (source of truth).** LLM output can never directly set
> verification state. `AffectedComponent` construction must use an
> explicit keyword allowlist (as `development/agent.py:154-162` already
> does for every other field:
> `AffectedComponent(name=c.get("name",""), repository=c.get("repository",""), file_path=c.get("file_path",""), ...)`)
> — never `AffectedComponent(**llm_component)`. Verification status is
> computed by code, after parsing, from the repository-scoped evidence
> lookup — it is never a key the LLM's JSON is read for, under any name.

> **Invariant B (override boundary).** Development-stage human overrides
> must never be able to inject, modify, or promote component verification
> state. Concretely: `components` (and any verification-status field on
> it) must never be added to `_OVERRIDABLE_FIELDS["development"]`
> (`workflow_service.py:618-627`). Today this stage has **no** overridable
> fields at all (`_OVERRIDABLE_FIELDS.get("development", frozenset())`
> returns empty) — this invariant exists to keep it that way for this
> specific field even if some other, unrelated Development field is ever
> made overridable in the future.

> **Invariant C (compute-last ordering).** Verification status must be
> calculated only after all other mutation of a component's `repository`
> and `file_path` is complete for that run — concretely, after
> `_apply_test_grounding`'s corrections (`agent.py:488-510`), which is
> also where it is positioned in current code's natural ordering. No
> future change may compute verification status before a mutation that
> could still change the pair being verified.

> **Invariant D (invalidation on mutation).** If a component's
> `repository` or `file_path` is ever mutated after its verification
> status was computed (no such path exists today, but none is
> structurally prevented either), the verification status must be reset
> to `NOT_CHECKED` and recomputed — it must never be carried forward
> against the new value. **Test status, stated explicitly rather than
> left implicit:** no current code path exercises this — there is
> nothing to write a regression test against today, and this document
> does not claim one exists (§10 records this as a future-test
> obligation, not a completed test). The invariant is specified now so
> that the first code change to ever introduce a post-verification
> mutation path is required to satisfy it and add the corresponding test
> at that time, rather than this ADR silently having no opinion on the
> question until a real incident forces one.

> **Invariant E (joint lookup).** See §4.3 — the lookup must test
> `file_path in evidence_by_repository.get(repository, set())` as one
> operation, never `repository` and `file_path` checked independently and
> ANDed.

> **Invariant F (anti-laundering).** An unverified (`NOT_CHECKED` or
> `UNVERIFIED`) component must never become `VERIFIED` merely because: it
> moves between stages; an LLM repeats the claim; a different LLM cites
> the component in its own output; serialized stage context contains it;
> a human override changes an unrelated field; or Code Generation
> independently reconstructs the same `(repository, file_path)` pair in
> its own output. `VERIFIED` requires the one deterministic evidence-
> producing operation (§4.2/§4.3) to have run and matched, full stop — no
> other event may substitute for it.

> **Invariant G (layer separation — no cross-layer shortcuts).** The
> three layers defined in §4.6 (verification state, diagnostic finding,
> write gate) must never read each other's inputs instead of their own.
> Concretely: Code Generation's write gate consults
> `file_path_verification` only, never `verification_warnings`/
> `verification_findings` text or category names. Engineering Review's
> blocking decision consults `verification_findings`' `category`/
> `blocking` only, never `file_path_verification` directly. A future
> change that has Code Generation "just check the warnings list since
> it's already there" (skipping the dedicated field), or has Engineering
> Review "just check `file_path_verification`" (skipping the dedicated
> finding), violates this invariant even if it happens to produce the
> same answer today — the layers must stay independently correct, not
> accidentally consistent. **Additionally, on the consuming side**:
> `_collect_known_file_paths()` must preserve the repository-partitioned
> shape (`dict[repository, set[file_path]]`) it already has today
> (`code_generation/verification.py:101-118`) — never flattened to a
> single `set[file_path]` for convenience. `validate_file_operations`
> must continue to look the known set up by the specific `repository`
> being written to (`known_file_paths.get(repository, set())`, already
> the case today), never by `file_path` alone divorced from repository —
> the identical Gap-B mistake this ADR closes on the Development side
> would be reintroduced on the Code Generation side if this lookup were
> ever collapsed to path-only.

> **Invariant H (operation-blindness at Development).**
> `component_repository_mismatch` (§4.5) is raised without regard to
> what operation (`create`/`modify`/`delete`) the component will
> eventually become, because `AffectedComponent` has no `operation`
> field and Development cannot know this yet (§4.7). It is **not**
> conditioned on a guessed or inferred operation type — doing so would
> require a heuristic this ADR does not introduce. The CREATE/MODIFY/
> DELETE distinction is applied exactly once, at Code Generation's write
> gate (Invariant 2), and nowhere earlier.

These invariants are already partially true today by incidental
properties of unrelated code (§1's write-once persistence, the absence of
a `development` entry in `_OVERRIDABLE_FIELDS`, the explicit-keyword
constructor pattern) — Invariants A, B, C, D, F, G, and H elevate those
incidental facts (and the new diagnostic-layer design in §4.5–§4.7) into
requirements this ADR's implementation must preserve deliberately, not
accidentally.

## 9. Approval / authorization boundary

This change does **not** touch, weaken, or duplicate KAN-28's write-
authorization boundary (`app/agents/git_ops/_authorization.py`). Two
conceptually separate guarantees remain separate, and neither replaces
the other:

> **Authorization** answers: *"Are we allowed to write to this repository
> at all?"* — governed exclusively by the `auto_execution` +
> approved-blueprint gate, the `POST /agent-runs` goal restriction, and
> `verify_repository()`'s identity check. None of these are touched by
> this ADR.

> **File verification** (this ADR) answers a narrower, different
> question: *"Given we are already authorized to write to this
> repository, does this specific existing `(repository, file_path)` pair
> represent a real, evidence-backed target for a `modify` or `delete`?"*

Concretely:

- Repository authorization (whether a write agent may run at all) is
  unaffected — neither touched here.
- Repository *identity* verification (`verify_repository()`,
  `code_generation/verification.py:128-217`) remains independently
  responsible for confirming the target repository itself — this ADR
  adds nothing to and removes nothing from that function.
- No new write-capable code path is introduced. The fix can only ever
  make `validate_file_operations` **more restrictive**, never more
  permissive — there is no direction in which this change could newly
  authorize a write that isn't authorized today.
- The two mechanisms are not merged into one, and this ADR does not
  propose a combined "authorized-and-verified" status — a repository can
  be fully authorized while a specific file within it is `UNVERIFIED`,
  and that is the correct, expected, and only state this ADR changes the
  outcome for.

## 10. Testing strategy

All tests are deterministic (no LLM calls), following the existing
pattern in `test_code_generation_verification.py`/
`test_hypothesis_verification_correlation.py`.

**Development-side:**
- Nonexistent `(repository, file_path)` → `UNVERIFIED`, still present in
  `plan.components`.
- Real, correctly-attributed `(repository, file_path)` → `VERIFIED`.
- Real file, wrong `repository` claimed → `UNVERIFIED` (case 2 — must
  fail before this fix, pass after; this is the test that would have
  passed under a naive "existence only" fix and must not).
- Repository A contains `X`, repository B also contains `X` → a claim of
  `(A, X)` verifies only from A's own evidence; a claim of `(B, X)`
  verifies only from B's — never cross-satisfied (case 15's positive
  control).
- Repository A is in scope but `X` exists only in repository B → a claim
  of `(A, X)` is `UNVERIFIED`, not `VERIFIED` (the AND-shaped
  reimplementation this test is specifically designed to catch, per
  Invariant E).
- Directory-shaped path → `UNVERIFIED` (case 5).
- LLM JSON includes a `"verified": true`/`"file_path_verification":
  "verified"` key on a raw component → ignored; status is still computed
  by the repository-scoped lookup, never read from the LLM's own claim
  (case 16, Invariant A) — assert by constructing the `AffectedComponent`
  via the real parsing function with a raw dict containing that key and
  confirming the resulting status is independent of it.
- A simulated Development-stage override payload containing a
  verification-shaped key is rejected by `override_stage_result` with the
  existing "no correctable fields" error (case 17, Invariant B) —
  regression-asserts `"development"` stays absent from
  `_OVERRIDABLE_FIELDS`.
- Existing `check_test_used_as_production` behavior unchanged — the
  renaming/dropping behavior for the test-vs-production case is
  unaffected by this fix.
- Mixed verified/unverified components in one plan → each item's status
  is independent; no cross-contamination.
- `graph_unavailable`/no evidence pool → all components default to
  `NOT_CHECKED` (case 9).
- **Repository A contains `X`; component claims repository B + `X`**
  (case 2, full outcome): asserts all four layers together —
  `file_path_verification == "unverified"`, a `component_repository_mismatch`
  finding is present in `verification_findings` (not `component_not_found`),
  the finding's `blocking` property is `True`, and `X` is absent from
  repository A's and repository B's respective known-verified sets alike.
- **Repository A contains `X`; component correctly claims repository A +
  `X`** (case 21, inverse/positive control): `file_path_verification ==
  "verified"`, **no** `component_repository_mismatch` and **no**
  `component_not_found` finding is present for this component.
- **`X` exists in neither repository** (case 22): `file_path_verification
  == "unverified"`, finding is `component_not_found` (unchanged, existing
  behavior), **not** `component_repository_mismatch` — asserts the two
  categories are mutually exclusive, not merely usually-different.
- **Old `verify_claims` result cannot suppress the new finding** (case
  23): construct an evidence pool where the flat, repository-blind
  `verify_claims([comp.name, comp.file_path], evidence_pool)` call
  produces **zero** warnings (i.e. `X` is present in *some* repository's
  evidence, so the old check is satisfied), while the component's claimed
  repository does not actually contain `X`. Assert
  `component_repository_mismatch` is still raised and
  `file_path_verification` is still `"unverified"` — proving the new
  mechanism does not read, and cannot be short-circuited by, the old
  mechanism's result.
- **Invariant D — explicitly not testable today, and stated as such, not
  silently omitted.** No test is added for "verification is invalidated
  after a post-computation mutation of `repository`/`file_path`," because
  no current code path performs such a mutation (§8, Invariant D). This
  bullet exists so the absence is a documented, deliberate scoping
  decision rather than something a future reviewer has to rediscover.
- **CREATE is never affected by a `component_repository_mismatch`
  finding** — a component flagged with the mismatch finding, when later
  used as a Code Generation `create` target, is not blocked by
  `validate_file_operations` (Invariant 2, Invariant H) — this test
  exists specifically to prove Engineering Review's blocking finding and
  Code Generation's write gate are independently correct, not
  accidentally coupled (§4.7).

**Code Generation-side:**
- `_collect_known_file_paths` only includes paths from components whose
  status is exactly `"verified"` — `"not_checked"` and `"unverified"` are
  both excluded identically.
- Old-format Development result (field absent) → treated as
  `NOT_CHECKED` for every component (case 10) — the deserialization
  default, not an inferred value.
- `create` operation at any path (any status) → never blocked by the
  known-path check (Invariant 2) — explicit regression guard so this fix
  can never accidentally couple `create` to verification status.
- `modify`/`delete` at a `VERIFIED` path → allowed (unchanged).
- `modify`/`delete` at an `UNVERIFIED` or `NOT_CHECKED` path → rejected
  (behavior-change assertion — fails before the fix, passes after).
- **Code Generation cannot regain trust by repeating an unverified
  Development path** — feed Code Generation a Development result
  containing an explicitly `UNVERIFIED` component, have the (simulated)
  Code Generation LLM output propose a `modify` at that exact
  `(repository, file_path)`, and assert `validate_file_operations` still
  rejects it (case 19, Invariant F).
- A `VERIFIED` component's status survives unchanged through Code
  Generation's read of the persisted result (case 20 — positive control,
  not only negative cases).
- Planning's `verify_repository()`/repository-scope behavior: unchanged —
  a regression test confirming this ADR's change has zero effect on
  repository-level verification (independent functions; the test makes
  that independence explicit and checked, not assumed).

**Real-pipeline regression test** (consistent with Phases 1–3's practice):
one test constructed from real, previously-captured production data —
reusing this engagement's existing captured fixtures (e.g. workflow
`74f8b66a`'s real `repository_usage`/component data, or a fresh
equivalent capture) run through the real `_collect_known_file_paths` →
`validate_file_operations` pipeline end-to-end, confirming a real
historical, correctly-attributed component still verifies and is usable,
and that a synthetically-injected wrong-repository or nonexistent path
added to that same real data set is correctly excluded.

**Full existing legitimate-workflow regression:** the complete
`test_report_view_model.py`, `test_code_generation_verification.py`, and
Development-agent test suites must remain green unmodified in their
existing assertions (aside from the new tests above).

## 11. Migration / backward compatibility

- **No schema migration.** `AgentStep.result` is already unstructured
  JSON; the new field is additive within it.
- **No API contract break.** `AffectedComponent`'s new field is optional
  with a safe `NOT_CHECKED` default — any code deserializing an old
  Development result without the field gets the fail-closed default, not
  an error.
- **No frontend change required** for this fix to function — the new
  field is consumed entirely on the backend by
  `_collect_known_file_paths`. Surfacing it in the blueprint UI is a
  possible, separate future enhancement, not required or proposed here.
- **In-flight workflows**: a workflow whose Development stage completed
  *before* this fix ships will, on reaching Code Generation after the
  fix, have every component at `NOT_CHECKED` (case 10) — any
  `modify`/`delete` it attempts is rejected until re-run. Intentional,
  fail-closed, not a bug.

## 12. Explicit non-goals

- Does not change `verify_claims()`'s generic, repository-agnostic
  contract — a separate, purpose-built joint lookup is added alongside
  it (§4.3), not a redefinition of the shared function.
- Does not touch `check_test_used_as_production` or component-name
  verification (§4, case 12) — a separate, already-solved problem.
- Does not implement Option C or D's live/dual-check mechanism — no
  staleness protection is added; a future ADR could propose it if
  concrete evidence of the staleness scenario is found, now correctly
  scoped against the real costs identified in §5 rather than the
  incorrect `max_graph_hops` objection this document originally raised.
- Does not modify `verify_repository()`, KAN-28's authorization boundary,
  or any GitHub write call (§9).
- Does not address ADR 0026 P2 Finding #1 (unlabeled commit/PR text) —
  unrelated, tracked separately as P3.
- Does not add any heuristic, prompt change, or LLM-based judgment as
  part of the safety mechanism — the entire fix is deterministic
  evidence-pair matching, consistent with every other verification
  mechanism in this codebase.
- Does not add `components`/verification status to
  `_OVERRIDABLE_FIELDS["development"]` (Invariant B) — explicitly stays
  out of scope for any future change, not only this one.
- No code, schema, or prompt change is made by this document itself.

## 13. Recommendation

**Option B — carry explicit per-component verification state**, using the
three-state `NOT_CHECKED`/`VERIFIED`/`UNVERIFIED` vocabulary (§4.4), a
per-component field (not a flat parallel path set — §5, case 15), a
joint repository-partitioned lookup (§4.3, Invariant E), the
`component_repository_mismatch` diagnostic layer kept structurally
distinct from verification state and the write gate (§4.5–§4.7,
Invariant G), and Invariants A–H (§8) enforced explicitly, not left as
incidental properties of unrelated code. This remains the smallest
change that fully closes both Gap A and Gap B for the confirmed code
path, is strictly additive/backward-compatible, correctly and
structurally handles the legitimate-new-file case, and avoids the new
external dependency, credential propagation, and live-failure-mode
surface Option C would require.

## 14. Update to ADR 0026's Phase 4 recommendation

ADR 0026 §7/§9 is **not rewritten** — its original text and conclusions
stand as the historical record of that audit; see ADR 0026's own
Addendum section for the pointer to this document. ADR 0026's P2 Finding
#1 (unlabeled commit/PR content) remains a separate, still-open,
lower-priority (P3) item, unaffected by this document.

## 15. Revision note

This document was revised once after an independent skeptical design
review of its first draft. That review's findings and this revision's
response to each, for traceability:

1. §4.4/§8 Invariant A — the review confirmed the LLM structurally cannot
   set verification state today only because `AffectedComponent` is
   constructed via an explicit keyword allowlist, and flagged that this
   ADR did not previously state that as a requirement. Now explicit
   (Invariant A).
2. §8 Invariant B — the review confirmed no override path exists for the
   `development` stage today only by the *absence* of an entry in
   `_OVERRIDABLE_FIELDS`, and flagged that this ADR did not previously
   commit to keeping it that way. Now explicit (Invariant B).
3. §8 Invariants C/D — the review found no current code mutates
   `repository`/`file_path` after construction, but that this ADR did not
   previously specify an ordering/invalidation rule for if one ever did.
   Now explicit.
4. §4.3/§8 Invariant E — the review identified that an AND-of-two-
   independent-checks reimplementation would silently reintroduce Gap B.
   Now explicit as a required lookup shape, with a dedicated test.
5. §5's Option B text previously offered "a parallel `verified_file_paths:
   set[str]`" as an equally-valid alternative shape; the review identified
   this as capable of reintroducing Gap B if implemented as a flat,
   path-only set. That alternative is now explicitly rejected unless
   keyed by `(repository, file_path)` (§5, case 15).
6. §5's Option C rejection previously cited `max_graph_hops=0` as a
   reason a live GitHub-based check was architecturally precluded; the
   review found this factually incorrect (`max_graph_hops` governs Neo4j
   only). Corrected in §5, with the real costs of Option C (external
   dependency, latency, credential propagation, new failure modes) now
   stated as the actual basis for deferring it — the recommendation
   (Option B) is unchanged by this correction.
7. §4.4 — the review recommended reusing the existing three-state
   `VerificationStatus` vocabulary instead of a boolean, for consistency
   with this codebase's established pattern and to preserve the
   `NOT_CHECKED` vs. `UNVERIFIED` distinction for diagnostics. Adopted,
   with an explicit note on where the type should live to avoid a
   backwards module dependency.
8. §7 cases 15–20 — added directly from the review's adversarial
   scenarios (laundering via a flat parallel set, LLM self-reporting
   verification, override injection, post-verification mutation,
   Code-Generation-side re-assertion, and the positive "stays verified"
   control), each cross-referenced to the invariant that closes it.

### Second revision — resolving the diagnostic-layer P2

A second review identified one remaining P2: this document didn't specify
whether a repository-mismatch (case 2) should be human-visible, distinct
from the existing `component_not_found` category, or how it should
interact with Engineering Review's blocking machinery. Resolved as
follows, per explicit decision:

1. §4.5 (new) — introduces `component_repository_mismatch` as a new,
   distinct `VerificationFinding` category, mutually exclusive with
   `component_not_found` by construction, added to
   `BLOCKING_CATEGORIES` for documentation completeness (blocking by
   default already, per the existing category rule).
2. §4.6 (new) — the three-layer table (verification state / diagnostic
   finding / write gate) and the explicit no-cross-layer-shortcuts rule,
   now also codified as Invariant G.
3. §4.7 (new) — `component_repository_mismatch` is blocking for
   Engineering Review **unconditionally**, not conditioned on eventual
   operation type, because Development has no `operation` field to
   condition on (Invariant H). CREATE is unaffected only at the write-gate
   layer, never at the diagnostic layer — an intentional asymmetry,
   reasoned through explicitly rather than left to be discovered as an
   inconsistency.
4. §3 tightened — replaces "used alongside it" with an explicit
   three-bullet statement that `verify_claims` is unmodified, the new
   check is separate, and neither substitutes for the other.
5. §7 cases 21–23 added: the positive inverse of case 2, the
   preserved-behavior restatement of "path exists nowhere," and the
   explicit non-suppression test between the old and new mechanisms.
6. §8 Invariant D — test status stated explicitly (no current mutation
   path, so no test exists yet; this is a documented scoping decision,
   not an oversight) rather than left as a silent gap for a reviewer to
   find.
7. §10 — six new test requirements added, directly implementing §7's
   cases 2 (full outcome), 21, 22, 23, Invariant D's explicit non-test,
   and the CREATE/mismatch independence check.

### Third revision — resolving the Case 6 / Case 22 contradiction

Discovered during the pre-implementation readiness trace, before any code
was written: §7 case 6 stated a legitimately-new-file proposal should be
`NOT_CHECKED`, while case 22 (a mechanically identical "path not in
evidence" situation) stated `UNVERIFIED`, citing §4.4's own formal
definition. Development has no `operation` field (Invariant H) and so
cannot distinguish the two situations at the point verification state is
computed — they cannot both be right. Resolved, per explicit decision:

1. §4.4 tightened to the precise, load-bearing definitions: `NOT_CHECKED`
   = evidence was unavailable to check against at all; `UNVERIFIED` =
   evidence was available and checked, the pair wasn't in it (regardless
   of *why* — a new-file proposal and a wrong claim are mechanically
   identical inputs and now correctly receive the same state). Added an
   explicit, quoted clarification: **`UNVERIFIED` does not mean the
   proposed change is invalid** — it describes evidence state only,
   never a judgment on the component or operation.
2. §7 case 6 corrected to `UNVERIFIED` (previously `NOT_CHECKED`, in
   contradiction with §4.4 and case 22), with an explicit note that this
   is a correction, not a new decision, and an explicit statement that
   `component_not_found` firing for a new-file proposal is **pre-existing
   behavior** (the unmodified `verify_claims` check already does this
   today, unrelated to this ADR).
3. §7 case 22 cross-referenced to case 6 as now-identical in outcome, and
   restated as pre-existing diagnostic behavior.
4. §5 Option B's case-6 discussion and §8 Invariant 2 both updated to
   reference `UNVERIFIED`, not `NOT_CHECKED`, for the new-file case, with
   Invariant 2 now stating explicitly that `create` is unaffected
   *regardless* of verification status, `UNVERIFIED` included.
5. No change to the three-layer design (verification state / diagnostic
   finding / write gate) or to any invariant's substance — this revision
   corrects a label/definition inconsistency between two examples, not
   the architecture.
