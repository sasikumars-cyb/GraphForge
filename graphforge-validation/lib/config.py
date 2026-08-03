"""Configuration for the validation framework — every value overridable
via environment variable, with defaults matching the local dev stack
(`docker-compose` on `graphforge-dev-*`, per the project's own README).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    api_base_url: str
    user_id: str
    database_url: str
    agent_run_timeout_seconds: int
    agent_poll_interval_seconds: float


def load_config() -> Config:
    return Config(
        api_base_url=os.environ.get("GRAPHFORGE_API_URL", "http://localhost:8000/api/v1"),
        # The `sasikumars-cyb` GitHub-connected user that owns every
        # repository in the validation suite. Override with
        # GRAPHFORGE_USER_ID if pointed at a different environment.
        user_id=os.environ.get("GRAPHFORGE_USER_ID", "420072cc-f0ce-4748-aa1d-c688afd8cf72"),
        database_url=os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://graphforge:graphforge@localhost:5433/graphforge",
        ),
        agent_run_timeout_seconds=int(os.environ.get("GRAPHFORGE_AGENT_TIMEOUT", "120")),
        agent_poll_interval_seconds=float(os.environ.get("GRAPHFORGE_AGENT_POLL_INTERVAL", "2")),
    )
