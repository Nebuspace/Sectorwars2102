# Multi-stage build. Two named targets:
#   * dev        — Poetry installs all groups, container expects source volume
#                  mount + uvicorn --reload (compose dev profile uses this)
#   * production — Poetry installs without dev group, source baked into image,
#                  uvicorn runs with workers + no reload (CI/published image)
#
# Pick the target via docker compose `build.target: dev|production` or
# `docker build --target=production`.

# ---------------------------------------------------------------------------
# base — everything both stages need (system deps, Docker CLI, tini, Poetry)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

# Bang sidecar image version pin. Propagated to runtime via ENV so
# bang_import_service.py can read os.environ['BANG_VERSION']. Overridable
# via docker-compose build args; CI smoke-tests the pinned tag is pullable.
ARG BANG_VERSION=1.3.0
ENV BANG_VERSION=${BANG_VERSION}

WORKDIR /app

# Docker CLI is required at runtime — gameserver spawns bang sidecar
# containers via `docker run` (translator path). tini is PID 1 so signals
# reach Python and bang subprocesses are reaped cleanly on `docker stop`.
RUN apt-get update && apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    tini \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry
RUN poetry config virtualenvs.create false

# Copy manifests separately for layer-cache hits on most rebuilds.
COPY pyproject.toml poetry.lock* /app/

ENTRYPOINT ["/usr/bin/tini", "--"]

# ---------------------------------------------------------------------------
# dev — full dependency set (test/lint tools), source mounted at runtime
# ---------------------------------------------------------------------------
FROM base AS dev

ENV GAMESERVER_MODE=development

# Install all groups (main + dev). Source isn't COPYed — compose volume-mounts
# /app from the host so edits hot-reload via uvicorn --reload.
RUN poetry lock --no-update 2>/dev/null || poetry lock && poetry install --no-root

# In dev, start.sh is expected on the mounted volume. Keep CMD pointing at it.
CMD ["sh", "-c", "echo \"Starting gameserver (mode=dev, BANG_VERSION=${BANG_VERSION})...\" && /app/start.sh"]

# ---------------------------------------------------------------------------
# production — main deps only, source baked in, no source mount
# ---------------------------------------------------------------------------
FROM base AS production

ENV GAMESERVER_MODE=production
# Worker count for uvicorn; can be overridden at `docker run` time.
ENV UVICORN_WORKERS=4

# main group only — drops black/pytest/ruff/mypy/etc.
RUN poetry lock --no-update 2>/dev/null || poetry lock && poetry install --no-root --without dev

# Bake source into the image.
COPY . /app/
RUN chmod +x /app/start.sh

CMD ["sh", "-c", "echo \"Starting gameserver (mode=production, BANG_VERSION=${BANG_VERSION}, workers=${UVICORN_WORKERS})...\" && /app/start.sh"]
