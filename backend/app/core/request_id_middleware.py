"""Assigns every HTTP request a correlation id.

Pure ASGI middleware (not Starlette's `BaseHTTPMiddleware`, which runs
the downstream app in a separate task via anyio) — this runs directly on
the request's own task, which is what guarantees the `request_id` set
here is present in the `contextvars` context copied by any
`asyncio.create_task()` a route handler spawns downstream (e.g.
`schedule_run_execution`/`schedule_title_generation` in
`app.orchestrator.background_execution`).
"""

from __future__ import annotations

import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.request_context import clear_context, set_request_id


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = MutableHeaders(scope=scope).get("x-request-id")
        request_id = incoming.strip() if incoming and incoming.strip() else str(uuid.uuid4())

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(raw=message.setdefault("headers", []))["X-Request-ID"] = request_id
            await send(message)

        # Cleared on the way in too: a request handled on a task that
        # previously ran a different request (the ASGI test client reuses
        # one task per test) must not inherit that request's ids.
        clear_context()
        set_request_id(request_id)
        try:
            await self.app(scope, receive, send_with_header)
        finally:
            clear_context()
