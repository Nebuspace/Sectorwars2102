"""PayPal subscription service for multi-regional platform monetization"""

import asyncio
import json
import os
import httpx
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field
from enum import Enum

from src.core.database import get_async_session
from src.core.config import get_config
from src.models.region import Region
from src.models.player import Player
from src.models.user import User

import logging

logger = logging.getLogger(__name__)


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
                logger.error(f"PayPal token request failed: {response.text}")
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
            logger.error(f"PayPal API request failed: {method} {endpoint} - {response.status_code} - {response.text}")
            raise Exception(f"PayPal API error: {response.status_code} - {response.text}")
        
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
        
        logger.info(f"Created galactic citizen subscription for user {user_id}: {result['id']}")
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
        
        logger.info(f"Created regional ownership subscription for user {user_id}, region {region_name}: {result['id']}")
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
            logger.info(f"Successfully cancelled subscription {subscription_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel subscription {subscription_id}: {str(e)}")
            return False
    
    async def suspend_subscription(self, subscription_id: str, reason: str = "Payment failure") -> bool:
        """Suspend a PayPal subscription"""
        suspend_data = {
            "reason": reason
        }
        
        try:
            await self._make_request("POST", f"/v1/billing/subscriptions/{subscription_id}/suspend", suspend_data)
            logger.info(f"Successfully suspended subscription {subscription_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to suspend subscription {subscription_id}: {str(e)}")
            return False
    
    async def activate_subscription(self, subscription_id: str, reason: str = "Payment resumed") -> bool:
        """Activate a suspended PayPal subscription"""
        activate_data = {
            "reason": reason
        }
        
        try:
            await self._make_request("POST", f"/v1/billing/subscriptions/{subscription_id}/activate", activate_data)
            logger.info(f"Successfully activated subscription {subscription_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to activate subscription {subscription_id}: {str(e)}")
            return False
    
    async def handle_subscription_webhook(self, webhook_event: PayPalWebhookEvent) -> bool:
        """Handle PayPal webhook events for subscription lifecycle"""
        try:
            event_type = webhook_event.event_type
            resource = webhook_event.resource
            subscription_id = resource.get("id")
            
            logger.info(f"Processing PayPal webhook: {event_type} for subscription {subscription_id}")
            
            async with get_async_session() as session:
                if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
                    await self._handle_subscription_activated(session, resource)
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
            logger.error(f"Failed to process PayPal webhook: {str(e)}")
            return False
    
    async def _handle_subscription_activated(self, session: AsyncSession, resource: Dict[str, Any]):
        """Handle subscription activation"""
        subscription_id = resource["id"]
        custom_id = resource.get("custom_id", "")
        
        # Parse custom_id to determine subscription type and user
        if custom_id.startswith("galactic_citizen_"):
            user_id = custom_id.replace("galactic_citizen_", "")
            await self._activate_galactic_citizenship(session, user_id, subscription_id)
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
        # Redact most of the subscription id to keep operator logs free of
        # clear-text billing identifiers.
        logger.warning(f"Payment failed for subscription {_redact(subscription_id)}")
        
        # You could implement a failure counter and suspend after X failures
        # For now, just log the event
    
    async def _handle_payment_completed(self, session: AsyncSession, resource: Dict[str, Any]):
        """Handle successful payment"""
        subscription_id = resource.get("billing_agreement_id")
        if not subscription_id:
            return
        
        amount = resource.get("amount", {}).get("total", "0.00")
        logger.info(f"Payment completed for subscription {_redact(subscription_id)}: ${amount}")
        
        # Ensure region/citizenship remains active
        result = await session.execute(
            select(Region).where(Region.paypal_subscription_id == subscription_id)
        )
        region = result.scalar_one_or_none()
        
        if region and region.status == "suspended":
            region.status = "active"
            logger.info(f"Reactivated region {region.name} after successful payment")
    
    async def _activate_galactic_citizenship(self, session: AsyncSession, user_id: str, subscription_id: str):
        """Activate galactic citizenship for user"""
        result = await session.execute(
            select(Player).options(selectinload(Player.user))
            .where(Player.user_id == user_id)
        )
        player = result.scalar_one_or_none()
        
        if player:
            player.is_galactic_citizen = True
            if hasattr(player.user, 'paypal_subscription_id'):
                player.user.paypal_subscription_id = subscription_id
            logger.info(f"Activated galactic citizenship for player {player.id}")
    
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
        
        async with get_async_session() as session:
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
                        logger.warning(f"Failed to get galactic subscription details: {e}")
            
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
                        logger.warning(f"Failed to get region subscription details: {e}")
        
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
            
            # Allow bypass only with explicit opt-in environment variable
            if os.environ.get("PAYPAL_SKIP_WEBHOOK_VALIDATION", "").lower() == "true":
                logger.warning("PayPal webhook signature validation bypassed - PAYPAL_SKIP_WEBHOOK_VALIDATION is set")
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
                    logger.error(f"PayPal verification API error: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"PayPal webhook signature validation error: {e}")
            return False


# Singleton instance for use across the application
paypal_service = PayPalService()