"""PayPal integration API routes for subscription management"""

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field, ValidationError
from datetime import datetime, timezone
import json

from src.auth.dependencies import get_current_user, get_current_player
from src.core.database import get_async_session
from src.models.user import User
from src.models.player import Player
from src.models.region import Region
from src.services.paypal_service import paypal_service, PayPalWebhookEvent, SubscriptionTier
from src.services.regional_auth_service import regional_auth, RegionalPermission

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/paypal", tags=["PayPal Integration"])


class CreateSubscriptionRequest(BaseModel):
    """Request to create a new subscription"""
    subscription_type: str = Field(..., description="galactic_citizen or regional_owner")
    region_name: Optional[str] = Field(None, description="Required for regional_owner subscriptions")
    return_url: str = Field(..., description="Success return URL")
    cancel_url: str = Field(..., description="Cancel return URL")


class SubscriptionStatusResponse(BaseModel):
    """Response with subscription status"""
    subscription_id: str
    status: str
    next_billing_time: Optional[str]
    amount: Dict[str, Any]


@router.post("/subscriptions/create")
async def create_subscription(
    request: CreateSubscriptionRequest,
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_async_session)
):
    """Create a new PayPal subscription"""
    try:
        if request.subscription_type == "galactic_citizen":
            # Check if user already has galactic citizenship
            if current_player.is_galactic_citizen:
                raise HTTPException(
                    status_code=400, 
                    detail="User already has galactic citizenship"
                )
            
            # Create galactic citizen subscription
            result = await paypal_service.create_galactic_citizen_subscription(
                user_id=str(current_user.id),
                return_url=request.return_url,
                cancel_url=request.cancel_url
            )
            
            return {
                "subscription_id": result["id"],
                "status": result["status"],
                "approval_url": next(
                    (link["href"] for link in result.get("links", []) 
                     if link["rel"] == "approve"), 
                    None
                ),
                "type": "galactic_citizen",
                "amount": "$5.00/month"
            }
        
        elif request.subscription_type == "regional_owner":
            if not request.region_name:
                raise HTTPException(
                    status_code=400,
                    detail="Region name required for regional ownership subscription"
                )
            
            # Validate region name format
            if not request.region_name.replace("-", "").replace("_", "").isalnum():
                raise HTTPException(
                    status_code=400,
                    detail="Region name must contain only letters, numbers, hyphens, and underscores"
                )
            
            # SECURITY: Enhanced region ownership validation
            result = await session.execute(
                select(Region).where(Region.name == request.region_name)
            )
            existing_region = result.scalar_one_or_none()
            
            if existing_region:
                if existing_region.owner_id != current_user.id:
                    # Region is owned by someone else
                    raise HTTPException(
                        status_code=409,
                        detail="Region name already taken by another user"
                    )
                else:
                    # User already owns this region
                    raise HTTPException(
                        status_code=400,
                        detail="You already own this region"
                    )
            
            # SECURITY: Check if user already has an active regional ownership subscription
            # This prevents users from bypassing the single-region limitation
            from src.models.player import Player
            player_result = await session.execute(
                select(Player).where(Player.user_id == current_user.id)
            )
            current_player_obj = player_result.scalar_one_or_none()
            
            if current_player_obj and current_player_obj.regional_owner_subscription_id:
                raise HTTPException(
                    status_code=400,
                    detail="You already have an active regional ownership subscription"
                )
            
            # SECURITY: Verify region name against reserved names
            reserved_names = {'nexus', 'admin', 'system', 'central', 'api', 'www', 'mail', 'ftp', 'test'}
            if request.region_name.lower() in reserved_names:
                raise HTTPException(
                    status_code=400,
                    detail="Region name is reserved and cannot be used"
                )
            
            # Create regional ownership subscription
            result = await paypal_service.create_regional_ownership_subscription(
                user_id=str(current_user.id),
                region_name=request.region_name,
                return_url=request.return_url,
                cancel_url=request.cancel_url
            )
            
            return {
                "subscription_id": result["id"],
                "status": result["status"],
                "approval_url": next(
                    (link["href"] for link in result.get("links", []) 
                     if link["rel"] == "approve"), 
                    None
                ),
                "type": "regional_owner",
                "region_name": request.region_name,
                "amount": "$25.00/month"
            }
        
        else:
            raise HTTPException(
                status_code=400,
                detail="Invalid subscription type. Must be 'galactic_citizen' or 'regional_owner'"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create subscription")
        raise HTTPException(status_code=500, detail="Failed to create subscription")


@router.get("/subscriptions")
async def get_user_subscriptions(
    current_user: User = Depends(get_current_user)
):
    """Get all subscriptions for the current user"""
    try:
        subscriptions = await paypal_service.get_user_subscriptions(str(current_user.id))
        return {"subscriptions": subscriptions}
    
    except Exception as e:
        logger.exception("Failed to get user subscriptions")
        raise HTTPException(status_code=500, detail="Failed to retrieve subscriptions")


@router.get("/subscriptions/{subscription_id}")
async def get_subscription_details(
    subscription_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session)
):
    """Get details for a specific subscription"""
    try:
        # Verify user owns this subscription
        user_subscriptions = await paypal_service.get_user_subscriptions(str(current_user.id))
        subscription_ids = [sub["subscription_id"] for sub in user_subscriptions]
        
        if subscription_id not in subscription_ids:
            raise HTTPException(
                status_code=403,
                detail="Access denied to this subscription"
            )
        
        details = await paypal_service.get_subscription_details(subscription_id)
        
        return {
            "subscription_id": details["id"],
            "status": details["status"],
            "plan_id": details["plan_id"],
            "start_time": details.get("start_time"),
            "next_billing_time": details.get("billing_info", {}).get("next_billing_time"),
            "last_payment": details.get("billing_info", {}).get("last_payment"),
            "subscriber": details.get("subscriber"),
            "links": details.get("links", [])
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get subscription details")
        raise HTTPException(status_code=500, detail="Failed to retrieve subscription details")


@router.post("/subscriptions/{subscription_id}/cancel")
async def cancel_subscription(
    subscription_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session)
):
    """Cancel a user's subscription"""
    try:
        # Verify user owns this subscription
        user_subscriptions = await paypal_service.get_user_subscriptions(str(current_user.id))
        subscription_ids = [sub["subscription_id"] for sub in user_subscriptions]
        
        if subscription_id not in subscription_ids:
            raise HTTPException(
                status_code=403,
                detail="Access denied to this subscription"
            )
        
        # Cancel the subscription
        success = await paypal_service.cancel_subscription(
            subscription_id, 
            reason="User requested cancellation"
        )
        
        if success:
            return {"message": "Subscription cancelled successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to cancel subscription")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to cancel subscription")
        raise HTTPException(status_code=500, detail="Failed to cancel subscription")


@router.post("/webhooks/paypal")
async def handle_paypal_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """Handle PayPal webhook events"""
    try:
        # Get request body and headers
        body = await request.body()
        headers = dict(request.headers)
        
        # Validate webhook signature (should be implemented for production)
        if not await paypal_service.validate_webhook_signature(headers, body.decode()):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        
        # Parse webhook event. A malformed/incomplete payload is a 400, never a
        # 500 — a 5xx tells PayPal to retry a payload that will never validate.
        event_data = json.loads(body.decode())
        try:
            webhook_event = PayPalWebhookEvent(**event_data)
        except (ValidationError, TypeError) as exc:
            logger.warning("Malformed PayPal webhook payload rejected: %s", exc)
            raise HTTPException(status_code=400, detail="Malformed webhook payload")

        # Replay-attack window: reject events whose create_time is more than 5
        # minutes from now (ADR-0058). A malformed timestamp is a 400, never a
        # 500 — a 5xx tells PayPal to retry, which would amplify a replay.
        try:
            event_time = datetime.fromisoformat(
                str(webhook_event.create_time).replace("Z", "+00:00")
            )
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid webhook timestamp")
        if abs((datetime.now(timezone.utc) - event_time).total_seconds()) > 300:
            logger.warning("Rejected PayPal webhook outside 5-minute window: %s", webhook_event.id)
            raise HTTPException(status_code=400, detail="Webhook timestamp outside acceptable window")

        # Process webhook in background (idempotency enforced inside the handler,
        # in the same transaction as the subscription mutation).
        background_tasks.add_task(
            paypal_service.handle_subscription_webhook,
            webhook_event
        )
        
        # Return immediate success response to PayPal
        return JSONResponse(
            status_code=200,
            content={"status": "webhook_received"}
        )
    
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except HTTPException:
        # Deliberate 4xx (bad timestamp / replay window) — must not be downgraded
        # to a 500, which would tell PayPal to retry the rejected event.
        raise
    except Exception as e:
        logger.exception("Webhook processing error")
        raise HTTPException(status_code=500, detail="Webhook processing failed")


@router.get("/plans")
async def get_subscription_plans():
    """Get available subscription plans and pricing"""
    return {
        "plans": [
            {
                "id": "galactic_citizen",
                "name": "Galactic Citizenship",
                "description": "Access to all regions, inter-regional travel, galactic trading privileges",
                "price": "$5.00/month",
                "features": [
                    "Access to all active regions",
                    "Inter-regional travel and trade",
                    "Galactic citizen privileges",
                    "Central Nexus access",
                    "Embassy and diplomatic immunity",
                    "Cross-regional communication"
                ]
            },
            {
                "id": "regional_owner",
                "name": "Regional Ownership",
                "description": "Own and govern your own 500-sector region with full administrative control",
                "price": "$25.00/month", 
                "features": [
                    "Own a 500-sector region",
                    "Full governance control (democracy, autocracy, council)",
                    "Economic policy control (taxes, trade bonuses)",
                    "Cultural customization (themes, languages, traditions)",
                    "Member management and moderation",
                    "Regional analytics and reports",
                    "Treaty negotiation capabilities",
                    "Election and policy management",
                    "All galactic citizen benefits included"
                ]
            }
        ]
    }


@router.get("/regions/available-names")
async def check_region_name_availability(
    name: str,
    session: AsyncSession = Depends(get_async_session)
):
    """Check if a region name is available"""
    try:
        # Validate name format
        if not name.replace("-", "").replace("_", "").isalnum():
            return {
                "available": False,
                "reason": "Region name must contain only letters, numbers, hyphens, and underscores"
            }
        
        if len(name) < 3 or len(name) > 50:
            return {
                "available": False,
                "reason": "Region name must be between 3 and 50 characters"
            }
        
        # Check against reserved names
        reserved_names = [
            "admin", "administrator", "root", "system", "default", "central-nexus",
            "nexus", "galactic", "platform", "official", "staff", "moderator",
            "test", "api", "www", "app", "mail", "support", "help"
        ]
        
        if name.lower() in reserved_names:
            return {
                "available": False,
                "reason": "This name is reserved"
            }
        
        # Check database
        result = await session.execute(
            select(Region).where(Region.name == name)
        )
        existing_region = result.scalar_one_or_none()
        
        if existing_region:
            return {
                "available": False,
                "reason": "Region name already taken"
            }
        
        return {
            "available": True,
            "name": name
        }
    
    except Exception as e:
        logger.exception("Error checking region name availability")
        raise HTTPException(status_code=500, detail="Failed to check name availability")


@router.get("/admin/subscriptions")
async def admin_get_all_subscriptions(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session)
):
    """Admin endpoint to view all subscriptions (requires admin permissions)"""
    try:
        # Check if user has admin permissions
        current_player = await session.execute(
            select(Player).where(Player.user_id == current_user.id)
        )
        player = current_player.scalar_one_or_none()
        
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        # Check galaxy admin permission
        has_permission = await regional_auth.check_regional_permission(
            str(current_user.id),
            "any",  # Galaxy-level permission
            RegionalPermission.GALAXY_ADMIN_FULL
        )
        
        if not has_permission:
            raise HTTPException(status_code=403, detail="Admin access required")
        
        # Get all regions with subscriptions
        result = await session.execute(
            select(Region).where(Region.paypal_subscription_id.isnot(None))
        )
        regions = result.scalars().all()
        
        # Get all galactic citizens
        result = await session.execute(
            select(Player).where(Player.is_galactic_citizen == True)
        )
        galactic_citizens = result.scalars().all()
        
        return {
            "regional_subscriptions": [
                {
                    "region_id": str(region.id),
                    "region_name": region.name,
                    "owner_id": str(region.owner_id),
                    "subscription_id": region.paypal_subscription_id,
                    "status": region.status,
                    "created_at": region.created_at.isoformat()
                }
                for region in regions
            ],
            "galactic_citizens": [
                {
                    "player_id": str(player.id),
                    "user_id": str(player.user_id),
                    "username": player.username
                }
                for player in galactic_citizens
            ],
            "summary": {
                "total_regional_subscriptions": len(regions),
                "total_galactic_citizens": len(galactic_citizens),
                "estimated_monthly_revenue": len(regions) * 25 + len(galactic_citizens) * 5
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Admin subscription query failed")
        raise HTTPException(status_code=500, detail="Failed to retrieve subscription data")