"""Logging configuration.

Kept deliberately simple (stdlib `logging`, not structlog) until real request
volume justifies structured/JSON logging. Request correlation (see
`app.core.request_context`) is added the same way — a `logging.Filter`
that injects context onto every record, rather than a new logging
framework — so none of the existing `logger.info(...)` call sites across
the codebase need to change.
"""

import logging
import sys

from app.core.config import get_settings
from app.core.request_context import current_context

_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | req=%(request_id)s wf=%(workflow_id)s "
    "run=%(workflow_run_id)s user=%(user_id)s | %(name)s | %(message)s"
)


class RequestContextFilter(logging.Filter):
    """Injects request/workflow/run/user correlation ids onto every log
    record. Unset fields render as "-" so the format string above never
    hits a `KeyError` for a log emitted outside any request (e.g. the
    startup lifespan hook)."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = current_context()
        record.request_id = ctx.get("request_id", "-")
        record.workflow_id = ctx.get("workflow_id", "-")
        record.workflow_run_id = ctx.get("workflow_run_id", "-")
        record.user_id = ctx.get("user_id", "-")
        return True


def configure_logging() -> None:
    settings = get_settings()
    level = logging.DEBUG if settings.debug else logging.INFO

    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
        force=True,
    )
    context_filter = RequestContextFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(context_filter)
