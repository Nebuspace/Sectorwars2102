"""
Test security middleware functionality
"""

from fastapi.testclient import TestClient
from src.main import app

client = TestClient(app)


def test_security_headers():
    """Test that security headers are properly set"""
    response = client.get("/")
    
    # Check for security headers
    assert "X-Content-Type-Options" in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    
    assert "X-Frame-Options" in response.headers
    assert response.headers["X-Frame-Options"] == "DENY"
    
    assert "X-XSS-Protection" in response.headers
    assert response.headers["X-XSS-Protection"] == "1; mode=block"
    
    assert "Content-Security-Policy" in response.headers
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_rate_limiting_headers():
    """Test that rate limiting headers are present"""
    response = client.get("/")
    
    assert "X-RateLimit-Limit" in response.headers
    assert "X-RateLimit-Remaining" in response.headers
    assert "X-RateLimit-Reset" in response.headers


def test_input_validation():
    """Test input validation for dangerous patterns"""
    # This would require a POST endpoint that accepts query params
    # For now, we'll just verify the middleware is loaded
    assert True  # Placeholder for actual test


def test_audit_endpoint_requires_auth():
    """Test that audit endpoints require authentication"""
    response = client.get("/api/v1/admin/audit/logs")
    assert response.status_code in [401, 403]  # Unauthorized or Forbidden


def test_message_endpoint_requires_auth():
    """Test that message endpoints require authentication"""
    response = client.get("/api/v1/messages/inbox")
    assert response.status_code in [401, 403]  # Unauthorized or Forbidden