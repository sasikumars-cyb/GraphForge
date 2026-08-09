# ADR 0024: Report V2 Phase 2 — a visualization-first Reports experience

## Status

**Accepted and implemented — READY WITH CAVEAT.** Backend Phase 1
(reasoning persistence — the `reasoning_summary` projection) and Phase 2
(this ADR: the deterministic `ReportViewModel` and the visualization-first
Reports page) have both shipped and been verified end-to-end against real
completed workflows, real persisted data, and real rendered pages (see
§14 and the release hand-off for the full verification record).

**"Ready" is scoped to the states the current backend can actually
produce.** Every reachable state — clean populated reports, zero
hypotheses, synthesis `FAILED`, synthesis `NOT_RUN`, resolved/unresolved
contradictions, bounded long investigations, and the legacy HTML fallback
— is production-ready, verified against real data, and correctly
rendered. Two specific badge combinations are **not** currently reachable
in production, by intentional Phase 1 design, not a Phase 2 defect — see
§7's correction and the "Known Limitation / Future Work" section below
for the precise boundary. Nothing in this ADR or its implementation
implies those two combinations occur today; where earlier drafts of this
document implied otherwise, they have been corrected in place (§7).

## 1. Current report architecture

Traced end-to-end against a real approved workflow
(`4e64b47a-5f4a-43bd-a860-a5f1e6c3fd98`), not assumed from the code alone.

```
approve_workflow()                                    (workflows.py:1487)
  → schedule_run_execution(goal="generate_report")
  → ReportGenerationAgent.run()                        (report_generation/agent.py)
      reads get_stage_result() for every completed stage
      → _STAGE_FORMATTERS[stage](result)                (stage_context.py)
          → flat prose strings, one per stage
      → one LLM call: "synthesize one self-contained HTML report"
      → { title, html }
  → report_finalizer on_complete callback
      → workflow_reports.html_content = html            (verbatim)
Reports page  → GET /reports/{id} → <iframe sandbox="" srcDoc={html} />
```

Two facts fully determine everything else in this ADR:

- **The LLM decides both content *and* presentation** in one pass — there is
  no intermediate structured representation between "stage results" and
  "final HTML string." Whatever the LLM chooses to mention, in whatever
  shape it chooses, is what ships.
- **`format_repository_relationships_block`** is the only formatter that
  reads `context_discovery`'s result, and it extracts only
  `explicit_repositories`/`suggested_repositories`/`selected_repositories`.
  Neither `engineering_understanding` nor the new `reasoning_summary` is
  ever read. The report LLM call has never seen a hypothesis or a
  contradiction, structured or as prose.
- **The frontend is a pass-through.** `ReportHtmlViewer` in
  [ReportsPage.tsx](../../frontend/src/pages/ReportsPage.tsx) fetches
  `html_content` and drops it into a sandboxed iframe. There is no
  component, no chart, no badge anywhere in the current Reports page that
  isn't inside that one HTML string.

Verified live: a real captured report (see the Phase-1-verification chat
transcript) for a workflow with 5 real persisted hypotheses and 1
contradiction contains zero occurrences of "hypothesis", "contradiction",
"SUPPORTED", "CONTRADICTED", or "VERIFIED" anywhere in its HTML.

## 2. Problems found

1. **Structured reasoning is invisible.** Confirmed above — not a rendering
   bug, an absence.
2. **No SUPPORTED vs VERIFIED distinction anywhere.** The current report
   states things with uniform prose authority; a reader has no way to tell
   "the reasoning engine believes this" from "a deterministic check
   confirmed this."
3. **No confidence visualization.** `ConfidenceJourney`/`confidence_breakdown[]`
   already exist in the backend (Phase 1) and are unused.
4. **No investigation timeline.** `TimelineEntry`/`build_discovery_report`'s
   real, bounded timeline data already exists and is unused.
5. **Density without hierarchy.** The whole document is `h2` + paragraphs +
   bullet lists at one visual weight — nothing is scannable in isolation;
   everything requires reading.
6. **The LLM is a single point of failure for structure.** Even if the
   prompt were extended to mention hypotheses, there is no guarantee of
   *how* — count, order, or omission on a bad day are all silently
   possible, because nothing downstream enforces a shape.
7. **A real gap in degraded-state signal** (found while designing this ADR,
   not previously known): `build_reasoning_summary` currently returns `{}`
   whenever a workspace has zero hypotheses *and* zero contradictions —
   which conflates three different situations: synthesis never ran (old
   data), synthesis failed (`ContextDiscoverySynthesisError`, caught and
   degraded), and synthesis succeeded but genuinely found nothing worth
   hypothesizing about. All three currently look identical downstream.
   Addressed in §11.

## 3. Target information architecture

One page, one scroll, in the order the user's eye should move — this *is*
the requested visual hierarchy, confirmed against what the backend can
actually back honestly (no stage invents a step to fill a gap):

```
Investigation Question         → the original request, verbatim
Readiness + Confidence         → one glance: ready/not, how sure, journey
Investigation Timeline         → bounded, what GraphForge actually did
What We Know / Don't Know      → findings vs open gaps, side by side
Hypotheses                     → competing explanations, each two-axis
Contradictions                 → prominent, claim → for/against → resolved?
Evidence & Provenance          → category counts + the prose-not-IDs caveat
What's Next                    → remediation steps / open questions
```

"What We Know / Don't Know" is inserted before Hypotheses (not in the
prompt's literal order) because it's the section a reader needs *before*
hypotheses make sense — hypotheses are explanations of what's still
ambiguous, and a reader who hasn't seen "what's known" yet has no frame for
"what's being debated." Evidence & Provenance moves after Contradictions,
not deep in an appendix, because "why should I believe any of this" is a
first-screen question per GraphForge's own principle, matching where
`InvestigationSummary.tsx`'s live Context Explorer already puts its
"Why GraphForge believes this" section.

## 4. Proposed visual hierarchy (component-level)

Reusing the existing design system throughout — no new visual language:

| Section | Primitive reused | New component |
|---|---|---|
| Question + readiness header | `Card`, `StatusBadge` | `ReportHeader` |
| Confidence journey | `LineChart` (`charts/SimpleCharts.tsx`) | `ConfidenceJourneyCard` |
| Timeline | `Card` list pattern (from `AgentActivityFeed`) | `InvestigationTimeline` |
| Known / Unknown | `EngineeringUnderstandingPanel`'s `SectionHeading`/`BulletList` | `KnowledgeSplitPanel` |
| Hypotheses | `StatusBadge` (tone system), `RiskBadge`'s dot+color+word pattern | `HypothesisCard` |
| Contradictions | `EvidencePanel`'s expandable-item pattern | `ContradictionCard` |
| Evidence & provenance | `EvidencePanel` (as-is, already handles this well) | none — reused directly |
| What's next | `EmptyState`-style action list | `NextActionsList` |
| Degraded/empty sections | `EmptyState` (already distinguishes "why" from "nothing") | reused directly |

No charting library added — `SimpleCharts.tsx`'s dependency-free SVG
primitives already cover a line/journey chart and horizontal bars, which is
everything this needs.

## 5. View-model schema

New file `backend/app/agents/report_generation/view_model.py`, built
**on top of** the existing Phase 1 `contracts.py`/`data_plumbing.py` —
nothing in Phase 1 changes shape; this is a second, thin assembly layer.

```python
@dataclass(frozen=True)
class ReportViewModel:
    header: HeaderVM
    confidence: ConfidenceSectionVM
    timeline: TimelineSectionVM
    knowledge: KnowledgeSectionVM
    hypotheses: HypothesesSectionVM
    contradictions: ContradictionsSectionVM
    evidence: EvidenceSectionVM
    next_actions: NextActionsSectionVM
    # executive_summary is the ONE LLM-authored field in the whole model —
    # everything else here is deterministic. See §13.
    executive_summary: str | None

@dataclass(frozen=True)
class HeaderVM:
    question: str                    # original_request, verbatim
    workflow_title: str
    repository: str | None
    readiness: Readiness
    generated_at: str

@dataclass(frozen=True)
class ConfidencePointVM:
    stage: str
    label: str
    confidence: float | None
    dropped: bool

@dataclass(frozen=True)
class ConfidenceSectionVM:
    availability: SectionAvailability
    current: float | None
    points: list[ConfidencePointVM]
    summary_sentence: str

@dataclass(frozen=True)
class TimelineStepVM:
    iteration: int
    provider: str
    action: str
    outcome: str
    summary: str          # already bounded prose, not raw evidence
    why_it_mattered: str  # == TimelineEntry.intent, relabeled for the reader

@dataclass(frozen=True)
class TimelineSectionVM:
    availability: SectionAvailability
    steps: list[TimelineStepVM]     # capped — see §12
    truncated_count: int            # 0 when nothing was cut

@dataclass(frozen=True)
class KnowledgeSectionVM:
    availability: SectionAvailability
    known: list[str]        # verified findings, short statements
    unknown: list[str]      # open KnowledgeGap summaries

@dataclass(frozen=True)
class HypothesisVM:
    statement: str
    synthesis_status: SynthesisStatus
    confidence: float
    verification_status: VerificationStatus   # NOT_CHECKED when uncorrelated — see §7
    supporting_count: int
    contradicting_count: int
    supporting_evidence: list[str]   # prose, capped — see §8
    contradicting_evidence: list[str]

@dataclass(frozen=True)
class HypothesesSectionVM:
    availability: SectionAvailability
    synthesis_state: SynthesisRunState   # see §11 — replaces the earlier `degraded: bool`
    items: list[HypothesisVM]

@dataclass(frozen=True)
class ContradictionVM:
    statement: str
    evidence_for: list[str]
    evidence_against: list[str]
    resolved: bool
    resolution_note: str

@dataclass(frozen=True)
class ContradictionsSectionVM:
    availability: SectionAvailability
    synthesis_state: SynthesisRunState
    items: list[ContradictionVM]

@dataclass(frozen=True)
class EvidenceSectionVM:
    availability: SectionAvailability
    categories: list[EvidenceCategoryCount]   # reused as-is from contracts.py
    total: int
    provenance_note: str    # fixed honest caption, see §8

@dataclass(frozen=True)
class NextActionsSectionVM:
    availability: SectionAvailability
    remediation_steps: list[str]
    open_questions: list[OpenQuestionEntry]   # reused as-is
```

## 6. Mapping rules

`build_report_view_model(bundles: dict[str, StageStepData | None], workflow: Workflow) -> ReportViewModel`
in `view_model.py` — a pure function, calling **only** the existing Phase 1
`map_*` functions in `data_plumbing.py`, never re-deriving a value they
already compute:

- `header` ← `bundles["context_discovery"].result["original_request"]` +
  `map_readiness(bundles["engineering_review"])`.
- `confidence` ← `map_confidence_journey(bundles)` (Phase 1, unchanged) +
  `ConfidencePointVM` is a 1:1 relabel of `ConfidenceStagePoint`.
- `timeline` ← `map_investigation_timeline(bundles["context_discovery"])`
  (Phase 1, unchanged), then capped per §12.
- `knowledge` ← `discovery_report.findings[]` where `verified=True` for
  "known"; `map` over `discovery_report.gaps[]` for "unknown" — both
  already real, persisted, bounded lists; no new backend function needed,
  a thin dict-read in `view_model.py` itself (these two lists were never
  wrapped in a `map_*` function in Phase 1 because Phase 1 only scoped the
  hypothesis/ledger/timeline gap explicitly).
- `hypotheses` ← `map_hypotheses(bundles["context_discovery"])`
  (Phase 1, unchanged) for the list; `verification_status` per hypothesis
  is looked up from `map_knowledge_ledger_rows`'s own hypothesis rows by
  `source_field` match. **Correction, added after a post-implementation
  QA pass found this claim overstated:** the lookup mechanism itself is
  real and correct, but `map_knowledge_ledger_rows` (Phase 1, unchanged)
  hardcodes `verification_status=None` on every hypothesis-sourced row it
  builds — stated explicitly in that function's own docstring ("a
  hypothesis is reasoning, never a code-run check"), a deliberate Phase 1
  design boundary, not a Phase 2 oversight. The practical effect: **every
  real hypothesis renders `NOT_CHECKED` today, always** — `SUPPORTED
  +VERIFIED` and `SUPPORTED+UNVERIFIED` cannot occur on a real hypothesis
  card until a future phase deliberately adds a correlation from a
  hypothesis to an independent verification check (there is currently no
  such correlation anywhere in the codebase, text-based or otherwise —
  proven in `test_report_view_model.py::
  TestRealPipelineNeverCorrelatesHypothesisVerification`, which shows
  even a hypothesis and a `verification_findings[]` entry with byte-
  identical claim text still land on two separate ledger rows). This ADR
  does not propose that correlation — it is out of scope for Phase 2, and
  is not being added as a side effect of this correction.
- `contradictions` ← `map_contradictions(bundles["context_discovery"])`
  (Phase 1, unchanged).
- `evidence` ← `map_evidence_summary(bundles["context_discovery"].evidence)`
  (Phase 1, unchanged).
- `next_actions` ← `blocking_reasons`/`remediation_steps` off the
  Engineering Review bundle + `map_open_questions` (Phase 1, unchanged).

**No new LLM call.** Every field above is a read of already-persisted,
already-mapped data. The one exception is `executive_summary` — see §13.

## 7. SUPPORTED vs VERIFIED semantics

Three separate concepts, never collapsed into one score or one badge, and
never sharing a visual channel (color ramp) with each other:

| Concept | Question it answers | Source | Rendered as |
|---|---|---|---|
| **Confidence** | *How strongly does the reasoning system believe this?* | `Hypothesis.confidence` (float, 0–1) | a bar + number, never color |
| **Synthesis status** (per hypothesis) | *What did the reasoning conclude about this specific claim?* | `Hypothesis.status` → `SynthesisStatus` | a badge, blue/green/red/gray family |
| **Verification status** | *Did a deterministic code check confirm this?* | Knowledge Ledger correlation → `VerificationStatus` | a second, adjacent badge, amber/green/gray family |

A fourth, section-scoped concept is easy to conflate with the second but is
not the same thing: **`SynthesisRunState`** (§11) answers *did reasoning
execution itself succeed*, at the level of the whole Hypotheses/
Contradictions section — not a per-claim belief. `SynthesisStatus` only
exists (and only means anything) once `SynthesisRunState` is `COMPLETED` or
`COMPLETED_EMPTY`; it is never rendered for `NOT_RUN`/`FAILED`, which is
precisely why §11 needs its own state instead of being folded into
`SynthesisStatus`'s existing `UNKNOWN` value — `UNKNOWN` is a belief a
completed synthesis reached about one claim, not a statement that no
belief was ever formed.

Rendered as two adjacent, independently-colored badges, never merged:

```
┌─────────────────┐  ┌─────────────────┐
│ Synthesis        │  │ Verification     │
│ ● SUPPORTED       │  │ ○ NOT CHECKED    │
└─────────────────┘  └─────────────────┘
```

- `SynthesisStatus` → `StatusBadge` tones: `SUPPORTED→success`,
  `INFERRED→info`, `CONTRADICTED→danger`, `UNKNOWN→neutral`.
- `VerificationStatus` → `VERIFIED→success`, `UNVERIFIED→warning`,
  `NOT_CHECKED→neutral`.
- Both badges use `RiskBadge`'s dot-before-word pattern (never color alone).
- **`INFERRED` is a UI/contract concept only — not a reachable backend
  state.** It exists in the `SynthesisStatus` enum so the badge and its
  legend are complete and self-documenting, and so a future reasoning-
  engine change has somewhere to land without a contract change. But the
  real, persisted `HypothesisStatus` a synthesis call can ever produce is
  `Literal["supported", "rejected", "unknown"]` — three values, no
  fourth. `map_synthesis_status` mirrors that exactly: it is a straight
  3-way dict with no `INFERRED` output on any input. **`INFERRED` does
  not, and as of this ADR must not, appear on any real hypothesis card in
  production.** No workaround, heuristic, or confidence threshold was
  added to manufacture it — see the "Known Limitation / Future Work"
  section below for what would need to change upstream first.
- A hypothesis's `verification_status` is `NOT_CHECKED` whenever the
  Knowledge Ledger has no correlated verification row for it — which is
  the honest, correct reading of "no code check exists for this specific
  claim," not a placeholder. **In production today this is every
  hypothesis, always** — see the "Known Limitation / Future Work" section
  below for exactly why, and do not read the correlation code described
  in §6 as evidence that `VERIFIED`/`UNVERIFIED` currently reach a real
  hypothesis card, because they don't.

## 8. Hypothesis visualization

`HypothesisCard`:

```
┌──────────────────────────────────────────────────────────┐
│ "The change bumps a timeout in the agent-runtime..."       │
│                                                              │
│ Synthesis: ● SUPPORTED   Confidence: ▓▓▓▓▓▓▓░░░ 70%         │
│ Verification: ○ NOT CHECKED                                 │
│                                                              │
│ ▸ Supporting (2)          ▸ Contradicting (1)                │
└──────────────────────────────────────────────────────────┘
```

- Confidence renders as a small horizontal bar (reusing
  `HorizontalBarChart`'s track/fill pattern at card scale, not the full
  chart component) plus the number — never the number alone, per the
  user's "prefer a visual" instruction, but the number stays too since a
  bar alone can't be read precisely.
- Supporting/contradicting evidence are **collapsed by default**, count-only
  (`EvidencePanel`'s own collapsed-state pattern), expanding to the raw
  prose lines. The expanded view carries a fixed caption: *"These are the
  reasoning engine's own notes, not linked evidence records — see
  Evidence & Provenance below for what was actually verified."* This is
  the honest treatment for prose-not-IDs the user asked for: never a
  clickable link, never a graph edge, always visually distinct from the
  real `EvidencePanel` (different icon set, no kind badges, explicit
  caption).
- Cards are sorted by confidence, descending — the strongest hypothesis is
  always first, answering "why does it believe the strongest hypothesis"
  by position, not just content.
- **0 hypotheses (real, not degraded):** `EmptyState`-style card:
  *"Investigation converged without competing hypotheses"* / *"GraphForge's
  reasoning found no distinct alternative explanations to weigh — see
  Timeline for what it examined."* Positive framing — this is a normal,
  even good, outcome, not a failure.
- **1 hypothesis:** renders as a single, full-width card — no grid
  awkwardness for n=1.
- **Many hypotheses:** grid of cards, capped at 6 visible with a "+N more,
  lower confidence" disclosure (never hidden entirely, per §12's scale rule).

## 9. Contradiction visualization

`ContradictionCard`, exactly the shape the user specified:

```
┌──────────────────────────────────────────────────────────┐
│ "The ticket title implies a concrete timeout exists..."     │
│                                                              │
│  Supporting evidence     │   Contradicting evidence          │
│  · Title contains         │   · No timeout config found      │
│    'timeout-bump'          │     in retrieved evidence         │
│                                                              │
│                    ○ Unresolved                              │
└──────────────────────────────────────────────────────────┘
```

- Two-column layout (stacks to one column below a width breakpoint,
  consistent with the rest of the app's responsive pattern).
- `resolved: true` → `success` `StatusBadge` ("Resolved") +
  `resolution_note` shown inline; `resolved: false` → `warning` tone
  ("Unresolved"), no note shown (there rarely is one for an open item).
- Contradictions get a **visually heavier card border** than hypotheses
  (`border-warning-line` vs `border-line-muted`) — this is the "make
  contradictions visually prominent" instruction; the color signals
  "pay attention here" without needing a bigger font.
- **0 contradictions:** same `EmptyState` honesty pattern as hypotheses —
  *"No contradictions found"* only when synthesis genuinely ran; see §11
  for the degraded case, which must never render this copy.

## 10. Timeline visualization

`InvestigationTimeline` — a vertical stepper, not a raw log:

```
① graph        looked up indexed repositories        17 found        →  narrowed to 2 candidates
② confluence    searched for the linked ticket        1 match          →  confirmed business goal
③ graph         traversed RateLimiter's callers        6 found        →  scoped the blast radius
   … 4 more steps (lower-signal retrievals) — Show all
```

- One row per `TimelineEntry` (already a bounded, curated timeline per
  Phase 1's `build_discovery_report` — not raw evidence, not graph nodes;
  confirmed by re-reading that function during this ADR).
- Each row: iteration number, provider icon (reuses `EvidencePanel`'s
  `KIND_CONFIG` icon set for consistency), the action taken, the outcome
  in one line, and `why_it_mattered` (`TimelineEntry.intent`) as a small
  trailing arrow-note — this directly answers the user's "why it mattered"
  column.
- Capped at **8 visible rows**, sorted by iteration; remaining collapse
  into a single "+N more steps" disclosure row (never individually
  hidden-but-present in the DOM — collapsed means not rendered until
  expanded, so a 40-iteration investigation doesn't ship 40 DOM nodes to
  every page load).

## 11. Degraded-state behavior

**Revised from the original proposal** after review: a single `degraded:
bool` cannot carry three states, and the review explicitly rejected
overloading it to try. This section defines the smallest deterministic
representation that can — a 4-value state, not a bool — and exactly how
each backend signal produces it, so there is no ambiguity left for
implementation.

### The four states

```python
class SynthesisRunState(StrEnum):
    NOT_RUN = "not_run"                  # (A) synthesis never executed
    FAILED = "failed"                    # (B) synthesis executed but errored
    COMPLETED_EMPTY = "completed_empty"  # (C) executed, succeeded, found nothing
    COMPLETED = "completed"              # executed, succeeded, produced items
```

`COMPLETED` is a fourth, additional state beyond the three the user named
— it is required so the "found nothing" copy (C) is never shown for a run
that actually *did* produce hypotheses (the common case). Sections A–C are
exactly the three named in the review; `COMPLETED` is the implicit fourth
that makes the enum exhaustive rather than leaving a "successful and
non-empty" case unnamed.

### Where each state comes from — no new LLM call

`synthesize_engineering_understanding` (`understanding.py`) already has
everything needed to compute this, at three existing sites, none of them
new:

1. **The zero-evidence early return** (nothing was ever gathered to reason
   over — no LLM call is even attempted here today). This is case **(A)
   NOT_RUN** — reasoning was not applicable, not attempted, not failed.
2. **The `except Exception` fallback** around the synthesis LLM call
   (already sets a local `degraded = True` today). This is case
   **(B) FAILED** — synthesis executed and errored.
3. **The clean-completion path** (`degraded = False` today). Whether this
   is **(C) COMPLETED_EMPTY** or **COMPLETED** depends only on whether
   `workspace.hypotheses`/`workspace.contradictions` ended up non-empty —
   already known at this point, no new computation.

**Proposed addition** — one line at each of the three sites above, all in
the same function, same persistence boundary as the original upstream fix:

```python
# site 1 (zero-evidence return):
state.derived["investigation_workspace_run_state"] = "not_run"
# site 2 (except branch):
state.derived["investigation_workspace_run_state"] = "failed"
# site 3 (clean completion, after workspace is built):
state.derived["investigation_workspace_run_state"] = "completed"  # refined below
```

`build_reasoning_summary` (`projection.py`) is the single place that turns
this raw string plus the workspace's own list lengths into the final,
exhaustive `SynthesisRunState`:

```python
def _resolve_run_state(raw: str | None, workspace: InvestigationWorkspace) -> str:
    if raw == "not_run":
        return SynthesisRunState.NOT_RUN
    if raw == "failed":
        return SynthesisRunState.FAILED
    if not workspace.hypotheses and not workspace.contradictions:
        return SynthesisRunState.COMPLETED_EMPTY
    return SynthesisRunState.COMPLETED
```

`raw is None` (a persisted result from before this addition shipped, or
any future call site that forgets to set it) resolves the same way as
`"not_run"` — the safe, honest default when the signal simply isn't there
is "we don't know that reasoning ran," never a guess at COMPLETED.

The persisted `reasoning_summary` dict gains exactly one new key:
`"synthesis_state": <one of the four string values>`. `degraded: bool` as
originally proposed is **dropped** — `synthesis_state` supersedes it
entirely, so there is exactly one field carrying this signal, not two
that could drift apart.

### Mapping to `Availability` and copy

| `SynthesisRunState` | `SectionAvailability.status` | Copy shown |
|---|---|---|
| `NOT_RUN` | `UNAVAILABLE` | *"Reasoning synthesis was not recorded for this investigation."* |
| `FAILED` | `DEGRADED` | *"Reasoning synthesis failed for this investigation — falling back to evidence-only findings. This is not the same as 'no hypotheses found.'"* |
| `COMPLETED_EMPTY` | `AVAILABLE` (with an empty `items` list) | *"Investigation converged without competing hypotheses."* (positive framing, §8) |
| `COMPLETED` | `AVAILABLE` (with real `items`) | the real hypothesis/contradiction cards |

`FAILED` maps to `DEGRADED`, not `UNAVAILABLE` — something genuinely
happened (a real technical event worth surfacing distinctly), which is
exactly what `Availability.DEGRADED` already exists for; `NOT_RUN` maps to
`UNAVAILABLE` because there is nothing to report on at all, degraded or
otherwise. `COMPLETED_EMPTY` and `COMPLETED` are both `AVAILABLE` — the
distinction between them lives in `items` being empty vs. populated, not
in availability, because an empty-but-genuinely-investigated result is not
an availability problem.

`HypothesesSectionVM`/`ContradictionsSectionVM` (§5) both carry
`synthesis_state: SynthesisRunState` directly (not re-derived from
`availability` + `len(items) == 0` at render time) — the template
switches on it explicitly, so there is exactly one place per section that
decides which of the four copy variants to show, and it can never
mismatch `SectionAvailability`'s own value because both come from the
same `map_*` function call.

Each state is its own `EmptyState`-style card — never the same generic
"No hypotheses." string standing in for more than one of them. This is
implemented once, shared by both `HypothesesSectionVM` and
`ContradictionsSectionVM`'s rendering, not duplicated per section.

## 12. Scale / performance rules

- **Never render the repository graph.** Nothing in this design reads
  `graph_components`/`evidence_package` (the 1,500-node case) — every
  section sources from the already-bounded `discovery_report`/
  `reasoning_summary`/Knowledge Ledger, which are curated summaries by
  construction (Phase 1's own design principle, reused, not re-derived).
- Timeline: cap 8 visible rows + collapsed overflow (§10).
- Hypotheses: cap 6 visible cards + collapsed overflow (§8).
- Contradictions: no hard cap observed in practice (real investigations
  produce few), but the same collapse pattern applies at >6 for safety.
- Evidence & provenance: category **counts** only (`EvidenceCategoryCount`,
  already Phase 1), never a per-item list on this page — the full,
  itemized trail already exists at the workflow's own Context Explorer
  (`EvidencePanel`) and is not duplicated here.
- All caps are view-model constants (`_MAX_TIMELINE_ROWS = 8`,
  `_MAX_HYPOTHESIS_CARDS = 6`), not hardcoded in JSX, so they're one place
  to tune and one place to test.

## 13. What remains LLM-generated vs deterministic

**Deterministic (everything in §5's schema except one field):** readiness,
confidence, timeline, hypotheses, contradictions, evidence counts, next
actions. All sourced from already-persisted structured data via pure
mapping functions. Zero new LLM calls.

**LLM-generated: `executive_summary` only** — a single short paragraph,
generated from the *already-built* `ReportViewModel` (not from raw stage
context, unlike today's agent), so the LLM is prompted with structured,
already-decided facts and asked only to narrate them in 2-3 sentences. It
cannot invent a hypothesis, a status, or a confidence value that isn't
already in the view model it's summarizing, because it never sees anything
else. This is the architectural rule the user stated directly: **the LLM
narrates, it does not decide whether/how structured reasoning appears.**
On LLM failure, `executive_summary` is simply `None` and the section is
omitted — the rest of the report is unaffected, since nothing else depends
on it.

`ReportGenerationAgent` keeps existing (still the dispatch target on
approval) but its body changes: build the view model first (deterministic,
cannot fail on an LLM outage), then attempt the one summary call, then
render. Today's `_STAGE_FORMATTERS`/whole-document HTML synthesis is
removed, not extended.

## 14. Testing strategy

Mirrors Phase 1's own discipline:

1. **`view_model.py` unit tests** — same `SimpleNamespace`/`StageStepData`
   fixture pattern as `test_report_data_plumbing.py`, one test class per
   `*SectionVM` builder, covering: 0/1/many hypotheses, 0/many
   contradictions, all three degraded-state branches (§11), a hypothesis
   correlated to a real ledger verification row vs. one that isn't.
2. **A real-workflow regression test**, same pattern as
   `test_reasoning_summary_real_workflow_regression.py` — the same
   captured fixture, run through `build_report_view_model`, asserting the
   view model's `hypotheses.items` has the right count/order/badges.
3. **Frontend component tests** (Vitest + RTL, existing convention —
   every component above has a `.test.tsx` sibling in this codebase) —
   one per new component, covering the same 0/1/many/degraded matrix at
   the rendering layer, not just the data layer.
4. **Real browser QA** (post-implementation, per the user's explicit QA
   requirement) — the 10-scenario matrix given in the request, each
   checked both at the persisted-data layer and the rendered-page layer.

## 15. What is explicitly NOT being built

- No new reasoning engine, no new LLM call beyond the one-paragraph
  executive summary (§13).
- No evidence→hypothesis graph edges — prose stays prose, visually
  distinct and captioned as such (§8).
- No change to Phase 1's `contracts.py`/`data_plumbing.py` shapes — `view_model.py`
  is purely additive, built on top.
- No rendering of the full repository/architecture graph inside a report.
- No per-item evidence list on the report page (that's Context Explorer's
  job, already built, not duplicated).
- No editing/annotation UI on the report itself — this ADR is read-only
  presentation.
- No PDF/export format — out of scope, HTML page only, same as today.
- No change to how/when a report is generated (still triggered by
  Engineering Review approval, same dispatch mechanism).
- No synthetic verification states, fabricated evidence↔hypothesis
  relationships, forced `INFERRED` states, or LLM-generated verification
  claims anywhere in the shipped implementation — confirmed in the
  release hand-off's verification record, not just asserted here.

## 16. Known Limitation / Future Work: Hypothesis ↔ deterministic verification correlation

Found and precisely traced during Phase 2's final QA pass (not before) —
recorded here as the authoritative statement of the gap, superseding any
earlier, looser wording in §6/§7 above.

**What exists today.** Two independent, correct signals:
- **Synthesis status** — `Hypothesis.status`, set once by the reasoning
  engine's synthesis call, projected verbatim through `reasoning_summary`
  and `map_hypotheses`. Real, populated, correct.
- **Verification status** — a real, independent axis of the Knowledge
  Ledger (`map_knowledge_ledger_rows`), populated from Planning's
  `repository_usage[]` and every stage's `verification_findings[]`. Real,
  populated, correct — for the claims those checks actually ran against.

Both axes are genuinely two-dimensional and neither is faked. The gap is
specifically that **they are never the same row.**

**What is missing.** `map_knowledge_ledger_rows` builds one `LedgerRow`
per hypothesis with `verification_status` hardcoded to `None`, and one
`LedgerRow` per verification check with `synthesis_status` hardcoded to
`None` — by explicit, documented Phase 1 design ("a hypothesis is
reasoning, never a code-run check"). No code path anywhere connects a
specific hypothesis to a specific verification check, even when they
plainly describe the same real-world claim — proven directly in
`test_report_view_model.py::TestRealPipelineNeverCorrelatesHypothesisVerification::
test_even_with_matching_verification_findings_text_no_correlation_occurs`,
where a hypothesis and a `verification_findings[]` entry with
byte-identical claim text still land on two uncorrelated rows. The
practical effect, confirmed against real persisted data
(`workflow_reports.view_model` for a live workflow with real hypotheses):
**every hypothesis's `verification_status` is `NOT_CHECKED` in production
today, with no exception.**

**Why Phase 2 does not implement it.** Three reasons, each sufficient on
its own:
1. It is out of scope for "turn already-persisted data into a
   visualization" — correlating two claims that don't share a stable
   identifier is a modeling decision, not a rendering one.
2. There is no stable identifier to correlate on. A hypothesis's
   `statement` is free-text prose (see the Phase 1 decision report's
   provenance findings); a `verification_findings[]` entry's `message` is
   also free-text prose from a different stage's own checker. Matching
   them by any means available today (text similarity, keyword overlap)
   would be exactly the kind of inference-presented-as-fact this whole
   initiative was built to eliminate — a fabricated evidence↔hypothesis
   relationship in different clothing.
3. The user explicitly instructed, during QA, not to build this
   correlation as a side effect of a QA/documentation pass — it needs its
   own deliberate design.

**What a future phase would need to decide before implementing it.**
- Whether hypotheses and verification checks should ever share a stable
  identifier at all — e.g. would Planning/Development/Testing's
  verification steps need to be told *which hypothesis* they're
  checking, turning correlation into something recorded at check time
  rather than inferred after the fact?
- If inferred after the fact is still the direction: what confidence
  threshold or matching method is honest enough to surface (and how its
  own uncertainty gets shown — a "possibly the same claim" state would be
  a fifth, new concept, not a confident `VERIFIED`/`UNVERIFIED`).
- Whether this is a reasoning-engine change (the synthesis call itself
  proposing a link, with its own citation) or a deterministic-code change
  (checked at verification time) — the two have very different honesty
  guarantees and belong in different layers of this codebase.
- Whether `INFERRED` should be given a real source at the same time (a
  separate decision — nothing about solving the verification-correlation
  gap requires also inventing an `INFERRED`-producing code path, and this
  ADR takes no position on whether it ever should).
