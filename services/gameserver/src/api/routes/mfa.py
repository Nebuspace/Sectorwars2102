"""
Multi-Factor Authentication (MFA) API endpoints.

Provides TOTP setup, verification, and management for enhanced security.
"""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_user, require_admin
from src.models.user import User
from src.services.mfa_service import MFAService


router = APIRouter(prefix="/auth/mfa", tags=["mfa", "auth"])


# Request/Response Models

class MFAGenerateResponse(BaseModel):
    """Response for MFA secret generation."""
    secret: str
    setup_url: str
    qr_code_data_url: str
    message: str


class MFAVerifyRequest(BaseModel):
    """Request to verify MFA setup."""
    code: str = Field(..., min_length=6, max_length=8, description="TOTP code from authenticator app")


class MFAVerifyResponse(BaseModel):
    """Response for MFA verification."""
    success: bool
    message: str
    backup_codes: Optional[List[str]] = None


class MFACheckRequest(BaseModel):
    """Request to check MFA code during login."""
    code: str = Field(..., min_length=6, max_length=8, description="TOTP code or backup code")


class MFACheckResponse(BaseModel):
    """Response for MFA code check."""
    valid: bool
    message: str


class MFAStatusResponse(BaseModel):
    """Response for MFA status check."""
    enabled: bool
    verified: bool
    backup_codes_remaining: Optional[int] = None
    last_used: Optional[str] = None


class BackupCodesResponse(BaseModel):
    """Response for backup codes."""
    backup_codes: List[str]
    message: str


class MFAAttemptsResponse(BaseModel):
    """Response for MFA attempts."""
    attempts: List[Dict[str, Any]]
    total: int


# MFA Management Endpoints

@router.post("/generate", response_model=MFAGenerateResponse)
async def generate_mfa_secret(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Generate a new MFA secret for the current admin user.
    Returns QR code and setup URL for authenticator app configuration.
    """
    mfa_service = MFAService(db)
    
    try:
        secret, setup_url, qr_code_data_url = mfa_service.generate_secret(str(current_user.id))
        
        return MFAGenerateResponse(
            secret=secret,
            setup_url=setup_url,
            qr_code_data_url=qr_code_data_url,
            message="MFA secret generated. Scan the QR code with your authenticator app and verify setup."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate MFA secret: {str(e)}")


@router.post("/verify", response_model=MFAVerifyResponse)
async def verify_mfa_setup(
    request: MFAVerifyRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Verify MFA setup by confirming a TOTP code.
    This completes the MFA setup process and generates backup codes.
    """
    mfa_service = MFAService(db)
    
    result = mfa_service.verify_setup(str(current_user.id), request.code)
    
    if result["success"]:
        return MFAVerifyResponse(
            success=True,
            message=result["message"],
            backup_codes=result.get("backup_codes")
        )
    else:
        return MFAVerifyResponse(
            success=False,
            message=result["message"]
        )


@router.post("/check", response_model=MFACheckResponse)
async def check_mfa_code(
    request: MFACheckRequest,
    request_obj: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Check if an MFA code is valid for the current user.
    Used during login flow for MFA verification.
    """
    mfa_service = MFAService(db)
    
    # Get client IP and user agent for logging
    ip_address = request_obj.client.host if request_obj.client else None
    user_agent = request_obj.headers.get("user-agent")
    
    is_valid = mfa_service.verify_code(
        str(current_user.id), 
        request.code,
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    return MFACheckResponse(
        valid=is_valid,
        message="Code is valid" if is_valid else "Invalid or expired code"
    )


@router.get("/status", response_model=MFAStatusResponse)
async def get_mfa_status(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get the current MFA status for the admin user.
    """
    mfa_service = MFAService(db)
    
    is_enabled = mfa_service.is_mfa_enabled(str(current_user.id))
    backup_codes = mfa_service.get_backup_codes(str(current_user.id)) if is_enabled else None
    
    # Get last used timestamp
    last_used = None
    if current_user.mfa_secret and current_user.mfa_secret.last_used:
        last_used = current_user.mfa_secret.last_used.isoformat()
    
    return MFAStatusResponse(
        enabled=is_enabled,
        verified=is_enabled,  # If enabled, it means it's verified
        backup_codes_remaining=len(backup_codes) if backup_codes else None,
        last_used=last_used
    )


@router.post("/disable")
async def disable_mfa(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Disable MFA for the current admin user.
    """
    mfa_service = MFAService(db)
    
    success = mfa_service.disable_mfa(str(current_user.id))
    
    if success:
        return {"message": "MFA has been disabled"}
    else:
        return {"message": "MFA was not enabled"}


@router.get("/backup-codes", response_model=BackupCodesResponse)
async def get_backup_codes(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get remaining backup codes for the current admin user.
    """
    mfa_service = MFAService(db)
    
    backup_codes = mfa_service.get_backup_codes(str(current_user.id))
    
    if backup_codes is None:
        raise HTTPException(status_code=404, detail="MFA is not enabled or no backup codes found")
    
    return BackupCodesResponse(
        backup_codes=backup_codes,
        message=f"You have {len(backup_codes)} backup codes remaining"
    )


@router.post("/regenerate-backup-codes", response_model=BackupCodesResponse)
async def regenerate_backup_codes(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Regenerate backup codes for the current admin user.
    This invalidates all existing backup codes.
    """
    mfa_service = MFAService(db)
    
    backup_codes = mfa_service.regenerate_backup_codes(str(current_user.id))
    
    if backup_codes is None:
        raise HTTPException(status_code=404, detail="MFA is not enabled")
    
    return BackupCodesResponse(
        backup_codes=backup_codes,
        message="New backup codes generated. Store them securely as they replace all previous codes."
    )


@router.get("/attempts", response_model=MFAAttemptsResponse)
async def get_mfa_attempts(
    hours: int = 24,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get recent MFA authentication attempts for security monitoring.
    """
    mfa_service = MFAService(db)
    
    attempts = mfa_service.get_recent_attempts(str(current_user.id), hours)
    
    return MFAAttemptsResponse(
        attempts=attempts,
        total=len(attempts)
    )