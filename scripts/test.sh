#!/usr/bin/env bash
# Runs backend and frontend test suites.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Backend: pytest"
(cd "$ROOT_DIR/backend" && source .venv/bin/activate && pytest)

echo "==> Frontend: vitest"
(cd "$ROOT_DIR/frontend" && npx vitest run)

echo "==> All tests passed"
