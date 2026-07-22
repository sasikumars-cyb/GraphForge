"""Phase 7: Deterministic Pull Request Impact Analysis.

Given a pull request, determines every component that could be affected
using the Knowledge Graph built by `app.indexer` (Phase 6) — no AI/LLM
calls anywhere in this package.

    models/    the output shape: RiskLevel, ImpactedNode, DependencyPath,
               ImpactAnalysisResult
    graph/     IImpactGraphReader + Neo4jImpactGraphReader — read-only
               traversal queries over the same graph app.graph writes
    services/  pure, unit-testable functions: risk classification and
               dependency-path construction
    engine/    ImpactAnalysisEngine — orchestrates the full workflow:
               read changed files -> map to graph nodes -> traverse ->
               classify risk -> persist

See ADR 0008 for the risk-classification rules and everything explicitly
out of scope (cross-repo REST/Feign correlation, diff-content analysis).
"""
