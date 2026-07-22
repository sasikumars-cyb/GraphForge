"""Read-only traversal queries over the architecture graph `app.graph`
writes - a different concern than `app.graph.IGraphRepository` (which
reads/writes the whole graph generically): this is specifically the set of
traversals `app.analysis.engine.ImpactAnalysisEngine` needs to derive
impact from a set of directly-changed nodes.
"""
