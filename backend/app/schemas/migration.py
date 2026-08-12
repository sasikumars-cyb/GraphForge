"""Migration Assistant's own structured turn shape — carried inside
`ConversationTurnPayload.migration`. See `app.services.migration_grounding`
for what actually computes `direct`/`indirect`; this module only defines
the wire shape.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.ask import ProvenanceKind


class MigrationRisk(BaseModel):
    label: str
    reason: str
    provenance: ProvenanceKind


class MigrationScope(BaseModel):
    source_technology: str
    target_technology: str
    # Repository/system names — human-readable, already resolved (see
    # `migration_grounding.display_names`, the same pattern
    # `ask_grounding` already uses for blast-radius node ids).
    direct: list[str] = Field(default_factory=list)
    indirect: list[str] = Field(default_factory=list)
    risks: list[MigrationRisk] = Field(default_factory=list)
    # One of the direct repositories' ids — the deep-link target for
    # "Explore impact"/"View repository" (see
    # `conversation_service._migration_actions`). `direct`/`indirect`
    # above are display names only; this is the one id action links need.
    primary_repository_id: str | None = None
