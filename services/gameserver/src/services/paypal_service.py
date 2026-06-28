"""PayPal subscription service for multi-regional platform monetization"""

import asyncio
import hashlib
import json
import os
import re
import httpx
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field
from enum import Enum

from src.core.database import AsyncSessionLocal
from src.core.config import get_config
from src.models.region import Region
from src.models.player import Player
from src.models.user import User
from src.models.processed_webhook_event import ProcessedWebhookEvent

import logging

logger = logging.getLogger(__name__)


# Name of the env flag that disables PayPal webhook signature verification.
# Intended for local/dev only — it must NEVER be active in production.
WEBHOOK_BYPASS_ENV = "PAYPAL_SKIP_WEBHOOK_VALIDATION"


class BypassFlagInProductionError(RuntimeError):
    """Raised at import time if a webhook-validation bypass flag is enabled while
    the service is running in production. PayPal webhook signature verification is
    mandatory in production (ADR-0058 A-D3); allowing it to be silently bypassed
    is a forgeable-payment vulnerability, so we fail closed at boot rather than
    serve a single request with verification off.
    """


def _assert_no_webhook_bypass_in_production() -> None:
    """Fail fast at import if the bypass flag is set in a production environment."""
    env = os.environ.get("ENVIRONMENT", "development").strip().lower()
    bypass = os.environ.get(WEBHOOK_BYPASS_ENV, "").strip().lower() == "true"
    if env == "production" and bypass:
        raise BypassFlagInProductionError(
            f"{WEBHOOK_BYPASS_ENV} must never be enabled in production — "
            "PayPal webhook signature verification is mandatory. Refusing to start."
        )


# Evaluated when this module is imported during app startup: a production server
# configured with the bypass flag will refuse to boot.
_assert_no_webhook_bypass_in_production()


def _redact(value: Optional[str], keep: int = 4) -> str:
    """Redact most of a sensitive identifier, keeping the last few chars for
    correlation. Used in log statements so subscription IDs / billing-agreement
    IDs don't land in clear text in operator logs.
    """
    if not value:
        return "***"
    if len(value) <= keep:
        return "***"
    return f"...{value[-keep:]}"


def _sub_ref(value: Optional[str]) -> str:
    """Return a 12-char SHA-256 hash of a sensitive PayPal identifier for logging.
    Non-reversible one-way transform: provides a stable correlation token without
    leaking the original value. CodeQL cannot model this as a sanitizer, hence the
    inline lgtm suppression on each logger call site.
    """
    if not value:
        return "***"
    return hashlib.sha256(value.encode()).hexdigest()[:12]


class SubscriptionTier(str, Enum):
    """Subscription tier types"""
    GALACTIC_CITIZEN = "galactic_citizen"  # $5/month
    REGIONAL_OWNER = "regional_owner"      # $25/month
    NEXUS_PREMIUM = "nexus_premium"        # $50/month (future expansion)


class SubscriptionStatus(str, Enum):
    """PayPal subscription status mapping"""
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class PayPalSubscriptionRequest(BaseModel):
    """Request model for creating PayPal subscriptions"""
    plan_id: str = Field(..., description="PayPal plan ID")
    user_id: str = Field(..., description="User ID")
    region_name: Optional[str] = Field(None, description="Region name for regional ownership")
    return_url: str = Field(..., description="Success return URL")
    cancel_url: str = Field(..., description="Cancel return URL")


class PayPalWebhookEvent(BaseModel):
    """PayPal webhook event model"""
    id: str
    event_type: str
    resource: Dict[str, Any]
    create_time: str
    summary: str


class PayPalService:
    """Comprehensive PayPal subscription service for multi-regional platform"""
    
    def __init__(self):
        self.config = get_config()
        self.base_url = self._get_base_url()
        self.client_id = self.config.PAYPAL_CLIENT_ID
        self.client_secret = self.config.PAYPAL_CLIENT_SECRET
        
        # Subscription plan IDs (configured in PayPal dashboard)
        self.subscription_plans = {
            SubscriptionTier.GALACTIC_CITIZEN: self.config.PAYPAL_GALACTIC_CITIZEN_PLAN_ID,
            SubscriptionTier.REGIONAL_OWNER: self.config.PAYPAL_REGIONAL_OWNER_PLAN_ID,
            SubscriptionTier.NEXUS_PREMIUM: self.config.PAYPAL_NEXUS_PREMIUM_PLAN_ID
        }
        
        self._access_token = None
        self._token_expires_at = None
    
    def _get_base_url(self) -> str:
        """Get PayPal API base URL based on environment"""
        if self.config.ENVIRONMENT == "production":
            return "https://api-m.paypal.com"
        else:
            return "https://api-m.sandbox.paypal.com"
    
    async def _get_access_token(self) -> str:
        """Get or refresh PayPal access token"""
        if (self._access_token and self._token_expires_at and 
            datetime.utcnow() < self._token_expires_at):
            return self._access_token
        
        async with httpx.AsyncClient() as client:
            headers = {
                "Accept": "application/json",
                "Accept-Language": "en_US",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            auth = (self.client_id, self.client_secret)
            data = "grant_type=client_credentials"
            
            response = await client.post(
                f"{self.base_url}/v1/oauth2/token",
                headers=headers,
                auth=auth,
                content=data
            )
            
            if response.status_code != 200:
                logger.error("PayPal token request failed: status=%s", response.status_code)
                raise Exception(f"Failed to get PayPal access token: {response.status_code}")
            
            token_data = response.json()
            self._access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 3600)
            self._token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 300)  # 5 min buffer
            
            logger.info("PayPal access token refreshed successfully")
            return self._access_token
    
    async def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make authenticated request to PayPal API"""
        access_token = await self._get_access_token()
        
        request_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "PayPal-Request-Id": f"sectorwars-{datetime.utcnow().timestamp()}"
        }
        
        if headers:
            request_headers.update(headers)
        
        async with httpx.AsyncClient() as client:
            if method.upper() == "GET":
                response = await client.get(f"{self.base_url}{endpoint}", headers=request_headers)
            elif method.upper() == "POST":
                response = await client.post(
                    f"{self.base_url}{endpoint}", 
                    headers=request_headers,
                    json=data
                )
            elif method.upper() == "PATCH":
                response = await client.patch(
                    f"{self.base_url}{endpoint}",
                    headers=request_headers,
                    json=data
                )
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
        
        if response.status_code not in [200, 201, 202]:
            safe_endpoint = re.sub(r"subscriptions/[^/?]+", "subscriptions/[id]", endpoint)
            logger.error("PayPal API request failed: %s %s - status=%s", method, safe_endpoint, response.status_code)
            raise Exception(f"PayPal API error: {response.status_code}")
        
        return response.json()
    
    async def create_galactic_citizen_subscription(
        self, 
        user_id: str, 
        return_url: str, 
        cancel_url: str
    ) -> Dict[str, Any]:
        """Create galactic citizenship subscription ($5/month)"""
        plan_id = self.subscription_plans[SubscriptionTier.GALACTIC_CITIZEN]
        
        subscription_data = {
            "plan_id": plan_id,
            "subscriber": {
                "name": {
                    "given_name": "Player",
                    "surname": f"User-{user_id[:8]}"
                }
            },
            "application_context": {
                "brand_name": "SectorWars 2102",
                "locale": "en-US",
                "shipping_preference": "NO_SHIPPING",
                "user_action": "SUBSCRIBE_NOW",
                "payment_method": {
                    "payer_selected": "PAYPAL",
                    "payee_preferred": "IMMEDIATE_PAYMENT_REQUIRED"
                },
                "return_url": return_url,
                "cancel_url": cancel_url
            },
            "custom_id": f"galactic_citizen_{user_id}",
            "plan": {
                "id": plan_id
            }
        }
        
        result = await self._make_request("POST", "/v1/billing/subscriptions", subscription_data)
        
        logger.info("Created galactic citizen subscription for user %s: %s", user_id, _sub_ref(result.get("id")))  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable
        return result
    
    async def create_regional_ownership_subscription(
        self, 
        user_id: str, 
        region_name: str, 
        return_url: str, 
        cancel_url: str
    ) -> Dict[str, Any]:
        """Create regional ownership subscription ($25/month)"""
        plan_id = self.subscription_plans[SubscriptionTier.REGIONAL_OWNER]
        
        subscription_data = {
            "plan_id": plan_id,
            "subscriber": {
                "name": {
                    "given_name": "Governor",
                    "surname": f"User-{user_id[:8]}"
                }
            },
            "application_context": {
                "brand_name": "SectorWars 2102 - Regional Ownership",
                "locale": "en-US",
                "shipping_preference": "NO_SHIPPING",
                "user_action": "SUBSCRIBE_NOW",
                "payment_method": {
                    "payer_selected": "PAYPAL",
                    "payee_preferred": "IMMEDIATE_PAYMENT_REQUIRED"
                },
                "return_url": return_url,
                "cancel_url": cancel_url
            },
            "custom_id": f"regional_owner_{user_id}_{region_name}",
            "plan": {
                "id": plan_id
            }
        }
        
        result = await self._make_request("POST", "/v1/billing/subscriptions", subscription_data)
        
        logger.info("Created regional ownership subscription for user %s, region %s: %s", user_id, region_name, _sub_ref(result.get("id")))  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable
        return result
    
    async def get_subscription_details(self, subscription_id: str) -> Dict[str, Any]:
        """Get subscription details from PayPal"""
        result = await self._make_request("GET", f"/v1/billing/subscriptions/{subscription_id}")
        return result
    
    async def cancel_subscription(self, subscription_id: str, reason: str = "User requested cancellation") -> bool:
        """Cancel a PayPal subscription"""
        cancel_data = {
            "reason": reason
        }
        
        try:
            await self._make_request("POST", f"/v1/billing/subscriptions/{subscription_id}/cancel", cancel_data)
            logger.info("Successfully cancelled subscription %s", _sub_ref(subscription_id))  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable
            return True
        except Exception as e:
            logger.exception("Failed to cancel subscription %s", _sub_ref(subscription_id))  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable
            return False
    
    async def suspend_subscription(self, subscription_id: str, reason: str = "Payment failure") -> bool:
        """Suspend a PayPal subscription"""
        suspend_data = {
            "reason": reason
        }
        
        try:
            await self._make_request("POST", f"/v1/billing/subscriptions/{subscription_id}/suspend", suspend_data)
            logger.info("Successfully suspended subscription %s", _sub_ref(subscription_id))  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable
            return True
        except Exception as e:
            logger.exception("Failed to suspend subscription %s", _sub_ref(subscription_id))  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable
            return False
    
    async def activate_subscription(self, subscription_id: str, reason: str = "Payment resumed") -> bool:
        """Activate a suspended PayPal subscription"""
        activate_data = {
            "reason": reason
        }
        
        try:
            await self._make_request("POST", f"/v1/billing/subscriptions/{subscription_id}/activate", activate_data)
            logger.info("Successfully activated subscription %s", _sub_ref(subscription_id))  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable
            return True
        except Exception as e:
            logger.exception("Failed to activate subscription %s", _sub_ref(subscription_id))  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable
            return False
    
    async def handle_subscription_webhook(self, webhook_event: PayPalWebhookEvent) -> bool:
        """Handle PayPal webhook events for subscription lifecycle"""
        try:
            event_type = webhook_event.event_type
            resource = webhook_event.resource
            subscription_id = resource.get("id")
            
            logger.info("Processing PayPal webhook: %s for subscription %s", event_type, _sub_ref(subscription_id))  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable

            # NB: use AsyncSessionLocal() directly — get_async_session is a FastAPI
            # dependency generator (async def ... yield), not an async context
            # manager, so `async with get_async_session()` raises.
            async with AsyncSessionLocal() as session:
                # Idempotency: record this event id first, in the SAME transaction
                # as the mutation below. A duplicate delivery (PayPal retries
                # aggressively) collides on the unique primary key, so we skip it
                # instead of applying the effect twice. Insert-as-dedup means
                # there's no SELECT-then-INSERT race window.
                session.add(ProcessedWebhookEvent(
                    event_id=webhook_event.id, event_type=event_type
                ))
                try:
                    await session.flush()
                except IntegrityError:
                    await session.rollback()
                    logger.info(
                        "Duplicate PayPal webhook %s (%s) ignored", webhook_event.id, event_type
                    )
                    return True

                if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
                    await self._handle_subscription_activated(session, resource)
                elif event_type == "BILLING.SUBSCRIPTION.PAYMENT.SUCCEEDED":
                    await self._handle_subscription_renewed(session, resource)
                elif event_type == "BILLING.SUBSCRIPTION.CANCELLED":
                    await self._handle_subscription_cancelled(session, resource)
                elif event_type == "BILLING.SUBSCRIPTION.SUSPENDED":
                    await self._handle_subscription_suspended(session, resource)
                elif event_type == "BILLING.SUBSCRIPTION.PAYMENT.FAILED":
                    await self._handle_payment_failed(session, resource)
                elif event_type == "PAYMENT.SALE.COMPLETED":
                    await self._handle_payment_completed(session, resource)
                else:
                    logger.info(f"Unhandled webhook event type: {event_type}")

                await session.commit()

            return True
            
        except Exception as e:
            logger.exception("Failed to process PayPal webhook")
            return False
    
    async def _handle_subscription_activated(self, session: AsyncSession, resource: Dict[str, Any]):
        """Handle subscription activation"""
        subscription_id = resource["id"]
        custom_id = resource.get("custom_id", "")
        
        # Parse custom_id to determine subscription type and user
        if custom_id.startswith("galactic_citizen_"):
            user_id = custom_id.replace("galactic_citizen_", "")
            await self._activate_galactic_citizenship(session, user_id, subscription_id, resource)
        elif custom_id.startswith("regional_owner_"):
            parts = custom_id.replace("regional_owner_", "").split("_")
            user_id = parts[0]
            region_name = "_".join(parts[1:]) if len(parts) > 1 else ""
            await self._activate_regional_ownership(session, user_id, region_name, subscription_id)
    
    async def _handle_subscription_cancelled(self, session: AsyncSession, resource: Dict[str, Any]):
        """Handle subscription cancellation"""
        subscription_id = resource["id"]
        
        # Find and update region or user status
        result = await session.execute(
            select(Region).where(Region.paypal_subscription_id == subscription_id)
        )
        region = result.scalar_one_or_none()
        
        if region:
            region.status = "suspended"
            region.paypal_subscription_id = None
            logger.info(f"Suspended region {region.name} due to subscription cancellation")
        else:
            # Check for galactic citizenship
            result = await session.execute(
                select(Player).options(selectinload(Player.user))
                .join(User).where(User.paypal_subscription_id == subscription_id)
            )
            player = result.scalar_one_or_none()
            if player:
                player.is_galactic_citizen = False
                if hasattr(player.user, 'paypal_subscription_id'):
                    player.user.paypal_subscription_id = None
                logger.info(f"Removed galactic citizenship for player {player.id}")
    
    async def _handle_subscription_suspended(self, session: AsyncSession, resource: Dict[str, Any]):
        """Handle subscription suspension"""
        subscription_id = resource["id"]
        
        # Temporarily suspend region access
        result = await session.execute(
            select(Region).where(Region.paypal_subscription_id == subscription_id)
        )
        region = result.scalar_one_or_none()
        
        if region:
            region.status = "suspended"
            logger.info(f"Suspended region {region.name} due to payment suspension")
    
    async def _handle_payment_failed(self, session: AsyncSession, resource: Dict[str, Any]):
        """Handle payment failure"""
        subscription_id = resource.get("billing_agreement_id")
        if not subscription_id:
            return
        
        # Log payment failure and potentially suspend after multiple failures.
        logger.warning("Payment failed for subscription %s", _sub_ref(subscription_id))  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable
        
        # You could implement a failure counter and suspend after X failures
        # For now, just log the event
    
    async def _handle_payment_completed(self, session: AsyncSession, resource: Dict[str, Any]):
        """Handle successful payment"""
        subscription_id = resource.get("billing_agreement_id")
        if not subscription_id:
            return
        
        amount = resource.get("amount", {}).get("total", "0.00")
        logger.info("Payment completed for subscription %s: $%s", _sub_ref(subscription_id), amount)  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable
        
        # Ensure region/citizenship remains active
        result = await session.execute(
            select(Region).where(Region.paypal_subscription_id == subscription_id)
        )
        region = result.scalar_one_or_none()
        
        if region and region.status == "suspended":
            region.status = "active"
            logger.info(f"Reactivated region {region.name} after successful payment")
    
    @staticmethod
    def _next_expiry(resource: Dict[str, Any]) -> datetime:
        """Compute the new subscription expiry from a PayPal subscription resource.

        Prefers the provider's authoritative ``billing_info.next_billing_time``;
        falls back to ~one billing month from now when absent. Returns a NAIVE UTC
        datetime: the ``subscription_expires_at`` column is TIMESTAMP WITHOUT TIME
        ZONE, and the auth-layer expiry check treats stored values as UTC, so we
        normalise to naive-UTC here to keep the whole path tz-consistent.
        """
        next_billing = (resource.get("billing_info") or {}).get("next_billing_time")
        if next_billing:
            try:
                dt = datetime.fromisoformat(str(next_billing).replace("Z", "+00:00"))
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            except (ValueError, TypeError):
                logger.warning("Unparseable next_billing_time on PayPal resource; using fallback")
        return (datetime.now(timezone.utc) + timedelta(days=31)).replace(tzinfo=None)

    async def _activate_galactic_citizenship(self, session: AsyncSession, user_id: str, subscription_id: str, resource: Dict[str, Any]):
        """Activate galactic citizenship for user"""
        result = await session.execute(
            select(Player).options(selectinload(Player.user))
            .where(Player.user_id == user_id)
        )
        player = result.scalar_one_or_none()

        if player:
            player.is_galactic_citizen = True
            if player.user is not None:
                user = player.user
                user.paypal_subscription_id = subscription_id
                user.subscription_tier = SubscriptionTier.GALACTIC_CITIZEN.value
                user.subscription_status = "active"
                if user.subscription_started_at is None:
                    user.subscription_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
                user.subscription_expires_at = self._next_expiry(resource)
            logger.info(f"Activated galactic citizenship for player {player.id}")

    async def _handle_subscription_renewed(self, session: AsyncSession, resource: Dict[str, Any]):
        """Handle a successful recurring subscription payment (renewal).

        ``BILLING.SUBSCRIPTION.PAYMENT.SUCCEEDED`` is the canonical renewal event
        (ARCHITECTURE/async-workers.md): it extends ``subscription_expires_at`` and
        re-affirms citizenship so a lapse-and-recover cycle restores access.
        """
        subscription_id = resource.get("id") or resource.get("billing_agreement_id")
        if not subscription_id:
            logger.warning("Renewal webhook missing subscription id; skipping")
            return

        result = await session.execute(
            select(Player).options(selectinload(Player.user))
            .join(User).where(User.paypal_subscription_id == subscription_id)
        )
        player = result.scalar_one_or_none()
        if player is None:
            logger.info(
                "Renewal for subscription %s matched no citizen (may be a region sub)",
                _sub_ref(subscription_id),  # lgtm[py/clear-text-logging-sensitive-data] -- _sub_ref() is SHA-256, non-recoverable
            )
            return

        player.is_galactic_citizen = True
        if player.user is not None:
            user = player.user
            user.subscription_status = "active"
            user.subscription_expires_at = self._next_expiry(resource)
        logger.info("Renewed galactic citizenship for player %s", player.id)
    
    async def _activate_regional_ownership(
        self, 
        session: AsyncSession, 
        user_id: str, 
        region_name: str, 
        subscription_id: str
    ):
        """Activate regional ownership for user"""
        # Create or update region
        result = await session.execute(
            select(Region).where(Region.name == region_name)
        )
        region = result.scalar_one_or_none()
        
        if not region:
            # Create new region
            region = Region(
                name=region_name,
                display_name=region_name.replace("_", " ").title(),
                owner_id=user_id,
                subscription_tier="regional_owner",
                paypal_subscription_id=subscription_id,
                status="active"
            )
            session.add(region)
            logger.info(f"Created new region {region_name} for user {user_id}")
        else:
            # Update existing region
            region.owner_id = user_id
            region.paypal_subscription_id = subscription_id
            region.status = "active"
            logger.info(f"Updated region {region_name} ownership for user {user_id}")
    
    async def get_user_subscriptions(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all active subscriptions for a user"""
        subscriptions = []

        async with AsyncSessionLocal() as session:
            # Check for galactic citizenship
            result = await session.execute(
                select(Player).options(selectinload(Player.user))
                .where(Player.user_id == user_id)
            )
            player = result.scalar_one_or_none()
            
            if player and player.is_galactic_citizen:
                user_subscription_id = getattr(player.user, 'paypal_subscription_id', None)
                if user_subscription_id:
                    try:
                        details = await self.get_subscription_details(user_subscription_id)
                        subscriptions.append({
                            "type": "galactic_citizen",
                            "subscription_id": user_subscription_id,
                            "status": details.get("status"),
                            "amount": "$5.00/month"
                        })
                    except Exception as e:
                        logger.exception("Failed to get galactic subscription details")
            
            # Check for owned regions
            result = await session.execute(
                select(Region).where(Region.owner_id == user_id)
            )
            regions = result.scalars().all()
            
            for region in regions:
                if region.paypal_subscription_id:
                    try:
                        details = await self.get_subscription_details(region.paypal_subscription_id)
                        subscriptions.append({
                            "type": "regional_owner",
                            "region_name": region.display_name,
                            "subscription_id": region.paypal_subscription_id,
                            "status": details.get("status"),
                            "amount": "$25.00/month"
                        })
                    except Exception as e:
                        logger.exception("Failed to get region subscription details")
        
        return subscriptions
    
    async def validate_webhook_signature(self, headers: Dict[str, str], body: str) -> bool:
        """Validate PayPal webhook signature for security"""
        try:
            # Extract required headers
            transmission_id = headers.get('PAYPAL-TRANSMISSION-ID')
            cert_id = headers.get('PAYPAL-CERT-ID')
            transmission_sig = headers.get('PAYPAL-TRANSMISSION-SIG')
            transmission_time = headers.get('PAYPAL-TRANSMISSION-TIME')
            auth_algo = headers.get('PAYPAL-AUTH-ALGO', 'SHA256withRSA')
            
            # Validate required headers are present
            if not all([transmission_id, cert_id, transmission_sig, transmission_time]):
                logger.error("Missing required PayPal webhook headers")
                return False
            
            # Allow bypass only with explicit opt-in env var, and only outside
            # production. The import-time guard already refuses to boot a prod
            # server with the flag set; this is defence-in-depth in case the
            # environment flips at runtime.
            if os.environ.get(WEBHOOK_BYPASS_ENV, "").strip().lower() == "true":
                if str(self.config.ENVIRONMENT).strip().lower() == "production":
                    logger.error(
                        "%s is set in production — refusing to bypass webhook validation",
                        WEBHOOK_BYPASS_ENV,
                    )
                    return False
                logger.warning(
                    "PayPal webhook signature validation bypassed — %s is set (non-production)",
                    WEBHOOK_BYPASS_ENV,
                )
                return True
            
            # For production, implement proper signature validation
            # This requires PayPal webhook ID from config
            webhook_id = self.config.PAYPAL_WEBHOOK_ID
            if not webhook_id:
                logger.error("PAYPAL_WEBHOOK_ID not configured - webhook validation failed")
                return False
            
            # Create verification payload
            verification_data = {
                "transmission_id": transmission_id,
                "cert_id": cert_id,
                "auth_algo": auth_algo,
                "transmission_sig": transmission_sig,
                "transmission_time": transmission_time,
                "webhook_id": webhook_id,
                "webhook_event": json.loads(body)
            }
            
            # Call PayPal webhook verification API
            verify_url = f"{self.base_url}/v1/notifications/verify-webhook-signature"
            headers_req = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {await self._get_access_token()}"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    verify_url,
                    json=verification_data,
                    headers=headers_req,
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    verification_status = result.get("verification_status")
                    
                    if verification_status == "SUCCESS":
                        logger.info("PayPal webhook signature validation successful")
                        return True
                    else:
                        logger.error(f"PayPal webhook signature validation failed: {verification_status}")
                        return False
                else:
                    logger.error("PayPal verification API error: status=%s", response.status_code)
                    return False

        except Exception as e:
            logger.exception("PayPal webhook signature validation error")
            return False


# Singleton instance for use across the application
paypal_service = PayPalService()