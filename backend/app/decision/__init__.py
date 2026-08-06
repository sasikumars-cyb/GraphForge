"""Engineering Decision — the canonical contract every GraphForge surface renders.

One `EngineeringDecision` answers, for one pull request at one commit: what
changed, what it could affect, what evidence supports and contradicts each
claim, what is deterministic versus AI-inferred, what remains unknown, what
would raise confidence, what the reviewer should do, and — derived from all
of the above, never asserted independently — whether it should merge.

Every presentation (GitHub PR comment, Check Run, merge gate, GraphForge UI,
Slack, API response, audit log, metrics) is a pure projection of this object.
None of them may introduce a fact this contract does not carry: that rule is
what makes it structurally impossible for a headline verdict to contradict
the body it summarizes, which is precisely the class of bug a hand-written
per-surface renderer produces.

This package is a leaf consumer, not a peer, of
`app.knowledge_engine.contracts`: it composes the real `ConfidenceModel` and
`EvidenceItem` types rather than restating them, so "verified" means exactly
the same thing here that it means everywhere else in the platform.
"""
