"""
This module provides a utility to override settings for testing.
"""
import os
import pytest

def load_env_file():
    """Load environment variables from .env file for test utilities"""
    env_paths = [
        '/workspaces/Sectorwars2102/.env',  # Host path
        '../../../.env',  # Relative from tests directory
        '../../../../.env',  # Alternative relative path
        '.env',  # Current directory
        '.env.test',  # Test-specific env file
    ]
    
    for env_path in env_paths:
        if os.path.exists(env_path):
            print(f"📁 Test utils loading environment from: {env_path}")
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        # Remove inline comments
                        if '#' in value:
                            value = value.split('#')[0].strip()
                        # Don't override existing environment variables
                        if key not in os.environ:
                            os.environ[key] = value
            return True
    
    print("⚠️ Test utils: No .env file found in expected locations")
    return False

# Load environment variables when this module is imported
load_env_file()


@pytest.fixture(scope="function", autouse=True)
def mock_settings_env():
    """
    Fixture to mock environment variables required for tests.
    Preserves .env file variables while adding test-specific overrides.
    """
    # Save original environment
    original_env = os.environ.copy()
    
    # Load .env file variables first (this will preserve production settings for integration tests)
    load_env_file()
    
    # Get the current DATABASE_URL to decide test strategy
    current_database_url = os.environ.get('DATABASE_URL')
    
    # Only override DATABASE_URL if it's not from production .env or if explicitly testing
    test_env_overrides = {
        "SECRET_KEY": "testsecretkey",
        "ENVIRONMENT": "test",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "adminpassword",
        "ADMIN_EMAIL": "admin@example.com",
    }
    
    # Only set fallback values for missing environment variables
    fallback_env = {
        "GITHUB_CLIENT_ID": "mock_github_client_id",
        "GITHUB_CLIENT_SECRET": "mock_github_client_secret",
        "GOOGLE_CLIENT_ID": "mock_google_client_id",
        "GOOGLE_CLIENT_SECRET": "mock_google_client_secret",
        "STEAM_API_KEY": "mock_steam_api_key",
        "FRONTEND_URL": "http://localhost:3000",
    }
    
    # Apply test overrides
    for key, value in test_env_overrides.items():
        os.environ[key] = value
    
    # Apply fallbacks only for missing variables
    for key, value in fallback_env.items():
        if key not in os.environ:
            os.environ[key] = value
    
    # Decide on DATABASE_URL strategy - always prefer PostgreSQL from .env
    if current_database_url and 'postgresql' in current_database_url:
        print(f"✅ Test utils using PostgreSQL database: {current_database_url[:50]}...")
        # Keep the production DATABASE_URL for all tests
    else:
        print("⚠️ Test utils: No PostgreSQL DATABASE_URL found!")
        if current_database_url:
            print(f"Current DATABASE_URL: {current_database_url}")
        print("❌ Using SQLite fallback - tests may not be accurate!")
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    
    yield
    
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)
