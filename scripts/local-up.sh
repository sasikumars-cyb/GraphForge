#!/usr/bin/env bash
# Starts the containerized dev stack with local (no-remote) repos enabled -
# the backend talks to /home/sasikumars/git_repositories/Hackathon/repos
# instead of real GitHub. See docker/docker-compose.local.yml.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker compose \
  -f "$ROOT_DIR/docker/docker-compose.yml" \
  -f "$ROOT_DIR/docker/docker-compose.local.yml" \
  up --build
