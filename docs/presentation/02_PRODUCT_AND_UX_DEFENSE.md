# 02 — Product & UX Defense (Presenter 1)

## Why this problem exists

The same senior engineer gets asked "does this affect X" ten times a
week (`PRODUCT_VISION.md` § Target Users). That knowledge — what depends
on what, who owns it, what broke last time — lives in people's heads and
evaporates when they're out or when they leave. `PRODUCT_VISION.md`'s
Ana persona (staff architect) states it directly: she "wants the
Architecture Agent and Knowledge Graph to encode what she knows so it
survives her being on vacation — or leaving."

## Why existing tools fail

`PRODUCT_VISION.md`'s competitive table, memorize the row headers:
memory-across-sessions, cross-repository code reasoning, deterministic
grounding, extensibility, SDLC-continuity. Generic chatbots and PR-bots
score "no" or "partial" on every one. The one line that matters: **"GraphForge does not
compete on 'better prompts.' It competes on having a graph the others
don't."**

## Why GraphForge

ChangeGuard (the predecessor) proved the narrow version: a deterministic
dependency graph plus a tool-using LLM agent beats prompting an LLM with a
diff alone, at PR-review scale. GraphForge generalizes that proof across
the whole SDLC. This is an **evolution**, not a rewrite — say this
explicitly if asked "how long have you been building this" — the
deterministic core has real production lineage, not a hackathon-weekend
LLM wrapper.

## User Journey

1. Engineer connects a repository → indexed deterministically (tree-sitter,
   zero AI cost).
2. Engineer asks a question (Repository Understanding / Dependency Query
   / Impact Analysis) or reviews a PR.
3. The answer is graph-grounded, confidence-scored, and links back to the
   evidence that produced it.
4. Every answer compounds — the next engineer's question benefits from
   what the graph already knows, not a re-derivation from scratch.

## UX decisions worth defending

- **No marketing chrome.** `PRODUCT_VISION.md`: "developer-native... dense,
  fast." Dark theme, Tailwind utilities only, no component library —
  `UI_GUIDELINES.md`'s explicit rule.
- **Confidence is never a bare adjective.** Always a percentage next to
  the claim it supports (`UI_GUIDELINES.md` § Interaction Guidelines) —
  evidence-over-assertion applied to the UI itself, not just the data
  model.
- **Color means action-category, not decoration** — primary/agentic/
  publish/danger, four fixed categories, no arbitrary new hues.
- **Every agent-produced claim links to its evidence** — enforced by using
  `EvidencePanel`, never a raw text dump.

## Demo narrative (what Presenter 1 sets up for Presenter 5)

Frame the demo as a **story**, not a feature tour: "Here's a PR that
silently breaks two downstream services — watch the graph catch it before
CI does." Hand off cleanly: "Now watch what the graph actually knows."

## Innovation

The genuinely novel claim, stated precisely (don't oversell): not "we use
AI to review code" (that's table stakes now) — it's that **every
AI-sourced claim has to survive independent, deterministic corroboration
before it's trusted as fact**, and that discipline is enforced
structurally (a `HypothesisGenerator` cannot write to the graph; a
`KnowledgeValidator` cannot call an LLM), not by convention.

## Business value

- Time-to-context: "assembled before you finish reading the ticket"
  vs. "ask a senior engineer" (`PRODUCT_VISION.md` § Definition of
  Success) — frame as engineer-hours saved, not a vague productivity claim.
- Knowledge that survives attrition — the org's own history becomes
  reusable, not re-derived per engineer, per incident.
- Every AI output is traceable — reduces the trust tax of "can I ship
  based on what the AI told me."

## Competitive advantages (the honest version)

1. Persistent, evidence-backed graph vs. session-scoped chat.
2. Deterministic core with LLM as an additive, gated layer — not the
   reverse.
3. A self-auditing validation suite that publishes its own known gaps —
   most teams don't do this; cite it as a credibility signal, not a
   weakness.

---

## Prepared answers

### "How well is your UX?"
**Short**: Deliberately unglamorous by design — dense, fast, evidence-
first, matching `UI_GUIDELINES.md`'s explicit "developer tool, not a
marketing site" stance. We did not invest hackathon time in visual
polish beyond that; we invested it in making sure every number on screen
is real.
**Don't claim**: user testing, accessibility audits, or usage analytics we
haven't run. If asked for a NPS/usability score: "we haven't measured
that — here's what we did prioritize instead" (the evidence-linking rule
above).

### "Why will engineers use this?"
**Short**: Because it answers a question they already ask a colleague ten
times a week, faster and with a paper trail, not because it's another tool
to learn. The workflow starts where they already are — a PR, a repo, a
ticket — not a new destination app.

### "How is this better than Copilot?"
**Short**: Different job. Copilot answers "what does this code do" in one
file, in the editor, in the moment. GraphForge answers "what breaks if I
ship this" across repositories, grounded in a graph that persists across
sessions. We don't compete with in-editor completion — we're the layer
above it that Copilot has no access to. Full version: `docs/handbook/12_DIFFICULT_QUESTIONS.md`
§ "Why not Cursor/Sourcegraph/Copilot."

### "How is this different from GraphRAG?"
**Short**: GraphRAG-style systems typically let an LLM's extracted
relationships get written to the graph directly. We do the opposite — an
LLM-sourced relationship is one more hypothesis that has to clear
independent, deterministic validators before it's trusted, and it can
never reach our highest confidence tier from one LLM's agreement alone.
Retrieval quality isn't the differentiator we're chasing; evidence-gated
trust is. Full version: `docs/handbook/12_DIFFICULT_QUESTIONS.md` §
"Why not GraphRAG."

### "Why this product? Why now?"
**Short**: We already had proof at PR-review scale (ChangeGuard) that
this thesis works. GraphForge is the generalization, not a new bet — the
risk of "does grounded, evidence-first AI reasoning actually work better
than prompting" is already retired; what's left is breadth of coverage,
which is exactly what our roadmap and known-gaps list show honestly.
