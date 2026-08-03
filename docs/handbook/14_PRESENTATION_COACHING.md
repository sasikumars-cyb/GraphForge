# Section 14 — Presentation Coaching

This is a **live exercise**, not a document to read passively. It only
works as a real back-and-forth: the panel (Claude) asks one question, you
answer in chat, the panel critiques your actual answer against the
grounded material in Sections 1–12 and 16, offers a stronger version, then
escalates difficulty. This file is the protocol and the seed questions —
say "run the presentation coaching drill" (or similar) in chat to start it
for real.

## Protocol

1. One question at a time. Never stack multiple questions in one turn.
2. Wait for the answer before critiquing.
3. Critique against the handbook's actual sourcing — if the answer invents
   a capability (e.g. claims Impact Analysis works cross-repository, which
   [16_REALITY_CHECK.md](16_REALITY_CHECK.md) documents as structurally
   broken today), the critique says so plainly, cites the gap, and shows
   the stronger, honest version of the same answer.
4. Escalate: developer-level → architect-level → VP-level → CTO-level, in
   that order, across the session. Don't jump straight to CTO-level
   difficulty on question one.
5. A good "stronger answer" almost always does one of: names the specific
   file/class/ADR, states the honest limitation in the same breath as the
   claim, or gives the one-sentence "why," not just the "what."

## Seed question bank (escalating)

**Developer-level (open here)**
1. "Walk me through what happens, in order, when a repository gets indexed."
2. "What's the difference between `app.knowledge` and `app.knowledge_engine`?"
3. "Why can't a validator call an LLM?"

**Architect-level**
4. "Why is Neo4j not the source of truth anymore? What replaced it?"
5. "How do you keep `ConfidenceEngine.aggregate` both incremental and
   correct? What actually forced the two extra fields on `ConfidenceModel`?"
6. "Where does the materializer sit today — is it live?"

**VP Engineering-level**
7. "If I ship this to production tomorrow, what's actually going to
   surprise my team first?"
8. "How do you know the confidence scores aren't just decorative?"
9. "What's your CI gate for a regression in graph quality?"

**CTO-level**
10. "Why should I believe an LLM-touched pipeline won't quietly degrade
    the graph's trustworthiness over six months?"
11. "What's the actual bound on how much of the graph can ever be
    LLM-sourced, and why?"
12. "If you had to cut scope by 50% for a launch, what goes, and why does
    the rest survive?"

## How to invoke

In chat: "Start the presentation coaching drill" (optionally: "start at
[developer/architect/VP/CTO] level"). Claude will ask question 1, wait,
critique, then continue — this cannot be pre-scripted end-to-end in a
static file, since your actual answers determine the critique and the next
question's difficulty.
