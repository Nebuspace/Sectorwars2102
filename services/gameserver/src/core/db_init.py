import logging
import os
import time
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from alembic.config import Config
from alembic import command

from src.core.config import settings
from src.core.database import engine, Base

logger = logging.getLogger(__name__)

def get_alembic_config():
    """Get Alembic configuration."""
    # Path to alembic.ini relative to the project root
    alembic_ini_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'alembic.ini')
    
    # Try absolute path as fallback if the relative path doesn't work
    if not os.path.exists(alembic_ini_path):
        logger.warning(f"Alembic config not found at {alembic_ini_path}, trying absolute paths")
        # Try common absolute paths
        possible_paths = [
            '/app/alembic.ini',  # Docker path
            os.path.join(os.getcwd(), 'alembic.ini'),  # Current working directory
            os.path.join(os.getcwd(), 'services/gameserver/alembic.ini')  # From project root
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                alembic_ini_path = path
                logger.info(f"Found alembic.ini at {alembic_ini_path}")
                break
    
    if not os.path.exists(alembic_ini_path):
        # Log error but return a default config as last resort
        logger.error(f"Alembic config file not found at {alembic_ini_path}")
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", "alembic")
    else:
        alembic_cfg = Config(alembic_ini_path)
    
    # Get database URL with fallbacks
    try:
        db_url = str(settings.get_db_url())
    except Exception as e:
        logger.error(f"Error getting database URL from settings: {str(e)}")
        # Use environment variable directly as fallback
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            logger.error("No DATABASE_URL found in environment")
    
    # Set the database URL - this overrides what's in alembic.ini
    if db_url:
        logger.info("Setting Alembic database URL (hidden for security)")
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    else:
        logger.error("Cannot set database URL for Alembic")
    
    return alembic_cfg

def does_db_exist():
    """Check if the database tables exist by trying to query multiple key tables."""
    try:
        inspector = inspect(engine)
        required_tables = ["users", "admin_credentials", "oauth_accounts", "refresh_tokens"]
        
        tables_status = {}
        for table in required_tables:
            exists = inspector.has_table(table)
            tables_status[table] = exists
            logger.info(f"Table check: {table} exists: {exists}")
        
        # Return True only if ALL required tables exist
        all_tables_exist = all(tables_status.values())
        logger.info(f"All required tables exist: {all_tables_exist}")
        return all_tables_exist
    except OperationalError as e:
        logger.error(f"Error connecting to database: {e}")
        return False
    except SQLAlchemyError as e:
        logger.error(f"Database error when checking table existence: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error when checking database: {e}")
        return False

def run_migrations():
    """Run Alembic migrations to latest version."""
    try:
        logger.info("Running database migrations...")
        alembic_cfg = get_alembic_config()
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations completed successfully")
        return True
    except Exception as e:
        logger.error(f"Error running migrations: {e}")
        return False

def create_tables_directly():
    """Create all tables directly using SQLAlchemy models."""
    try:
        logger.info("Creating database tables directly using SQLAlchemy models...")
        
        # Import all models to ensure they're registered with Base.metadata
        
        # Create all tables
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
        
        # Verify tables were created
        if does_db_exist():
            logger.info("Verified all required tables now exist")
            return True
        else:
            logger.error("Failed to verify table creation")
            return False
            
    except Exception as e:
        logger.error(f"Error creating tables directly: {e}")
        return False

def initialize_database():
    """Initialize the database if needed by checking for tables and running migrations."""
    try:
        if not does_db_exist():
            logger.info("Database tables do not exist. Trying migrations first...")
            migration_success = run_migrations()
            
            # Add a short delay after running migrations to ensure they've been applied
            # This helps with potential race conditions in PostgreSQL
            logger.info("Waiting for migrations to complete and be visible...")
            time.sleep(2)
            
            if migration_success and does_db_exist():
                logger.info("Database initialized successfully through migrations")
                return True
            
            # If migrations failed or didn't create all tables, try direct creation
            if not migration_success or not does_db_exist():
                logger.warning("Migrations did not complete successfully, trying direct table creation...")
                if create_tables_directly():
                    logger.info("Database initialized successfully through direct creation")
                    return True
                else:
                    logger.error("Failed to initialize database through both methods")
                    return False
        else:
            logger.info("Database tables already exist")
            return True
    except Exception as e:
        logger.error(f"Error during database initialization: {e}")
        return False 