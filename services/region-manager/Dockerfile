# Multi-stage build. Two named targets:
#   * dev        — pip install + reload-on-edit, source mount expected from compose
#   * production — same image but no source mount, runs uvicorn with workers
#
# Pick the target via docker compose `build.target: dev|production` or
# `docker build --target=production`.

# ---------------------------------------------------------------------------
# base — system deps + python deps (cached across stages)
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS base

WORKDIR /app

# docker.io provides the Docker CLI used by region_provisioner to `docker run`
# regional gameserver containers. curl is for the healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# ---------------------------------------------------------------------------
# dev — source mount expected from compose; uvicorn --reload
# ---------------------------------------------------------------------------
FROM base AS dev

ENV REGION_MANAGER_MODE=development

# No COPY of src/ — compose volume-mount overlays /app at runtime.
RUN mkdir -p /app/logs

CMD ["python", "-m", "uvicorn", "src.main:app", \
     "--host", "0.0.0.0", "--port", "8080", "--reload", \
     "--proxy-headers", "--forwarded-allow-ips=*"]

# ---------------------------------------------------------------------------
# production — source baked in, workers, no reload
# ---------------------------------------------------------------------------
FROM base AS production

ENV REGION_MANAGER_MODE=production
ENV UVICORN_WORKERS=2

COPY src/ ./src/
COPY templates/ ./templates/
RUN mkdir -p /app/logs

CMD ["sh", "-c", \
     "python -m uvicorn src.main:app \
          --host 0.0.0.0 --port 8080 \
          --workers ${UVICORN_WORKERS:-2} \
          --proxy-headers --forwarded-allow-ips=*"]
