"""Logging configuration.

Kept deliberately simple (stdlib `logging`, not structlog) until real request
volume justifies structured/JSON logging.
"""

import logging
import sys

from app.core.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = logging.DEBUG if settings.debug else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
        force=True,
    )
