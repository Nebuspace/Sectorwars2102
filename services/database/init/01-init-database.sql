-- SectorWars 2102 Database Initialization Script
-- This script creates the main database, users, and sets up permissions
-- Executed automatically when the PostgreSQL container starts

-- Create the main application database if it doesn't exist
-- Note: POSTGRES_DB creates the database automatically, but this ensures it exists
SELECT 'CREATE DATABASE sectorwars_dev'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sectorwars_dev');

-- Connect to the main database
\c sectorwars_dev;

-- Create application user if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_user WHERE usename = 'sectorwars_app') THEN
        -- Create application user with limited privileges
        CREATE USER sectorwars_app WITH 
            ENCRYPTED PASSWORD 'sectorwars_app_password_123'
            CREATEDB
            NOSUPERUSER
            NOCREATEROLE;
        
        -- Grant necessary privileges to application user
        GRANT CONNECT ON DATABASE sectorwars_dev TO sectorwars_app;
        GRANT CREATE ON DATABASE sectorwars_dev TO sectorwars_app;
        GRANT USAGE ON SCHEMA public TO sectorwars_app;
        GRANT CREATE ON SCHEMA public TO sectorwars_app;
        
        RAISE NOTICE 'Created application user: sectorwars_app';
    ELSE
        RAISE NOTICE 'Application user sectorwars_app already exists';
    END IF;
END
$$;

-- Create read-only user for analytics and reporting
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_user WHERE usename = 'sectorwars_readonly') THEN
        -- Create read-only user
        CREATE USER sectorwars_readonly WITH 
            ENCRYPTED PASSWORD 'sectorwars_readonly_password_123'
            NOCREATEDB
            NOSUPERUSER
            NOCREATEROLE;
        
        -- Grant read-only privileges
        GRANT CONNECT ON DATABASE sectorwars_dev TO sectorwars_readonly;
        GRANT USAGE ON SCHEMA public TO sectorwars_readonly;
        GRANT SELECT ON ALL TABLES IN SCHEMA public TO sectorwars_readonly;
        
        -- Grant read permissions on future tables
        ALTER DEFAULT PRIVILEGES IN SCHEMA public 
            GRANT SELECT ON TABLES TO sectorwars_readonly;
        
        RAISE NOTICE 'Created read-only user: sectorwars_readonly';
    ELSE
        RAISE NOTICE 'Read-only user sectorwars_readonly already exists';
    END IF;
END
$$;

-- Create essential extensions
-- UUID extension for generating UUIDs
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
COMMENT ON EXTENSION "uuid-ossp" IS 'UUID generation functions';

-- Crypto extension for password hashing and encryption
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
COMMENT ON EXTENSION "pgcrypto" IS 'Cryptographic functions for password hashing';

-- Create custom functions and utilities
-- Function to generate secure random passwords
CREATE OR REPLACE FUNCTION generate_random_password(length INTEGER DEFAULT 12)
RETURNS TEXT AS $$
BEGIN
    RETURN array_to_string(
        ARRAY(
            SELECT chr(
                CASE WHEN random() < 0.5 
                     THEN ascii('0') + floor(random() * 10)::int
                     ELSE ascii('a') + floor(random() * 26)::int
                END
            )
            FROM generate_series(1, length)
        ), 
        ''
    );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION generate_random_password(INTEGER) IS 'Generate a random password of specified length';

-- Function to get current database version
CREATE OR REPLACE FUNCTION get_database_version()
RETURNS TABLE(
    database_name TEXT,
    postgresql_version TEXT,
    created_at TIMESTAMP WITH TIME ZONE
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        current_database()::TEXT as database_name,
        version()::TEXT as postgresql_version,
        NOW() as created_at;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_database_version() IS 'Get current database and PostgreSQL version information';

-- Create database metadata table
CREATE TABLE IF NOT EXISTS _database_metadata (
    id SERIAL PRIMARY KEY,
    key VARCHAR(100) UNIQUE NOT NULL,
    value TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

COMMENT ON TABLE _database_metadata IS 'Store database metadata and configuration';

-- Insert initial metadata
INSERT INTO _database_metadata (key, value) VALUES 
    ('database_name', 'sectorwars_dev'),
    ('initialized_at', NOW()::TEXT),
    ('version', '1.0.0'),
    ('environment', 'development')
ON CONFLICT (key) DO NOTHING;

-- Create updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION update_updated_at_column() IS 'Automatically update updated_at columns';

-- Apply updated_at trigger to metadata table
CREATE TRIGGER update_database_metadata_updated_at
    BEFORE UPDATE ON _database_metadata
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Set proper permissions for all users on the metadata table
GRANT SELECT ON _database_metadata TO sectorwars_app, sectorwars_readonly;
GRANT INSERT, UPDATE, DELETE ON _database_metadata TO sectorwars_app;
GRANT USAGE ON SEQUENCE _database_metadata_id_seq TO sectorwars_app;

-- Log successful initialization
INSERT INTO _database_metadata (key, value) VALUES 
    ('last_initialization', NOW()::TEXT)
ON CONFLICT (key) DO UPDATE SET 
    value = EXCLUDED.value,
    updated_at = NOW();

-- Display initialization summary
SELECT 
    'Database initialization completed successfully' as status,
    current_database() as database,
    current_user as current_user,
    NOW() as timestamp;

-- Display user summary
-- FIXED, WO-QTI-MIGRATION-CHAIN-FRESH: pg_user has no usecreaterole column
-- on PG15 (that flag is rolcreaterole on pg_roles, not exposed via the
-- pg_user view) -- the old query raised "column usecreaterole does not
-- exist" and aborted this script mid-file under ON_ERROR_STOP=1, so
-- 02-create-users.sql and 03-seed-data.sql never ran. pg_roles carries the
-- same informational fields (username/superuser/can-create-db) plus the
-- create-role flag this summary intends to show, so the query now reads
-- from pg_roles rather than dropping the column.
SELECT
    rolname as username,
    rolsuper as is_superuser,
    rolcreatedb as can_create_db,
    rolcreaterole as can_create_role
FROM pg_roles
WHERE rolname IN ('postgres', 'sectorwars_app', 'sectorwars_readonly')
ORDER BY rolname;