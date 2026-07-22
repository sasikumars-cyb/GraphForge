"""Phase 6: the Architecture Discovery Engine.

Given a tracked `Repository` (app.models.repository), this package clones
it, detects its language/framework, parses the source deterministically
(no AI/LLM calls anywhere in this package), and produces an
`ArchitectureModel` (models/) describing what it found. `graph/builder.py`
turns that into the generic node/edge shape `app.graph` persists to Neo4j.

    scanner/     language detection + git cloning
    parsers/     ILanguageParser + concrete parsers (java/ for Spring Boot)
    extractors/  the Java-specific tree-sitter queries parsers/java/ uses
    graph/       ArchitectureModel -> app.graph.models.GraphPayload
    models/      the internal architecture model (plain dataclasses)
    services/    orchestrates the full pipeline end to end
    workers/     background-task entrypoint the API triggers

Java + Spring Boot (Maven) only, for this phase — see ADR 0007 for the
language-parser extension point and everything explicitly out of scope.
"""
