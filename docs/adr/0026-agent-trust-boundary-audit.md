# ADR 0026: Agent Trust Boundary Audit

## Status

Proposed. **Design/audit only — no implementation in this ADR or alongside
it.** This document has two distinct halves, deliberately ordered:
first a complete, evidence-based **inventory** of every LLM-generated
field across the agent chain and how much trust each one is structurally
given (§§3–6); only after the inventory is complete does §7 discuss
possible future directions, and even those are framed as open questions
for a later decision, not a plan to build. Nothing in this document
changes behavior. It extends the scope of the Context Discovery →
Reasoning → Verification → Report audit closed in ADR 0025's
implementation record — that audit covered five stages; this one covers
every agent in `app/agents/`, including the external-write agents.

## 1. Why this document exists

ADR 0025 (Phase 3) closed a real gap — hypotheses could never correlate
with verification — and the adversarial audit that followed it (recorded
in PR #23's description, not a standalone ADR) confirmed the fix was
sound within its own scope: Context Discovery → Reasoning → Verification
→ Report. That audit's own P2 finding #2 was explicit that it was
**not** exhaustive: *"No exhaustive whole-codebase LLM-field sweep was
completed this pass — recommend as a dedicated future audit task."* This
document is that task.

The question this audit asks, for every LLM-generated field in the
system, not just the five stages already covered:

> **If this field is wrong, what actually happens?** Is it shown to a
> human as a belief, shown as fact, or does it steer what code runs next?

This is a different question from "is a write authorized." GraphForge
already has a dedicated, code-enforced answer to that question — see §2.
This audit is about the field-level question one layer down: for a
write agent that *is* authorized to run, is what it's about to write
itself trustworthy, or just permitted?

## 2. Existing prior art — not re-derived here

`app/agents/git_ops/_authorization.py` (KAN-28) is already, in effect, a
trust-boundary audit for the three real external-write agents
(`create_branch`, `commit_changes`, `create_pull_request`). It is cited
directly rather than repeated:

- **Full inventory of write-capable agents**, grepped across
  `app/agents/` and `app/context_pipeline/` at its own audit time: the
  three git_ops write agents are the only ones that touch an external
  system. Jira write is `NotImplementedError` (nothing to gate). `Confluence`
  and `Jira` read paths are read-only. `run_tests` reads GitHub Check Runs,
  a read.
- **Two independent, code-verified facts** proving all three write
  agents can only ever run via a legitimately human-approved flow:
  1. `workflow_service.create_workflow` refuses to create an
     `auto_execution` workflow unless `source_workflow_id` references a
     Planning workflow with `status == "approved"`, reachable only via
     the authenticated `POST /workflows/{id}/approve` endpoint.
  2. `POST /agent-runs`'s `_load_standalone_planning_context` restricts
     standalone runs to `_PLANNING_CONTEXT_SUPPORTED_GOALS =
     {"develop_change_plan", "plan_tests"}` — none of `WRITE_GOALS`. A
     direct `goal="create_pull_request"` call always runs with
     `extras["workflow"]` absent, and each agent's own guard raises
     before any GitHub call.
  3. `tests/unit/ai/test_manifest_dependency_integrity.py` keeps this
     honest going forward: any manifest declaring
     `DEPENDENCY_GITHUB_WRITE` must also declare
     `requires_external_write_authorization=True`.

**This audit does not re-check or dispute that finding.** It is treated
as settled: *whether a write agent may run at all* is soundly gated.
What KAN-28 does **not** ask, and what §5 of this document adds, is a
narrower question: once a write agent is legitimately running, is
**each individual field it is about to write** — a branch name, a commit
message, a PR title/body, a target repository — itself trustworthy, or
only permitted? Authorization and field-level trust are independent
axes; a fully-authorized write can still write an untrustworthy value if
nothing downstream checks the value itself.

## 3. Classification scheme (carried forward from the Phase 3 audit)

Every LLM-generated field in the inventory below is classified into
exactly one of four categories:

| Class | Meaning | Bar it must clear |
|---|---|---|
| **A. Belief** | Model interpretation, explicitly presented as reasoning/belief, never asserted as settled fact. | UI must visibly represent it as belief (e.g. a confidence score, a "hypothesis" label) — no further check needed. |
| **B. Claim** | A model-generated statement about the world that could be read as factual by a human or by other code. | Needs a deterministic check before being trusted as fact; until then must render/behave as unverified. |
| **C. Verified fact** | Backed by deterministic evidence, or independently computed/checked by code before use. | Already meets the bar — the interesting question is *what* the check actually verifies, not whether one exists. |
| **D. Control / input** | A model-generated value that influences what the workflow *does* next (routing, target selection, an action to take) — a distinct risk axis from A/B/C even when the value is never shown to a human as a "fact." | Needs its own side-effect-risk assessment: what is the blast radius if this value is wrong, and what — if anything — catches that before the side effect happens? |

A field can be more than one class depending on how it's used downstream
(e.g. `repository` in code_generation is Class B on first receipt from
the LLM, and becomes Class D at the moment `verify_repository()` gates a
write on it — both are recorded).

## 4. Scope

Every directory under `app/agents/` plus the LLM-facing parts of
`app/context_pipeline/`:

`api_intelligence`, `blueprint`, `code_generation`, `context_discovery`,
`dependency_query`, `development`, `documentation`,
`documentation_health`, `documentation_planning`, `engineering_review`,
`frontier`, `git_ops` (`create_branch`, `commit_changes`,
`create_pull_request`, `run_tests`), `impact_analysis`, `planning`,
`report_generation`, `repository_understanding`, `testing`, and
`context_pipeline/reasoning` (synthesis).

Stages already fully inventoried by the prior audit (Context Discovery,
Planning's verification-relevant fields, Development, Testing,
Report Generation, the synthesis Hypothesis/Contradiction/subject_entity
fields) are **summarized, not re-derived**, with a pointer back to that
audit and to ADR 0025. New ground for this document is: `blueprint`,
`engineering_review`'s non-`readiness_status` fields, `documentation`,
`documentation_planning`, `documentation_health`, `api_intelligence`,
`repository_understanding`, `impact_analysis`, `dependency_query`,
`frontier`, and the three write agents' own field-level trust (as
distinct from KAN-28's run-level authorization).

## 5. Inventory

### 5.1 Already audited (summary only — see the Phase 3 adversarial audit and ADR 0025 for detail)

| Field | Stage | Class | Disposition |
|---|---|---|---|
| `Hypothesis.description`, `.confidence`, `.status` | synthesis | A | Rendered as belief; `HypothesisStatus` closed to 3 literal values, never freeform. |
| `Hypothesis.subject_entity` | synthesis | B → D | LLM classifies claim type (B); once set, gates whether a deterministic repository match may run at all (D). Prompt-only gate — the one open non-determinism the Phase 3 audit already documented and explicitly declined to "fix" with a heuristic. |
| `VerificationFinding.message`, `.category` | Planning/Development/Testing | B (message) / C (blocking classification) | `message` is descriptive text, never re-parsed as fact by downstream code; `blocking` is computed from a closed `NON_BLOCKING_CATEGORIES` allowlist, not LLM-asserted. |
| `RepositoryUsage.verified` | Planning | C | The one real structured per-item deterministic signal in the codebase; set by `verify_claims`/`check_entity_mismatch`, never the LLM's self-report. |
| `capability_priority` / `next_investigation_candidates` | reasoning | D (bounded) | Bounded to closed `_KNOWN_CAPABILITIES` set; unrecognized labels silently dropped; confidence clamped `[0,1]`. |
| `Evidence.kind`/`.reference`/`.summary` | all agents | C | Code-authored at each real tool-call site, not raw LLM text; `kind` closed to 5 literals. |
| `readiness_status` | engineering_review | B → C (downgrade-only) | LLM-set, then deterministically downgraded (never upgraded) if `blocking_findings` exist and status was `"ready"`. |
| `executive_summary` | report_generation | A (explicitly labeled) | The one deliberately LLM-authored field in the whole `ReportViewModel`; structurally cannot touch any other field (`dataclasses.replace` on a frozen dataclass). |
| `repository` (target), file operations | code_generation | B → D, then C | `verify_repository()`/`validate_file_operations()` gate every write; fail-closed (`CodeGenerationRepositoryError`/`CodeGenerationValidationError`) before any file/git operation. |
| `confidence_score` | code_generation | (n/a — not LLM) | Always `calculate_confidence(...)`, a deterministic function; the LLM's self-reported confidence, if any, is discarded. |

### 5.2 New ground — write-execution chain, field-by-field

| Field | Agent | Class | What actually checks it | Blast radius if wrong |
|---|---|---|---|---|
| `branch_name` | `create_branch_agent.py` | **C — not LLM at all** | Fully deterministic: `f"graphforge/exec-{workflow_id_short}"`. Confirmed via source read, zero LLM involvement in this agent. | None — there is nothing here to be wrong. |
| `commit_message` | `commit_changes_agent.py` | **B, unverified content; D for its first line** | Read directly from `code_generation`'s LLM output (`code_result.get("commit_message", "")`). Only checked for non-emptiness (`if not commit_message: raise CommitChangesExecutionError`) — no deterministic re-check of its *content*. | Low. It's descriptive text attached to a commit whose *target repository and file diffs* are already independently verified by `code_generation`'s gates (§5.1). A wrong/misleading commit message does not change what code is written or where — it's metadata, not a target/scope decision. Visible externally (GitHub UI) but not itself an action. |
| `title`, `body` (PR) | `create_pull_request_agent.py` | **B, unverified content** | `title = commit_message.splitlines()[0]` (falls back to a fixed `_DEFAULT_TITLE`); `body = code_result.get("executive_summary") or commit_message or _DEFAULT_TITLE`. Both are LLM-authored text (`commit_message` from `code_generation`, `executive_summary` — note: this is `code_generation`'s own field of that name, a different value from `report_generation`'s `executive_summary`, despite the shared name) with no deterministic re-check of content before being posted to GitHub. | Low-to-moderate. Purely descriptive; does not influence `head`/`base`/diff, which come from already-verified upstream fields (see next row). It is, however, the one artifact in this entire chain that becomes **publicly visible on GitHub** verbatim, unlike internal report/UI text — a meaningfully different exposure even at equal factual stakes. |
| `repository`, `branch_name` used as PR `head`/`base` | `create_pull_request_agent.py` | **D, but re-derived from an already-verified value, not re-asserted by an LLM at this stage** | `repository` is read from `commit_changes`'s stored result, which in turn was `code_generation`'s already-`verify_repository()`-gated value (§5.1) — not a fresh LLM claim at this stage. `base_branch` comes from the tracked `Repository` row's `default_branch` (DB, not LLM). `repository` is additionally re-confirmed tracked here (`Repository.user_id == user_id, Repository.full_name == repository`) before any GitHub call — a second, independent existence check at this stage. | Low — this is the one place in the write chain where the target is checked twice by two different deterministic mechanisms (`verify_repository()` upstream, the tracked-repository lookup here) before use. |
| Idempotency key (`repository_id` + `head_ref`) | `create_pull_request_agent.py` | C | Both are the deterministic/DB-backed values above, not LLM output; used to look up an existing `PullRequest` row before creating a new one, and to recover from a GitHub-reported race. | None — this is a safety mechanism, not a trust surface. |

**Net assessment of the write chain's field-level trust**: every field
that determines **where** a write lands (repository, branch, base) is
either fully deterministic or independently re-verified at least once,
and the PR-creation stage adds a second independent repository check on
top of `code_generation`'s. Every field that is LLM-authored text
(`commit_message`, PR `title`/`body`) is **not** independently
fact-checked before being posted, but none of them are read back by any
downstream code as a target, a scope, or a decision input — they are
terminal, human-facing (and in the PR case, externally-visible)
descriptive text. This is the same "belief clearly presented as such"
pattern as `executive_summary` in Report V2, with one difference worth
flagging plainly: `executive_summary`/`commit_message` in this chain are
never labeled as LLM-generated to the human reading them on GitHub,
unlike the Report V2 UI's explicit badging. That labeling gap is
recorded as a finding in §6, not fixed here.

### 5.3 New ground — remaining agents not previously audited

| Agent | Field(s) | Class | Disposition |
|---|---|---|---|
| `blueprint` | plan narrative, step descriptions | A | Presented as a proposal for human approval (`POST /workflows/{id}/approve` is the actual gate — see §2 fact 1); the blueprint's *content* is never itself treated as fact by other code, only read by a human before they decide whether to approve. |
| `engineering_review` | `blocking_findings[]` content, `summary`, non-`readiness_status` fields | B | `readiness_status` has the deterministic downgrade override (§5.1); the *findings list itself* (what's wrong, why) is LLM-authored prose, presented to a human reviewer, not independently re-verified item-by-item. Same "labeled belief, human-in-the-loop" pattern as `blueprint`. |
| `documentation`, `documentation_planning`, `documentation_health` | generated doc content, health/staleness assessments | A/B | Documentation content is explicitly informational, never consumed as a control input by any other agent (grep-confirmed: no agent reads another agent's documentation output as a decision input). Health/staleness scores are Class B (presented with enough confidence to look factual) but drive no automated action — a human reads them. |
| `api_intelligence`, `repository_understanding` | endpoint/structure summaries, narrative descriptions | A/B | Consumed downstream mainly as *context* fed into later prompts (i.e. as input to another LLM call, not as a structured control value) — this is itself worth flagging: an LLM belief becoming another LLM's context is a different risk shape than becoming a structured field, and isn't fully covered by the A/B/C/D scheme as currently defined (see §6, finding 3). |
| `impact_analysis` | affected-repository/impact narrative | B | Descriptive; no confirmed code path where impact_analysis output gates a write or overrides a verification result. Not fully re-traced call-site-by-call-site in this pass — flagged as needing the same call-graph rigor `_authorization.py` applied to the write agents, not assumed safe by category alone (see §6, finding 4). |
| `dependency_query`, `frontier` | query results, dependency narrative | A/C mixed | Where these wrap a real graph traversal, the result is Class C (a real query result, not LLM invention); where they add narrative interpretation on top, that layer is Class A. Not separately re-traced field-by-field in this pass. |

## 6. Findings (inventory-derived; no fixes proposed here)

Severities follow the same P0–P3 scale used in the Phase 3 adversarial
audit. **No P0 or P1 findings were produced by this pass.** This is
stated plainly, not implied by omission — consistent with the standing
instruction not to manufacture findings to justify the audit.

- **P2 — Finding 1.** `commit_message` and the PR `title`/`body` are
  LLM-authored text posted to a real, external, publicly-visible system
  (GitHub) with no deterministic re-check of their content, and no
  visible "AI-generated" labeling at the point a human reads them on
  GitHub (unlike Report V2's explicit UI badging of `executive_summary`).
  The blast radius is low because these fields are never read back as a
  target/scope/decision input anywhere downstream — traced through both
  `commit_changes_agent.py` and `create_pull_request_agent.py`. Bounded,
  but real: an untrustworthy or misleading commit/PR message could
  reasonably mislead a human reviewer on GitHub who has no reason to
  suspect the text wasn't human-written.

- **P2 — Finding 2.** The A/B/C/D classification scheme, as used so far,
  implicitly assumes an LLM-generated field's consumer is either a human
  (needs A/B/C framing) or a deterministic check (needs a C-grade gate).
  §5.3 surfaced a third consumer shape not yet formally covered:
  **another LLM call**, where one agent's belief/claim becomes a later
  agent's *prompt context* rather than a structured field. This changes
  the failure mode (compounding uncertainty across a chain of LLM calls,
  rather than a single field being wrong) and isn't fully assessed by
  this pass — `api_intelligence`/`repository_understanding` were
  identified as likely instances but not individually call-graph-traced.

- **P3 — Finding 3.** `impact_analysis`, `dependency_query`, and
  `frontier` were classified by category/spot-check rather than the
  full grep-every-call-site rigor `_authorization.py` applied to the
  three write agents, or the row-by-row rigor ADR 0025's False Positive
  Matrix applied to `subject_entity`. No evidence of a problem was
  found, but "no evidence found because it wasn't fully traced" is
  weaker than "traced and confirmed safe" — recorded honestly as a gap
  in this pass's coverage, not a confirmed absence of risk.

- **P3 — Finding 4 (carried forward, not new).** The claim-type gate for
  `subject_entity` remains prompt-only, as already documented in ADR
  0025 and the Phase 3 adversarial audit. Not re-litigated here; included
  only so this document's finding list is a complete picture of open
  items across the whole chain, not just what's new in this pass.

## 7. Possible future directions (explicitly not proposed for adoption here)

This section exists only because the user's request distinguished
"audit inventory" from "proposing fixes," and asked that fixes come
after and be clearly separated. Nothing below is a recommendation to
implement; each is an option to weigh in a future, separate decision:

- **For Finding 1**: a possible future option is a fixed, non-LLM prefix
  or suffix on GitHub-visible PR bodies (e.g. *"This description was
  drafted by an automated agent."*) — cheap, no new verification logic,
  addresses the labeling gap specifically, without touching commit
  message content at all. An alternative, heavier option would be a
  deterministic re-check that the commit message's claimed files match
  the actual diff — but that duplicates work `code_generation`'s
  `validate_file_operations` already does upstream, and its value would
  need to be weighed against that redundancy.
- **For Finding 2**: before extending the A/B/C/D scheme with a formal
  "E. Compounding context" category, it would be worth first confirming
  (by tracing, not assuming) whether any LLM-to-LLM context chain in
  this codebase currently lacks a deterministic fact anchor anywhere in
  the chain — if every such chain terminates in a Class C check
  somewhere (the way Report V2's chain does), the risk may already be
  bounded by existing structure and not need a new category at all.
- **For Finding 3**: a follow-up, narrowly-scoped audit of
  `impact_analysis`/`dependency_query`/`frontier` specifically, using
  the same call-graph-grep discipline `_authorization.py` used — this
  would be a small, self-contained piece of future work, not a redesign.

No timeline, priority ranking, or implementation commitment is made for
any of the above in this document.

## 8. Explicit non-goals

- No code, prompt, schema, or UI change of any kind is made or proposed
  for adoption by this ADR.
- This document does not modify, supersede, or reopen ADR 0025 or its
  Phase 3 implementation.
- This document does not modify `_authorization.py` or dispute its
  conclusion — it is cited as settled, correct prior art (§2).
- §7 is explicitly not a plan. It exists to record options considered,
  not commitments made.

## 9. Recommendation

Accept this document as the audit-inventory record for the "Agent Trust
Boundary Audit." No P0/P1 action is required. The two P2 findings (§6)
are candidates for a future, separately-scoped Phase — whether and when
to act on them is a decision for that future turn, not this one.
