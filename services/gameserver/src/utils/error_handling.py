"""
Secure error handling utilities that prevent information disclosure
"""

import logging
import traceback
import uuid
from typing import Any, Dict, Optional, Union
from datetime import datetime
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from pydantic import ValidationError

logger = logging.getLogger(__name__)


class SecurityError(HTTPException):
    """Security-related error that should be logged but not expose details"""
    def __init__(self, detail: str = "Security violation detected", internal_detail: str = None):
        super().__init__(status_code=403, detail=detail)
        self.internal_detail = internal_detail


class BusinessLogicError(HTTPException):
    """Business logic error that can be safely shown to users"""
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(status_code=status_code, detail=detail)


def generate_error_id() -> str:
    """Generate unique error ID for tracking"""
    return str(uuid.uuid4())


def sanitize_error_message(error: Exception, show_details: bool = False) -> str:
    """Sanitize error message to prevent information disclosure"""
    
    # Production-safe error messages
    safe_messages = {
        ValidationError: "Invalid input data provided",
        ValueError: "Invalid data format",
        TypeError: "Data type error",
        KeyError: "Required data missing",
        AttributeError: "Invalid operation",
        PermissionError: "Insufficient permissions",
        FileNotFoundError: "Resource not found",
        ConnectionError: "Service temporarily unavailable",
        TimeoutError: "Request timeout"
    }
    
    # Database-specific error handling
    if isinstance(error, IntegrityError):
        if "unique constraint" in str(error).lower():
            return "Data already exists"
        elif "foreign key constraint" in str(error).lower():
            return "Referenced data not found"
        elif "not null constraint" in str(error).lower():
            return "Required field missing"
        else:
            return "Data integrity error"
    
    if isinstance(error, SQLAlchemyError):
        return "Database operation failed"

    # FastAPI request validation (body/query/path) — not a pydantic
    # ValidationError subclass, so it needs its own branch. Field-level
    # detail (loc/msg/type, no raw submitted values) is attached separately
    # in create_error_response via the `validation_errors` key.
    if isinstance(error, RequestValidationError):
        return "Invalid request data"

    # HTTP exceptions should return their detail message
    if isinstance(error, (HTTPException, StarletteHTTPException)):
        return str(error.detail) if error.detail else "An error occurred"

    # Get safe message or use generic fallback
    error_type = type(error)
    safe_message = safe_messages.get(error_type, "An unexpected error occurred")
    
    # In development, we might want to show more details — but never the
    # multi-line stack trace. Strip newlines and cap length so traceback frames
    # never leak to the client (py/stack-trace-exposure).
    if show_details and hasattr(error, 'detail'):
        detail = str(error.detail).splitlines()[0] if error.detail else safe_message
        return detail[:200]
    elif show_details:
        first_line = str(error).splitlines()[0] if str(error) else safe_message
        return first_line[:200]

    return safe_message


def log_error_securely(
    error: Exception, 
    request: Request = None, 
    user_id: str = None,
    additional_context: Dict[str, Any] = None
) -> str:
    """Log error securely with proper context and return error ID"""
    
    error_id = generate_error_id()
    timestamp = datetime.utcnow().isoformat()
    
    # Build context without sensitive data
    context = {
        "error_id": error_id,
        "timestamp": timestamp,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "user_id": user_id,
    }
    
    if request:
        # Safe request information (no sensitive headers/params)
        context.update({
            "method": request.method,
            "path": request.url.path,
            "user_agent": request.headers.get("user-agent", "unknown")[:200],
            "client_ip": get_client_ip(request),
            "content_type": request.headers.get("content-type"),
        })
        
        # Only log query params that aren't sensitive
        safe_params = {
            k: v for k, v in request.query_params.items()
            if k.lower() not in ['password', 'token', 'secret', 'api_key', 'auth']
        }
        if safe_params:
            context["query_params"] = safe_params
    
    if additional_context:
        # Filter out potentially sensitive keys
        safe_context = {
            k: v for k, v in additional_context.items()
            if k.lower() not in ['password', 'token', 'secret', 'api_key', 'auth', 'credential']
        }
        context.update(safe_context)
    
    # Log with appropriate level based on error type
    if isinstance(error, SecurityError):
        logger.critical(f"SECURITY VIOLATION [{error_id}]: {context}", extra={"security_event": True})
    elif isinstance(error, (HTTPException, BusinessLogicError)):
        logger.warning(f"Business error [{error_id}]: {context}")
    elif isinstance(error, SQLAlchemyError):
        logger.error(f"Database error [{error_id}]: {context}")
    else:
        logger.error(f"Unexpected error [{error_id}]: {context}")
        # Also log the full traceback for debugging
        logger.debug(f"Traceback for [{error_id}]:\n{traceback.format_exc()}")
    
    return error_id


def get_client_ip(request: Request) -> str:
    """Safely extract client IP address"""
    # Check for forwarded IP (proxy/load balancer)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # Take the first IP (closest to client)
        return forwarded_for.split(",")[0].strip()
    
    # Check for real IP header
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    
    # Fall back to direct connection
    return request.client.host if request.client else "unknown"


def create_error_response(
    error: Exception,
    request: Request = None,
    user_id: str = None,
    show_details: bool = False
) -> JSONResponse:
    """Create standardized error response"""
    
    error_id = log_error_securely(error, request, user_id)
    
    # Determine status code
    if isinstance(error, (HTTPException, StarletteHTTPException)):
        status_code = error.status_code
    elif isinstance(error, (ValidationError, RequestValidationError)):
        status_code = 422
    elif isinstance(error, PermissionError):
        status_code = 403
    elif isinstance(error, (KeyError, ValueError)):
        status_code = 400
    else:
        status_code = 500
    
    # Create safe error message. Rebuild from primitives + cap length so
    # CodeQL sees that no Exception object flows into the response body
    # (py/stack-trace-exposure). sanitize_error_message already returns
    # short, single-line strings (or fixed safe constants); this second
    # pass is a defensive belt-and-suspenders that makes the taint
    # severance explicit at the response-construction site.
    raw_message = sanitize_error_message(error, show_details)
    safe_message = (str(raw_message).splitlines()[0] if raw_message else "An error occurred")[:200]

    response_data: Dict[str, Any] = {
        "error": True,
        "message": safe_message,
        "error_id": str(error_id),
        "timestamp": datetime.utcnow().isoformat(),
    }

    # Add field-level validation details for both pydantic ValidationError
    # and FastAPI's RequestValidationError (body/query/path validation —
    # NOT a ValidationError subclass, so it needs to be named explicitly).
    # Always attached (not gated behind show_details): field locations are
    # never sensitive on their own. Each entry is rebuilt from primitives
    # (loc/msg/type only — never err.get("input"), which pydantic v2
    # populates with the raw submitted value) + capped, so the response
    # cannot inherit any raw exception payload (defense-in-depth for
    # py/stack-trace-exposure and to keep submitted values out of logs/UI).
    if isinstance(error, (ValidationError, RequestValidationError)):
        response_data["validation_errors"] = [
            {
                "field": ".".join(str(loc) for loc in err["loc"])[:200],
                "message": str(err["msg"])[:500],
                "type": str(err["type"])[:100],
            }
            for err in error.errors()
        ]

    return JSONResponse(
        status_code=status_code,
        content=response_data,
        headers={
            "X-Error-ID": str(error_id),
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler for FastAPI"""
    
    # Get user ID if available
    user_id = getattr(request.state, 'user_id', None)
    
    # Determine if we should show details (development mode)
    show_details = getattr(request.app.state, 'debug', False)
    
    return create_error_response(exc, request, user_id, show_details)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handler for HTTP exceptions"""
    
    user_id = getattr(request.state, 'user_id', None)
    show_details = getattr(request.app.state, 'debug', False)
    
    return create_error_response(exc, request, user_id, show_details)


async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """Handler for Pydantic validation errors"""

    user_id = getattr(request.state, 'user_id', None)
    show_details = getattr(request.app.state, 'debug', False)

    return create_error_response(exc, request, user_id, show_details)


async def request_validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handler for FastAPI request validation errors (body/query/path).

    NOT a pydantic ValidationError subclass, so it needs its own
    registration — see create_error_response for the shared field-level
    (no raw payload) validation_errors handling.
    """

    user_id = getattr(request.state, 'user_id', None)
    show_details = getattr(request.app.state, 'debug', False)

    return create_error_response(exc, request, user_id, show_details)


async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Handler for SQLAlchemy database errors."""

    user_id = getattr(request.state, 'user_id', None)
    show_details = getattr(request.app.state, 'debug', False)

    return create_error_response(exc, request, user_id, show_details)


def setup_error_handling(app):
    """Setup comprehensive error handling for FastAPI app"""

    # Add exception handlers. Every 500-class and 422 path funnels through
    # create_error_response so it carries the documented envelope
    # {error, message, error_id, timestamp} + X-Error-ID header
    # (sw2102-docs FINDINGS.md 2026-06-14 entry — the player-client's
    # apiRequest fallback logic was built against it).
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    app.add_exception_handler(ValidationError, validation_exception_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)

    # Set debug flag based on environment
    from src.core.config import settings
    app.state.debug = settings.DEBUG
    
    logger.info("Error handling configured successfully")


# Security-specific error responses
def create_security_error(detail: str = "Access denied", internal_detail: str = None) -> SecurityError:
    """Create security error with internal logging"""
    return SecurityError(detail, internal_detail)


def create_validation_error(field: str, message: str) -> BusinessLogicError:
    """Create user-friendly validation error"""
    return BusinessLogicError(f"Invalid {field}: {message}")


def create_not_found_error(resource: str = "resource") -> BusinessLogicError:
    """Create not found error"""
    return BusinessLogicError(f"The requested {resource} was not found", 404)


def create_conflict_error(message: str) -> BusinessLogicError:
    """Create conflict error"""
    return BusinessLogicError(message, 409)


def create_rate_limit_error(retry_after: int = 60) -> HTTPException:
    """Create rate limit error with retry header"""
    return HTTPException(
        status_code=429,
        detail="Rate limit exceeded. Please try again later.",
        headers={"Retry-After": str(retry_after)}
    )