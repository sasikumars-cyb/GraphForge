# Context Discovery Benchmark — PROT-5764 Repository Resolution

**Status:** Specification only. No production GraphForge code is changed by this
document. This benchmark is a test case Context Discovery must pass, not an
implementation.

**Origin:** An independent, GraphForge-free investigation (Jira MCP + GitHub
`gh` CLI, human-in-the-loop, documented in this session's transcript) resolved
PROT-5764 to a specific repository by chaining evidence across three
repositories, not by matching a name. This benchmark formalizes that
investigation into a repeatable, scorable test so Context Discovery's actual
reasoning quality — not this one session's luck — can be measured.

---

## 1. Benchmark objective

> Given only the Jira ticket PROT-5764, can GraphForge's Context Discovery
> independently identify the correct repository by following behavioral and
> architectural evidence across the engineering ecosystem — evidence that
> spans more than one repository — rather than by matching a keyword or
> substring against a repository name?

This benchmark does not test whether GraphForge can retrieve a ticket. It
tests whether Context Discovery's output is **evidence-backed** in the same
sense a competent engineer's triage note would be: a named file/function, a
reason that file is relevant, and an explanation of why competing candidates
were rejected.

A run that returns the correct repository with no supporting evidence, or
with evidence that amounts to "the name contains a matching substring," is a
**failing run** even though the repository field is correct. Getting the
right answer for the wrong reason is explicitly a failure mode this
benchmark exists to catch (see §8, "correct by coincidence").

## 2. Input ticket

```
Key:     PROT-5764
Summary: Avangrid TnT - event_owner should be set to 'client'
         if pipeline failed with schema validation failure
Type:    Bug, P3
Labels:  DS_Internal, DS_STG
Description (paraphrased): A batch run failed with a schema validation
  failure. The resulting Track & Trace events were tagged
  event_owner = 'internal'; the reporter expected event_owner = 'client'
  for a schema-validation-caused failure, since the failure is caused by
  client-submitted data, not by the platform itself. A "repair" pass later
  in the day produced the correct 'client' value for one of the two
  affected events, but not the other, and the reporter treated this as an
  open bug, not confirmation the system already works correctly.
```

The benchmark harness may supply the exact ticket fields (title, description,
labels) as Context Discovery would normally receive them. It must **not**
supply the resolved repository name, file paths, or function names as input
— those are the values under test.

## 3. Expected repository

```
Repository:
ds-databricks-avangrid-em-ct-dataingest

Confidence:
High
```

This value is the benchmark's **answer key**, used only for scoring after a
run completes. It must never be injected into Context Discovery's prompt,
retrieval filters, or ranking logic. See §10 for the implementation
constraint this implies.

## 4. Evidence chain

Each step below is written as the independent investigation actually found
it. A Context Discovery run does not need to visit these in this exact order
or use these exact tools — it needs to *arrive at equivalent facts*. The
scoring rubric (§8) checks for the facts, not the path.

### Step 1 — Jira ticket → domain concepts

```
Evidence:    Ticket title + description text
Entity discovered: "Avangrid" (utility/tenant brand), "TnT" (an
             abbreviation, not a literal system name), "event_owner"
             (a field name), "client" vs "internal" (an enum-like
             classification), "schema validation failure" (a pipeline
             failure category)
Why it matters: None of these terms alone identifies a repository.
             "Avangrid" is a brand shared across 10+ repositories.
             "TnT" must first be expanded/confirmed as "Track and Trace"
             before it's useful as a search term.
Next direction: Search the engineering ecosystem for "Track and Trace" /
             "transaction tracker" / "TnT" as a named subsystem, not for
             "Avangrid" as a repository-name substring.
```

### Step 2 — Domain clue → TnT as a real subsystem

```
Evidence:    Cross-repository search for "track and trace", "TnT",
             "transaction tracker", "tnt_errors"
Entity discovered: TnT is a real, named subsystem with multiple
             independent implementations across the org — at least one
             HTTP-API-based "Transaction Tracker" service and at least
             one Databricks/Spark shared-job-based "TnT" error-logging
             utility. These are NOT the same system.
Why it matters: A naive investigation stops at the first "Transaction
             Tracker"-named repository it finds. That repository may be
             the correct *concept* match and still be the wrong
             *repository* — see Failure Case / competing repo analysis
             in §6.
Next direction: Determine which TnT implementation is actually reachable
             from an Avangrid-tenant batch pipeline failure, not which
             TnT implementation has the most complete-looking API.
```

### Step 3 — `event_owner` / `client` as a concrete, checkable field

```
Evidence:    Code search for "event_owner", "EventOwner", "owner" in
             TnT-adjacent code
Entity discovered: Two independent implementations of a client/internal
             ownership concept exist in the ecosystem:
             (a) an enum-based implementation (CLIENT/INTERNAL/PIPELINE)
                 attached to an HTTP ingest-event API, and
             (b) a string-literal implementation ('client'/'internal')
                 attached to a Databricks error-logging table with a
                 column literally named `owner`.
Why it matters: Both are real, both are plausible, and picking the wrong
             one leads to a wrong repository even though the concept
             match is correct. The two must be disambiguated by which
             one is actually reachable from an Avangrid batch failure —
             not by which one looks more complete or is easier to find.
Next direction: For each candidate implementation, find its caller(s)
             and check whether any caller is Avangrid-tenant-specific.
```

### Step 4 — Schema validation as the specific failure mode

```
Evidence:    Code search for "schema validation" / "schema_validator" /
             "validate_schema" near each TnT-implementation candidate
Entity discovered: A schema-validation module exists directly alongside
             one of the two `owner`/`event_owner` implementations found
             in Step 3 (not the other).
Why it matters: The ticket is specifically about a *schema validation*
             failure, not a generic pipeline failure. A repository that
             merely has "schema validation" code somewhere with no
             connection to the TnT/owner mechanism is not evidence (see
             Failure Case C, §7). The evidence only counts once schema
             validation and the owner-setting mechanism are shown to be
             in the same failure path.
Next direction: Trace the actual exception/control flow from the schema
             validator to the TnT error-emission call.
```

### Step 5 — TnT error handling: shared library vs. caller

```
Evidence:    The owner-setting mechanism found in Step 3(b) is not
             defined inline — it is imported from a separate,
             shared/reusable library used by many pipelines.
Entity discovered: A shared library repository that owns the actual
             "write an error row with owner=client|internal" capability,
             exposing **multiple, purpose-differentiated methods** (not
             one generic method) — including at least one method whose
             own documentation names schema/validation-type failures as
             its intended use, and at least one separate method intended
             for genuine internal/infrastructure failures.
Why it matters: This is the single most important disambiguating fact in
             the whole chain. The shared library is not buggy — it
             already exposes the correct capability. This is exactly the
             kind of fact that separates "found a repository that talks
             about client/internal" from "found the repository
             responsible for the defect." A shared library that HAS the
             right method is supporting evidence, not the defect site
             (Failure Case D, §7).
Next direction: Find every caller of this shared library and check which
             caller(s) fail to use the schema/validation-specific method.
```

### Step 6 — Caller implementation: the actual defect

```
Evidence:    Among the shared library's callers, one caller's top-level
             error handling wraps its entire pipeline run in a single,
             undifferentiated exception handler that always invokes the
             shared library's generic/internal-error method — including
             when the exception originated from that same caller's own
             schema-validation step.
Entity discovered: A specific orchestration file, in a specific
             repository, containing:
               (a) a call into the schema-validation module found in
                   Step 4,
               (b) a single broad exception handler with no branch for
                   validation-type failures, and
               (c) a call into the shared library's generic/internal
                   error-emission method from that handler.
Why it matters: This is the actual mechanism that produces the ticket's
             observed defect: a schema-validation failure (client's
             fault) is emitted with the shared library's internal-error
             method (owner='internal') because the caller never branches
             to the client-specific method the shared library already
             offers. This is genuine multi-repository causal reasoning:
             the defect is not "in" the shared library and not "in" the
             validator alone — it is in how the caller wires the two
             together.
Next direction: Confirm this caller belongs to the correct tenant/brand
             by checking its own workflow/job configuration.
```

### Step 7 — Workflow/configuration confirms tenant scope

```
Evidence:    The caller repository's deployment/workflow configuration
             (job definitions, one per tenant or tenant-brand) includes
             an entry matching the specific batch type named in the
             ticket description (a short, non-obvious operational code,
             not the literal word "Avangrid").
Entity discovered: A workflow/job definition whose name encodes both the
             brand family the ticket mentions and the specific batch
             variant named in the ticket text.
Why it matters: This is the fact that discriminates between two (or
             more) near-identical, same-brand repositories that both
             plausibly look correct from Steps 1–6 alone. Repository
             identity is only fully resolved once this configuration-
             level fact is checked — code similarity between sibling
             repositories is not sufficient on its own.
Next direction: none — this is the terminal confirmation step.
```

### Step 8 — Final repository

```
Evidence:    The convergence of Steps 4–7: schema validator + generic
             exception handler + shared-library caller + tenant-specific
             workflow config, all inside one repository.
Entity discovered: ds-databricks-avangrid-em-ct-dataingest
Why it matters: This is the terminal node the evidence chain converges
             on. No single step in isolation identifies it; the
             identification is the intersection of Steps 4, 6, and 7.
Next direction: n/a — report repository + evidence + confidence.
```

## 5. Expected cross-repository relationships

Context Discovery must demonstrate, not merely assert, traversal of:

```
Application repository (caller)
      ↓  imports / calls
Shared library (owns the owner=client|internal capability)
      ↓  implements
TnT error-logging mechanism (writes owner to an error table)
      ↓  is invoked with the WRONG method by the caller for THIS failure type
owner = internal   (observed, wrong)
owner = client      (available in the shared library, unused by this caller
                      for this failure path)
```

A run that identifies the shared library and stops — reporting it as *the*
answer — has not completed this traversal. A run that identifies the
application repository but cannot state what capability it's failing to use,
and where that capability lives, has also not completed this traversal (see
§8 weighting: "cross-repository relationship resolution" is scored
independently of "repository identification").

## 6. Competing repositories (must be considered, must not win as primary)

| Repository | Legitimate reason it surfaces | Why it is not the answer |
|---|---|---|
| Shared library repository (owns the TnT `owner=client\|internal` implementation) | Contains the literal `event_owner`/`owner` mechanism named in the ticket; contains a method whose docstring explicitly mentions schema/validation errors | It is infrastructure the defect *uses*, not infrastructure that *contains* the defect. Its own behavior is correct — both the client and internal paths exist and work as documented. |
| A sibling repository for the same tenant/brand family, without the specific batch-variant workflow named in the ticket | Same brand, same shared-library dependency, same general architecture, same schema-validation pattern | Does not contain the specific workflow/job configuration matching the batch variant described in the ticket. Near-miss on every dimension except the one that actually discriminates (§4 Step 7). |
| A large, generic, multi-tenant ingest platform with its own, differently-named client/internal/pipeline ownership enum | Superficially the strongest keyword match — has an enum literally containing the word used in the ticket ("client") | Zero code-level connection to the TnT/`tnt_errors`/shared-library mechanism actually described by the ticket. This is the clearest case of a plausible-looking but structurally unrelated system; a run that selects this repository is scored as a false positive regardless of how confident it reports being. |

Do not name these repositories inside Context Discovery's search/ranking
logic. They are listed here only so the benchmark scorer knows what a
plausible wrong answer looks like.

## 7. Negative / failure cases

A run is graded against each of these independently. Any one triggered is a
scoring deduction (§8) regardless of whether the final repository field
happens to be correct.

- **Failure Case A — name-substring win.** The returned evidence consists
  primarily of "repository name contains the tenant/brand string from the
  ticket" with no code-level behavioral evidence. Fails even if the
  repository field is correct.
- **Failure Case B — unrelated field-name win.** The returned evidence cites
  a repository containing a field or variable named the same as the one in
  the ticket (e.g. an unrelated `owner`/`event_owner`-style field in a
  system with no relationship to TnT/pipeline failures). Fails regardless of
  repository field correctness.
- **Failure Case C — unconnected capability win.** The returned evidence
  cites a repository with real schema-validation code that has no
  demonstrated connection to the TnT/error-ownership mechanism. Having both
  concepts present in the same repository is not evidence of a connection
  between them; the evidence must show the two are wired together (a call,
  an import, a shared control-flow path).
- **Failure Case D — shared-library misattribution.** The run selects the
  shared TnT library as the *primary/defect* repository. The shared library
  must be surfaced as supporting evidence/dependency (correctly identifying
  it is a "cross-repository relationship resolution" credit, §8) but
  selecting it as the primary answer is scored as an incorrect resolution,
  since the library's own behavior is not defective.
- **Failure Case E — premature stop.** The run stops at the first
  repository that satisfies *any single* piece of evidence (e.g. stops at
  the shared library because it found `owner='client'` there, or stops at a
  sibling repository because it found a matching schema validator) without
  checking whether a more specific, better-connected candidate exists.

## 8. Success criteria / scoring

```
Repository identification                   25%
Behavioral evidence (specific file/function) 20%
Cross-repository relationship resolution     20%
Supporting code evidence (quotes/paths)      15%
Competing-repository rejection (§6, §7)      15%
Confidence/provenance quality                 5%
```

Rationale for adjusting the example weighting given in the task: "behavioral
evidence" and "cross-repository relationship resolution" are weighted equal
to each other and nearly equal to plain repository identification, because
this benchmark's entire premise (per the task's own framing) is that the
repository cannot be correctly and defensibly identified *without* both.
"Competing-repository rejection" is raised from 10% to 15% because §7's five
failure cases are exactly the failure modes this benchmark exists to guard
against — a scoring scheme that under-weights them would let a
lucky-but-shallow run pass.

**Scoring bands:**

- **Pass (≥80%):** Correct repository, with named files/functions, a
  demonstrated (not merely asserted) shared-library relationship, and
  explicit rejection reasoning for at least the strongest competing
  repository from §6.
- **Partial (50–79%):** Correct repository but with a weak or incomplete
  evidence chain (e.g. finds the caller but not the shared library's
  differentiated methods, or vice versa), or fails to explain why a
  competing repository was rejected.
- **Fail (<50%):** Wrong repository, OR correct repository reached via any
  of the five failure cases in §7 ("correct by coincidence").

## 9. Expected final answer format

```
Repository:
<name>

Confidence:
High / Medium / Low

Evidence:
- <specific file/function/config, quoted or paraphrased with enough
  precision to be independently re-checked>
- ...

Cross-repository relationships discovered:
- <application repo> depends on <shared library repo> for <capability>
- <capability present but unused for this failure path, and why that
  matters>

Competing repositories considered and rejected:
- <repo>: <why it was considered> / <why it was rejected>
- ...

Reasoning chain:
Jira ticket → <concept> → <concept> → ... → <repository>
```

A response missing the "competing repositories considered and rejected"
section is automatically capped at the "Partial" scoring band regardless of
how correct the repository field is — absence of that section is itself
evidence the run did not do comparative reasoning (§8, Failure Case E).

## 10. What constitutes a false positive

A **false positive** is any run where the reported repository does not
match §3, OR matches §3 but is reached through one of the §7 failure cases.
Correctness of the repository field alone is not sufficient to avoid a false
positive under this benchmark — see §8's "correct by coincidence" framing,
which is the central distinction this whole benchmark is designed to
enforce.

## 11. What constitutes insufficient evidence

A run has **insufficient evidence** (scored as Partial or Fail even with the
correct repository) when any of the following hold:

- No specific file, function, or configuration artifact is named — only
  repository-level or conceptual claims.
- The shared-library relationship (§5) is asserted but not demonstrated
  (no evidence the caller actually imports/invokes it).
- At least one competing repository from §6 is not addressed at all.
- The confidence level stated is not justified by the evidence quality
  actually presented (e.g. "High" confidence backed only by a name match).

---

## Implementation constraint (non-prescriptive)

This benchmark tests **outcome and evidence quality only**. It intentionally
does not specify, require, or forbid any particular technique — no LLM use,
embedding strategy, fuzzy-matching approach, alias table, graph-traversal
algorithm, or ranking function is prescribed. Any Context Discovery
implementation that produces the evidence and relationships described in
§4–§9, through whatever internal mechanism, passes. The one hard constraint
is negative, not positive: the ticket key, tenant name, and expected
repository name from §2–§3 must never appear inside Context Discovery's own
search/ranking/matching code as a special case — they may only appear in
this document, as the answer key used for scoring after a run completes.
