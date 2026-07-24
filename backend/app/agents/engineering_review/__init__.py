"""Engineering Review Agent — Blueprint Readiness capability.

Validates a Planning workflow's own artifacts (Planning + Development +
Testing outputs) for completeness, risk coverage, and dependency/test
adequacy, and produces an Engineering Readiness Report a human reviews
before approving the blueprint. Reviews planning artifacts, never a git
diff — that remains the separate, unchanged Review Agent (review_pr),
which only ever runs inside an Auto Execution workflow.
"""
