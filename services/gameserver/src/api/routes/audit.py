"""
Audit logging API endpoints for admin access
"""

import logging
from typing import Optional, List, Any
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.auth.admin_scopes import AUDIT_VIEW
from src.auth.dependencies import require_scope
from src.services.audit_service import AuditService
from src.models.admin_action_log import AdminActionLog
from src.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/audit", tags=["audit"])


class AdminActionLogItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    admin_user_id: Optional[UUID] = None
    scope_used: Optional[str] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    payload_snapshot: Optional[Any] = None
    result: Optional[str] = None
    failure_reason: Optional[str] = None
    reviewed_by: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    at: datetime


class AdminActionLogPageOut(BaseModel):
    items: List[AdminActionLogItemOut]
    total: int
    page: int
    limit: int
    pages: int


@router.post("/log")
async def create_audit_log(
    request: dict,
    admin: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db)
):
    """
    Create a manual audit log entry (for admin actions that need explicit logging)
    """
    try:
        audit_log = await AuditService.create_audit_log(
            db=db,
            method="MANUAL",
            path="/admin/audit/log",
            client_ip=request.get("client_ip", "127.0.0.1"),
            user_id=admin.id,
            user_type="admin",
            action=request.get("action"),
            resource_type=request.get("resource"),
            resource_id=request.get("resource_id"),
            response_summary=request.get("details")
        )
        
        if audit_log:
            return {
                "success": True,
                "auditId": str(audit_log.id)
            }
        else:
            return {
                "success": False,
                "message": "Failed to create audit log"
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/actions", response_model=AdminActionLogPageOut)
async def list_admin_actions(
    page: int = Query(1, ge=1, le=10000),
    limit: int = Query(50, ge=1, le=500),
    admin_user_id: Optional[UUID] = None,
    action: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    admin: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db),
):
    """Paginated read-only list over AdminActionLog (RBAC audit trail)."""
    try:
        filters = []
        if admin_user_id is not None:
            filters.append(AdminActionLog.admin_user_id == admin_user_id)
        if action is not None:
            filters.append(AdminActionLog.action == action)
        if target_type is not None:
            filters.append(AdminActionLog.target_type == target_type)
        if target_id is not None:
            filters.append(AdminActionLog.target_id == target_id)
        if start_date is not None:
            filters.append(AdminActionLog.at >= start_date)
        if end_date is not None:
            filters.append(AdminActionLog.at <= end_date)

        base = db.query(AdminActionLog)
        if filters:
            base = base.filter(and_(*filters))

        total = base.count()
        offset = (page - 1) * limit
        rows = (
            base.order_by(AdminActionLog.at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        pages = (total + limit - 1) // limit if total else 0

        return AdminActionLogPageOut(
            items=[AdminActionLogItemOut.model_validate(row) for row in rows],
            total=total,
            page=page,
            limit=limit,
            pages=pages,
        )
    except Exception as e:
        logger.error("list_admin_actions failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to list admin actions")


@router.get("/logs")
async def get_audit_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    user_id: Optional[UUID] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    admin: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db)
):
    """
    Get audit logs with filtering and pagination
    """
    try:
        offset = (page - 1) * limit
        
        logs = await AuditService.get_audit_logs(
            db=db,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset
        )
        
        # Get total count for pagination
        from sqlalchemy import func, and_
        from src.models.audit_log import AuditLog
        
        count_query = db.query(func.count(AuditLog.id))
        
        # Apply same filters for count
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
            count_query = count_query.filter(and_(*filters))
        
        total = count_query.scalar()
        
        return {
            "logs": [log.to_dict() for log in logs],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/violations")
async def get_security_violations(
    start_date: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=500),
    admin: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db)
):
    """
    Get recent security violations
    """
    try:
        violations = await AuditService.get_security_violations(
            db=db,
            start_date=start_date,
            limit=limit
        )
        
        return {
            "violations": [v.to_dict() for v in violations],
            "total": len(violations)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{user_id}/activity")
async def get_user_activity_summary(
    user_id: UUID,
    days: int = Query(30, ge=1, le=365),
    admin: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db)
):
    """
    Get activity summary for a specific user
    """
    try:
        summary = await AuditService.get_user_activity_summary(
            db=db,
            user_id=user_id,
            days=days
        )
        
        return summary
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))