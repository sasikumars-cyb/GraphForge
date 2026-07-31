#!/usr/bin/env sh
# Applies pending migrations before starting the server, in both the dev and
# runtime images. Single instance, hackathon-scoped project - no concern yet
# about multiple replicas racing to migrate at once.
set -e

# AWS_PROFILE set to the empty string (e.g. docker-compose.yml's
# ${AWS_PROFILE:-} substitution when docker/.env doesn't define it) is NOT
# equivalent to "unset" for boto3/botocore: unlike AWS_ACCESS_KEY_ID (which
# botocore's EnvProvider treats "" as absent and falls through on),
# AWS_PROFILE="" is read as an explicit profile named "" and raises
# ProfileNotFound instead of falling through to the next credential source
# (IAM role, etc). Unset it here so an empty AWS_PROFILE behaves like no
# AWS_PROFILE at all - verified this crashes boto3.client() calls otherwise.
if [ -z "${AWS_PROFILE:-}" ]; then
    unset AWS_PROFILE
fi

alembic upgrade head
exec "$@"
