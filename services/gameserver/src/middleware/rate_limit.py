"""
Rate limiting middleware for API security
"""

import time
import asyncio
from typing import Dict, Optional, Callable
from collections import defaultdict, deque
from dataclasses import dataclass, field
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

import logging

logger = logging.getLogger(__name__)


@dataclass
class RateLimitRule:
    """Rate limiting rule configuration"""
    requests: int  # Number of requests allowed
    window: int    # Time window in seconds
    burst: int = None  # Optional burst limit (higher short-term limit)
    burst_window: int = 10  # Burst window in seconds


@dataclass 
class ClientState:
    """Track rate limit state for a client"""
    requests: deque = field(default_factory=deque)
    burst_requests: deque = field(default_factory=deque)
    last_request: float = 0
    blocked_until: float = 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware with configurable rules per endpoint"""
    
    def __init__(self, app, default_rule: RateLimitRule = None):
        super().__init__(app)
        self.default_rule = default_rule or RateLimitRule(requests=100, window=60)
        self.rules: Dict[str, RateLimitRule] = {}
        self.clients: Dict[str, ClientState] = defaultdict(ClientState)
        
        # Cleanup task to prevent memory leaks. Started lazily on the first
        # dispatch() call — at module-import time there is no running event
        # loop, so asyncio.create_task() would raise RuntimeError.
        self._cleanup_task = None

        # Rate limit configuration by endpoint pattern
        self._configure_default_rules()
    
    def _configure_default_rules(self):
        """Configure default rate limiting rules for different endpoint types"""
        # Authentication endpoints - stricter limits
        self.add_rule("/api/v1/auth/login", RateLimitRule(requests=5, window=60, burst=10, burst_window=10))
        self.add_rule("/api/v1/auth/register", RateLimitRule(requests=3, window=300))  # 3 per 5 minutes
        self.add_rule("/api/v1/auth/password", RateLimitRule(requests=3, window=300))
        
        # PayPal endpoints - moderate limits
        self.add_rule("/api/v1/paypal/", RateLimitRule(requests=10, window=60))
        
        # Admin endpoints - higher limits but monitored
        self.add_rule("/api/v1/admin/", RateLimitRule(requests=200, window=60))
        
        # Public status endpoints - higher limits
        self.add_rule("/api/v1/status", RateLimitRule(requests=30, window=60))
        
        # WebSocket upgrade - strict limits
        self.add_rule("/ws/", RateLimitRule(requests=5, window=300))  # 5 connections per 5 minutes
        
        # General API endpoints
        self.add_rule("/api/v1/", RateLimitRule(requests=60, window=60))
    
    def add_rule(self, pattern: str, rule: RateLimitRule):
        """Add a rate limiting rule for a URL pattern"""
        self.rules[pattern] = rule
        logger.info(f"Added rate limit rule for {pattern}: {rule.requests} requests per {rule.window}s")
    
    def _get_client_id(self, request: Request) -> str:
        """Get client identifier for rate limiting"""
        # Try to get user ID from request state (if authenticated)
        if hasattr(request.state, 'user_id'):
            return f"user:{request.state.user_id}"
        
        # Fall back to IP address
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # Take the first IP in case of multiple proxies
            client_ip = forwarded_for.split(",")[0].strip()
        else:
            client_ip = request.client.host if request.client else "unknown"
        
        return f"ip:{client_ip}"
    
    def _get_applicable_rule(self, path: str) -> RateLimitRule:
        """Get the most specific rate limiting rule for a path"""
        # Find the longest matching pattern
        best_match = ""
        applicable_rule = self.default_rule
        
        for pattern, rule in self.rules.items():
            if path.startswith(pattern) and len(pattern) > len(best_match):
                best_match = pattern
                applicable_rule = rule
        
        return applicable_rule
    
    def _cleanup_old_requests(self, client_state: ClientState, rule: RateLimitRule, current_time: float):
        """Remove old requests outside the time window"""
        # Clean main window
        while (client_state.requests and 
               current_time - client_state.requests[0] > rule.window):
            client_state.requests.popleft()
        
        # Clean burst window if applicable
        if rule.burst:
            while (client_state.burst_requests and 
                   current_time - client_state.burst_requests[0] > rule.burst_window):
                client_state.burst_requests.popleft()
    
    def _is_rate_limited(self, client_id: str, rule: RateLimitRule, current_time: float) -> tuple[bool, Optional[str]]:
        """Check if client is rate limited"""
        client_state = self.clients[client_id]
        
        # Check if client is currently blocked
        if current_time < client_state.blocked_until:
            return True, f"Blocked until {client_state.blocked_until - current_time:.1f}s"
        
        # Clean up old requests
        self._cleanup_old_requests(client_state, rule, current_time)
        
        # Check burst limit first (if configured)
        if rule.burst and len(client_state.burst_requests) >= rule.burst:
            # Block client for the burst window
            client_state.blocked_until = current_time + rule.burst_window
            logger.warning(f"Client {client_id} exceeded burst limit: {rule.burst} requests in {rule.burst_window}s")
            return True, f"Burst limit exceeded. Try again in {rule.burst_window}s"
        
        # Check main rate limit
        if len(client_state.requests) >= rule.requests:
            # Calculate when the client can make the next request
            oldest_request = client_state.requests[0]
            wait_time = rule.window - (current_time - oldest_request)
            logger.warning(f"Client {client_id} exceeded rate limit: {rule.requests} requests in {rule.window}s")
            return True, f"Rate limit exceeded. Try again in {wait_time:.1f}s"
        
        return False, None
    
    def _record_request(self, client_id: str, rule: RateLimitRule, current_time: float):
        """Record a request for rate limiting"""
        client_state = self.clients[client_id]
        
        # Record in main window
        client_state.requests.append(current_time)
        
        # Record in burst window if applicable
        if rule.burst:
            client_state.burst_requests.append(current_time)
        
        client_state.last_request = current_time
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Main middleware dispatch method"""
        # Lazy-start the cleanup task on first request. We are inside a
        # request here, so the event loop is guaranteed to be running.
        if self._cleanup_task is None:
            self._start_cleanup_task()

        current_time = time.time()
        client_id = self._get_client_id(request)
        path = request.url.path
        
        # Skip rate limiting for health checks and static files
        if path in ["/", "/health", "/ping"] or path.startswith("/static/"):
            return await call_next(request)
        
        # Get applicable rate limiting rule
        rule = self._get_applicable_rule(path)
        
        # Check rate limits
        is_limited, message = self._is_rate_limited(client_id, rule, current_time)
        
        if is_limited:
            logger.warning(f"Rate limit exceeded for {client_id} on {path}: {message}")
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {message}",
                headers={"Retry-After": "60"}
            )
        
        # Record the request
        self._record_request(client_id, rule, current_time)
        
        # Add rate limit headers to response
        response = await call_next(request)
        
        # Add informational headers
        client_state = self.clients[client_id]
        remaining = rule.requests - len(client_state.requests)
        reset_time = int(current_time + rule.window)
        
        response.headers["X-RateLimit-Limit"] = str(rule.requests)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        response.headers["X-RateLimit-Reset"] = str(reset_time)
        
        if rule.burst:
            burst_remaining = rule.burst - len(client_state.burst_requests)
            response.headers["X-RateLimit-Burst-Limit"] = str(rule.burst)
            response.headers["X-RateLimit-Burst-Remaining"] = str(max(0, burst_remaining))
        
        return response
    
    def _start_cleanup_task(self):
        """Start background task to clean up old client data"""
        async def cleanup_loop():
            while True:
                try:
                    await asyncio.sleep(300)  # Run every 5 minutes
                    current_time = time.time()
                    
                    # Remove clients with no recent activity (1 hour)
                    inactive_clients = [
                        client_id for client_id, state in self.clients.items()
                        if current_time - state.last_request > 3600
                    ]
                    
                    for client_id in inactive_clients:
                        del self.clients[client_id]
                    
                    if inactive_clients:
                        logger.info(f"Cleaned up {len(inactive_clients)} inactive rate limit clients")
                        
                except Exception as e:
                    logger.error(f"Error in rate limit cleanup task: {e}")
        
        # Note: In a real application, you'd want to use a proper task manager
        # This is a simplified version for demonstration
        self._cleanup_task = asyncio.create_task(cleanup_loop())
    
    def get_client_stats(self, client_id: str = None) -> Dict:
        """Get rate limiting statistics"""
        if client_id:
            client_state = self.clients.get(client_id)
            if client_state:
                return {
                    "client_id": client_id,
                    "current_requests": len(client_state.requests),
                    "burst_requests": len(client_state.burst_requests),
                    "last_request": client_state.last_request,
                    "blocked_until": client_state.blocked_until
                }
            return {"error": "Client not found"}
        
        # Return overall statistics
        return {
            "total_clients": len(self.clients),
            "active_clients": sum(1 for state in self.clients.values() 
                                 if time.time() - state.last_request < 300),
            "blocked_clients": sum(1 for state in self.clients.values() 
                                  if state.blocked_until > time.time()),
            "rules_configured": len(self.rules)
        }


# Default rate limit middleware instance
rate_limit_middleware = RateLimitMiddleware(
    app=None,  # Will be set when added to FastAPI app
    default_rule=RateLimitRule(requests=100, window=60)
)


def get_rate_limit_stats() -> Dict:
    """Get current rate limiting statistics"""
    return rate_limit_middleware.get_client_stats()