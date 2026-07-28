"""Registers and indexes local (no-remote) repositories with GraphForge.

For repos that only exist on the local filesystem and can't be pushed to
GitHub - see docker/docker-compose.local.yml, which bind-mounts
/home/sasikumars/git_repositories/Hackathon/repos into the backend
container at /local-repos and sets VCS_PROVIDER=local_git.

Run from `backend/` against the containerized dev stack started via
`scripts/local-up.sh`:

    uv run python scripts/seed_local.py

Talks to the API over HTTP (http://localhost:8000 by default), the same
way scripts/seed_demo.py does.
"""

import asyncio
import os

import httpx

API_BASE_URL = os.environ.get("DEMO_API_BASE_URL", "http://localhost:8000/api/v1")
LOCAL_EMAIL = os.environ.get("LOCAL_SEED_EMAIL", "local@graphforge.example.com")
LOCAL_PASSWORD = os.environ.get("LOCAL_SEED_PASSWORD", "correct-horse-battery-staple")  # noqa: S105
LOCAL_REPOSITORIES_CONTAINER_ROOT = "/local-repos"

REPOSITORIES = [
    {"provider_repo_id": "local-ds-databricks-avangrid-em-ct-dataingest", "name": "ds-databricks-avangrid-em-ct-dataingest"},
    {"provider_repo_id": "local-ds-databricks-pseg-nj-dataingest", "name": "ds-databricks-pseg-nj-dataingest"},
    {"provider_repo_id": "local-ds-databricks-soco-apc-c2m-rcs-dataingest", "name": "ds-databricks-soco-apc-c2m-rcs-dataingest"},
    {"provider_repo_id": "local-ds-databricks-soco-gpc-c2m-rcs-dataingest", "name": "ds-databricks-soco-gpc-c2m-rcs-dataingest"},
    {"provider_repo_id": "local-up-databricks-shared-jobs", "name": "up-databricks-shared-jobs"},
]


async def get_or_create_token(client: httpx.AsyncClient) -> str:
    register_response = await client.post(
        "/auth/register",
        json={"email": LOCAL_EMAIL, "password": LOCAL_PASSWORD, "full_name": "Local User"},
    )
    if register_response.status_code not in (200, 201, 409):
        register_response.raise_for_status()

    login_response = await client.post(
        "/auth/login", json={"email": LOCAL_EMAIL, "password": LOCAL_PASSWORD}
    )
    login_response.raise_for_status()
    return str(login_response.json()["access_token"])


async def track_repositories(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    """Returns {repo_name: repository_id}."""
    response = await client.post(
        "/repositories",
        headers=headers,
        json={
            "repositories": [
                {
                    "provider_repo_id": repo["provider_repo_id"],
                    "owner": "local",
                    "name": repo["name"],
                    "full_name": f"local/{repo['name']}",
                    "private": False,
                    "default_branch": "main",
                    "html_url": f"{LOCAL_REPOSITORIES_CONTAINER_ROOT}/{repo['name']}",
                }
                for repo in REPOSITORIES
            ]
        },
    )
    response.raise_for_status()
    return {item["name"]: item["id"] for item in response.json()}


async def index_and_wait(
    client: httpx.AsyncClient, headers: dict[str, str], repository_id: str, name: str
) -> None:
    trigger = await client.post(f"/repositories/{repository_id}/index", headers=headers)
    if trigger.status_code == 409:
        print(f"  {name}: indexing already in progress, waiting for it instead")
    else:
        trigger.raise_for_status()

    for _ in range(120):
        status_response = await client.get(f"/repositories/{repository_id}/index", headers=headers)
        status_response.raise_for_status()
        job = status_response.json()
        if job["status"] == "completed":
            summary = job.get("result_summary") or {}
            print(f"  {name}: indexed ({summary})")
            return
        if job["status"] == "failed":
            raise RuntimeError(f"Indexing failed for {name}: {job.get('error_message')}")
        await asyncio.sleep(2)

    raise RuntimeError(f"Indexing did not complete for {name} within 240s")


async def main() -> None:
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
        token = await get_or_create_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        print("Tracking repositories...")
        repo_ids = await track_repositories(client, headers)

        print("Indexing repositories...")
        for repo in REPOSITORIES:
            await index_and_wait(client, headers, repo_ids[repo["name"]], repo["name"])

        print("\nDone. Sign in to the UI as:")
        print(f"  email:    {LOCAL_EMAIL}")
        print(f"  password: {LOCAL_PASSWORD}")
        print("\nRepositories:")
        for repo in REPOSITORIES:
            print(f"  {repo['name']}: /repositories/{repo_ids[repo['name']]}")


if __name__ == "__main__":
    asyncio.run(main())
