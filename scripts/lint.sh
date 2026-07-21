#!/usr/bin/env bash
# Lints and format-checks both services. Exits non-zero if anything fails.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Backend: ruff"
(cd "$ROOT_DIR/backend" && source .venv/bin/activate && ruff check .)

echo "==> Backend: black --check"
(cd "$ROOT_DIR/backend" && source .venv/bin/activate && black --check .)

echo "==> Backend: mypy"
(cd "$ROOT_DIR/backend" && source .venv/bin/activate && mypy app)

echo "==> Frontend: oxlint"
(cd "$ROOT_DIR/frontend" && npm run lint)

echo "==> Frontend: prettier --check"
(cd "$ROOT_DIR/frontend" && npm run format:check)

echo "==> All lint checks passed"
