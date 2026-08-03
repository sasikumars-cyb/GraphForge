"""Thin async HTTP client over GraphForge's own REST API. Every method
here is a direct, unmodified call to an existing endpoint — see each
method's docstring for which router it calls. No response is
reinterpreted beyond parsing JSON; comparison logic lives entirely in
`scripts/compare_*.py`, never here.

Authentication mints a normal login access token via GraphForge's own
`app.core.security.create_access_token` — the same function
`app/api/v1/routers/auth.py` uses after a real GitHub OAuth callback.
This framework runs against a trusted local/CI instance it also has
database access to (see `lib/memory.py`), so minting a token this way
avoids needing a browser-based OAuth flow in an automated regression
run; it reuses GraphForge's own signing function rather than
reimplementing JWT issuance.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx

from lib.bootstrap import ensure_backend_importable
from lib.config import Config

ensure_backend_importable()

from app.core.security import create_access_token  # noqa: E402


class AgentRunTimeoutError(RuntimeError):
    pass


class GraphForgeClient:
    def __init__(self, config: Config) -> None:
        self._config = config
        token = create_access_token(subject=config.user_id)
        self._client = httpx.AsyncClient(
            base_url=config.api_base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "GraphForgeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- Repositories (app/api/v1/routers/repositories.py) --------------

    async def list_repositories(self) -> list[dict[str, Any]]:
        """`GET /repositories`."""
        resp = await self._client.get("/repositories")
        resp.raise_for_status()
        return resp.json()

    async def get_repository_name_maps(self) -> tuple[dict[str, str], dict[str, str]]:
        """`(name -> id, id -> name)` for every tracked repository, keyed
        by the short repo name (the part after the GitHub owner/) — every
        fixture in `validation/*.yaml` refers to repos by that short name."""
        repos = await self.list_repositories()
        name_to_id: dict[str, str] = {}
        id_to_name: dict[str, str] = {}
        for repo in repos:
            short_name = repo["full_name"].split("/")[-1]
            name_to_id[short_name] = repo["id"]
            id_to_name[repo["id"]] = short_name
        return name_to_id, id_to_name

    async def get_repository_graph(self, repository_id: str) -> dict[str, Any]:
        """`GET /repositories/{id}/graph` — full node/edge set for one repo."""
        resp = await self._client.get(f"/repositories/{repository_id}/graph")
        resp.raise_for_status()
        return resp.json()

    async def get_cross_repository_edges(self) -> list[dict[str, Any]]:
        """`GET /repositories/cross-repository-edges` — every
        CALLS_SERVICE/SHARES_TOPIC/DEPENDS_ON_REPOSITORY edge across all
        of this user's tracked repositories."""
        resp = await self._client.get("/repositories/cross-repository-edges")
        resp.raise_for_status()
        return resp.json()

    async def get_cross_repository_links(self) -> list[dict[str, Any]]:
        """`GET /repositories/cross-repository-links` — Kafka topic
        producer/consumer overlap, component-level."""
        resp = await self._client.get("/repositories/cross-repository-links")
        resp.raise_for_status()
        return resp.json()

    async def get_latest_indexing_job(self, repository_id: str) -> dict[str, Any] | None:
        """`GET /repositories/{id}/index`. Returns None if no job has ever run."""
        resp = await self._client.get(f"/repositories/{repository_id}/index")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    # -- Parity (app/api/v1/routers/parity.py) ---------------------------

    async def get_parity_report(self, repository_id: str) -> dict[str, Any]:
        """`GET /repositories/{id}/parity` — live legacy-vs-materialized
        graph comparison, computed fresh on every call."""
        resp = await self._client.get(f"/repositories/{repository_id}/parity")
        resp.raise_for_status()
        return resp.json()

    # -- Agent runs (app/api/v1/routers/agent_runs.py) -------------------

    async def create_run(
        self, subject_reference: str, goal: str, model: str | None = None
    ) -> dict[str, Any]:
        """`POST /agent-runs` — schedules an agent run; returns immediately
        with `run_id` and `status` (the run itself executes in a background
        task, same as indexing)."""
        body: dict[str, Any] = {"subject_reference": subject_reference, "goal": goal}
        if model:
            body["model"] = model
        resp = await self._client.post("/agent-runs", json=body)
        resp.raise_for_status()
        return resp.json()

    async def get_run(self, run_id: str) -> dict[str, Any]:
        """`GET /agent-runs/{run_id}`."""
        resp = await self._client.get(f"/agent-runs/{run_id}")
        resp.raise_for_status()
        return resp.json()

    async def run_agent_and_wait(
        self, subject_reference: str, goal: str, model: str | None = None
    ) -> dict[str, Any]:
        """Creates a run and polls `GET /agent-runs/{run_id}` until it
        reaches a terminal status (completed/failed/partial) or the
        configured timeout elapses. Returns the final `RunDetailResponse`
        dict — callers read `steps[0].result` for the agent's output."""
        created = await self.create_run(subject_reference, goal, model)
        run_id = created["run_id"]
        deadline = time.monotonic() + self._config.agent_run_timeout_seconds
        terminal = {"completed", "failed", "partial", "cancelled"}
        while True:
            run = await self.get_run(run_id)
            if run["status"] in terminal:
                return run
            if time.monotonic() > deadline:
                raise AgentRunTimeoutError(
                    f"Agent run {run_id} ({goal} on {subject_reference}) did not reach a "
                    f"terminal status within {self._config.agent_run_timeout_seconds}s "
                    f"(last status: {run['status']!r})"
                )
            await asyncio.sleep(self._config.agent_poll_interval_seconds)


def repo_subject_reference(repository_id: str) -> str:
    """The `subject_reference` format `POST /agent-runs` expects for a
    repository-scoped goal — see `app/api/v1/routers/agent_runs.py`'s
    `_resolve_repository_subject`."""
    uuid.UUID(repository_id)  # fail fast on a malformed id
    return f"repo:{repository_id}"
