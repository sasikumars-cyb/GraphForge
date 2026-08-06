"""Renderers — pure projections of one `EngineeringDecision`.

Every renderer in this package is a function of exactly one argument (the
decision) with no I/O, no database access, and no second source of facts. That
constraint is the whole point: a renderer that could reach for another source
would be able to print something the decision does not contain, and two
renderers reaching independently is how a headline ends up contradicting the
body beneath it.

`tests/unit/decision/test_renderer_projection.py` enforces this structurally
rather than by convention — it asserts that every entity name, verdict word and
evidence locator appearing in a rendered surface is traceable to a field on the
decision it was given.
"""
