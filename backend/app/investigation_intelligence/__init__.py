"""ADR 0021 — Investigation Intelligence: cross-investigation retrieval and
planning experience.

Answers a different question than Engineering Memory does. Engineering
Memory (`app.learning_engine`, `app.repositories.
engineering_memory_repository`) stores *engineering knowledge* — is this
specific architecture-graph relationship correct. This package stores
*retrieval and planning experience* — which strategy finds answers well,
for this repository/source, and is the planner's own strategy choice
getting better over time. Different domain, different tables (see
`app.models.investigation_intelligence`), no foreign keys between them.

`InvestigationIntelligenceService` (`service.py`) is the only interface
anything outside this package ever touches — `app.context_pipeline.
reasoning.engine` receives an already-constructed instance (or `None`)
through `SessionContext`, never imports `models.py` or touches an
`AsyncSession` for this purpose directly. See ADR 0021 for the full
design and the runtime audit that motivated it.
"""

from __future__ import annotations
