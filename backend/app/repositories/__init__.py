"""RFC-001's data-access layer for the Engineering Session aggregate.

Naming note: "Repository" here is the DDD/persistence pattern (a
collection-like interface over one aggregate's storage) — a different
thing from `app.models.repository.Repository`, the ORM model for an
indexed *code* repository (a GitHub/GitLab project). The two are
unrelated; this package is never imported alongside that model under an
unqualified `Repository` name, to keep the two meanings from colliding at
a single import site.

Every class in this package is a thin query/write wrapper around one
aggregate's tables — no business rules, no transaction commits (that's
`app.services`'s job; see `app/services/session_service.py` and its
siblings). This split exists specifically because RFC-001's Implementation
Requirements name "Repositories" and "Services" as separate deliverables,
matching a standard layered-architecture split even though most of this
codebase's existing services query the database directly (see
`app.services.test_case_upload_service` for the pre-existing, simpler
pattern) — RFC-001's aggregate is complex enough (ten related tables) that
separating "how do I read/write this row" from "what does committing a
Decision actually mean" is worth the extra layer.
"""
