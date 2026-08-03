"""`ServiceRequestBuilder` is a structural Protocol — any callable with the
right signature satisfies it, including a bound method (how a
`BaseFrontierAgent` subclass's `build_service_requests` will be used)."""

from __future__ import annotations

from app.agents._contract import AgentContext, Subject
from app.agents.frontier.service_executor import RepositoryProfileCall
from app.agents.frontier.service_request_builder import ServiceRequestBuilder


class _Agent:
    def build_service_requests(self, context: AgentContext) -> list[RepositoryProfileCall]:
        return []


def test_bound_method_satisfies_the_protocol() -> None:
    agent = _Agent()
    assert isinstance(agent.build_service_requests, ServiceRequestBuilder)


def test_plain_function_satisfies_the_protocol() -> None:
    def build(context: AgentContext) -> list[RepositoryProfileCall]:
        return []

    assert isinstance(build, ServiceRequestBuilder)


def test_builder_returns_calls_without_touching_context_extras() -> None:
    context = AgentContext(
        subject=Subject(subject_id="repo:x", subject_type="repository"), goal="g", extras={}
    )
    agent = _Agent()
    assert agent.build_service_requests(context) == []
