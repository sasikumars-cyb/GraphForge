# --- Dev stage: used by docker/docker-compose.yml for local development ---
# Source is bind-mounted over /app at runtime, so edits on the host are
# picked up immediately by uvicorn's --reload. The COPY here only exists so
# `pip install -e` has something to build metadata against at image-build
# time; the code that actually runs is whatever the bind mount provides.
FROM python:3.12-slim AS dev

# git: the indexer (app.indexer) shells out to it to clone repositories for
# architecture scanning.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker-entrypoint.sh /entrypoint.sh

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[dev]" \
    && chmod +x /entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- Build stage: resolve dependencies into a venv for the production image ---
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# --- Runtime stage: slim image with only the built venv + app code ---
FROM python:3.12-slim AS runtime

# git: same reason as the dev stage above (app.indexer clones repositories).
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
