"""
Input validation and sanitization utilities for enhanced security
"""

import re
import html
import uuid
from typing import Any, Optional
from pydantic import BaseModel, Field, validator
from fastapi import HTTPException

# Common validation patterns
EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{3,30}$')
REGION_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{3,50}$')
SECTOR_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{1,30}$')
PASSWORD_PATTERN = re.compile(r'^.{12,128}$')  # Min 12 chars, max 128
ALPHANUMERIC_PATTERN = re.compile(r'^[a-zA-Z0-9]+$')

# Reserved names that cannot be used
RESERVED_NAMES = {
    'admin', 'administrator', 'root', 'system', 'api', 'www', 'mail', 'ftp',
    'nexus', 'central', 'core', 'test', 'debug', 'dev', 'staging', 'prod',
    'production', 'localhost', 'database', 'db', 'server', 'config', 'null',
    'undefined', 'true', 'false', 'public', 'private', 'secure', 'auth',
    'login', 'logout', 'register', 'signup', 'signin', 'password', 'passwd'
}

# SQL injection patterns to detect
SQL_INJECTION_PATTERNS = [
    r'(union\s+select)', r'(drop\s+table)', r'(delete\s+from)', 
    r'(insert\s+into)', r'(update\s+.*\s+set)', r'(create\s+table)',
    r'(alter\s+table)', r'(exec\s*\()', r'(script\s*>)', r'(<\s*script)',
    r'(javascript:)', r'(vbscript:)', r'(onload\s*=)', r'(onerror\s*=)'
]

# XSS patterns to detect
XSS_PATTERNS = [
    r'<script[^>]*>.*?</script>', r'<iframe[^>]*>.*?</iframe>',
    r'javascript:', r'vbscript:', r'onload\s*=', r'onerror\s*=',
    r'onclick\s*=', r'onmouseover\s*=', r'onfocus\s*='
]


class ValidationError(HTTPException):
    """Custom validation error with security context"""
    def __init__(self, field: str, message: str, value: Any = None):
        super().__init__(
            status_code=400,
            detail=f"Validation failed for {field}: {message}"
        )
        self.field = field
        self.value = value  # Don't log sensitive values


def sanitize_string(value: str, max_length: int = 1000) -> str:
    """Sanitize string input to prevent XSS and injection attacks"""
    if not isinstance(value, str):
        raise ValidationError("input", "Must be a string")
    
    # Length check
    if len(value) > max_length:
        raise ValidationError("input", f"String too long (max {max_length} characters)")
    
    # HTML escape to prevent XSS
    sanitized = html.escape(value, quote=True)
    
    # Check for suspicious patterns
    for pattern in XSS_PATTERNS:
        if re.search(pattern, value, re.IGNORECASE):
            raise ValidationError("input", "Contains potentially malicious content")
    
    for pattern in SQL_INJECTION_PATTERNS:
        if re.search(pattern, value, re.IGNORECASE):
            raise ValidationError("input", "Contains potentially malicious SQL patterns")
    
    return sanitized.strip()


def validate_email(email: str) -> str:
    """Validate and sanitize email address"""
    if not email or not isinstance(email, str):
        raise ValidationError("email", "Email is required")
    
    email = email.strip().lower()
    
    if len(email) > 254:  # RFC 5321 limit
        raise ValidationError("email", "Email address too long")
    
    if not EMAIL_PATTERN.match(email):
        raise ValidationError("email", "Invalid email format")
    
    return email


def validate_username(username: str) -> str:
    """Validate and sanitize username"""
    if not username or not isinstance(username, str):
        raise ValidationError("username", "Username is required")
    
    username = username.strip()
    
    if not USERNAME_PATTERN.match(username):
        raise ValidationError("username", "Username must be 3-30 characters, letters, numbers, underscore, hyphen only")
    
    if username.lower() in RESERVED_NAMES:
        raise ValidationError("username", "Username is reserved")
    
    return username


def validate_region_name(region_name: str) -> str:
    """Validate and sanitize region name"""
    if not region_name or not isinstance(region_name, str):
        raise ValidationError("region_name", "Region name is required")
    
    region_name = region_name.strip()
    
    if not REGION_NAME_PATTERN.match(region_name):
        raise ValidationError("region_name", "Region name must be 3-50 characters, letters, numbers, underscore, hyphen only")
    
    if region_name.lower() in RESERVED_NAMES:
        raise ValidationError("region_name", "Region name is reserved")
    
    return region_name


def validate_password(password: str) -> str:
    """Validate password strength"""
    if not password or not isinstance(password, str):
        raise ValidationError("password", "Password is required")
    
    if not PASSWORD_PATTERN.match(password):
        raise ValidationError("password", "Password must be 12-128 characters long")
    
    # Check for common weak patterns
    if password.lower() in ['password', '123456789', 'qwertyuiop', 'abcdefghij']:
        raise ValidationError("password", "Password is too common")
    
    # Require at least one letter and one number
    if not re.search(r'[a-zA-Z]', password) or not re.search(r'[0-9]', password):
        raise ValidationError("password", "Password must contain at least one letter and one number")
    
    return password


def validate_uuid(value: str, field_name: str = "id") -> str:
    """Validate UUID format"""
    if not value or not isinstance(value, str):
        raise ValidationError(field_name, "ID is required")
    
    try:
        uuid.UUID(value)
        return value
    except ValueError:
        raise ValidationError(field_name, "Invalid ID format")


def validate_integer(value: Any, field_name: str, min_val: int = None, max_val: int = None) -> int:
    """Validate integer with optional bounds"""
    try:
        int_val = int(value)
    except (ValueError, TypeError):
        raise ValidationError(field_name, "Must be an integer")
    
    if min_val is not None and int_val < min_val:
        raise ValidationError(field_name, f"Must be at least {min_val}")
    
    if max_val is not None and int_val > max_val:
        raise ValidationError(field_name, f"Must be at most {max_val}")
    
    return int_val


def validate_float(value: Any, field_name: str, min_val: float = None, max_val: float = None) -> float:
    """Validate float with optional bounds"""
    try:
        float_val = float(value)
    except (ValueError, TypeError):
        raise ValidationError(field_name, "Must be a number")
    
    if min_val is not None and float_val < min_val:
        raise ValidationError(field_name, f"Must be at least {min_val}")
    
    if max_val is not None and float_val > max_val:
        raise ValidationError(field_name, f"Must be at most {max_val}")
    
    return float_val


def validate_coordinates(x: Any, y: Any, z: Any = None) -> tuple:
    """Validate 2D or 3D coordinates"""
    x_val = validate_float(x, "x_coordinate", -1000000, 1000000)
    y_val = validate_float(y, "y_coordinate", -1000000, 1000000)
    
    if z is not None:
        z_val = validate_float(z, "z_coordinate", -1000000, 1000000)
        return (x_val, y_val, z_val)
    
    return (x_val, y_val)


def validate_json_object(value: Any, field_name: str, max_depth: int = 10) -> dict:
    """Validate JSON object with depth limit"""
    if not isinstance(value, dict):
        raise ValidationError(field_name, "Must be a JSON object")
    
    def check_depth(obj, current_depth=0):
        if current_depth > max_depth:
            raise ValidationError(field_name, f"JSON object too deeply nested (max depth {max_depth})")
        
        if isinstance(obj, dict):
            for v in obj.values():
                check_depth(v, current_depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                check_depth(item, current_depth + 1)
    
    check_depth(value)
    return value


def sanitize_search_query(query: str) -> str:
    """Sanitize search query to prevent injection"""
    if not query or not isinstance(query, str):
        return ""
    
    # Remove dangerous characters
    query = re.sub(r'[<>"\';\\]', '', query)
    
    # Limit length
    query = query[:100]
    
    # Remove multiple spaces
    query = re.sub(r'\s+', ' ', query)
    
    return query.strip()


class SecureBaseModel(BaseModel):
    """Base model with built-in security validation"""
    
    class Config:
        # Prevent extra fields to avoid injection
        extra = "forbid"
        # Validate assignments
        validate_assignment = True
        # Use enums by value
        use_enum_values = True


# Common validation schemas
class CreateUserRequest(SecureBaseModel):
    """Secure user creation request"""
    username: str = Field(..., min_length=3, max_length=30)
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=12, max_length=128)
    
    @validator('username')
    def validate_username_field(cls, v):
        return validate_username(v)
    
    @validator('email')
    def validate_email_field(cls, v):
        return validate_email(v)
    
    @validator('password')
    def validate_password_field(cls, v):
        return validate_password(v)


class CreateRegionRequest(SecureBaseModel):
    """Secure region creation request"""
    name: str = Field(..., min_length=3, max_length=50)
    description: Optional[str] = Field(None, max_length=500)
    
    @validator('name')
    def validate_region_name_field(cls, v):
        return validate_region_name(v)
    
    @validator('description')
    def validate_description_field(cls, v):
        if v is not None:
            return sanitize_string(v, 500)
        return v


class SearchRequest(SecureBaseModel):
    """Secure search request"""
    query: str = Field(..., min_length=1, max_length=100)
    limit: Optional[int] = Field(10, ge=1, le=100)
    offset: Optional[int] = Field(0, ge=0)
    
    @validator('query')
    def validate_search_query_field(cls, v):
        return sanitize_search_query(v)