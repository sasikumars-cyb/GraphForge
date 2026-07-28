# Planning Agent Remediation Plan

**Source:** 100% review of run `6cbfa820-167e-47f8-8425-ff01bd1bdd91`
(*"Prepare implementation plan for PROT-5723"*, completed 43.5 s, self-reported
confidence 90%, provider bedrock).

**Reference ground truth:** `/home/sasikumars/git_repositories/Hackathon/repos/ds-databricks-soco-gpc-c2m-rcs-dataingest`

**Deadline context:** management demo **Sat 1 Aug 2026**. Today is Tue 28 Jul 2026.

---

## Status — first tranche shipped (28 Jul 2026)

Eight defects fixed and verified. Three diagnoses in this document were
**wrong** and are corrected below; the original text is left in place so the
correction is legible.

| ID | Fix | Verified by |
|----|-----|-------------|
| F1 | 1527 orphaned Neo4j nodes purged | every remaining graph has an owning Postgres row |
| A2 | URLs + absolute paths stripped before term extraction | `jira`/`local`/`already` now weight **0.0000** |
| A1 | IDF dampened to `1/(1+log1p(df))` | `manifest` **0.0092 → 0.1501** |
| A4 | Ranking matches `file_path` | `notebooks/parse_manifest.py` members now rank |
| A5 | Test components discounted ×0.3 | *added — see below* |
| A6 | Per-repo budget scales 8–20 | *added — see below* |
| D1 | Blueprint edges render | **49 edges** across 5 diagrams = backend's 17+7+7+15+3 |
| C1 | `verification_warnings` rendered | all 6 shown at the top of the Summary tab |

683 backend + 270 frontend tests pass, including 4 new regression tests.

### Correction 1 — F1 was misdiagnosed, and does not affect ranking

This document proposed adding "a delete-before-write step to the indexing
pipeline". That step **already exists**:
`Neo4jGraphRepository.replace_repository_graph` opens with
`MATCH (n {repository_id: $repository_id}) DETACH DELETE n`, and the
`DELETE /repositories/{id}` route clears the graph *before* deleting the row.
No code change was needed or made.

The real state was 8 graphs for 5 Postgres rows: **3 true orphans** (no owning
row, from repos removed outside the app) and one legitimate pair — two
different users had each registered the same local repo, which is correct
multi-tenancy, not duplication. Only the 3 orphans were deleted.

Critically: **F1 was not a prerequisite for A1, as claimed.** Retrieval is
already scoped to the requesting user's own repositories — the pool is exactly
2039 components, which is precisely this user's four repos. The orphans and the
other user's copy never entered any ranking. F1 was hygiene, not correctness.

### Correction 2 — the zero-edge bug was two bugs, and §D's root cause was wrong

This document asserted the cause was xyflow's measurement gate, fixable by
declaring node dimensions. That fixed only *half* of it — the
`visibility: hidden` on all 26 nodes. Edges stayed at zero.

The actual cause: `node.internals.handleBounds` was `undefined` for **every**
node (0 of 8 populated). Edge geometry is derived from it, so `getEdgePosition`
returned null, `EdgeWrapper` bailed at its `sourceX === null` guard, and every
edge was dropped silently. The store held all 7/17 edges the whole time — this
was never a data problem, and no console error was ever emitted.

The fix also could not use the obvious API. `useUpdateNodeInternals` resolves
node elements from `store.getState().domNode` **synchronously at call time**,
so calling it from a mount effect — before the flow registers its container —
builds an empty update map and silently does nothing. Ten retries changed
nothing. Driving the store's `updateNodeInternals` directly, with the element
lookup inside the frame, works.

### Correction 3 — A5 and A6 were not optional

§2 deferred A5 (test-code dominance) and A6 (fixed per-repo cutoff) to a later
tranche. With A2+A1+A4 landed, the GPC top-12 became **eleven members of a
single `test_manifest_pipeline.py`**, because every component sharing a file
scores identically once the path is matched, and they arrive as an
alphabetically-dense block. The production parser sat at #13.

A5 and A6 are therefore load-bearing, not polish: without them the first three
fixes do not deliver their stated benefit. A tiebreak that de-prioritises
private/dunder names was added for the same reason — two of five slots were
going to `__init__`.

**Result** — GPC top-5 sent to the LLM, before and after:

| Before | After |
|---|---|
| `scripts.jira_comment` | `soco_ingest.src.config.pipeline_config` |
| `test_already_processed_file_exits_early` | `TransformManifestParser` ← the class that swallows the failure |
| `_to_local_path` | `WidgetContext` |
| `gpc_local_datetime_with_offset` | `build_internal_manifest` |
| `test_to_local_path_converts_dbfs_uri` | `declare_widgets` *(in `notebooks/parse_manifest.py`)* |

`_publish` — the method that swallows the taskValues exception — now ranks #6,
and 4 of the top 12 are members of `manifest_parser.py`. **Acceptance criterion
9 remains unproven**: the model now has the failing files in front of it, which
is a real chance, not a guarantee.

### Still outstanding

B1–B6 (verification correctness, incl. the bidirectional-substring hole),
E1–E8 (brownfield prompt branch, diagram integrity), D2/D3.

---

## 0. Why this plan exists

The run produced a fluent, well-structured plan that was **wrong about the one
thing that mattered** — the root cause — and displayed `verified: true` beside
fabricated file paths and components belonging to three other tenants'
repositories.

Scored against the real repository, **2 of 11 substantive claims were correct**,
and 4 of 7 `affected_components` do not exist in the target repo. Every one of
the 7 blueprint diagrams rendered **zero edges**.

None of this is a model-quality problem. Every failure traces to a specific,
fixable defect in retrieval, verification, or rendering. This document enumerates
all 27 of them and the change that closes each.

### The reference failure, stated once

PROT-5723 is *"pipeline change to address bigger manifest"*. The real cause is the
Databricks `dbutils.jobs.taskValues` **48 KiB per-value limit**. Four payloads in
`parse_manifest` scale linearly with manifest file count:

| Payload | Site | Guarded? |
|---|---|---|
| `raw_manifest_json` | `soco_ingest/notebooks/parse_manifest.py:121` | no — set first, largest, breaks first |
| `flat_manifest` | `soco_ingest/notebooks/parse_manifest.py:77` | no |
| `transform_manifest_payload` | `soco_ingest/src/parsers/manifest_parser.py:197` | failure swallowed |
| `parse_manifest_logs` | `soco_ingest/notebooks/parse_manifest.py:106,139` | no |

At ~220 bytes/record the ceiling is roughly **120–250 files**. The fix is to spill
the manifest to Delta/GCS and pass a pointer.

The agent instead diagnosed *"memory exhaustion, timeout, or executor failure"* on
a **32 GB `n2-highmem-4` driver**, and proposed chunking at **10 000 files/batch** —
roughly 50× past the real ceiling, and ineffective anyway because chunks still
travel through `taskValues`.

**This plan does not hand-code the PROT-5723 answer into GraphForge.** Every fix
below is generic. The acceptance test is that a re-run reaches the taskValues
conclusion *on its own*, because the right files finally reach the prompt.

---

## 1. Defect register

27 defects across 6 areas. IDs are referenced by the workstreams in §2.

### A — Retrieval & grounding (root cause of the wrong diagnosis)

| ID | Defect | Evidence |
|----|--------|----------|
| **A1** | **IDF inversion.** `_term_weights` (`tools.py:284-306`) scores `1/(1+df)`, so a token matching one component beats a token matching the domain. Measured over the 2039 live components: `jira` 0.5000, `already` 0.3333, `local` 0.2000 vs `manifest` **0.00917** (df=108). The ticket's key noun scores **54× lower** than a token from the pasted URL. | reproduced numerically |
| **A2** | **URL and filesystem-path pollution.** `extract_key_terms` consumed **16 of its 25 slots** on `https`, `atlassian`, `browse`, `jira`, `home`, `sasikumars`, `git`, `repositories`, `hackathon`, `repos`, `local`, `already`… `manifest` landed at position 23. One directory deeper in the user's path and it would have been cut entirely. | `classifier.py:663-688` |
| **A3** | `max_terms=25` is too tight once noise is removed. | `classifier.py:663` |
| **A4** | **Ranking never sees file paths.** `_relevance` is fed `f"{c['name']} {c['type']}"` only (`tools.py:333, 424`). A component in `notebooks/parse_manifest.py` gets no credit for its path. | `tools.py:305,333` |
| **A5** | **Test code dominates the graph.** GPC indexes 976 test components vs 115 production + 28 notebooks (79% tests). Retrieval draws mostly from tests. | Neo4j count |
| **A6** | `max_components_per_repo=5` is a fixed cutoff regardless of repo size. For a 1232-component repo, 5 slots is a rounding error. | `tools.py:355` |
| **A7** | Terms matching *nothing* (df=0) receive weight **1.0** — the maximum. Harmless today only because they never match; a latent landmine. | `tools.py:306` |

**Measured consequence.** Top-5 GPC components actually sent to the LLM:

```
scripts.jira_comment                      0.5000   ← "jira", from the pasted URL
test_already_processed_file_exits_early   0.3333   ← "already", from "the repo is already in my local"
_to_local_path                            0.2000   ← "local",   idem
gpc_local_datetime_with_offset            0.2000   ← "local"
test_to_local_path_converts_dbfs_uri      0.2000   ← "local"
```

The genuinely relevant components rank **#24** (`manifest_parser`), **#42**
(`TransformManifestParser`), **#92**, **#173**, **#175**, **#176** — all outside
the cutoff. They were in the graph the whole time. **A throwaway sentence about
where the repo sits on disk produced 3 of the top 6 components.**

### B — Verification (why wrong claims showed green)

| ID | Defect | Evidence |
|----|--------|----------|
| **B1** | `usage.verified = usage.name in indexed_repo_names` — checks **only that the repo name exists**. A green badge therefore sits beside fabricated `files_affected`. | `agent.py:793` |
| **B2** | **Bidirectional substring matching makes verification near-vacuous.** `_claim_supported` returns true if `claim in evidence` **or** `evidence in claim`. Any short evidence string that happens to be a substring of a long claim verifies it. This is the mechanism that passed all four cross-repo components. | `verification.py:169-184` |
| **B3** | **Evidence pool is cross-repo pooled with no ownership check** — one flat `set[str]` over every repo. | `verification.py:157-166`, `agent.py:632` |
| **B4** | `verified: bool = True` — fails **open**. | `schemas.py:82` |
| **B5** | Entity/tenant check flags `BEGIN`, `END`, `JIRA` — tokens from GraphForge's **own** `wrap_untrusted_content` wrapper, not the ticket. | `prompt_utils.wrap_untrusted_content` |
| **B6** | Repos named in the plan but **not indexed** are never flagged. Phase 3 plans MPC work; MPC is not indexed and not on disk. The prompt rule required writing "not yet indexed". | run output |

**Measured consequence** — ownership of the 7 `affected_components`:

| Component | Actually owned by |
|---|---|
| `test_already_processed_file_exits_early` | APC + GPC ✅ |
| `soco_ingest.src.loaders.raw_delta_loader` | APC + GPC ✅ |
| `ValidationIssue` | APC + GPC ✅ |
| `pipeline.validation.schema_validator` | **avangrid** ❌ |
| `databricks.Process_manifest.IngestPipelineSelectorJob` | **pseg-nj** ❌ |
| `databricks.Process_manifest.validate_transform_manifest_job` | **pseg-nj** ❌ |
| `trnasform_manifest_logger_job` | **pseg-nj** ❌ |

**4 of 7 (57%) are not in the target repo**, and **all 7 passed with zero
warnings**. Note the typo `trnasform` was propagated verbatim from pseg-nj
source — proof the agent is matching names, not reading code.

### C — UI (why the user never saw any of this)

| ID | Defect | Evidence |
|----|--------|----------|
| **C1** | **`verification_warnings` is rendered nowhere.** `grep -rn "verification_warnings" frontend/src/` returns **nothing** — not in a component, not even in `types/agent.ts`. It is produced by **four** backend agents (planning, development, testing, engineering_review) and displayed by none. | grep |
| **C2** | The Evidence tab says *"…could not be verified — see verification_warnings"* — a pointer to something the UI does not render. A dead end. | `agent.py:826-828` |
| **C3** | `verified: true` renders as an unqualified green badge with no per-file or per-component breakdown. | `RepositoryUsage` render path |

This is the single highest-value fix: the backend's honesty mechanism caught **5
of the 6 fabrications** and then threw the result away at the UI boundary.

### D — Blueprint rendering (the demo-killer)

| ID | Defect | Evidence |
|----|--------|----------|
| **D1** | **Every diagram renders 0 edges.** `.react-flow__edge-path` count is 0; `.react-flow__edges` holds only arrowhead marker defs; all 26 nodes carry inline `style.visibility: hidden` — React Flow v12's *unmeasured* marker. Stable across viewport resize, explicit Fit View, fullscreen, and Expand-all. No console errors. Backend supplies 17 + 7 + 15 + 3 edges; none reach the screen. | DOM inspection |
| **D2** | `groupIntoSections(blueprint.diagrams)` is **not memoized** — a new array identity every render. | `BlueprintExplorer.tsx:313` |
| **D3** | `DiagramCard` mounts with `opacity: 0` and a fixed `height: minHeight` (default 320), staggered by `setTimeout(index * 55)`. Mounting React Flow into a container that is hidden or zero-sized is exactly what prevents ResizeObserver from ever measuring. | `DiagramCard.tsx:118-128, 236-253` |

**Root cause (high confidence).** Nodes never reach `measured` state, so v12
suppresses both node visibility and edge rendering. Confirm before fixing, but
the fix is robust either way: **dagre already computes exact dimensions**
(`BlueprintRenderer.tsx:95` — `{width: NODE_W=200, height: NODE_H=60}`), and
those numbers are currently applied only to `style.width`. Promoting them to
top-level `node.width`/`node.height`/`node.measured` bypasses the measurement
gate entirely.

### E — Prompt & content quality

| ID | Defect | Evidence |
|----|--------|----------|
| **E1** | **Greenfield prompt on a brownfield ticket.** The prompt instructs *"Design the architecture… **Only then** check the repository inventory… Everything else is new work."* For "fix an existing pipeline" this guarantees an architecture doc instead of a patch — which is exactly what came out: 8 layers ending in *Curated & Warehouse* and *Analytics & BI*, neither of which exists (the real pipeline ends at GCS CSV exports). | prompt + run output |
| **E2** | **Self-contradiction.** Executive summary says *"~40% of required capabilities exist in ds-databricks-soco-gpc…"* while `repository_usage` for the same repo says `estimated_reuse_pct: 75`. | run output |
| **E3** | Diagrams 2 and 3 carry **no `grounded` flag at all**. Diagram 1 correctly self-labels `grounded: false` — that honesty must be mandatory, not optional. | run output |
| **E4** | **Ghost nodes.** The Data Model diagram synthesizes 7 nodes (`ManifestEntries`, `LandingZone`, `QuarantineZone`, …) by naive pluralization of relationship strings, plus 6 invented entities. `Manifest`/`ManifestEntry` are not modeled types in the repo. | run output |
| **E5** | Diagram 0 labels plain **directories** `RISK` merely for containing a matched file (`scripts`, `tests/unittest`). Misleading vocabulary on the one diagram that is otherwise ground truth. | run output |
| **E6** | Roadmap deliverable text truncated mid-word: *"…from job run 7"*. | run output |
| **E7** | Risk matrix crams full paragraphs into node labels — unreadable as a matrix. | run output |
| **E8** | Diagram 2 invents *Auto Loader*, *Quarantine Routing*, and *BI tools*. The real 5-stage flow is documented in the repo's own `docs/architecture.md` — the agent had it and ignored it. | run output |

### F — Data hygiene

| ID | Defect | Evidence |
|----|--------|----------|
| **F1** | Neo4j holds **8 Repository nodes for 4 real repos** and **4078 components of which 2039 are orphaned duplicates**. Four `repository_id`s have no Postgres row — stale indexes never cleaned up. Every df in A1 is computed over this polluted pool. | Neo4j query |

---

## 2. Workstreams

Ordered by **credibility-per-hour before Saturday**, not by defect ID.

### P0 — Make the product tell the truth (Tue–Wed, ~3 h)

Nothing here changes what the agent concludes. It changes whether a viewer can
*see* that the agent was unsure — which is the difference between a demo that
looks dishonest and one that looks rigorous.

**P0.1 — Render `verification_warnings` (C1, C2, C3) · ~1 h**

1. Add `verification_warnings: string[]` to the planning/development/testing
   result types in `frontend/src/types/agent.ts`.
2. New `VerificationPanel` component: amber, collapsed by default, header
   *"N claims could not be verified against indexed code"*, one row per warning.
3. Mount it in the run detail Summary tab **above** the plan body — a caveat
   below the fold is not a caveat.
4. Badge the count next to the confidence score, so 90% never appears unqualified.

> **Demo value:** turns the worst finding into the best one. "The system flagged
> its own six unsupported claims" is a stronger story than a clean-looking plan.

**P0.2 — Fix blueprint edge rendering (D1, D2, D3) · ~2 h**

1. *Confirm* the root cause first — in the browser console, check
   `nodesInitialized` and whether every node has `measured === undefined`.
2. Primary fix: in `BlueprintRenderer.tsx:146-183`, add top-level
   `width: NODE_W, height: NODE_H` and `measured: { width: NODE_W, height: NODE_H }`
   to each node. dagre already assumes exactly these dimensions, so layout and
   render agree by construction and the ResizeObserver gate is bypassed.
3. Memoize `groupIntoSections` (D2).
4. Ensure `DiagramCard` does not mount React Flow while `opacity: 0` / zero-height
   (D3) — render the card, then animate, or animate a wrapper that never
   collapses the measured box.
5. **Acceptance:** `document.querySelectorAll('.react-flow__edge-path').length`
   equals the backend edge count on all 7 diagrams, at desktop and mobile widths.

### P1 — Make retrieval find the right code (Wed–Thu, ~5 h)

This is the workstream that would have changed the answer.

**P1.1 — Clean the input before term extraction (A2, A3) · ~1 h**

In `classifier.py`, before `_TOKEN_RE.finditer`:
- strip URLs (`https?://\S+`) and absolute filesystem paths (`(/[\w.-]+){2,}`);
- extend `_GENERIC_STOPWORDS` with URL/VCS/host noise: `https`, `http`, `www`,
  `atlassian`, `browse`, `jira`, `github`, `gitlab`, `repos`, `repositories`,
  `local`, `already`, `folder`, `directory`;
- raise `max_terms` 25 → 40.

**P1.2 — Weight ticket title and body above incidental prose (A1) · ~1.5 h**

Terms from the Jira **summary/title** are what the ticket is about; terms from
the requester's surrounding chatter are not. Pass a per-term source prior into
`_term_weights` and multiply: title ×3, description ×1.5, free text ×1.

**P1.3 — Floor the IDF weight (A1, A7) · ~30 m**

Replace `1/(1+df)` with `1/(1+log(1+df))` (or clamp to `[0.15, 1.0]`). A token
matching 108 components must not lose to one matching 1. Drop `df == 0` tokens
from the weight map explicitly rather than letting them sit at 1.0.

**P1.4 — Rank on file path, not just name (A4) · ~30 m**

Feed `f"{c['name']} {c['type']} {c.get('file_path','')}"` to `_relevance` in both
`rank_repositories` and `format_graph_context`. This alone lifts
`notebooks/parse_manifest.py` substantially.

**P1.5 — De-emphasize test components (A5) · ~45 m**

Multiply relevance by ~0.3 where `file_path` contains `/tests/` or the name
starts `test_`. Do not exclude outright — tests are legitimate evidence — but
they must not occupy all five slots.

**P1.6 — Scale the per-repo cutoff (A6) · ~30 m**

`max_components_per_repo`: 5 → `clamp(ceil(repo_component_count / 100), 8, 20)`.
Guard the prompt's token budget when raising this.

> **Acceptance for P1:** re-run PROT-5723 verbatim. `manifest_parser`,
> `parse_manifest`, and `TransformManifestParser` must all appear in the GPC
> top-5. This is a *retrieval* assertion — assert it in a unit test against a
> fixture component pool, so it holds without burning Bedrock budget.

### P2 — Make verification actually verify (Thu, ~3 h)

**P2.1 — Kill the substring escape hatch (B2) · ~1 h**

Replace `_claim_supported`'s bidirectional `in` with:
- exact normalized match, **or**
- path-segment-anchored match (`endswith("/" + claim)` for file paths), **or**
- full token-set containment of the claim's tokens.

Never bare `evidence in claim_n`. Add regression tests for the four cross-repo
components — each must now fail verification.

**P2.2 — Per-repo evidence pools + ownership check (B3) · ~1 h**

Build `dict[repo_name, set[str]]` instead of one flat set. Check
`affected_components` against the **target** repo's pool, and emit an explicit
warning naming the owning repo when a component belongs elsewhere:
> *"`pipeline.validation.schema_validator` is indexed under **avangrid**, not
> under the target repository."*

**P2.3 — `verified` means verified (B1, B4) · ~30 m**

`schemas.py:82` default `True` → `False` (fail closed). At `agent.py:793`:

```python
usage.verified = (
    usage.name in indexed_repo_names
    and not files_check.unverified
    and not owned_elsewhere
)
```

**P2.4 — Un-indexed repository detector (B6) · ~30 m**

Scan the rendered plan for repo-like tokens; warn for any not in
`indexed_repo_names` (would have caught MPC).

**P2.5 — Exclude wrapper tokens from the entity check (B5) · ~15 m**

Run `check_entity_mismatch` against the **inner** untrusted payload, or stoplist
the wrapper's own vocabulary (`BEGIN`, `END`, `JIRA`, `UNTRUSTED`, `CONTENT`).

### P3 — Prompt & diagram integrity (Fri, ~3 h)

**P3.1 — Branch on task mode (E1) · ~1.5 h**

Classify the brief as `bugfix | enhancement | greenfield` (ticket verbs:
*fix / fails / error / address* vs *build / migrate / introduce*). For
`bugfix`/`enhancement`, swap in a brownfield prompt that leads with *"locate the
failing code path in the indexed repository and explain the mechanism"* and
**suppresses** the 8-layer architecture scaffold entirely.

> This is the single highest-leverage content fix. The greenfield framing is
> what turned a one-line bug into an 8-layer architecture.

**P3.2 — Mandatory `grounded` flag (E3) · ~30 m**

Make `grounded` required on every diagram; render an *"Illustrative — not derived
from indexed code"* chip whenever it is false. Diagram 1 already does the right
thing; make it non-optional.

**P3.3 — No ghost nodes (E4) · ~30 m**

Emit Data Model nodes only for entities explicitly declared in `entities`. Delete
the pluralization-from-relationship-strings path.

**P3.4 — Consistency check (E2) · ~30 m**

Assert the reuse percentage quoted in the executive summary matches
`estimated_reuse_pct`; prefer injecting the deterministic value into the summary
over asking the model to keep two numbers in sync.

**P3.5 — Label hygiene (E5, E6, E7) · ~30 m**

Stop labelling directories `RISK` (use `contains matches`); fix roadmap
truncation; move long risk text out of node labels into the selection detail
panel.

### P4 — Data hygiene (15 m, any time)

**F1** — delete the 4 orphaned Neo4j `Repository` nodes and their 2039 detached
components; add a delete-before-write step to the indexing pipeline so
re-indexing replaces rather than accumulates. Do this **before** measuring P1, or
every df is computed over a polluted pool.

---

## 3. Schedule

| Day | Work | Hours |
|---|---|---|
| **Tue 28 Jul** | P4 (Neo4j cleanup) → P0.1 (render warnings) → start P0.2 | 3.5 |
| **Wed 29 Jul** | Finish P0.2 (edges) → P1.1–P1.4 | 5 |
| **Thu 30 Jul** | P1.5–P1.6 → P2.1–P2.5 | 5 |
| **Fri 31 Jul** | P3.1–P3.5 → **re-run PROT-5723** → capture before/after | 4 |
| **Sat 1 Aug** | Demo | — |

**If time is cut, ship P0 + P1 only.** Those two alone change both what the demo
shows and what the agent concludes. P2 and P3 are correctness debt that can land
after.

---

## 4. Acceptance criteria

The plan is done when a verbatim re-run of PROT-5723 satisfies:

1. `manifest_parser`, `parse_manifest`, `TransformManifestParser` all appear in
   the GPC top-5 retrieved components. *(unit-testable, no Bedrock spend)*
2. No `affected_component` belongs to avangrid or pseg-nj; any that does is
   warned with its owning repo named.
3. `verified: true` appears only where the repo name, every `files_affected`
   entry, and component ownership all check out.
4. All `verification_warnings` are visible in the UI without opening devtools.
5. Every diagram renders its full backend edge count; zero nodes stuck at
   `visibility: hidden`.
6. Every diagram carries an explicit `grounded` flag; ungrounded ones are chipped.
7. Executive summary and `estimated_reuse_pct` agree.
8. MPC (or any un-indexed repo named in the plan) is flagged as not indexed.
9. **Stretch:** the plan's root-cause section names `taskValues` / a payload-size
   limit rather than memory exhaustion.

Criterion 9 is the real prize and is **not** guaranteed by criteria 1–8 — but
with the right files in the prompt the model has a genuine chance at it, which it
did not have in this run.

---

## 5. Explicitly out of scope

- Any hard-coded knowledge of PROT-5723, Databricks, or the 48 KiB limit. The
  agent must reach it from retrieved code or not at all.
- Re-indexing the target repos (already indexed and current).
- Model or provider changes. This is a retrieval and presentation problem, not a
  reasoning-capacity problem.

---

## Appendix — What the run got right

Worth preserving, and worth saying out loud in the demo:

- **Diagram 0, "Indexed Codebase Structure (Ground Truth)"** — genuinely
  grounded. Directory tree and counts match Neo4j exactly (1232 total, 949
  tests/unittest, 113 scripts, 28 notebooks). This is the artifact that cannot
  hallucinate, and it is what separates GraphForge from a chatbot.
- `kafka_topics_involved: []` — correctly empty rather than confabulated.
- Diagram 1 honestly self-labels `grounded: false`.
- The backend verification layer **caught 5 of the 6 fabrications**. It was
  working. The UI discarded its output.
- Deploy sequencing (STG → UAT → PROD) and the APC replication call were correct
  — APC does carry the identical `taskValues` pattern across 5 call sites.
