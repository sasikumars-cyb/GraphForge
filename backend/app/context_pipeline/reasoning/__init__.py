"""Context Discovery's reasoning engine.

This package inverts the old control flow. Previously
`ContextResolutionPipeline` ran a hardcoded provider sequence
(Jira -> Confluence-if-Jira -> GitHub -> Graph) and a reasoning step then
interpreted whatever fell out of it. Here the reasoning engine owns the
workflow and providers are passive tools it chooses between:

    assess what is known (from facts alone)
        -> identify which knowledge requirements are still unmet
        -> ask every investigator what it could contribute right now
        -> pick the single most valuable next action
        -> run it, fold the resulting facts/evidence into working memory
        -> assess again
        -> repeat until requirements are met, or no investigator has
           anything left to offer
        -> only then, ask the human exactly one question

Module map, bottom-up:

- `ledger`        Facts, EvidenceRecords and Inferences. Append-only,
                  every fact traceable to the investigation that produced
                  it. Facts and interpretation never mix.
- `capabilities`  The capability registry: one declaration per thing
                  discovery needs to know, carrying its weighted confidence
                  signals, gap framing, remediation, and — paired together so
                  they cannot drift — its clarification question and the
                  verification that checks an answer to it.
- `investigation` The action/outcome vocabulary and the Investigator
                  protocol that makes providers passive.
- `investigators` The concrete Jira/Confluence/GitHub/Graph/request-parse
                  investigators, each proposing work only when it could
                  actually close an open gap.
- `memory`        `WorkingContext` — the evolving working memory, plus the
                  gap model and the narration transcript.
- `engine`        The reasoning loop itself, and verify-then-resolve for
                  human answers.
- `projection`    Rendering working memory into the flat result Planning
                  and the UI consume.
"""
