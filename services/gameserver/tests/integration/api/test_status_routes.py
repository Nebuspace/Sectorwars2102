"""
Tests for the status endpoints in the API.
These are the utility endpoints available in /api/v1/status/*
"""
from fastapi.testclient import TestClient

from src.core.config import settings


def test_status_health_endpoint(client: TestClient):
    """Test the health check endpoint."""
    response = client.get(f"{settings.API_V1_STR}/status/health")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "status" in data
    assert data["status"] == "healthy"
    assert "service" in data
    assert data["service"] == "gameserver"


def test_status_root_endpoint(client: TestClient):
    """Test the status root endpoint."""
    response = client.get(f"{settings.API_V1_STR}/status")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "message" in data
    assert "Game API Server is operational" in data["message"]
    assert "environment" in data
    assert "status" in data
    assert data["status"] == "healthy"


def test_status_version_endpoint(client: TestClient):
    """Test the version endpoint."""
    response = client.get(f"{settings.API_V1_STR}/status/version")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "version" in data
    # Version should follow semantic versioning (x.y.z)
    assert len(data["version"].split(".")) == 3


def test_status_ping_endpoint(client: TestClient):
    """Test the ping endpoint."""
    response = client.get(f"{settings.API_V1_STR}/status/ping")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "ping" in data
    assert data["ping"] == "pong"
    assert "timestamp" in data


def test_main_root_endpoint(client: TestClient):
    """Test the root endpoint (/)."""
    response = client.get("/")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "message" in data
    assert "Hello from Sector Wars 2102 Game API!" in data["message"]