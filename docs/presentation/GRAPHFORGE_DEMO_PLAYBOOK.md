# GraphForge Demo Playbook — Judge Panel

This is a **copy/paste-and-go** guide, not a technical explainer. Every
request below was typed into the *running* application against the
**real, connected Jira instance** (`cybage-team-n8wdf7c7.atlassian.net`)
and the **real indexed repositories**, and the response was read back
before being written into this document. Nothing here is invented.

Complements `06_DEMO_GUIDE.md` (which covers the PR-review/blast-radius
demo on the microservices repos) — this playbook covers **Ask
GraphForge, Impact Analysis, Migration Assistant, and Refinement
Planner**, anchored on one real, connected story: Jira issue **NPT-30**
against the real `etl-core` repository.

---

## Where this data comes from

- **Jira connection**: real, healthy, REST-connected to
  `cybage-team-n8wdf7c7.atlassian.net` (project **NPT** — NovaPipeline
  Technologies, a Spark/Databricks/Delta Lake data platform).
- **Indexed repositories** used below: `etl-core`, `ingestion-framework`,
  `streaming-pipeline`, `warehouse-jobs` (all `sasikumars-cyb/*`,
  Python/PySpark, genuinely indexed with real dependency and call-graph
  data).
- **Excluded on purpose**: the **KAN** Jira project is GraphForge's own
  internal backlog (it contains entries like "Jira/Confluence/GitHub
  Entry Resolvers... do not exist") — do not reference it live, it will
  contradict the demo. **SAM1** is a thin example project with no
  indexed repository behind it — skip it too.

---

## Demo cheat sheet

| # | Capability | Exact user request | What I should demonstrate |
|---|---|---|---|
| 1 | Ask GraphForge | `What would be affected if we change the SCD Type 2 merge logic in etl-core?` | Engineering intelligence — real repos, real relationship count |
| 2 | Impact Analysis | *Select repository:* `sasikumars-cyb/ingestion-framework` | Dependency-aware blast radius (visual graph) |
| 3 | Migration Assistant | `Move our Spark jobs to Databricks. What will be affected and what should we plan for?` | Migration impact across 4 real repos + risk grading |
| 4 | Refinement Planner ⭐ | `Refine NPT-30 and prepare it for sprint refinement. Identify the epic, stories, tasks, spikes, dependencies, open questions, and testing work we should discuss.` | Real Jira issue → Epic/Stories/Spike, grounded in real code |
| 5 | Visual ticket dependencies | `Show me the dependencies between the proposed work items.` | Dependency graph, critical path ending at NPT-30 |
| 6 | Conversational refinement | `What happens if the tie-break spike takes longer than expected?` | Multi-turn reasoning, no repeated IDs |
| 7 | Planning workflow | *Click* **"Create planning workflow"** (action button under the NPT-30 plan) | Investigation → implementation planning handoff |
| 8 | Testing | `What should we test?` | Context-aware validation, not a generic checklist |

---

## Scenario 1 — Ask GraphForge

### Capability
Ask GraphForge (general engineering intelligence, `/` home page)

### What I type
```text
What would be affected if we change the SCD Type 2 merge logic in etl-core?
```

### Why this is a good demo
`etl-core` is a real, indexed Python repository containing a real
`SCDType2Merger` class. This isn't a canned question — it's the exact
kind of thing a Delivery Manager asks before approving a risky change,
and GraphForge answers it from the actual dependency graph, not a
guess.

### Expected GraphForge journey
```text
User question
    ↓
GraphForge resolves "etl-core" against real indexed repositories
    ↓
Traces real DEPENDS_ON / CONTAINS / IMPORTS relationships (2-hop)
    ↓
Computes blast radius deterministically
    ↓
Produces answer: 2 repositories affected, MEDIUM impact, ~300 relationships traced
    ↓
User asks a follow-up
    ↓
GraphForge refines using the same grounded context
```

### What I should show the judges
* Two **real repository names** in the answer: `etl-core`,
  `ingestion-framework` — not invented services
* The **relationship count** (traced, not guessed)
* The **"Derived"** provenance tag on the Dependency Graph evidence
* "Explore impact" / "View dependency graph" action buttons handing off
  to the deeper Impact Analysis view

### Follow-up question
```text
Which repositories depend on this one?
```

### Demo talking point
> "That's not a language model guessing what a data pipeline usually
> depends on — that's two real repositories, traced through 300 real
> relationships in our own dependency graph."

---

## Scenario 2 — Impact / Blast Radius

### Capability
Impact Analysis (`AI Workspace → Impact Analysis`)

### What I type
This capability is a repository picker, not a chat box — select it
directly:
```text
Select repository: sasikumars-cyb/ingestion-framework
```

### Why this is a good demo
`ingestion-framework` sits underneath three other indexed repos
(`etl-core`, `streaming-pipeline` both depend on it) — it's a real,
central dependency, not an arbitrary pick, so its blast radius actually
looks like something worth worrying about.

### Expected GraphForge journey
```text
Repository selected
    ↓
GraphForge queries the real dependency graph
    ↓
Computes blast radius by hop distance (direct, then indirect)
    ↓
Renders the graph: dependencies, consumers, shared infrastructure
```

### What I should show the judges
* The **visual blast-radius graph** rendering real dependency names:
  `pyspark`, `delta-spark`, `confluent-kafka`, `great-expectations`,
  `azure-storage-file-datalake`
* Hop-distance rings (1-hop vs 2-hop)
* That this is instant and requires zero LLM call to render

### Follow-up question
```text
What if I only fix the first two affected systems?
```

### Demo talking point
> "This is a real dependency graph, not a diagram someone drew in
> Confluence — every node here came from actually indexing the code."

---

## Scenario 3 — Migration Assistant

### Capability
Migration Assistant (`AI Workspace → Migration Assistant`)

### What I type
```text
Move our Spark jobs to Databricks. What will be affected and what should we plan for?
```

### Why this is a good demo
This mirrors real, open work in the connected Jira backlog — **NPT-20**
("Upgrade Databricks Runtime to 14.3 LTS") and **NPT-24** ("Evaluate
Photon engine for aggregation workloads") are both real, open NPT
tickets about this exact platform. *(The target "Databricks" is a demo
assumption — the real Jira tickets confirm the team is already on
Databricks and actively working this migration; GraphForge doesn't fetch
that framing automatically, it grounds the "Spark" source technology
against real indexed dependencies.)*

### Expected GraphForge journey
```text
User question
    ↓
GraphForge parses (source technology, target technology) = (Spark, Databricks)
    ↓
Finds every repository whose real dependencies reference Spark
    ↓
Computes blast radius per repository, grades risk by fan-out
    ↓
Produces scope + risk-graded plan
    ↓
User asks a follow-up
    ↓
GraphForge refines the same migration scope
```

### What I should show the judges
* **Four real repositories** found automatically: `etl-core`,
  `ingestion-framework`, `streaming-pipeline`, `warehouse-jobs`
* `streaming-pipeline` graded **HIGH** risk, the others **MEDIUM** — a
  real, computed severity split, not a flat list
* The explicit recommendation to migrate `streaming-pipeline` first
  because of its downstream fan-out
* "Create migration plan" / "Validate migration" handoff actions

### Follow-up question
```text
What if we only fix streaming-pipeline first?
```

### Demo talking point
> "GraphForge didn't ask me which repositories use Spark — it found all
> four itself, and it's telling me which one to be most careful with,
> based on how connected it actually is."

---

## Scenario 4 — Refinement Planner ⭐ PRIMARY DEMO SCENARIO

### Capability
Refinement Planner (`AI Workspace → Refinement Planner`)

### What I type
```text
Refine NPT-30 and prepare it for sprint refinement. Identify the epic, stories, tasks, spikes, dependencies, open questions, and testing work we should discuss.
```

### Why this is a good demo
**NPT-30 is a real, open Jira bug** ("SCD2 merge creates duplicate
current records when source batch contains duplicate keys") with a
genuinely detailed description referencing real code —
`SCDType2Merger.merge()`, `scd_type2.py`, `_detect_changes()`. GraphForge
fetches the real Jira content, grounds it against the real `etl-core`
repository, and produces a breakdown that references the *actual*
class and file names in the codebase — not paraphrased, not invented.

### Expected GraphForge journey
```text
User names a real Jira issue (NPT-30)
    ↓
GraphForge fetches the real issue from the connected Jira instance
    ↓
Grounds it against the real etl-core repository (indexed code)
    ↓
Decomposes into epic + existing ticket + proposed stories/tasks + spike
    ↓
Computes readiness score from genuine completeness criteria
    ↓
User asks follow-up questions
    ↓
GraphForge refines the same plan in place
```

### What I should show the judges
* **NPT-30 tagged "EXISTING"** with a working **"View NPT-30 in Jira"**
  link — this is a real ticket, not a fabricated one
* Real code references in the generated work: `ExactDeduplicator`,
  `SCDType2Merger.merge()`, `test_scd2.py`
* A genuine **spike** ("Investigate ExactDeduplicator API, ordering
  support, and NULL-key behavior") — identified because the tie-break
  ordering is a real open unknown, not decoration
  * Readiness: **"Mostly ready — 75%"**, tagged "derived from
  completeness criteria"
* Evidence chips: **Jira** (source data), **Dependency Graph**
  (derived), **Refinement Analysis** (derived)

### Follow-up question
```text
Show me the dependencies between the proposed work items.
```

### Demo talking point
> "I didn't write any of this. I pointed GraphForge at a real, open bug
> in our backlog, and it read the ticket, found the actual class it's
> about in our codebase, and proposed a plan that names real code."

---

## Scenario 5 — Visual ticket dependencies

### Capability
Refinement Planner → dependency graph (deep-linked from Scenario 4)

### What I type
```text
Show me the dependencies between the proposed work items.
```

### Why this is a good demo
This is the same NPT-30 plan, visualized — no context switch, no new
tool to explain. The critical path genuinely terminates at the real
Jira ticket (NPT-30), which is a small but concrete detail that proves
this isn't a static mockup.

### Expected GraphForge journey
```text
"Show dependencies" action clicked
    ↓
GraphForge computes critical path (longest-path DAG algorithm)
    ↓
Renders existing (NPT-30) vs. proposed tickets, direction, and type
    ↓
User clicks a node
    ↓
Ticket Intelligence Panel opens with full grounded context
```

### What I should show the judges
* **Existing vs. proposed** visual distinction (solid vs. dashed
  border) — NPT-30 is the one solid, "EXISTING" node
* Directional, labeled edges (`blocks`, `depends_on`)
* The **critical path** callout at the top of the page, ending at
  `NPT-30`
* Click a node → Ticket Intelligence Panel: objective, acceptance
  criteria, blockers, provenance

### Follow-up question
```text
Which ticket is the bottleneck?
```

### Demo talking point
> "This isn't a hand-drawn diagram — this is computed from the actual
> dependency edges the plan produced, and it's telling us the real
> ticket is the one everything else is waiting on."

---

## Scenario 6 — Conversational refinement

### Capability
Refinement Planner (multi-turn, same NPT-30 conversation)

This is the strongest continuity demo — **every one of these four
messages was run live in one real conversation** during verification.

### Message 1
```text
Refine NPT-30 and prepare it for sprint refinement. Identify the epic, stories, tasks, spikes, dependencies, open questions, and testing work we should discuss.
```

### Message 2
```text
Show me the dependencies between the proposed work items.
```

### Message 3
```text
What happens if the tie-break spike takes longer than expected?
```
**Verified live response:** *"If PROPOSED-05 (the tie-break spike)
slips, PROPOSED-02 (wire ExactDeduplicator) is blocked, and since
PROPOSED-03 and PROPOSED-04 depend on PROPOSED-02, this cascades to
NPT-30. This path is the critical path, so the epic's completion is
delayed accordingly. To mitigate, we could time-box the spike or start
independent design work for PROPOSED-03/04 earlier."*

### Message 4
```text
What should we test?
```
**Verified live response:** grounded specifically in `PROPOSED-04`'s own
acceptance criteria and `test_scd2.py` — not a generic QA checklist.

### Why this is a good demo
The user never repeats "NPT-30," "the tie-break spike," or any ticket
ID after the first message — GraphForge resolves "that," "the spike,"
and "these" from conversation state every time.

### What I should show the judges
* Zero repeated ticket IDs after message 1
* Message 3's answer names the exact blocked/cascading tickets by ID
* Message 4's answer references a specific file (`test_scd2.py`), not
  boilerplate testing advice
* The plan visible on screen never resets — it's the same investigation
  throughout

### Follow-up question
```text
Can you make the tie-break work smaller?
```

### Demo talking point
> "Notice I never repeated a ticket number. GraphForge is carrying the
> entire investigation state across every one of these questions."

---

## Scenario 7 — Planning workflow

### Capability
Planning (deep-linked from Refinement Planner)

### What I do
Click **"Create planning workflow"** underneath the NPT-30 plan (no
typing required — it's an action button, verified working this session:
it prefills the Planning page with the current investigation's
context).

### Why this is a good demo
It shows GraphForge doesn't treat refinement as a dead end — the same
grounded investigation flows straight into an implementation plan
without the user re-explaining anything.

### Expected GraphForge journey
```text
Refinement conversation (NPT-30, plan, dependencies)
    ↓
"Create planning workflow" clicked
    ↓
Planning page opens, pre-filled with the refinement's own context
    ↓
User can generate an architecture-grounded implementation plan from there
```

### What I should show the judges
* The Planning page opens with the NPT-30 context **already filled
  in** — no retyping
* This is the same underlying investigation loop, not a different tool

### Follow-up question
Not applicable — this is a hand-off action, not a chat turn.

### Demo talking point
> "Refinement doesn't dead-end in a summary — one click and the same
> grounded context becomes an implementation plan."

---

## Scenario 8 — Testing / validation

### Capability
Refinement Planner conversational follow-up (same as Scenario 6,
message 4 — shown standalone here per the judges' checklist)

### What I type
```text
What should we test?
```

### Why this is a good demo
The answer names a real file (`test_scd2.py`) and a real proposed task
(`PROPOSED-04`) instead of returning a generic "write unit tests, write
integration tests" checklist — proof the recommendation is grounded in
the actual plan, not templated.

### Expected GraphForge journey
```text
User asks for a testing strategy
    ↓
GraphForge reads the current plan's work items and acceptance criteria
    ↓
Identifies which proposed task already owns test coverage
    ↓
Produces a testing recommendation grounded in that task, not generic advice
```

### What I should show the judges
* The answer names `test_scd2.py` and `PROPOSED-04` specifically
* No mention of unrelated systems — scoped to what's actually in this
  plan
* "Generate testing strategy" deep-link to the full Testing capability
  if more detail is wanted

### Follow-up question
```text
Generate a full testing strategy for this.
```
(Deep-links via the "Generate testing strategy" action.)

### Demo talking point
> "That's not a generic testing checklist — it's telling us exactly
> which task already covers this, and which file the tests belong in."

---

## The 3-minute GraphForge story

One connected sequence, all built on the real NPT-30 ticket:

```text
1. Open Refinement Planner.
2. Type: "Refine NPT-30 and prepare it for sprint refinement..."
3. Show the real epic/story/spike breakdown — point out NPT-30 is
   tagged EXISTING with a working Jira link.
4. Ask: "Show me the dependencies between the proposed work items."
5. Show the graph — existing vs. proposed, critical path ending at NPT-30.
6. Ask: "What happens if the tie-break spike takes longer than expected?"
7. Point out zero repeated ticket IDs, and the cascading blocked-ticket answer.
8. Ask: "What should we test?"
9. Point out the answer names the real file (test_scd2.py), not a generic checklist.
```

Close on the core message:

> "GraphForge doesn't just turn requirements into tickets. It understands
> the engineering context behind the requirement, connects the work
> through dependencies, identifies uncertainty, and lets the team refine
> the plan conversationally."

---

## What makes GraphForge different?

### 1. Engineering context
GraphForge understands the organization's actual repositories, real
Jira issues, and the dependencies between them — not a generic model of
"what software usually looks like."

### 2. Dependency intelligence
It reasons about what depends on what and calculates downstream impact
deterministically — blast radius, critical path, and risk grading are
computed, never guessed.

### 3. Conversational investigation
The user refines the same investigation through multiple questions —
"that," "these," "the spike" — without ever restating a ticket ID.

### 4. Refinement intelligence
A real Jira issue becomes a proposed Epic, Stories, Tasks, and a genuine
Spike where uncertainty actually exists — grounded in the real code the
issue references.

### 5. Evidence and provenance
Every claim is tagged — **Fact** (real Jira data), **Derived**
(graph-computed), or **AI Insight** — so the audience always knows
which is which.

---

## Demo checklist

Before the presentation, verify:

- [ ] Login works (`sasmobileplay@gmail.com`, real session — confirm
      the JWT hasn't gone stale; re-login if `/api/v1/auth/me` 401s)
- [ ] AI Workspace loads
- [ ] NPT-30 still exists and is reachable (`View NPT-30 in Jira` link
      resolves) — Jira issues can be edited/closed by teammates
- [ ] `etl-core`, `ingestion-framework`, `streaming-pipeline`,
      `warehouse-jobs` are still indexed (Repositories page)
- [ ] Dependency graph loads and is readable at your presentation
      resolution
- [ ] Refinement Planner produces the NPT-30 plan cleanly (run it once,
      same-day, before presenting — LLM responses aren't cached)
- [ ] Conversational follow-up (`What happens if the tie-break spike
      takes longer than expected?`) resolves correctly, no repeated IDs
- [ ] Migration scenario (`Move our Spark jobs to Databricks...`) still
      finds all 4 repositories
- [ ] Planning workflow hand-off pre-fills correctly
- [ ] Testing recommendation still names `test_scd2.py`
- [ ] No console errors on any of the 4 main pages
- [ ] No fake/generated Jira IDs anywhere on screen
- [ ] All evidence chips are clickable and show real content

### Recommended primary demo
> ⭐ **PRIMARY DEMO SCENARIO: Scenario 6 — Conversational Refinement on
> NPT-30.** It is the only sequence that is simultaneously (a) built on
> a real, verifiable Jira ticket and real code, (b) fully conversational
> with zero repeated IDs, and (c) demonstrates dependency computation,
> spike detection, downstream impact, and testing grounding in one
> unbroken thread. Rehearse this one first.

---

## Known limitations — do not demo these

- **Confluence** is not fetchable standalone (no URL, no bare page
  reference) — GraphForge says so honestly rather than pretending; don't
  bring it up unless you specifically want to show that honesty.
- **KAN** (GraphForge's own internal Jira project) — contains entries
  admitting real product gaps. Never reference it live.
- **SAM1** ("Example Billing System") — no indexed repository behind
  it; a refinement request against it will not ground to real code.
- LLM responses are **not deterministic in wording** — the exact phrasing
  in "Verified live response" quotes above may vary slightly on
  re-run; the grounded facts (which repos, which ticket IDs, which
  files) will not.
