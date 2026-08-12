# ADR 0028: Generic Extraction Architecture — Assessment, Not a New Design

## Status

Assessment / recommendation document, requested explicitly as a pre-implementation
deliverable ("do not implement blindly — first produce an architectural
assessment"). This is **not a new architecture proposal**. Its central finding is
that GraphForge already has one: **ADR 0018 (Engineering Intelligence Platform)**,
substantially implemented (RFC-01 through RFC-06D), running in shadow mode on
every real indexing run today, and never wired up to actually drive the Neo4j
graph. That gap — not a missing architecture — is what this document identifies
and scopes a path to close.

Everything below was produced by inspecting the actual code
(`app/indexer/`, `app/knowledge_engine/`, `app/graph/`, `app/ai/`), running the
current SQL extractor against a representative construct set, and reading
ADR 0018 in full. Claims are cited to file paths; where I recommend something
not yet in the codebase (a SQL parser library, a specific migration step),
it's marked as a recommendation requiring evaluation, not a confirmed fact.

---

## 1. Current architecture assessment

### 1.1 The indexing pipeline as it exists today

```
repo → clone_repository() → detect_language() → get_parser()
     → ILanguageParser.parse() → ArchitectureModel
     → build_graph() → GraphPayload → replace_repository_graph() → Neo4j
```

- **`indexer/parsers/registry.py`**: a hard `DetectedLanguage → ILanguageParser`
  map. Exactly two entries (`SpringBootJavaParser`, `PythonParser`). A repo
  matching neither is hard-rejected (`UnsupportedRepositoryError`, HTTP 422) —
  see `language_detector.py`.
- **`ArchitectureModel`** (`indexer/models/architecture.py`): a single, growing,
  language-*aware* dataclass. It has `controllers`/`services`/`feign_clients`
  (Java/Spring-specific), `python_modules`/`python_classes` (Python-specific),
  and, after this cycle's work, `sql_files`/`sql_table_references`
  (SQL-specific). Every new language or artifact type has historically meant a
  new set of fields here plus new extraction code plus new sections of
  `graph/builder.py` that know how to turn those fields into
  `GraphNode`/`GraphEdge`. **This is Approach A** (one bespoke extractor per
  language), and it is exactly the growth pattern the SQL work this cycle
  extended — correctly, per its own scope, but it doesn't scale to 20
  languages without 20 rounds of this.
- **`graph/builder.py`**: one function (`build_graph`), ~700 lines, that
  switches on which `ArchitectureModel` fields are populated and hand-encodes
  the node/edge shape for each. It is the single source of truth for label/
  relationship vocabulary, and it is unconditionally trusted — anything it
  emits goes straight to Neo4j via `replace_repository_graph`, with **zero
  confidence, evidence, or validation step** between parse and graph write.
- **Neo4j schema** (`graph/neo4j_common.py`): a hard allowlist
  (`_ALLOWED_LABELS`/`_ALLOWED_REL_TYPES`) that must be extended by hand for
  every new node/edge kind — confirmed the hard way this cycle: `SqlFile`/
  `LOADS_SQL` silently would have been refused without it, caught only by a
  real Neo4j integration test.
- **Evidence/source-location model**: `SourceLocation(file_path, line)` on
  every `ArchitectureModel` entity — good, consistent, already
  language-agnostic. But it stops at the model boundary: nothing in
  `graph/builder.py` writes evidence, confidence, or provenance onto the
  `GraphNode`/`GraphEdge` it produces. A `READS_FROM` edge today is
  indistinguishable, at the graph level, whether it came from a
  100%-certain AST call or (hypothetically) a guess.
- **AI/LLM infrastructure** (`ai/config/resolver.py`, `ai/providers/registry.py`,
  `agents/llm.py`): already fully generic and reusable — five providers
  (OpenAI, Groq, DeepSeek, Gemini, Bedrock) behind one `ILLMProvider`
  interface, stage-aware configuration resolution, opt-in fallback chains,
  per-call `LLMInvocation` persistence (ADR 0012). **This part of the "how do
  we scale to 20 languages" question is already solved** — provider/model
  abstraction is not a gap.

### 1.2 The part almost nobody outside this codebase would expect: it's already been redesigned once

`app/knowledge_engine/` and `app/indexer/hypotheses/` implement **ADR 0018**, a
five-stage pipeline — **Evidence → Hypothesis → Validation → Confidence →
Knowledge** — that is, point for point, the architecture requested in this
task:

| This task's request | ADR 0018's existing contract |
|---|---|
| Normalized IR (Entity/Relationship/Evidence/Confidence) | `knowledge_engine/contracts/{evidence,hypothesis,confidence,provenance,knowledge}.py` |
| Every LLM relationship carries evidence/confidence/method/model/validation | `Hypothesis` + `GeneratorIdentity(kind, name, version)` + `Provenance` + `ConfidenceModel` — **already exact-match**, including `kind: Literal["deterministic","rule","llm","runtime","docs","infra"]` |
| VERIFIED / DETERMINISTIC / INFERRED / UNVERIFIED classification | `ConfidenceState = VERIFIED \| HIGHLY_LIKELY \| ...` (`confidence.py`) |
| Validation before trust | `KnowledgeValidator` (`validation.py`) — deterministic only, "a validator that itself asks an LLM 'does this seem right' isn't validating, it's generating a second, uncoordinated hypothesis" (the module's own docstring) |
| LLM gated by cost/config, not "send every file" | `GeneratorPolicy`/`LLMEnabledGeneratorPolicy` (`generator_policy.py`), `Settings.enable_frontier_llm_generator` (off by default) |
| Deterministic parsers as one generator among many | `DeterministicParserHypothesisGenerator` (`indexer/hypotheses/deterministic_generator.py`) — **reuses `graph/builder.py` unmodified**, converts its `GraphPayload` into `Hypothesis` objects rather than re-deriving anything |
| LLM as a second, pluggable generator | `llm_generator.py` / `build_frontier_hypothesis_generator()`, registered in `generator_registry.py` |
| Projection into Neo4j from validated knowledge, not raw parse | `knowledge_engine/materializer.py` — "pure projection from Engineering Memory (`KnowledgeRelationship`) + `EngineeringEvidencePack`) into `GraphPayload`... No reasoning happens here" |
| Pre-cutover safety net | `knowledge_engine/shadow_compare.py` — runs the Materializer's projection against the real graph on **every real indexing run today** and diffs them, explicitly "before the live write path is cut over to go exclusively through the Materializer" |

**This is running right now.** Every time this cycle's SQL/Spark changes were
indexed against the audit repository, `materializer_shadow_compare_mismatch`
log lines fired — that's ADR 0018's pipeline actively shadow-processing every
real index, silently, already.

**What's missing is not the architecture. It's the last mile**: `graph/builder.py`
is still the only thing that writes to Neo4j; the Hypothesis/Validation/
Confidence/Materializer pipeline computes an independent, parallel answer that
nothing consumes. ADR 0018's own roadmap names the missing piece explicitly:
**RFC-07 — "First graph promotion for a language with no existing parser"**:
*"`graph/builder.py` gains a `KnowledgeRelationship → GraphPayload` path;
`parsers/registry.py`'s hard 422 gate relaxed to fall through to the generic
pipeline when no dedicated parser exists."* That sentence **is the answer to
this task's central product question**, already designed, not yet built.

---

## 2. SQL parser limitations — tested, not assumed

I ran the current `sql_lineage.py` regex extractor against a representative
construct set (results below are the actual output, not a prediction):

| Construct | Result |
|---|---|
| Nested CTEs (`WITH a AS (...), b AS (...) SELECT FROM b`) | Reads real tables correctly, **but also fabricates `a` and `b` as tables** |
| `CREATE TEMPORARY TABLE t AS SELECT ...` | **Missed entirely** — `TEMPORARY` between `CREATE`/`TABLE` isn't recognized; no write recorded at all |
| `CREATE VIEW v AS SELECT ...` | **Missed entirely** — no `CREATE VIEW` support; the view itself is invisible |
| `FROM (SELECT ...) alias` (subquery) | Correctly skips the outer wrapper, finds the inner table |
| `FROM TABLE(some_udf(...))` (table-valued function) | **False positive** — extracts `TABLE` as a table name |
| BigQuery `` `project.dataset.table` `` (single backtick pair) | Works, coincidentally |
| Snowflake `"DB"."SCHEMA"."TABLE"` (ANSI double-quoting) | **Missed entirely — zero output** |
| SQL Server `[dbo].[Orders]` (bracket quoting) | **Missed entirely — zero output** |
| `WITH orders AS (SELECT FROM raw.orders_v2) SELECT FROM orders` | Correctly reads `raw.orders_v2`, **but also emits `orders` as a second, separate table** — and if a real table named `orders` exists elsewhere in the same repo, this CTE alias **silently merges onto that real table's `DataTable` node** (identity is by name only) |

The last row is the one that matters most: this isn't noise, it's a
**correctness failure with a specific, damaging shape** — a query that never
touches a real `orders` table can end up showing a `READS_FROM`/`WRITES_TO`
edge to it, because the CTE alias happens to share its name. For an
impact-analysis product ("what breaks if I change this table"), a false
lineage edge is worse than a missing one: a missing edge under-reports risk
(annoying, safe-ish); a false edge over-reports it in a way an engineer will
eventually distrust after being burned once, or — worse — actually rely on to
scope a migration incorrectly.

**Conclusion: regex is not sufficient for a production engineering-intelligence
product, and the answer is not "add more regex."** A regex can pattern-match
keywords; it cannot know that `orders` inside the `WITH` clause's scope is a
different binding than `orders` the real table, because that's a scoping
question — the one thing regex fundamentally cannot represent, no matter how
many patterns are added. The `IS DISTINCT FROM`/`EXTRACT(...FROM...)` false
positives fixed this cycle were **the same class of bug**, found and fixed one
at a time; nested CTEs, views, temp tables, and two entire quoting dialects
are the next four rounds of the identical whack-a-mole, and there will be a
fifth after that (Oracle's `FROM DUAL` variants, SQL Server's `OPENROWSET`,
BigQuery's `UNNEST`/array-of-struct syntax, recursive CTEs, `LATERAL`/`CROSS
APPLY`, dynamic pivot).

### Recommended direction (evaluation required, not adopted yet)

A real SQL AST is the only way to distinguish a CTE binding, a subquery
alias, a temp table, a view, and a table-valued function from an actual
table reference — that's scope resolution, which requires a parse tree with
named scopes, not text matching. Two realistic options:

- **`sqlglot`** (Python, no new language runtime) — a mature, actively
  maintained SQL parser/transpiler with native dialect support for Spark,
  Databricks, Snowflake, BigQuery, Redshift, Postgres, T-SQL, and more; it
  already resolves CTE/subquery/view scoping and exposes exactly the
  read/write table-reference extraction this feature needs (`sqlglot.lineage`
  / `sqlglot.optimizer.scope`). This stays in the Python process, needs no
  new build toolchain, and directly replaces `sql_lineage.py`'s regex core
  without touching anything upstream (the `SqlReference` output shape stays
  the same; only how it's produced changes).
- **`tree-sitter-sql`** — consistent with this codebase's existing
  tree-sitter investment (Java/Python already use it), but dialect coverage
  and scope-resolution maturity for SQL specifically are weaker than
  `sqlglot`'s as of this evaluation; would need to be paired with hand-written
  scope tracking on top of the raw parse tree, largely re-deriving what
  `sqlglot` already provides.

**Recommendation: evaluate `sqlglot` as a drop-in replacement for
`sql_lineage.py`'s internals**, keeping the module's existing public
signature (`extract_sql_table_references(sql_text) -> list[SqlReference]`) so
every caller (`spark.py`, `sql_file_extractor.py`) and every test written
this cycle needs zero changes — this is a scoped, low-risk swap of an
internal implementation, not a pipeline redesign. This should land as its
own change, benchmarked against the same construct table above plus a CTE/
view/temp-table/quoting regression suite, before being wired into the
generator pipeline described below.

---

## 3. Deterministic vs. LLM responsibility boundary

Grounded in what's actually reliable from static analysis vs. what genuinely
requires semantic judgment — not a generic list, but specific to what this
codebase's parsers can and cannot already prove:

### Deterministic (AST/parser-sourced, high reliability)

- File/module/class/function/method identity and containment (already:
  `PythonParser`, `SpringBootJavaParser`)
- Imports, explicit inheritance, literal decorators
- A call whose callee resolves unambiguously by name (already: `CALLS` via
  `function_node_id_by_bare_name`) — and, symmetrically, **the decision to
  *not* resolve an ambiguous one** is itself a deterministic, correct
  output, not a gap
- Literal string arguments, and literal values built by adjacent-string
  concatenation or local-scope constant substitution (this cycle's
  `literal_resolution.py`) — genuinely provable, not inference
- SQL table references **once scope-resolved by a real parser** (CTE vs.
  table vs. view vs. subquery) — deterministic in principle, currently
  approximated by regex (§2)
- Declared dependencies (`pyproject.toml`, `pom.xml`, `package.json`,
  `go.mod`, `*.csproj`) — a manifest format is a known, parseable grammar,
  never a judgment call
- Explicit configuration (a YAML/JSON key naming a table, topic, queue, or
  service by literal string) — the *value* is deterministic; what that value
  *means to the running system* (§ below) may not be

### Semantic / LLM-assisted (syntax alone is insufficient)

- **Dynamically constructed queries/paths** where the literal value is
  provably unresolvable by any static rule (e.g. `spark.sql(cfg.get_query())`
  where `cfg` comes from a remote config service) — this is where an LLM
  reading the surrounding code, docstrings, and naming conventions can
  propose "this probably targets the `orders` table" as a **hypothesis**,
  never a fact
- **Framework-specific implicit behavior**: an ORM's implicit table naming
  convention, a Spark job's implicit output path derived from a job-name
  variable, a Kafka consumer group's effective topic set resolved through a
  registry pattern this cycle's `.sql`-registry rule already handles the
  *literal* case of, but a *computed* registry (built from an API call, not
  a dict literal) is exactly where static analysis's honest answer is "I
  don't know" and an LLM's contribution is a scored guess, not a fact
- **Cross-artifact semantic relationships**: "this Kubernetes Deployment's
  `image:` tag names a service that this other repo's `Dockerfile` builds" —
  no shared AST connects a YAML file to a Dockerfile to a repo name; this is
  inherently a semantic-matching problem
- **Business-level relationships**: "this pipeline is the canonical source
  of truth for customer billing data" — not extractable from any syntax,
  ever; genuinely requires either documentation, an LLM's synthesis of
  multiple weak signals, or a human
- **Undocumented/tribal-knowledge dependencies**: a service that depends on
  another only through a shared file/bucket naming convention with no code
  reference at all

The dividing line, concretely: **if a validator (a second, independent,
deterministic check) could confirm or refute the claim by re-reading source,
it's deterministic-generator territory, even if today's extractor doesn't
yet reach it (view resolution, better SQL parsing). If no re-reading of
source could ever confirm it without also being an inference, it's
LLM-generator territory**, and ADR 0018's confidence model already accounts
for exactly this: an LLM-sourced hypothesis with no corroborating validator
result caps below `Verified` by design (`ConfidenceModel`'s multi-source
corroboration requirement).

---

## 4. Normalized intermediate representation

**Already exists, partially** — `knowledge_engine/contracts/` is the IR the
task asks for:

```
Evidence   → knowledge_engine/contracts/evidence.py (EvidenceItem, EngineeringEvidencePack)
Entity     → GraphNode (app/graph/models.py) — reused, not re-invented, by Hypothesis
Relationship → Hypothesis (relationship_type: str, open vocabulary) → KnowledgeRelationship
Confidence → knowledge_engine/contracts/confidence.py (ConfidenceModel, ConfidenceState)
ExtractionMethod → GeneratorIdentity(kind, name, version) (provenance.py)
```

The one real gap against the task's request: **`ArchitectureModel` is not yet
this IR** — it's still the older, language-*aware* shape `graph/builder.py`
consumes directly. `deterministic_generator.py` already builds the bridge
(`architecture_model_to_evidence_pack`) by calling `build_graph()` and
converting its output, which is the right shape of bridge, but it's a
one-way, after-the-fact conversion, not the primary path a new language's
extractor would target. Closing that — making `Evidence`/`Hypothesis` the
thing a *new* language extractor produces directly, rather than the thing an
existing `ArchitectureModel` gets converted into after the fact — is
precisely RFC-07's scope.

Entity/relationship vocabulary is already open (`str`, not a closed enum) by
explicit design (`evidence.py`'s own docstring: "a registered vocabulary,
not a closed enum requiring a schema change per new evidence source") — this
already satisfies "language-specific extractors should produce normalized
entities/relationships rather than directly encoding language-specific
assumptions into Neo4j," for everything upstream of `graph/builder.py`. The
Neo4j write-side allowlist (`_ALLOWED_LABELS`/`_ALLOWED_REL_TYPES`) is the
one place that remains a closed, hand-maintained set — appropriately so,
since Cypher can't parameterize label/relationship names and this allowlist
is the injection-safety boundary, not a design smell.

---

## 5. Evidence/confidence model

Already fully specified by ADR 0018; nothing new needed here. The example
the task gives —

```
Relationship: customer_pipeline READS_FROM customer_table
Evidence: spark.sql(...), pipeline.py:142
Extraction: deterministic
Confidence: 1.0
```

— is, field-for-field, a `Hypothesis` with `GeneratorIdentity(kind="deterministic")`
and an `EvidenceItem` citing `SourceLocation`. The LLM example —

```
Relationship: service_a DEPENDS_ON customer_pipeline
Extraction: LLM-assisted, Confidence: 0.82, Validation: pending
```

— is a `Hypothesis` with `GeneratorIdentity(kind="llm")`, unresolved through
`KnowledgeValidator` yet, sitting below whatever this deployment's promotion
threshold is (`ConfidenceState`). **The classification scheme the task asks
to "consider" (VERIFIED / DETERMINISTIC / INFERRED / UNVERIFIED) already
exists as `ConfidenceState`**, and RFC-07's stated promotion gate ("Promotion
gated to `Verified`/`Highly Likely` only") already answers "what happens if
evidence can't support the relationship" — it simply never reaches the
graph.

---

## 6. Multi-language scaling strategy — the central product question, answered concretely

**If GraphForge supports 20 languages, how much new code per language?**

| Approach | Per-language cost | Reliability | Verdict |
|---|---|---|---|
| **A — bespoke extractor per language** (today's actual pattern: `PythonParser`, `SpringBootJavaParser`, this cycle's SQL work) | A full `ILanguageParser` + extractor set + new `graph/builder.py` section + new Neo4j allowlist entries, every time | Highest, for what it covers | Does not scale to 20; confirmed by this cycle's own SQL regex spiral |
| **B — tree-sitter/AST → normalized IR, no LLM** | One tree-sitter grammar + a *generic* structural extractor (imports/calls/declarations — the part that's syntactically similar across most languages) per language; no bespoke graph-builder section | High for structure, **zero for semantics** (framework behavior, dynamic values, cross-artifact meaning) | Necessary but insufficient alone — covers §3's deterministic column only |
| **C — AST + deterministic extractors + LLM semantic layer (ADR 0018)** | One tree-sitter grammar + a generic structural extractor (shared machinery, not bespoke per language) that emits `Evidence`/`Hypothesis`, **plus the existing, already-built LLM generator picks up the semantic gap for free** — no new LLM code per language, since the generator is language-agnostic by construction (it reads evidence packs, not source ASTs directly) | High where deterministic, appropriately-scored where not | **Matches the codebase's own already-frozen decision** (ADR 0018 §"Consequences": Neo4j becomes a rebuildable projection; existing parsers retained as calibration reference) |
| **D — LLM-only extraction** | Zero new parser code per language | Unbounded false-positive/negative risk, unbounded cost, no reproducibility, no source-location precision | Explicitly what ADR 0018's confidence model exists to prevent from silently becoming graph truth; rejected |

**Recommendation: Approach C — but this is not a fresh recommendation, it's
validating a decision this codebase already made and partially built.** The
concrete per-language cost under C, once RFC-07 lands: **one tree-sitter
grammar (if not already available) + one generic evidence extractor that
walks it for imports/declarations/calls/literals** (a few hundred lines,
mechanical, not "bespoke semantic understanding" — this cycle's
`extractors/python/tree_utils.py` is close to the right shape for what this
generic layer needs, generalized). No new `graph/builder.py` section, no new
Neo4j allowlist maintenance beyond registering the (already-open) vocabulary
entries this language's evidence uses, no new LLM integration code — the
Frontier Hypothesis Generator (`llm_generator.py`) is already
language-agnostic; it operates on evidence packs and prompts, not on
`ArchitectureModel` shape.

**This is the answer to "how much new code per language": one grammar + one
generic evidence walker, not one grammar + one bespoke extractor + one
bespoke graph-builder section + one bespoke validation story**, which is
what's true today and what RFC-07 replaces.

---

## 7. Cost/scalability strategy

Not a new design — the mechanisms this task asks for already exist or have
an existing, adjacent precedent to extend, not invent:

- **Deterministic first**: already the pipeline's structure (`GeneratorPolicy`
  gates whether the LLM generator runs at all; deterministic generators
  always run).
- **Content hashing / incremental**: `indexer/scanner/incremental.py` (KAN-32)
  already re-parses only changed files on a real push; the same
  `changed_files` scoping this cycle's `.sql`-file extension already reuses
  for the incremental path is the natural hook for scoping *which* files a
  future LLM generator would ever see — never the whole repo, only what
  changed.
- **Confidence thresholds gating cost**: `LLMEnabledGeneratorPolicy`
  (off by default) plus the multi-source corroboration requirement already
  bound how much of the graph can be LLM-sourced without additional
  deterministic evidence to corroborate against (ADR 0018's own stated
  consequence) — this is already a cost *and* a trust bound, not just a
  trust bound.
- **Structural chunking / candidate detection** (not yet built, genuinely
  new): the missing piece is a cheap, deterministic pre-filter that decides
  *which* evidence items are even candidates for an LLM call — e.g., only a
  `spark.sql()`/`.format()` call site where literal resolution *failed* (this
  cycle's `literal_resolution.py` already produces exactly this signal: a
  `None` result *is* the candidate marker) is worth spending an LLM call on;
  a fully-resolved literal never needs one. This turns "send every file" into
  "send only the specific call sites deterministic extraction already proved
  it cannot resolve" — a naturally small, self-selecting set.
- **Caching**: `LLMInvocation` (ADR 0012) already persists every call;
  extending it with a content hash of the evidence-pack input as a cache key
  is a small, additive change, not a new subsystem.
- **Model selection**: already solved generically (`ai/config/resolver.py`'s
  stage-aware resolution) — a new `hypothesis_generation` stage key is all a
  future LLM generator needs to pick a specific, possibly cheaper, model
  independent of every other agent's configuration.

---

## 8. Validation strategy

Already specified, not new: `KnowledgeValidator` (deterministic only, never
itself calls an LLM), `ConfidenceEngine.aggregate` (validators determine
trust — "the LLM never decides confidence, the validator computes
confidence"), and RFC-06B ("Evidence-Semantic Validator Architecture")
closing the specific gap of validating an LLM's *semantic* claims against
real evidence. The task's proposed pipeline —

```
LLM inference → Evidence check → Static analysis validation → Confidence scoring → Graph
```

— is ADR 0018's pipeline with the stage names slightly reordered
(`Hypothesis → Validation → Confidence → Knowledge`, where "Knowledge" is the
promoted, graph-eligible result). Nothing to design here; the gap is
promotion (RFC-07), not validation logic.

---

## 9. Migration path from the current implementation

This cycle's SQL/Spark work is **not wasted or in conflict** with this
architecture — it's exactly the kind of deterministic evidence source ADR
0018 expects, just currently wired to the wrong endpoint (`build_graph()`
directly instead of the Hypothesis pipeline). Concretely, in dependency
order:

1. **Swap `sql_lineage.py`'s internals for a real SQL parser** (§2,
   `sqlglot` recommended for evaluation) — same public function signature,
   zero changes to callers or the 59 tests written this cycle beyond
   updating the now-fixed CTE/view/temp-table/quoting cases from
   "documented limitation" to "passing test."
2. **Confirm `DeterministicParserHypothesisGenerator` already covers the
   SQL/Spark additions for free** — since it converts `build_graph()`'s
   *output* (`GraphPayload`), and `build_graph()` was extended this cycle to
   include `SqlFile`/`DataTable`/`LOADS_SQL`, the shadow Hypothesis pipeline
   should already be seeing these facts as of this cycle's changes. This is
   a verification step (does `shadow_compare.py`'s diff still report zero
   mismatch after this cycle's changes?), not new code.
3. **Land RFC-07** — the actual architectural unlock: `graph/builder.py`
   gains a `KnowledgeRelationship → GraphPayload` promotion path, gated to
   `Verified`/`Highly Likely` confidence, and `parsers/registry.py`'s hard
   422 falls through to it for any language with no dedicated
   `ILanguageParser`. This is the single change that makes "language #21"
   cheap.
4. **Generalize a tree-sitter evidence walker** for the syntactically
   common subset (imports, declarations, calls, literals) that most
   C-family/curly-brace/indentation languages share enough structure for —
   scoped per new language only by which tree-sitter grammar to point it at,
   not by writing a new walker.
5. **Wire the Frontier LLM generator to the specific unresolvable-literal
   signal** (§7) as its first concrete, cost-bounded semantic contribution —
   dynamic SQL/config values that deterministic resolution already proved
   it cannot resolve, each becoming exactly one candidate, never a whole-file
   scan.

Steps 1–2 are safe, additive, and immediately valuable regardless of when/
whether 3–5 land. Steps 3–5 are the actual "scale to 20 languages" unlock and
should be scoped as their own reviewed change(s), not bundled into this
assessment.

---

## 10. Recommendation

**Approach C, hybrid AST + deterministic extraction + LLM semantic layer —
confirmed against the existing implementation, not merely assumed.** The
validation specifically requested ("I want a concrete architectural answer...
validate that assumption against the existing GraphForge implementation
rather than simply agreeing with it") comes out affirmative for a reason
independent of the four options' abstract tradeoffs: **this codebase already
built Approach C once, deliberately, with a full RFC roadmap and working
shadow-mode implementation, specifically because Approach A (what the
codebase had before ADR 0018, and what this cycle's SQL work necessarily
extended) was already recognized as not scaling.** The only unimplemented
piece is RFC-07 — the language-agnostic graph-promotion path that turns
"new language" from "new parser + new graph-builder section + new trust
story" into "new grammar + one generic evidence walker, promoted through a
trust pipeline every language already shares."

**Do not design a second architecture. Finish this one.**

## Minimum architectural changes to move toward this model

In priority order, each independently landable and reviewable:

1. Evaluate and (if it holds up) swap `sql_lineage.py`'s regex core for
   `sqlglot` — fixes the CTE/view/temp-table/dialect gaps found in §2
   without touching any caller.
2. Verify `shadow_compare.py` reports zero mismatch for this cycle's SQL/
   Spark additions — confirms the existing shadow pipeline already absorbed
   this cycle's work for free, or surfaces a real gap in
   `deterministic_generator.py`'s conversion if it doesn't.
3. Scope and land RFC-07 as its own reviewed change — the actual
   multi-language unlock.
4. Only after RFC-07 exists: generalize a tree-sitter evidence walker and
   pick the next language to prove it on (a language with no existing
   parser, per RFC-07's own success criterion of "non-empty, spot-checked-
   correct graphs for real repos" before widening further).

No code was changed to produce this document, per the task's explicit
instruction; the above is a proposed sequence for separately-reviewed
follow-up work.
