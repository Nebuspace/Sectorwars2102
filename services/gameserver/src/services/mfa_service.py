"""
Multi-Factor Authentication (MFA) service.

Provides TOTP (Time-based One-Time Password) functionality for enhanced security.
"""

import secrets
import base64
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session
import pyotp
import qrcode
from io import BytesIO

from src.models.user import User
from src.models.mfa import MFASecret, MFAAttempt


class MFAService:
    """Service for managing Multi-Factor Authentication."""
    
    def __init__(self, db: Session):
        self.db = db
        
    def generate_secret(self, user_id: str) -> Tuple[str, str, str]:
        """
        Generate a new MFA secret for a user.
        
        Returns:
            Tuple of (secret, setup_url, qr_code_data_url)
        """
        # Generate a 32-character base32 secret
        secret = pyotp.random_base32()
        
        # Get user for account name
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError("User not found")
        
        # Create TOTP object
        totp = pyotp.TOTP(secret)
        
        # Generate setup URL for QR code
        setup_url = totp.provisioning_uri(
            name=user.email or user.username,
            issuer_name="Sectorwars2102 Admin"
        )
        
        # Generate QR code
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(setup_url)
        qr.make(fit=True)
        
        # Create QR code image
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Convert to data URL
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        qr_code_data = base64.b64encode(buffer.getvalue()).decode()
        qr_code_data_url = f"data:image/png;base64,{qr_code_data}"
        
        # Store secret in database (unverified)
        existing_secret = self.db.query(MFASecret).filter(MFASecret.user_id == user_id).first()
        if existing_secret:
            # Update existing secret
            existing_secret.secret = secret
            existing_secret.is_verified = False
            existing_secret.backup_codes = None
            existing_secret.created_at = datetime.utcnow()
            existing_secret.verified_at = None
        else:
            # Create new secret
            mfa_secret = MFASecret(
                user_id=user_id,
                secret=secret,
                is_verified=False
            )
            self.db.add(mfa_secret)
        
        self.db.commit()
        
        return secret, setup_url, qr_code_data_url
    
    def verify_setup(self, user_id: str, code: str) -> Dict[str, Any]:
        """
        Verify the initial MFA setup with a TOTP code.
        
        Returns:
            Dict with verification status and backup codes
        """
        mfa_secret = self.db.query(MFASecret).filter(MFASecret.user_id == user_id).first()
        if not mfa_secret:
            return {"success": False, "message": "No MFA secret found. Please generate a new one."}
        
        # Verify the code
        totp = pyotp.TOTP(mfa_secret.secret)
        is_valid = totp.verify(code, valid_window=1)  # Allow 1 window (30 seconds) of drift
        
        if is_valid:
            # Mark as verified and generate backup codes
            mfa_secret.is_verified = True
            mfa_secret.verified_at = datetime.utcnow()
            
            # Generate backup codes
            backup_codes = self._generate_backup_codes()
            mfa_secret.backup_codes = json.dumps(backup_codes)
            
            self.db.commit()
            
            return {
                "success": True,
                "message": "MFA setup completed successfully",
                "backup_codes": backup_codes
            }
        else:
            return {"success": False, "message": "Invalid verification code"}
    
    def verify_code(self, user_id: str, code: str, ip_address: Optional[str] = None, 
                   user_agent: Optional[str] = None) -> bool:
        """
        Verify a TOTP code for authentication.
        
        Returns:
            True if code is valid, False otherwise
        """
        mfa_secret = self.db.query(MFASecret).filter(
            MFASecret.user_id == user_id,
            MFASecret.is_verified == True
        ).first()
        
        if not mfa_secret:
            self._log_attempt(user_id, code, False, ip_address, user_agent, "no_mfa_setup")
            return False
        
        # Check if it's a backup code
        if self._is_backup_code(mfa_secret, code):
            self._use_backup_code(mfa_secret, code)
            self._log_attempt(user_id, code, True, ip_address, user_agent, "backup_code")
            mfa_secret.last_used = datetime.utcnow()
            self.db.commit()
            return True
        
        # Verify TOTP code
        totp = pyotp.TOTP(mfa_secret.secret)
        is_valid = totp.verify(code, valid_window=1)  # Allow 1 window of drift
        
        failure_reason = None if is_valid else "invalid_code"
        self._log_attempt(user_id, code, is_valid, ip_address, user_agent, failure_reason)
        
        if is_valid:
            mfa_secret.last_used = datetime.utcnow()
            self.db.commit()
        
        return is_valid
    
    def is_mfa_enabled(self, user_id: str) -> bool:
        """Check if MFA is enabled and verified for a user."""
        mfa_secret = self.db.query(MFASecret).filter(
            MFASecret.user_id == user_id,
            MFASecret.is_verified == True
        ).first()
        return mfa_secret is not None
    
    def disable_mfa(self, user_id: str) -> bool:
        """Disable MFA for a user."""
        mfa_secret = self.db.query(MFASecret).filter(MFASecret.user_id == user_id).first()
        if mfa_secret:
            self.db.delete(mfa_secret)
            self.db.commit()
            return True
        return False
    
    def get_backup_codes(self, user_id: str) -> Optional[List[str]]:
        """Get remaining backup codes for a user."""
        mfa_secret = self.db.query(MFASecret).filter(
            MFASecret.user_id == user_id,
            MFASecret.is_verified == True
        ).first()
        
        if mfa_secret and mfa_secret.backup_codes:
            return json.loads(mfa_secret.backup_codes)
        return None
    
    def regenerate_backup_codes(self, user_id: str) -> Optional[List[str]]:
        """Regenerate backup codes for a user."""
        mfa_secret = self.db.query(MFASecret).filter(
            MFASecret.user_id == user_id,
            MFASecret.is_verified == True
        ).first()
        
        if mfa_secret:
            backup_codes = self._generate_backup_codes()
            mfa_secret.backup_codes = json.dumps(backup_codes)
            self.db.commit()
            return backup_codes
        return None
    
    def get_recent_attempts(self, user_id: str, hours: int = 24) -> List[Dict[str, Any]]:
        """Get recent MFA attempts for security monitoring."""
        since = datetime.utcnow() - timedelta(hours=hours)
        attempts = self.db.query(MFAAttempt).filter(
            MFAAttempt.user_id == user_id,
            MFAAttempt.attempt_time >= since
        ).order_by(MFAAttempt.attempt_time.desc()).all()
        
        return [
            {
                "id": str(attempt.id),
                "success": attempt.success,
                "ip_address": attempt.ip_address,
                "user_agent": attempt.user_agent,
                "attempt_time": attempt.attempt_time.isoformat(),
                "failure_reason": attempt.failure_reason
            }
            for attempt in attempts
        ]
    
    def _generate_backup_codes(self, count: int = 10) -> List[str]:
        """Generate backup codes."""
        codes = []
        for _ in range(count):
            # Generate 8-character alphanumeric code
            code = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(8))
            codes.append(code)
        return codes
    
    def _is_backup_code(self, mfa_secret: MFASecret, code: str) -> bool:
        """Check if the code is a valid backup code."""
        if not mfa_secret.backup_codes:
            return False
        
        backup_codes = json.loads(mfa_secret.backup_codes)
        return code.upper() in backup_codes
    
    def _use_backup_code(self, mfa_secret: MFASecret, code: str):
        """Remove a backup code after use."""
        if mfa_secret.backup_codes:
            backup_codes = json.loads(mfa_secret.backup_codes)
            if code.upper() in backup_codes:
                backup_codes.remove(code.upper())
                mfa_secret.backup_codes = json.dumps(backup_codes)
    
    def _log_attempt(self, user_id: str, code: str, success: bool, 
                    ip_address: Optional[str], user_agent: Optional[str], 
                    failure_reason: Optional[str]):
        """Log an MFA authentication attempt."""
        attempt = MFAAttempt(
            user_id=user_id,
            code_entered=code[:6] + "***" if len(code) > 6 else code,  # Partially obscure code
            success=success,
            ip_address=ip_address,
            user_agent=user_agent[:500] if user_agent else None,  # Truncate user agent
            failure_reason=failure_reason
        )
        self.db.add(attempt)