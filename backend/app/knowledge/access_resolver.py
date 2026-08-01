"""The single implementation of "how does GraphForge currently reach this
knowledge source" — transport selection, endpoint, credentials, and the
REST->MCP auto-wire, all in one place.

Why this module exists
-----------------------
Before it, the same question — "is Confluence reachable, and how" — was
answered independently in at least three places that could (and did)
disagree:

- `app.tools.setup._build_tool_config` derived a Tool Registry config dict
  from a `KnowledgeConnection` row, including a REST->MCP auto-wire step
  (reuse the REST credential as an MCP bearer token against a known hosted
  endpoint).
- `app.agents.planning.confluence_context.get_confluence_mcp_config`
  (removed by this change) ran its own, separate query requiring the
  connection row's `transport` column to literally equal `"mcp"` — a state
  no UI flow ever produces (Settings -> Integrations always writes
  `transport="rest"`; see `TransportSpec.known_mcp_endpoint`'s own
  docstring), so this path was permanently unreachable for any
  UI-created connection despite the Tool Registry path proving the same
  connection *was* MCP-reachable via auto-wire.
- `app.tools.implementations.confluence_tool.ConfluenceTool` (removed by
  this change) additionally implemented a *second* REST/MCP fallback
  dance of its own, reachable only via `executor.execute("confluence",
  ...)` — which nothing in the codebase ever called (verified: zero
  production call sites, only its own unit tests instantiated it
  directly).

That divergence is exactly how "the UI reports a healthy Confluence
connection, Jira works, but Context Discovery can't find Confluence
documentation" became possible: two implementations of the same
resolution problem, one of them (the only one anything Documentation-
related actually used) systematically wrong for how the UI creates
connections.

Design
------
`derive_access_methods` is pure (no I/O — takes a connection's already-
loaded `transport`/`config`/`credentials`, returns every way a requested
capability can be reached, most-preferred first) so it can be the one
place both a synchronous, already-in-memory context (Tool Registry sync,
which has no `capability` of its own — it just needs "the full config for
this transport", so it passes `capability=None` to skip capability
filtering) and an async, DB-backed context (`resolve_knowledge_access`,
used by `ConfluenceProvider`) go through, without either re-implementing
transport/auto-wire logic itself. Consumers ask for a `ProviderCapability`
(`app.context_pipeline.models`, the taxonomy providers already declare
against), never for a transport or protocol by name — per the
"consumers request capabilities" principle this module exists to enforce.

`TransportSpec.capabilities` (see `app.knowledge.registry`) is the
declarative single source of truth for "which transport can satisfy which
capability" — extending it for a new source or a new capability is the
only change ever needed here; this module contains no source-specific
branching.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context_pipeline.models import ProviderCapability
from app.core.crypto import decrypt_secret
from app.knowledge.registry import Transport, get_source
from app.models.knowledge_connection import KnowledgeConnection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccessMethod:
    """One concrete way to reach a knowledge source: a transport plus the
    generic field values needed to use it — the same generic field names
    `TransportSpec.credential_field_map` declares for that transport (e.g.
    `{"base_url": ..., "email": ..., "api_token": ...}` for REST, or
    `{"server_url": ..., "api_key": ...}` for MCP). Callers that need a
    tool-specific or protocol-specific shape (a `JiraTool` config dict, an
    MCP client's `(server_url, auth_token)` pair) read `fields` themselves
    — this type intentionally knows nothing about any one caller's shape.

    `synthesized=True` marks a method derived via the REST->MCP auto-wire
    (the connection was never configured for this transport directly) —
    kept visible rather than folded away, since a caller logging or
    debugging "why is this MCP call using my REST token" benefits from the
    distinction `tools.setup` already surfaced via its own log line.
    """

    transport: Transport
    fields: dict[str, str]
    synthesized: bool = False


@dataclass(frozen=True)
class ResolvedKnowledgeAccess:
    """Every currently-available way to reach one source for one
    capability, most-preferred first (MCP before REST — matching the
    runtime fallback order `JiraTool`/the old `ConfluenceTool` already
    used), plus the connection's raw `config` for a caller that needs a
    source-specific extra not modeled generically (e.g. Confluence's MCP
    `cloudId`, which is just the connection's own `base_url` — an
    Atlassian quirk that doesn't belong in this module's vocabulary)."""

    source_type: str
    capability: ProviderCapability
    methods: tuple[AccessMethod, ...]
    config: dict[str, Any]

    @property
    def available(self) -> bool:
        return bool(self.methods)

    def preferred(self) -> AccessMethod | None:
        return self.methods[0] if self.methods else None


def derive_access_methods(
    source_type: str,
    stored_transport: str,
    capability: ProviderCapability | None,
    config: dict[str, Any],
    credentials: dict[str, Any],
) -> tuple[AccessMethod, ...]:
    """Pure, I/O-free: given one connection's stored transport and its
    config/credentials, return every way `capability` can currently be
    reached for `source_type`.

    `capability=None` returns every method regardless of what capability
    it satisfies — the Tool Registry sync's own use (a Tool Registry entry
    serves whatever capabilities its tool implements; it isn't resolving
    for one specific capability, so there's nothing to filter on).
    """
    source_spec = get_source(source_type)
    if source_spec is None:
        return ()

    merged: dict[str, Any] = {**config, **credentials}
    methods: list[AccessMethod] = []

    own_spec = next((t for t in source_spec.transports if t.transport == stored_transport), None)
    own_complete = bool(
        own_spec is not None
        and own_spec.credential_field_map
        and all(merged.get(key) for key in own_spec.credential_field_map)
    )
    if own_complete and (capability is None or capability in own_spec.capabilities):
        methods.append(
            AccessMethod(
                transport=own_spec.transport,
                fields={key: merged[key] for key in own_spec.credential_field_map},
            )
        )

    # Auto-wire: reuse a REST connection's own credential as the MCP bearer
    # token against the sibling MCP TransportSpec's known hosted endpoint —
    # only attempted once the REST connection's own fields have already
    # proven complete (`own_complete`), matching the pre-existing behavior
    # this replaces exactly (an incomplete REST connection never got
    # auto-wired either).
    if own_complete and own_spec.transport == Transport.REST and own_spec.auto_wire_credential:
        mcp_spec = next((t for t in source_spec.transports if t.transport == Transport.MCP), None)
        if (
            mcp_spec is not None
            and mcp_spec.known_mcp_endpoint
            and (capability is None or capability in mcp_spec.capabilities)
        ):
            token = merged.get(own_spec.auto_wire_credential)
            server_url_key = mcp_spec.credential_field_map.get("server_url")
            api_key_key = mcp_spec.credential_field_map.get("api_key")
            if token and server_url_key and api_key_key:
                methods.append(
                    AccessMethod(
                        transport=Transport.MCP,
                        fields={"server_url": mcp_spec.known_mcp_endpoint, "api_key": token},
                        synthesized=True,
                    )
                )

    methods.sort(key=lambda m: 0 if m.transport == Transport.MCP else 1)
    return tuple(methods)


async def resolve_knowledge_access(
    db: AsyncSession, source_type: str, capability: ProviderCapability
) -> ResolvedKnowledgeAccess:
    """The single async entrypoint for "how do I currently reach
    `source_type` for `capability`" — the one place Context Discovery (or
    any future consumer outside the Tool Registry) resolves knowledge
    access. Reads the same `KnowledgeConnection` table `app.tools.setup`'s
    sync functions do, and derives access the same way
    (`derive_access_methods`) — there is no second copy of transport/
    auto-wire logic here, only a DB read plus a call into the shared pure
    function above.

    Mirrors `resync_knowledge_connections_for_source`'s own connection
    selection (first enabled row for `source_type`) rather than
    introducing a second policy for "which connection wins when more than
    one exists" — same one-connection-drives-one-tool-instance model the
    rest of the Knowledge Connection system already assumes.
    """
    result = await db.execute(
        select(KnowledgeConnection).where(
            KnowledgeConnection.source_type == source_type,
            KnowledgeConnection.enabled.is_(True),
        )
    )
    row = result.scalars().first()
    if row is None:
        return ResolvedKnowledgeAccess(source_type, capability, (), {})

    credentials: dict[str, Any] = {}
    if row.encrypted_credentials:
        try:
            credentials = json.loads(decrypt_secret(row.encrypted_credentials))
        except Exception:
            logger.warning(
                "knowledge_access_credential_decrypt_failed source_type=%s connection_id=%s",
                source_type,
                row.id,
            )
            return ResolvedKnowledgeAccess(source_type, capability, (), row.config or {})

    methods = derive_access_methods(
        source_type, row.transport, capability, row.config or {}, credentials
    )
    return ResolvedKnowledgeAccess(source_type, capability, methods, row.config or {})
