#!/usr/bin/env bash
# Starts the containerized dev stack with a host directory of local (no
# GitHub remote) repositories exposed for tracking/indexing - the backend
# sees LOCAL_REPOS_HOST_PATH (set in docker/.env) at /local-repos. See
# docker/docker-compose.local-repos.yml.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${LOCAL_REPOS_HOST_PATH:-}" ] && ! grep -q "^LOCAL_REPOS_HOST_PATH=" "$ROOT_DIR/docker/.env" 2>/dev/null; then
  echo "LOCAL_REPOS_HOST_PATH is not set. Add it to docker/.env, e.g.:" >&2
  echo "  LOCAL_REPOS_HOST_PATH=/path/to/your/local/repos" >&2
  exit 1
fi

docker compose \
  -f "$ROOT_DIR/docker/docker-compose.yml" \
  -f "$ROOT_DIR/docker/docker-compose.local-repos.yml" \
  up --build
