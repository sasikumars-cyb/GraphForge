#!/usr/bin/env sh
# Applies pending migrations before starting the server, in both the dev and
# runtime images. Single instance, hackathon-scoped project - no concern yet
# about multiple replicas racing to migrate at once.
set -e

alembic upgrade head
exec "$@"
