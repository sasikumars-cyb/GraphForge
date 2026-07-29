"""Request-scoped correlation context, carried via `contextvars`.

Every field here is populated only where the id already exists naturally
(the request handler, `get_current_user`, a workflow/run row just
committed) — nothing is invented. `RequestIDMiddleware` (see
`app.core.request_id_middleware`) is the only thing that *generates* an
id (`request_id`, when the caller didn't supply one); everything else
just threads an id that already exists into the logging filter.

Values set here are visible to any `asyncio.create_task()` spawned from
the same call stack, because a Task copies its creator's `contextvars`
context at creation time (see `app.orchestrator.background_execution`,
which relies on exactly this to correlate a fire-and-forget run's logs
with the request that scheduled it).
"""

from __future__ import annotations

import contextvars

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
workflow_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workflow_id", default=None
)
workflow_run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workflow_run_id", default=None
)
user_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_id", default=None)

_ALL_VARS = (request_id_var, workflow_id_var, workflow_run_id_var, user_id_var)


def set_request_id(request_id: str) -> None:
    request_id_var.set(request_id)


def set_workflow_context(
    *, workflow_id: str | None = None, workflow_run_id: str | None = None
) -> None:
    """Attach whichever of workflow_id/workflow_run_id is already known.

    Called from the workflow/run routers right before handing work to
    `schedule_run_execution`/`schedule_title_generation`, so the
    background task's copied context already carries it.
    """
    if workflow_id is not None:
        workflow_id_var.set(workflow_id)
    if workflow_run_id is not None:
        workflow_run_id_var.set(workflow_run_id)


def set_user_id(user_id: str) -> None:
    user_id_var.set(user_id)


def current_context() -> dict[str, str]:
    """Snapshot of whichever fields are set right now. Omits unset fields
    rather than inventing placeholders — the logging filter fills those
    in with "-" for display."""
    ctx = {
        "request_id": request_id_var.get(),
        "workflow_id": workflow_id_var.get(),
        "workflow_run_id": workflow_run_id_var.get(),
        "user_id": user_id_var.get(),
    }
    return {k: v for k, v in ctx.items() if v is not None}


def clear_context() -> None:
    """Hard-reset every field to unset.

    Called by `RequestIDMiddleware` both before and after each request.
    Deliberately a hard `.set(None)` on all four vars rather than a
    token-based `.reset()` of only the one var the middleware itself
    set: downstream code (`get_current_user`, the workflow routers) sets
    workflow_id/workflow_run_id/user_id directly, with no reset of its
    own, so the middleware boundary is what has to guarantee nothing
    survives past the response — otherwise a test client that reuses one
    asyncio task across sequential requests (unlike a real server, which
    hands each connection its own task) would leak one request's ids
    into the next.
    """
    for var in _ALL_VARS:
        var.set(None)
