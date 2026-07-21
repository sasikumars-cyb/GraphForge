#!/usr/bin/env bash
# First-time environment setup: env files + backend venv + frontend deps.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Setting up environment files"
[ -f "$ROOT_DIR/backend/.env" ] || cp "$ROOT_DIR/backend/.env.example" "$ROOT_DIR/backend/.env"
[ -f "$ROOT_DIR/frontend/.env.local" ] || cp "$ROOT_DIR/frontend/.env.example" "$ROOT_DIR/frontend/.env.local"

echo "==> Installing backend dependencies (Python venv)"
cd "$ROOT_DIR/backend"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]"

echo "==> Installing frontend dependencies (npm)"
cd "$ROOT_DIR/frontend"
npm install

echo "==> Done. Run scripts/dev.sh to start the stack."
