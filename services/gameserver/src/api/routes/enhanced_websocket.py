"""
Enhanced WebSocket API Routes for Foundation Sprint Integration
Provides secure real-time trading and AI features

OWASP Security Implementation:
- A01: Proper access control with JWT validation
- A03: Input validation and sanitization
- A04: Rate limiting and business logic validation
- A07: Strong authentication for WebSocket connections
- A08: Message integrity with HMAC signatures
- A09: Comprehensive audit logging
"""

import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, UTC

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis

from sqlalchemy.orm import Session
from src.core.database import get_async_db, get_async_session, get_db
from src.auth.dependencies import get_current_user, validate_websocket_token
from src.services.enhanced_websocket_service import get_enhanced_websocket_service
from src.services.audit_service import AuditService
from src.models.player import Player
from src.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ws", tags=["websocket"])

# Security scheme for WebSocket authentication
security = HTTPBearer()

# Redis client for pub/sub
redis_client = None


async def get_redis_client():
    """Get or create Redis client for WebSocket pub/sub"""
    global redis_client
    if redis_client is None:
        redis_client = await redis.from_url(
            settings.REDIS_URL or "redis://localhost:6379",
            encoding="utf-8",
            decode_responses=True
        )
    return redis_client


@router.websocket("/trading/{player_id}")
async def enhanced_trading_websocket(
    websocket: WebSocket,
    player_id: str,
    token: str = Query(..., description="JWT authentication token"),
    db: AsyncSession = Depends(get_async_session)
):
    """
    Enhanced WebSocket endpoint for Foundation Sprint real-time trading
    
    Features:
    - Real-time market data streaming
    - Secure trading command execution
    - AI-powered trading signals
    - ARIA conversational interface
    - Trading automation rules
    
    Security:
    - JWT token validation
    - Rate limiting per connection
    - Message signature validation
    - Comprehensive audit logging
    """
    
    # Initialize services
    redis = await get_redis_client()
    websocket_service = get_enhanced_websocket_service(redis)
    audit_service = AuditService()
    
    # Client information for security
    client_info = {
        "ip": websocket.client.host if websocket.client else "unknown",
        "user_agent": websocket.headers.get("user-agent", "unknown")
    }
    
    try:
        # Validate JWT token (OWASP A07)
        try:
            player = await validate_websocket_token(token, db)
            if not player or str(player.id) != player_id:
                await websocket.close(code=4001, reason="Invalid authentication")
                logger.warning(f"WebSocket auth failed for player {player_id}")
                await audit_service.log_event(
                    db,
                    event_type="websocket_auth_failed",
                    player_id=player_id,
                    details={"ip": client_info["ip"]}
                )
                return
        except Exception as e:
            logger.error(f"WebSocket authentication error: {e}")
            await websocket.close(code=4001, reason="Authentication error")
            return
        
        # Establish connection
        connected = await websocket_service.connect(
            websocket=websocket,
            player_id=player_id,
            auth_token=token,
            client_info=client_info
        )
        
        if not connected:
            return
        
        # Log successful connection
        await audit_service.log_event(
            db,
            event_type="websocket_connected",
            player_id=player_id,
            details={
                "endpoint": "enhanced_trading",
                "ip": client_info["ip"],
                "features": ["real_time_market", "ai_trading", "secure_trading"]
            }
        )
        
        # Message handling loop
        while True:
            try:
                # Receive message
                raw_message = await websocket.receive_text()
                
                # Parse and validate message (OWASP A03)
                try:
                    message = json.loads(raw_message)
                    
                    # Sanitize message content
                    message = _sanitize_message(message)
                    
                except json.JSONDecodeError:
                    await websocket_service.send_error(
                        player_id,
                        "Invalid JSON format"
                    )
                    continue
                
                # Log high-value operations
                if message.get("type") in ["trading_command", "automation_rule"]:
                    await audit_service.log_event(
                        db,
                        event_type=f"websocket_{message['type']}",
                        player_id=player_id,
                        details={
                            "message_type": message.get("type"),
                            "command": message.get("command"),
                            "timestamp": datetime.now(UTC).isoformat()
                        }
                    )
                
                # Handle message
                await websocket_service.handle_message(player_id, message, db)
                
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected for player {player_id}")
                break
            
            except Exception as e:
                logger.error(f"Error processing WebSocket message: {e}")
                await websocket_service.send_error(
                    player_id,
                    "Message processing error"
                )
    
    except Exception as e:
        logger.error(f"WebSocket error for player {player_id}: {e}")
    
    finally:
        # Clean disconnect
        await websocket_service.disconnect(player_id)
        
        # Log disconnection
        try:
            await audit_service.log_event(
                db,
                event_type="websocket_disconnected",
                player_id=player_id,
                details={"endpoint": "enhanced_trading"}
            )
        except Exception as e:
            logger.warning(f"Failed to log WebSocket disconnect audit event: {e}")


@router.websocket("/market-stream")
async def public_market_stream(
    websocket: WebSocket,
    token: str = Query(..., description="JWT authentication token"),
    commodities: str = Query("ALL", description="Comma-separated commodity list"),
    db: Session = Depends(get_db)
):
    """
    Authenticated WebSocket endpoint for real-time market data streaming.
    Requires JWT token. Read-only market data.

    Features:
    - Real-time price updates
    - Market trends and volumes
    - AI predictions (public data only)
    """
    client_ip = websocket.client.host if websocket.client else "unknown"

    # Authenticate before accepting connection
    if not token:
        await websocket.close(code=4001, reason="Authentication token required")
        return

    try:
        from src.auth.dependencies import get_current_user_from_token
        user = await get_current_user_from_token(token, db)
        if not user:
            await websocket.close(code=4001, reason="Invalid authentication token")
            return
    except Exception:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    try:
        await websocket.accept()
        
        # Parse commodities
        if commodities == "ALL":
            commodity_list = ["ORE", "ORGANICS", "EQUIPMENT", "FUEL", "LUXURY", "TECHNOLOGY"]
        else:
            commodity_list = [c.strip().upper() for c in commodities.split(",")]
            valid_commodities = ["ORE", "ORGANICS", "EQUIPMENT", "FUEL", "LUXURY", "TECHNOLOGY"]
            commodity_list = [c for c in commodity_list if c in valid_commodities]
        
        if not commodity_list:
            await websocket.send_json({
                "type": "error",
                "message": "No valid commodities specified"
            })
            await websocket.close()
            return
        
        # Send initial connection message
        await websocket.send_json({
            "type": "connection_established",
            "commodities": commodity_list,
            "update_interval": 1000,  # milliseconds
            "timestamp": datetime.now(UTC).isoformat()
        })
        
        logger.info(f"Public market stream connected from {client_ip} for {commodity_list}")
        
        # Market data streaming loop
        redis = await get_redis_client()
        pubsub = redis.pubsub()
        
        # Subscribe to market channels
        channels = [f"market:{commodity}" for commodity in commodity_list]
        await pubsub.subscribe(*channels)
        
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    # Forward market data to client
                    market_data = json.loads(message["data"])
                    await websocket.send_json({
                        "type": "market_update",
                        "commodity": message["channel"].split(":")[1],
                        "data": market_data,
                        "timestamp": datetime.now(UTC).isoformat()
                    })
                    
                # Check for client heartbeat
                try:
                    # Non-blocking receive to check connection
                    await websocket.receive_text()
                except Exception as e:
                    logger.warning(f"Failed to check WebSocket client heartbeat: {e}")

        except WebSocketDisconnect:
            logger.info(f"Public market stream disconnected from {client_ip}")
            
    except Exception as e:
        logger.error(f"Public market stream error: {e}")
        
    finally:
        if 'pubsub' in locals():
            await pubsub.unsubscribe()
            await pubsub.close()


def _sanitize_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize WebSocket message content (OWASP A03)
    Prevents XSS, injection attacks, and malicious content
    """
    sanitized = {}
    
    for key, value in message.items():
        # Sanitize keys
        if not isinstance(key, str) or len(key) > 50:
            continue
        
        key = key.strip()
        if not key or any(char in key for char in ["<", ">", "'", '"', "&"]):
            continue
        
        # Sanitize values based on type
        if isinstance(value, str):
            # Limit string length
            value = value[:1000]
            # Remove potential XSS
            value = value.replace("<", "&lt;").replace(">", "&gt;")
            value = value.replace("javascript:", "").replace("data:", "")
            
        elif isinstance(value, (int, float)):
            # Validate numeric ranges
            if isinstance(value, int) and abs(value) > 10**9:
                continue
            if isinstance(value, float) and abs(value) > 10**15:
                continue
                
        elif isinstance(value, list):
            # Limit list size
            value = value[:100]
            # Recursively sanitize list items
            value = [_sanitize_value(item) for item in value]
            
        elif isinstance(value, dict):
            # Limit nested depth
            if len(str(value)) > 10000:
                continue
            # Recursively sanitize
            value = _sanitize_message(value)
            
        else:
            # Skip unsupported types
            continue
        
        sanitized[key] = value
    
    return sanitized


def _sanitize_value(value: Any) -> Any:
    """Sanitize individual values"""
    if isinstance(value, str):
        return value[:500].replace("<", "&lt;").replace(">", "&gt;")
    elif isinstance(value, (int, float, bool, type(None))):
        return value
    else:
        return str(value)[:100]


# Health check endpoint for WebSocket service
@router.get("/health")
async def websocket_health():
    """Check WebSocket service health"""
    try:
        redis = await get_redis_client()
        await redis.ping()
        
        return {
            "status": "healthy",
            "service": "enhanced_websocket",
            "features": [
                "real_time_market_data",
                "secure_trading",
                "ai_integration",
                "aria_chat",
                "automation_rules"
            ],
            "timestamp": datetime.now(UTC).isoformat()
        }
    except Exception as e:
        logger.error(f"WebSocket health check failed: {e}")
        return {
            "status": "unhealthy",
            # Don't leak traceback to health probes (py/stack-trace-exposure).
            "error": "WebSocket health check failed",
            "timestamp": datetime.now(UTC).isoformat()
        }