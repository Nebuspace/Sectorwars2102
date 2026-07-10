#!/bin/bash
set -e

# Function to wait for database connection
wait_for_db() {
    echo "Waiting for database connection..."
    max_attempts=30
    attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if python -c "
from sqlalchemy import create_engine, text
import os
import sys

# Check if DATABASE_URL is set
database_url = os.environ.get('DATABASE_URL')
if not database_url:
    print('ERROR: DATABASE_URL environment variable is not set!')
    print('This container requires DATABASE_URL to be provided by docker-compose.')
    sys.exit(1)

print(f'Using DATABASE_URL: {database_url[:50]}...')

try:
    engine = create_engine(database_url)
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    print('Database connection successful')
    sys.exit(0)
except Exception as e:
    print(f'Database connection failed: {e}')
    sys.exit(1)
"; then
            echo "Database is ready!"
            return 0
        fi
        
        echo "Attempt $attempt/$max_attempts failed. Waiting 2 seconds..."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    echo "WARNING: Could not connect to database after $max_attempts attempts. Starting server anyway..."
    return 1
}

# Function to mark the current migration in Alembic if it's not already marked
mark_migration_version() {
    local migration_version="$1"
    echo "Checking if migration $migration_version is already marked..."
    if ! alembic current | grep -q "$migration_version"; then
        echo "Migration not marked as complete. Marking it manually..."
        alembic stamp "$migration_version"
    else
        echo "Migration already marked as complete."
    fi
}

# Wait for database before proceeding
wait_for_db
DB_AVAILABLE=$?

if [ $DB_AVAILABLE -eq 0 ]; then
    # Print the current Alembic revision
    echo "Current Alembic revision:"
    alembic current || echo "Could not check current revision"

    # Apply all pending migrations
    echo "Applying pending Alembic migrations..."
    if ! alembic upgrade head; then
        echo "Migration failed but continuing with startup..."
        # Mark our migration as complete to prevent repeated failures
        mark_migration_version "b42e19a78c52" || echo "Could not mark migration version"
    fi

    # Verify the current migration version again
    echo "Current Alembic revision after migration attempt:"
    alembic current || echo "Could not check current revision"
else
    echo "Skipping database migrations due to connection issues"
fi

# Schema is owned entirely by Alembic (see `alembic upgrade head` above) --
# no ORM-metadata fallback path here anymore. A fresh/empty DB is expected to
# already be schema'd by the migration run before this point; this script no
# longer creates or patches tables/enums out-of-band.

# Start the FastAPI application
# Dev mode runs with --reload (hot-reload, single process). Production runs
# with --workers (no reload). The Dockerfile sets GAMESERVER_MODE per stage;
# default is dev so direct `start.sh` runs locally behave as they did before.
echo "Starting FastAPI application (mode=${GAMESERVER_MODE:-development})..."
if [ "${GAMESERVER_MODE}" = "production" ]; then
    exec python -m uvicorn src.main:app \
        --host 0.0.0.0 --port 8080 \
        --workers "${UVICORN_WORKERS:-4}" \
        --proxy-headers --forwarded-allow-ips='*'
else
    exec python -m uvicorn src.main:app \
        --host 0.0.0.0 --port 8080 \
        --reload \
        --proxy-headers --forwarded-allow-ips='*'
fi