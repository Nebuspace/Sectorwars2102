"""
Audit Service for persisting audit logs to database
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID
import logging
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import desc, and_

from src.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


class AuditAction(str, Enum):
    """Enumeration of audit actions."""
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGOUT = "logout"
    EXPORT = "export"
    IMPORT = "import"
    INTERVENTION = "intervention"
    EMERGENCY = "emergency"


class AuditService:
    """Service for managing audit logs"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def log_action(
        self,
        user_id: UUID,
        action: AuditAction,
        resource_type: str,
        resource_id: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """Log an administrative action."""
        audit_log = AuditLog(
            method="API",
            path=f"/admin/{resource_type}/{resource_id}",
            client_ip="127.0.0.1",  # Will be updated by middleware
            user_id=user_id,
            user_type="admin",
            action=action.value,
            resource_type=resource_type,
            resource_id=resource_id,
            request_body=details
        )
        self.db.add(audit_log)
    
    @staticmethod
    async def create_audit_log(
        db: Session,
        method: str,
        path: str,
        client_ip: str,
        user_agent: Optional[str] = None,
        user_id: Optional[UUID] = None,
        user_type: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[UUID] = None,
        status_code: Optional[int] = None,
        duration_ms: Optional[int] = None,
        query_params: Optional[Dict[str, Any]] = None,
        request_body: Optional[Dict[str, Any]] = None,
        response_summary: Optional[str] = None,
        security_flags: Optional[Dict[str, Any]] = None,
        violation_detected: Optional[str] = None
    ) -> AuditLog:
        """Create a new audit log entry"""
        
        try:
            # Sanitize request body to remove sensitive data
            sanitized_body = AuditService._sanitize_request_body(request_body) if request_body else None
            
            audit_log = AuditLog(
                method=method,
                path=path,
                client_ip=client_ip,
                user_agent=user_agent,
                user_id=user_id,
                user_type=user_type,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                status_code=status_code,
                duration_ms=duration_ms,
                query_params=query_params,
                request_body=sanitized_body,
                response_summary=response_summary,
                security_flags=security_flags,
                violation_detected=violation_detected
            )
            
            db.add(audit_log)
            db.commit()
            db.refresh(audit_log)
            
            return audit_log
            
        except Exception as e:
            logger.error(f"Failed to create audit log: {str(e)}")
            db.rollback()
            # Don't raise - we don't want audit failures to break the application
            return None
    
    @staticmethod
    def _sanitize_request_body(body: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive data from request body"""
        sensitive_fields = [
            'password', 'token', 'secret', 'api_key', 'private_key',
            'credit_card', 'ssn', 'pin', 'cvv', 'authorization'
        ]
        
        sanitized = body.copy()
        
        for field in sensitive_fields:
            if field in sanitized:
                sanitized[field] = "[REDACTED]"
            # Also check for variations like 'Password', 'PASSWORD', etc.
            for key in list(sanitized.keys()):
                if field in key.lower():
                    sanitized[key] = "[REDACTED]"
        
        return sanitized
    
    @staticmethod
    def extract_action_from_path(path: str) -> Optional[str]:
        """Extract action name from API path"""
        path_parts = path.strip('/').split('/')
        
        # Map common paths to actions
        action_map = {
            ('auth', 'login'): 'login',
            ('auth', 'logout'): 'logout',
            ('auth', 'register'): 'register',
            ('admin', 'economy', 'intervention'): 'economy_intervention',
            ('admin', 'ships', 'emergency'): 'ship_emergency_action',
            ('admin', 'messages', 'moderate'): 'message_moderation',
            ('messages', 'send'): 'send_message',
            ('combat', 'engage'): 'combat_engagement',
            ('drones', 'deploy'): 'drone_deployment',
            ('players',): 'player_management',
            ('teams',): 'team_management'
        }
        
        # Check for matches
        for path_tuple, action in action_map.items():
            if all(part in path_parts for part in path_tuple):
                return action
        
        # Default to the last meaningful part of the path
        if len(path_parts) > 1:
            return path_parts[-1]
        
        return None
    
    @staticmethod
    def extract_resource_type_from_path(path: str) -> Optional[str]:
        """Extract resource type from API path"""
        resource_types = [
            'auth', 'players', 'ships', 'economy', 'messages', 
            'combat', 'drones', 'teams', 'sectors', 'planets'
        ]
        
        path_lower = path.lower()
        for resource in resource_types:
            if resource in path_lower:
                return resource
        
        return None
    
    @staticmethod
    async def get_audit_logs(
        db: Session,
        user_id: Optional[UUID] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[AuditLog]:
        """Query audit logs with filters"""
        
        query = db.query(AuditLog)
        
        # Apply filters
        filters = []
        if user_id:
            filters.append(AuditLog.user_id == user_id)
        if action:
            filters.append(AuditLog.action == action)
        if resource_type:
            filters.append(AuditLog.resource_type == resource_type)
        if start_date:
            filters.append(AuditLog.timestamp >= start_date)
        if end_date:
            filters.append(AuditLog.timestamp <= end_date)
        
        if filters:
            query = query.filter(and_(*filters))
        
        # Order by timestamp descending and apply pagination
        logs = query.order_by(desc(AuditLog.timestamp))\
                   .limit(limit)\
                   .offset(offset)\
                   .all()
        
        return logs
    
    @staticmethod
    async def get_security_violations(
        db: Session,
        start_date: Optional[datetime] = None,
        limit: int = 100
    ) -> List[AuditLog]:
        """Get audit logs with security violations"""
        
        query = db.query(AuditLog).filter(
            AuditLog.violation_detected.isnot(None)
        )
        
        if start_date:
            query = query.filter(AuditLog.timestamp >= start_date)
        
        violations = query.order_by(desc(AuditLog.timestamp))\
                         .limit(limit)\
                         .all()
        
        return violations
    
    @staticmethod
    async def get_user_activity_summary(
        db: Session,
        user_id: UUID,
        days: int = 30
    ) -> Dict[str, Any]:
        """Get summary of user activity"""
        
        from datetime import timedelta
        start_date = datetime.utcnow() - timedelta(days=days)
        
        logs = await AuditService.get_audit_logs(
            db,
            user_id=user_id,
            start_date=start_date,
            limit=1000  # Get more logs for summary
        )
        
        # Summarize activity
        summary = {
            "total_actions": len(logs),
            "actions_by_type": {},
            "resources_accessed": {},
            "violations": 0,
            "last_activity": None
        }
        
        for log in logs:
            # Count actions
            if log.action:
                summary["actions_by_type"][log.action] = \
                    summary["actions_by_type"].get(log.action, 0) + 1
            
            # Count resources
            if log.resource_type:
                summary["resources_accessed"][log.resource_type] = \
                    summary["resources_accessed"].get(log.resource_type, 0) + 1
            
            # Count violations
            if log.violation_detected:
                summary["violations"] += 1
            
            # Track last activity
            if not summary["last_activity"] and log.timestamp:
                summary["last_activity"] = log.timestamp.isoformat()
        
        return summary