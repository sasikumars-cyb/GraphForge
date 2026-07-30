#!/usr/bin/env bash
# Run this ON the EC2 demo instance (in the cloned repo directory) to pick up
# the latest branch and redeploy. See docs/deployment/demo/README.md.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

git pull
docker compose -f docker/docker-compose.ec2-demo.yml up --build -d
docker compose -f docker/docker-compose.ec2-demo.yml ps
