"""Tests for request correlation: contextvars, the ASGI middleware, and the
logging filter that injects them into every log record.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from httpx import AsyncClient

from app.core.logging import RequestContextFilter
from app.core.request_context import (
    clear_context,
    current_context,
    request_id_var,
    set_request_id,
    set_user_id,
    set_workflow_context,
    user_id_var,
    workflow_id_var,
    workflow_run_id_var,
)


@pytest.fixture(autouse=True)
def _clear_context_around_test() -> None:
    """These tests set contextvars directly (not through a request), so
    make sure nothing set by one test can leak into the next regardless
    of test ordering."""
    clear_context()
    yield
    clear_context()


# ---------------------------------------------------------------------------
# contextvars plumbing
# ---------------------------------------------------------------------------


def test_current_context_omits_unset_fields_rather_than_inventing_them() -> None:
    assert current_context() == {}

    set_request_id("req-1")
    assert current_context() == {"request_id": "req-1"}


def test_set_workflow_context_only_sets_provided_fields() -> None:
    set_workflow_context(workflow_id="wf-1")
    assert current_context() == {"workflow_id": "wf-1"}

    set_workflow_context(workflow_run_id="run-1")
    assert current_context() == {"workflow_id": "wf-1", "workflow_run_id": "run-1"}


def test_clear_context_resets_every_field() -> None:
    set_request_id("req-1")
    set_workflow_context(workflow_id="wf-1", workflow_run_id="run-1")
    set_user_id("user-1")
    assert current_context() == {
        "request_id": "req-1",
        "workflow_id": "wf-1",
        "workflow_run_id": "run-1",
        "user_id": "user-1",
    }

    clear_context()

    assert current_context() == {}
    assert request_id_var.get() is None
    assert workflow_id_var.get() is None
    assert workflow_run_id_var.get() is None
    assert user_id_var.get() is None


# ---------------------------------------------------------------------------
# Nested async execution / background task propagation
# ---------------------------------------------------------------------------


async def test_asyncio_task_inherits_context_set_before_creation() -> None:
    """This is the exact mechanism `schedule_run_execution` /
    `schedule_title_generation` rely on: set the context, then
    asyncio.create_task — no manual passing of ids required."""
    set_request_id("req-parent")
    set_workflow_context(workflow_id="wf-parent", workflow_run_id="run-parent")

    seen: dict[str, str] = {}

    async def _child() -> None:
        seen.update(current_context())

    task = asyncio.create_task(_child())
    await task

    assert seen == {
        "request_id": "req-parent",
        "workflow_id": "wf-parent",
        "workflow_run_id": "run-parent",
    }


async def test_child_task_context_changes_do_not_leak_back_to_parent() -> None:
    """A Task copies the context at creation time — it does not share it.
    Mutating context inside the child must not be visible to the parent
    once the child returns, proving there's no cross-task leakage."""
    set_request_id("req-parent")

    async def _child() -> None:
        set_workflow_context(workflow_id="wf-child-only")
        set_request_id("req-child-only")

    await asyncio.create_task(_child())

    assert current_context() == {"request_id": "req-parent"}


async def test_two_concurrent_tasks_keep_independent_contexts() -> None:
    """Two runs scheduled back-to-back must not see each other's ids —
    this is the concurrency guarantee the correlation feature exists for."""

    async def _run(run_id: str) -> str:
        set_workflow_context(workflow_run_id=run_id)
        await asyncio.sleep(0)  # yield, so the two interleave
        return current_context()["workflow_run_id"]

    results = await asyncio.gather(
        asyncio.create_task(_run("run-a")),
        asyncio.create_task(_run("run-b")),
    )

    assert set(results) == {"run-a", "run-b"}


# ---------------------------------------------------------------------------
# Logging filter
# ---------------------------------------------------------------------------


def _make_record() -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )


def test_logging_filter_defaults_to_dash_outside_any_context() -> None:
    record = _make_record()
    assert RequestContextFilter().filter(record) is True
    assert record.request_id == "-"
    assert record.workflow_id == "-"
    assert record.workflow_run_id == "-"
    assert record.user_id == "-"


def test_logging_filter_injects_whatever_is_currently_set() -> None:
    set_request_id("req-9")
    set_workflow_context(workflow_id="wf-9", workflow_run_id="run-9")
    set_user_id("user-9")

    record = _make_record()
    RequestContextFilter().filter(record)

    assert record.request_id == "req-9"
    assert record.workflow_id == "wf-9"
    assert record.workflow_run_id == "run-9"
    assert record.user_id == "user-9"


def test_existing_log_calls_are_unaffected_by_the_filter(caplog: pytest.LogCaptureFixture) -> None:
    """A plain, unmodified `logger.info("plain message")` call must keep
    working — the filter only adds attributes, it never changes the
    message or requires call sites to pass extra fields."""
    logger = logging.getLogger("test_request_context_plain")
    logger.addFilter(RequestContextFilter())
    with caplog.at_level(logging.INFO, logger="test_request_context_plain"):
        logger.info("plain message")

    assert "plain message" in caplog.text


# ---------------------------------------------------------------------------
# Middleware, via a real ASGI request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_generates_a_request_id_when_none_supplied(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    request_id = response.headers.get("x-request-id")
    assert request_id
    # A valid uuid4 hex string with dashes — 36 chars.
    assert len(request_id) == 36


@pytest.mark.asyncio
async def test_middleware_preserves_a_caller_supplied_request_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "caller-supplied-id"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "caller-supplied-id"


@pytest.mark.asyncio
async def test_concurrent_requests_receive_different_request_ids(client: AsyncClient) -> None:
    responses = await asyncio.gather(
        client.get("/api/v1/health"),
        client.get("/api/v1/health"),
        client.get("/api/v1/health"),
    )

    ids = {r.headers["x-request-id"] for r in responses}
    assert len(ids) == 3


@pytest.mark.asyncio
async def test_context_does_not_leak_between_sequential_requests(client: AsyncClient) -> None:
    """The ASGI test client runs every request on the same asyncio task,
    unlike a real server which hands each connection its own task — so
    this is the scenario that would surface a leak if `clear_context()`
    weren't called on both entry and exit of the middleware."""
    first = await client.get("/api/v1/health")
    second = await client.get("/api/v1/health")

    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    # And after both requests have fully completed, no request is "in
    # flight" from this test's own perspective — context is clean.
    assert current_context() == {}
