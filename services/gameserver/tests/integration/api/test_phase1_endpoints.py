#!/usr/bin/env python3
"""
Test script to verify Phase 1 endpoints are working
Run this to ensure the gameserver is providing the expected APIs
"""

import requests

BASE_URL = "http://localhost:8080/api/v1"

def test_status():
    """Test that the API is running"""
    print("Testing API status...")
    response = requests.get(f"{BASE_URL}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    print("✅ API status: OK")

def test_security_headers():
    """Test that security headers are present"""
    print("\nTesting security headers...")
    response = requests.get(f"{BASE_URL}/status")
    
    required_headers = [
        "X-Content-Type-Options",
        "X-Frame-Options", 
        "X-XSS-Protection",
        "Content-Security-Policy",
        "X-RateLimit-Limit"
    ]
    
    for header in required_headers:
        assert header in response.headers, f"Missing header: {header}"
        print(f"✅ {header}: {response.headers[header][:50]}...")

def test_audit_endpoints():
    """Test audit endpoints (requires admin auth)"""
    print("\nTesting audit endpoints...")
    
    # Should require authentication
    response = requests.get(f"{BASE_URL}/admin/audit/logs")
    assert response.status_code in [401, 403]
    print("✅ Audit logs endpoint requires auth")

def test_message_endpoints():
    """Test message endpoints (requires player auth)"""
    print("\nTesting message endpoints...")
    
    # Should require authentication
    response = requests.get(f"{BASE_URL}/messages/inbox")
    assert response.status_code in [401, 403]
    print("✅ Message inbox endpoint requires auth")
    
    response = requests.post(f"{BASE_URL}/messages/send", json={
        "content": "test",
        "recipient_id": "00000000-0000-0000-0000-000000000000"
    })
    assert response.status_code in [401, 403]
    print("✅ Send message endpoint requires auth")

def test_admin_message_endpoints():
    """Test admin message moderation endpoints"""
    print("\nTesting admin message endpoints...")
    
    response = requests.get(f"{BASE_URL}/admin/messages/all")
    assert response.status_code in [401, 403]
    print("✅ Admin messages endpoint requires auth")

def test_rate_limiting():
    """Test that rate limiting is working"""
    print("\nTesting rate limiting...")
    
    # Make multiple requests
    for i in range(5):
        response = requests.get(f"{BASE_URL}/status")
        limit = int(response.headers.get("X-RateLimit-Limit", 0))
        remaining = int(response.headers.get("X-RateLimit-Remaining", 0))
        
        if i == 0:
            print(f"Rate limit: {limit} requests per minute")
        
        assert limit > 0
        assert remaining >= 0
        assert remaining <= limit
    
    print("✅ Rate limiting is active")

def main():
    print("=== Testing Sectorwars2102 Gameserver Phase 1 Endpoints ===\n")
    
    try:
        test_status()
        test_security_headers()
        test_audit_endpoints()
        test_message_endpoints()
        test_admin_message_endpoints()
        test_rate_limiting()
        
        print("\n✅ All tests passed! Phase 1 endpoints are working correctly.")
        print("\nEndpoints available:")
        print("- Security: OWASP headers, rate limiting, input validation")
        print("- Audit: /api/v1/admin/audit/* (requires admin auth)")
        print("- Messages: /api/v1/messages/* (requires player auth)")
        print("- Admin Messages: /api/v1/admin/messages/* (requires admin auth)")
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1
    except requests.exceptions.ConnectionError:
        print("\n❌ Could not connect to gameserver. Is it running?")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())