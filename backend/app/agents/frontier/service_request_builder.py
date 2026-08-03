"""`ServiceRequestBuilder` — the Protocol every Frontier agent's
`build_service_requests` hook satisfies. Not a class agents subclass (a
bound method already satisfies it); this module exists so the constraint
is enforced by a type, not just a docstring convention.

The constraint is structural, not just documentation: implementations
take `AgentContext` and return `list[ServiceCall]` — plain dataclasses
(`app.agents.frontier.service_executor`). There is no `AsyncSession` or
`IGraphRepository` parameter available to reach for, so a
`build_service_requests` implementation cannot query a database or
traverse a graph even by accident; it can only decide what to ask for.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.agents._contract import AgentContext
from app.agents.frontier.service_executor import ServiceCall


@runtime_checkable
class ServiceRequestBuilder(Protocol):
    def __call__(self, context: AgentContext) -> list[ServiceCall]: ...
