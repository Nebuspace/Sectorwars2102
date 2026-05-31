FROM python:3.11

# Bang sidecar image version pin. Propagated to runtime via ENV so
# bang_import_service.py can read os.environ['BANG_VERSION']. Overridable
# via docker-compose build args; CI smoke-tests the pinned tag is pullable.
ARG BANG_VERSION=1.3.0
ENV BANG_VERSION=${BANG_VERSION}

WORKDIR /app

# Install Docker CLI (for bang sidecar `docker run` invocations) + tini
# (PID 1 signal forwarding so bang subprocesses are reaped cleanly on
# `docker stop`).
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

# Install Poetry
RUN pip install poetry

# Configure Poetry: Don't create virtual environment, install dependencies globally
RUN poetry config virtualenvs.create false

# Copy dependency files first for better caching
COPY pyproject.toml poetry.lock* /app/

# Install dependencies (generate lock if missing, but don't update existing)
RUN poetry lock --no-update 2>/dev/null || poetry lock && poetry install --no-root

# Copy the rest of the project
COPY . /app/

# Add executable permission to the scripts
RUN chmod +x /app/start.sh

# tini as PID 1 so signals reach Python and bang sidecar subprocesses
# are reaped (no zombie processes when generation jobs are cancelled).
ENTRYPOINT ["/usr/bin/tini", "--"]

# Keep the container running with the start script; echo BANG_VERSION at
# boot for log-grep-able version provenance.
CMD ["sh", "-c", "echo \"Starting gameserver (BANG_VERSION=${BANG_VERSION})...\" && /app/start.sh"]
