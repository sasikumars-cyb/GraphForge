"""Unit tests for the typed `AgentContext.extras` accessors — pure,
no I/O beyond constructing plain objects."""

from __future__ import annotations

import uuid

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.frontier.agent_context import (
    get_db,
    get_graph_repository,
    get_repository_id,
    get_stage,
    get_user_id,
    get_workflow,
)
from app.core.exceptions import NotFoundError


def _context(**extras: object) -> AgentContext:
    return AgentContext(
        subject=Subject(
            subject_id="repo:11111111-1111-1111-1111-111111111111", subject_type="repository"
        ),
        goal="analyze_repository_understanding",
        extras=extras,
    )


def test_get_db_returns_injected_session() -> None:
    sentinel = object()
    context = _context(db=sentinel)
    assert get_db(context) is sentinel


def test_get_user_id_returns_none_when_absent() -> None:
    context = _context(db=object())
    assert get_user_id(context) is None


def test_get_graph_repository_returns_none_when_manifest_declares_no_neo4j() -> None:
    context = _context(db=object())
    assert get_graph_repository(context) is None


def test_get_graph_repository_returns_injected_repository() -> None:
    sentinel = object()
    context = _context(db=object(), graph_repository=sentinel)
    assert get_graph_repository(context) is sentinel


def test_get_repository_id_parses_subject_id() -> None:
    context = _context(db=object())
    assert get_repository_id(context) == uuid.UUID("11111111-1111-1111-1111-111111111111")


def test_get_repository_id_rejects_non_repository_subject() -> None:
    context = AgentContext(
        subject=Subject(subject_id="pr:123", subject_type="pull_request"),
        goal="review_pr",
        extras={"db": object()},
    )
    with pytest.raises(NotFoundError):
        get_repository_id(context)


def test_get_workflow_returns_none_for_standalone_run() -> None:
    context = _context(db=object())
    assert get_workflow(context) is None


def test_get_workflow_returns_injected_workflow() -> None:
    sentinel = object()
    context = _context(db=object(), workflow=sentinel)
    assert get_workflow(context) is sentinel


def test_get_stage_falls_back_to_default_when_no_workflow_stage() -> None:
    context = _context(db=object())
    assert get_stage(context, "repository_understanding") == "repository_understanding"
