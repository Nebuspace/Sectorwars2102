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

# Directly create tables from models if they don't exist
echo "Ensuring all required tables exist..."
python -c "
from src.core.database import Base, engine
from sqlalchemy import inspect, text
from src.models import *

inspector = inspect(engine)
required_tables = ['players', 'ships', 'teams', 'reputations', 'team_reputations', 
                   'planets', 'ports', 'player_planets', 'player_ports', 'ship_specifications']

# Check first if PostgreSQL enums exist and create them if not
with engine.connect() as conn:
    # Create enums if they don't exist
    for enum_name in ['ship_type', 'failure_type', 'upgrade_type', 'insurance_type', 'reputation_level']:
        result = conn.execute(text(\"\"\"
            SELECT 1 FROM pg_type WHERE typname = :enum_name
        \"\"\").bindparams(enum_name=enum_name))
        if not result.scalar():
            print(f'Enum {enum_name} does not exist, creating manually...')
            if enum_name == 'ship_type':
                conn.execute(text(\"\"\"
                    CREATE TYPE ship_type AS ENUM (
                        'LIGHT_FREIGHTER', 'CARGO_HAULER', 'FAST_COURIER', 'SCOUT_SHIP', 
                        'COLONY_SHIP', 'DEFENDER', 'CARRIER', 'WARP_JUMPER'
                    )
                \"\"\"))
            elif enum_name == 'failure_type':
                conn.execute(text(\"\"\"
                    CREATE TYPE failure_type AS ENUM (
                        'NONE', 'MINOR', 'MAJOR', 'CATASTROPHIC'
                    )
                \"\"\"))
            elif enum_name == 'upgrade_type':
                conn.execute(text(\"\"\"
                    CREATE TYPE upgrade_type AS ENUM (
                        'ENGINE', 'CARGO_HOLD', 'SHIELD', 'HULL', 'SENSOR', 
                        'DRONE_BAY', 'GENESIS_CONTAINMENT', 'MAINTENANCE_SYSTEM'
                    )
                \"\"\"))
            elif enum_name == 'insurance_type':
                conn.execute(text(\"\"\"
                    CREATE TYPE insurance_type AS ENUM (
                        'NONE', 'BASIC', 'STANDARD', 'PREMIUM'
                    )
                \"\"\"))
            elif enum_name == 'reputation_level':
                conn.execute(text(\"\"\"
                    CREATE TYPE reputation_level AS ENUM (
                        'PUBLIC_ENEMY', 'CRIMINAL', 'OUTLAW', 'PIRATE', 'SMUGGLER', 
                        'UNTRUSTWORTHY', 'SUSPICIOUS', 'QUESTIONABLE', 'NEUTRAL',
                        'RECOGNIZED', 'ACKNOWLEDGED', 'TRUSTED', 'RESPECTED', 
                        'VALUED', 'HONORED', 'REVERED', 'EXALTED'
                    )
                \"\"\"))
        else:
            print(f'Enum {enum_name} already exists')

# Check if tables exist and create them if they don't
existing_tables = inspector.get_table_names()
missing_tables = [table for table in required_tables if table not in existing_tables]

if missing_tables:
    print(f'Creating missing tables: {missing_tables}')
    # Create all tables
    Base.metadata.create_all(engine, checkfirst=True)
    print('Tables created successfully')
else:
    print('All required tables already exist')
"

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