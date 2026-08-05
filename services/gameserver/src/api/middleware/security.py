#!/usr/bin/env python3
"""
Security Middleware for Sectorwars2102
Implements OWASP security headers and comprehensive protection measures
"""

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import logging
import time
import hashlib
import secrets
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Admin REST rate-limit tiers — canon OPERATIONS/admin-ui.md § Admin REST
# rate limits (Read 100/min · Write 30/min · Bulk 10/min · Reports 5/hr).
# Constants only — no ORM. Unwired duplicate middleware/rate_limit.py stays dead.
# ---------------------------------------------------------------------------
ADMIN_TIER_READ = "read"
ADMIN_TIER_WRITE = "write"
ADMIN_TIER_BULK = "bulk"
ADMIN_TIER_REPORTS = "reports"

# (limit, window_seconds)
ADMIN_TIER_LIMITS: dict[str, Tuple[int, int]] = {
    ADMIN_TIER_READ: (100, 60),
    ADMIN_TIER_WRITE: (30, 60),
    ADMIN_TIER_BULK: (10, 60),
    ADMIN_TIER_REPORTS: (5, 3600),
}

# Path substrings that classify as reports/exports (any method).
_ADMIN_REPORT_MARKERS = ("/report", "/reports", "/export", "/exports")
# Path substrings that classify as bulk (mutating methods only).
_ADMIN_BULK_MARKERS = ("/bulk", "/batch", "/mass")


def classify_admin_tier(method: str, path: str) -> Optional[str]:
    """Classify an admin request into a canon rate-limit tier.

    Returns None if ``path`` is not an admin API path.

    Classifier rules (documented):
    1. Path must contain ``/admin`` (covers ``/api/v1/admin/*`` and legacy
       ``/admin/*``).
    2. Reports — path contains ``/report(s)`` or ``/export(s)`` → reports
       (5/hour), regardless of method.
    3. Bulk — mutating method (POST/PUT/PATCH/DELETE) AND path contains
       ``/bulk``, ``/batch``, or ``/mass`` → bulk (10/min).
    4. Write — any other mutating method → write (30/min).
    5. Read — GET/HEAD/OPTIONS → read (100/min).
    """
    if "/admin" not in path:
        return None

    path_lower = path.lower()
    method_upper = method.upper()

    if any(marker in path_lower for marker in _ADMIN_REPORT_MARKERS):
        return ADMIN_TIER_REPORTS

    is_mutating = method_upper in ("POST", "PUT", "PATCH", "DELETE")
    if is_mutating and any(marker in path_lower for marker in _ADMIN_BULK_MARKERS):
        return ADMIN_TIER_BULK

    if is_mutating:
        return ADMIN_TIER_WRITE

    return ADMIN_TIER_READ


def peek_request_user_id(request: Request) -> Optional[str]:
    """Best-effort user id for rate-limit keying.

    Prefers ``request.state.user_id`` (set by admin auth dependency after the
    route runs — useful when present). For the *current* request, middleware
    runs before dependencies, so we also lightly decode a Bearer JWT ``sub``
    when present. Decode failures fall through to IP keying — never raise.
    """
    state_uid = getattr(request.state, "user_id", None)
    if state_uid is not None:
        return str(state_uid)

    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        from src.auth.jwt import decode_token

        payload = decode_token(token)
        sub = payload.get("sub")
        return str(sub) if sub else None
    except Exception:
        return None


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Comprehensive security middleware implementing OWASP recommendations
    """
    
    def __init__(self, app, strict_transport_security_max_age: int = 31536000):
        super().__init__(app)
        self.strict_transport_security_max_age = strict_transport_security_max_age
        self.nonce_cache = {}  # For CSP nonces
        
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate CSP nonce for this request
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        
        # Process the request
        response = await call_next(request)
        
        # Add comprehensive security headers
        self._add_security_headers(response, request, nonce)
        
        return response
    
    def _add_security_headers(self, response: Response, request: Request, nonce: str) -> None:
        """Add all OWASP-recommended security headers"""
        
        # 1. Content Security Policy (CSP) - Prevents XSS attacks
        csp_directives = [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            "style-src 'self' 'unsafe-inline'",  # Allow inline styles for now
            "img-src 'self' data: https:",
            "font-src 'self'",
            "connect-src 'self' wss: https:",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "upgrade-insecure-requests"
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)
        
        # 2. X-Content-Type-Options - Prevents MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # 3. X-Frame-Options - Prevents clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        
        # 4. X-XSS-Protection - Legacy XSS protection for older browsers
        response.headers["X-XSS-Protection"] = "1; mode=block"
        
        # 5. Referrer-Policy - Controls referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # 6. Permissions-Policy (formerly Feature-Policy) - Controls browser features
        permissions = [
            "accelerometer=()",
            "camera=()",
            "geolocation=()",
            "gyroscope=()",
            "magnetometer=()",
            "microphone=()",
            "payment=()",
            "usb=()"
        ]
        response.headers["Permissions-Policy"] = ", ".join(permissions)
        
        # 7. Strict-Transport-Security - Forces HTTPS (only for production)
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                f"max-age={self.strict_transport_security_max_age}; "
                "includeSubDomains; preload"
            )
        
        # 8. Cache-Control for sensitive endpoints
        if "/admin" in str(request.url) or "/api/auth" in str(request.url):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        
        # 9. Remove sensitive headers
        headers_to_remove = ["Server", "X-Powered-By"]
        for header in headers_to_remove:
            if header in response.headers:
                del response.headers[header]


class RateLimitingMiddleware(BaseHTTPMiddleware):
    """
    Enhanced rate limiting middleware with different limits per endpoint type.

    Admin ``/admin`` paths use canon per-admin tiers (see ``classify_admin_tier``
    + ``ADMIN_TIER_LIMITS``). Non-admin paths keep the legacy per-IP buckets.
    """

    def __init__(
        self,
        app,
        default_requests_per_minute: int = 60,
        auth_requests_per_minute: int = 10,
        admin_requests_per_minute: int = 120,
    ):
        super().__init__(app)
        self.default_limit = default_requests_per_minute
        self.auth_limit = auth_requests_per_minute
        # Legacy flat admin_limit retained for constructor compat; unused when
        # tier classification succeeds (all /admin paths classify).
        self.admin_limit = admin_requests_per_minute
        self.request_counts: dict[str, int] = {}
        self.cleanup_interval = 300  # Cleanup old entries every 5 minutes
        self.last_cleanup = time.time()

    def _client_ip(self, request: Request) -> str:
        if request.client and request.client.host:
            return request.client.host
        return "unknown"

    def _legacy_client_id(self, request: Request) -> str:
        user_agent = request.headers.get("User-Agent", "")
        client_ip = self._client_ip(request)
        return hashlib.sha256(f"{client_ip}:{user_agent}".encode()).hexdigest()

    def _admin_bucket_key(
        self, request: Request, tier: str, window_start: int
    ) -> str:
        """Per-admin key when identity is known; else per-IP fallback.

        Two admins behind one IP get independent budgets when each presents
        a distinct Bearer ``sub`` / ``request.state.user_id``.
        """
        user_id = peek_request_user_id(request)
        if user_id:
            identity = f"admin:{user_id}"
        else:
            identity = f"admin-ip:{self._client_ip(request)}"
        return f"{identity}:{tier}:{window_start}"

    def _check_and_increment(
        self, key: str, limit: int
    ) -> Tuple[bool, int]:
        """Increment the bucket. Returns (allowed, remaining_after)."""
        count = self.request_counts.get(key, 0)
        if count >= limit:
            return False, 0
        self.request_counts[key] = count + 1
        return True, max(0, limit - (count + 1))

    def _rate_limit_response(
        self, limit: int, window: int, window_start: int, tier: Optional[str] = None
    ) -> JSONResponse:
        retry_after = max(1, int(window_start + window - time.time()))
        content = {
            "error": "Rate limit exceeded",
            "message": "Too many requests. Please try again later.",
            "retry_after": retry_after,
        }
        if tier:
            content["tier"] = tier
        return JSONResponse(
            status_code=429,
            content=content,
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(window_start + window),
            },
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for non-admin testclient traffic (legacy).
        # Admin tier tests must exercise real enforcement — do not skip /admin.
        user_agent = request.headers.get("User-Agent", "")
        path = str(request.url.path)
        if "testclient" in user_agent and "/admin" not in path:
            return await call_next(request)

        current_time = time.time()
        if current_time - self.last_cleanup > self.cleanup_interval:
            self._cleanup_old_entries(current_time)
            self.last_cleanup = current_time

        admin_tier = classify_admin_tier(request.method, path)
        if admin_tier is not None:
            limit, window = ADMIN_TIER_LIMITS[admin_tier]
            window_start = int(current_time / window) * window
            key = self._admin_bucket_key(request, admin_tier, window_start)
            allowed, remaining = self._check_and_increment(key, limit)
            if not allowed:
                logger.warning(
                    "Admin rate limit exceeded tier=%s path=%s key=%s",
                    admin_tier,
                    path,
                    key.split(":")[0],
                )
                return self._rate_limit_response(limit, window, window_start, admin_tier)
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(window_start + window)
            response.headers["X-RateLimit-Tier"] = admin_tier
            return response

        # Legacy non-admin buckets (auth / default)
        client_id = self._legacy_client_id(request)
        if "/auth" in path:
            limit = self.auth_limit
            window = 60
        else:
            limit = self.default_limit
            window = 60

        window_start = int(current_time / window) * window
        key = f"{client_id}:{window_start}:{path}"

        allowed, remaining = self._check_and_increment(key, limit)
        if not allowed:
            logger.warning(
                "Rate limit exceeded for %s on %s", self._client_ip(request), path
            )
            return self._rate_limit_response(limit, window, window_start)

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(window_start + window)
        return response

    def _cleanup_old_entries(self, current_time: float) -> None:
        """Remove old entries from request counts.

        Retain up to ~2 hours so the reports (5/hr) window is not wiped
        mid-bucket by the legacy 2-minute cutoff.
        """
        cutoff_time = current_time - 7200
        keys_to_remove = []

        for key in self.request_counts:
            # Keys end with …:{window_start} — take the last numeric segment.
            parts = key.rsplit(":", 1)
            if len(parts) == 2:
                try:
                    timestamp = int(parts[1])
                    if timestamp < cutoff_time:
                        keys_to_remove.append(key)
                except ValueError:
                    continue

        for key in keys_to_remove:
            del self.request_counts[key]

        if keys_to_remove:
            logger.info("Cleaned up %s old rate limit entries", len(keys_to_remove))


class InputValidationMiddleware(BaseHTTPMiddleware):
    """
    Input validation middleware to prevent injection attacks
    """
    
    # Dangerous patterns that might indicate injection attempts
    DANGEROUS_PATTERNS = [
        # SQL Injection patterns
        "';", '";', "--", "/*", "*/", "xp_", "sp_", "exec", "execute",
        "select", "insert", "update", "delete", "drop", "union", "having",
        
        # NoSQL Injection patterns  
        "$where", "$ne", "$gt", "$lt", "$regex", "$exists",
        
        # Command Injection patterns
        ";", "|", "&", "`", "$(", "${", "&&", "||",
        
        # Path Traversal patterns
        "../", "..\\", "%2e%2e", "%252e%252e",
        
        # XSS patterns (though React should handle most)
        "<script", "</script", "javascript:", "onerror=", "onload=",
        "onclick=", "onmouseover=", "<iframe", "<object", "<embed"
    ]
    
    def __init__(self, app):
        super().__init__(app)
        
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip validation for GET requests and file uploads
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return await call_next(request)
        
        # Check query parameters
        for param_name, param_value in request.query_params.items():
            if self._contains_dangerous_pattern(str(param_value)):
                logger.warning(
                    f"Dangerous pattern detected in query parameter '{param_name}' "
                    f"from {request.client.host}"
                )
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "Invalid input",
                        "message": "Request contains potentially dangerous content"
                    }
                )
        
        # For POST/PUT/PATCH requests, we'll validate in the routes themselves
        # as we need to parse the body which can only be done once
        
        return await call_next(request)
    
    def _contains_dangerous_pattern(self, value: str) -> bool:
        """Check if value contains any dangerous patterns"""
        value_lower = value.lower()
        return any(pattern in value_lower for pattern in self.DANGEROUS_PATTERNS)


class AuditLoggingMiddleware(BaseHTTPMiddleware):
    """
    Audit logging middleware for security-relevant events
    """
    
    # Endpoints that should be audit logged
    AUDIT_ENDPOINTS = [
        "/auth/login", "/auth/logout", "/auth/register",
        "/admin", "/api/admin",
        "/messages/send", "/messages/moderate",
        "/economy/intervention",
        "/ships/emergency",
        "/combat/intervene"
    ]
    
    def __init__(self, app):
        super().__init__(app)
        
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Check if this endpoint should be audit logged
        should_audit = any(
            endpoint in str(request.url.path) 
            for endpoint in self.AUDIT_ENDPOINTS
        )
        
        if should_audit:
            start_time = time.time()
            
            # Capture request details
            audit_entry = {
                "timestamp": start_time,
                "method": request.method,
                "path": str(request.url.path),
                "client_ip": request.client.host,
                "user_agent": request.headers.get("User-Agent", ""),
                "query_params": dict(request.query_params)
            }
            
            # Process request
            response = await call_next(request)
            
            # Add response details
            end_time = time.time()
            audit_entry.update({
                "status_code": response.status_code,
                "duration_ms": round((end_time - start_time) * 1000, 2),
                "user_id": getattr(request.state, "user_id", None)
            })
            
            # Log the audit entry
            logger.info(f"AUDIT: {audit_entry}")
            
            # Write to database if available
            try:
                from src.core.database import get_db
                from src.services.audit_service import AuditService

                # Get database session (sync — audit writer is sync code)
                db = next(get_db())
                
                # Prepare data for database
                query_params = audit_entry.get("query_params")
                
                # Extract action and resource type
                action = AuditService.extract_action_from_path(audit_entry["path"])
                resource_type = AuditService.extract_resource_type_from_path(audit_entry["path"])
                
                # Create audit log in database
                await AuditService.create_audit_log(
                    db=db,
                    method=audit_entry["method"],
                    path=audit_entry["path"],
                    client_ip=audit_entry["client_ip"],
                    user_agent=audit_entry["user_agent"],
                    user_id=audit_entry.get("user_id"),
                    user_type=getattr(request.state, "user_type", None),
                    action=action,
                    resource_type=resource_type,
                    status_code=audit_entry["status_code"],
                    duration_ms=audit_entry["duration_ms"],
                    query_params=query_params if query_params else None,
                    security_flags=getattr(request.state, "security_flags", None),
                    violation_detected=getattr(request.state, "violation_detected", None)
                )
                
                db.close()
                
            except Exception as e:
                logger.error(f"Failed to write audit log to database: {str(e)}")
                # Don't fail the request if audit logging fails
            
            return response
        
        return await call_next(request)


def setup_security_middleware(app):
    """
    Configure all security middleware for the application
    """
    # Order matters - apply in reverse order of desired execution
    app.add_middleware(AuditLoggingMiddleware)
    app.add_middleware(InputValidationMiddleware)
    app.add_middleware(RateLimitingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    
    logger.info("Security middleware configured successfully")