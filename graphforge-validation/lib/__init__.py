"""GraphForge Regression Validation Framework — shared library code.

Everything here is a thin client over GraphForge's own REST API (plus,
for the two facts no REST endpoint exposes — Engineering Memory
provenance and a way to mint a session token without a browser OAuth
flow — direct imports of GraphForge's own `app.knowledge_engine` and
`app.core.security` modules). No graph traversal, confidence computation,
or relationship-matching logic is reimplemented here: every fact this
framework asserts on comes from calling GraphForge, never from
recomputing what GraphForge should have computed.
"""
