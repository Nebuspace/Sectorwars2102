"""
Enhanced WebSocket Service for Backend Integration Bridge
Connects Foundation Sprint frontend to live game server APIs with enterprise security

This service extends the existing WebSocket infrastructure to support:
- Real-time market data streaming
- AI-powered trading signals
- Secure trading command execution
- ARIA conversational interface
- OWASP-compliant security measures
"""

import json
import asyncio
import hashlib
import hmac
import os
import time
from typing import Dict, List, Set, Optional, Any, Union
from datetime import datetime, timedelta, UTC
from dataclasses import dataclass, asdict
from collections import defaultdict
from uuid import uuid4
import logging

from fastapi import WebSocket, WebSocketDisconnect, HTTPException
import jwt as jose_jwt
from jwt import PyJWTError as JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
import redis.asyncio as redis

# Import existing services
from src.services.websocket_service import ConnectionManager, connection_manager
from src.services.ai_trading_service import AITradingService
from src.services.trading_service import TradingService
from src.services.enhanced_ai_service import EnhancedAIService
from src.services.realtime_market_service import RealTimeMarketService, get_market_service
from src.services.redis_pubsub_service import RedisPubSubService, get_pubsub_service
from src.models.player import Player
from src.models.user import User
from src.models.market_transaction import MarketTransaction
from src.models.ai_trading import AIMarketPrediction
from src.core.config import settings
from src.core.database import AsyncSessionLocal
from src.core.security import verify_password

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Rate limiting configuration for WebSocket messages"""
    max_messages_per_second: int = 100
    max_trading_commands_per_minute: int = 30
    max_ai_requests_per_minute: int = 60
    burst_allowance: int = 10


@dataclass
class WebSocketSession:
    """Enhanced WebSocket session with security context"""
    session_id: str
    player_id: str
    connection_time: datetime
    last_activity: datetime
    ip_address: str
    user_agent: str
    authenticated: bool = False
    permissions: Set[str] = None
    rate_limiter: Dict[str, List[float]] = None
    
    def __post_init__(self):
        if self.permissions is None:
            self.permissions = set()
        if self.rate_limiter is None:
            self.rate_limiter = defaultdict(list)


class EnhancedWebSocketService:
    """
    Enhanced WebSocket Service for Foundation Sprint Integration
    Provides real-time trading, AI assistance, and market data streaming
    """
    
    def __init__(self, redis_client: redis.Redis = None):
        # Leverage existing connection manager
        self.connection_manager = connection_manager
        
        # Redis for pub/sub and caching
        self.redis = redis_client
        
        # Service dependencies
        self.ai_trading_service = None  # Lazy loaded
        self.trading_service = None     # Lazy loaded
        self.enhanced_ai_service = None # Lazy loaded
        self.market_service = get_market_service(redis_client)  # Real-time market data
        
        # Security configuration
        self.rate_limit_config = RateLimitConfig()
        
        # Session management
        self.sessions: Dict[str, WebSocketSession] = {}
        
        # Market data subscriptions
        self.market_subscriptions: Dict[str, Set[str]] = defaultdict(set)  # player_id -> commodities
        
        # Message signing secret loaded from environment/config
        self.message_secret = os.environ.get("WS_MESSAGE_SECRET", settings.JWT_SECRET)
        
        logger.info("Enhanced WebSocket Service initialized for Foundation Sprint")
    
    # =============================================================================
    # CONNECTION MANAGEMENT
    # =============================================================================
    
    async def connect(self, websocket: WebSocket, player_id: str, 
                     auth_token: str, client_info: Dict[str, str]) -> bool:
        """
        Establish secure WebSocket connection with authentication
        OWASP A01 & A07 compliance
        """
        try:
            # Create session
            session = WebSocketSession(
                session_id=str(uuid4()),
                player_id=player_id,
                connection_time=datetime.now(UTC),
                last_activity=datetime.now(UTC),
                ip_address=client_info.get("ip", "unknown"),
                user_agent=client_info.get("user_agent", "unknown")
            )
            
            # Validate authentication
            if not await self._validate_auth_token(player_id, auth_token):
                logger.warning(f"Invalid auth token for player {player_id}")
                await websocket.close(code=4001, reason="Authentication failed")
                return False
            
            session.authenticated = True
            
            # Load player permissions
            session.permissions = await self._load_player_permissions(player_id)
            
            # Store session
            self.sessions[player_id] = session
            
            # Accept connection using base connection manager
            user_data = {
                "username": await self._get_player_username(player_id),
                "session_id": session.session_id,
                "foundation_sprint": True,  # Mark as enhanced connection
                "permissions": list(session.permissions)
            }
            
            await self.connection_manager.connect(websocket, player_id, user_data)
            
            # Send connection confirmation with capabilities
            await self.send_message(player_id, {
                "type": "connection_established",
                "session_id": session.session_id,
                "capabilities": {
                    "real_time_market": True,
                    "ai_trading": True,
                    "secure_trading": True,
                    "aria_chat": True,
                    "automation_rules": True
                },
                "rate_limits": {
                    "messages_per_second": self.rate_limit_config.max_messages_per_second,
                    "trades_per_minute": self.rate_limit_config.max_trading_commands_per_minute,
                    "ai_requests_per_minute": self.rate_limit_config.max_ai_requests_per_minute
                }
            })
            
            logger.info(f"Player {player_id} connected with enhanced WebSocket session {session.session_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error establishing WebSocket connection: {e}")
            await websocket.close(code=4000, reason="Connection error")
            return False
    
    async def disconnect(self, player_id: str):
        """Clean up enhanced WebSocket session"""
        try:
            # Remove session
            if player_id in self.sessions:
                del self.sessions[player_id]
            
            # Clear subscriptions
            if player_id in self.market_subscriptions:
                del self.market_subscriptions[player_id]
            
            # Use base disconnect
            await self.connection_manager.disconnect(player_id)
            
            logger.info(f"Player {player_id} disconnected from enhanced WebSocket")
            
        except Exception as e:
            logger.error(f"Error during WebSocket disconnect: {e}")
    
    # =============================================================================
    # MESSAGE HANDLING WITH SECURITY
    # =============================================================================
    
    async def handle_message(self, player_id: str, message: Dict[str, Any], db: AsyncSession):
        """
        Handle incoming WebSocket messages with comprehensive security
        OWASP A03 & A04 compliance
        """
        try:
            # Get session
            session = self.sessions.get(player_id)
            if not session or not session.authenticated:
                logger.warning(f"Unauthenticated message from {player_id}")
                return
            
            # Update activity
            session.last_activity = datetime.now(UTC)
            
            # Validate message structure
            if not self._validate_message_structure(message):
                await self.send_error(player_id, "Invalid message structure")
                return
            
            # Check rate limiting
            message_type = message.get("type")
            if not self._check_rate_limit(session, message_type):
                await self.send_error(player_id, "Rate limit exceeded", code="RATE_LIMIT")
                return
            
            # Validate message signature (OWASP A08)
            if not self._validate_message_signature(message):
                await self.send_error(player_id, "Invalid message signature")
                return
            
            # Route message to appropriate handler
            if message_type == "market_subscribe":
                await self._handle_market_subscribe(player_id, message, db)
            
            elif message_type == "market_unsubscribe":
                await self._handle_market_unsubscribe(player_id, message)
            
            elif message_type == "trading_command":
                await self._handle_trading_command(player_id, message, db, session)
            
            elif message_type == "ai_request":
                await self._handle_ai_request(player_id, message, db, session)
            
            elif message_type == "aria_chat":
                await self._handle_aria_chat(player_id, message, db, session)
            
            elif message_type == "automation_rule":
                await self._handle_automation_rule(player_id, message, db, session)

            elif message_type == "heartbeat":
                await self._handle_heartbeat(player_id)
            
            else:
                # Fall back to base handler for standard messages
                await self.connection_manager.handle_websocket_message(player_id, message)
                
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")
            await self.send_error(player_id, "Internal server error")
    
    # =============================================================================
    # MARKET DATA STREAMING
    # =============================================================================
    
    async def _handle_market_subscribe(self, player_id: str, message: Dict[str, Any], db: AsyncSession):
        """Subscribe to real-time market data for commodities"""
        try:
            commodities = message.get("commodities", [])
            if not commodities or not isinstance(commodities, list):
                await self.send_error(player_id, "Invalid commodities list")
                return
            
            # Validate commodities (canonical from RESOURCE_TYPES.md)
            valid_commodities = [
                "ORE", "BASIC_FOOD", "GOURMET_FOOD", "FUEL",
                "TECHNOLOGY", "EXOTIC_TECHNOLOGY", "LUXURY_GOODS"
            ]
            commodities = [c.upper() for c in commodities if c.upper() in valid_commodities]
            
            if not commodities:
                await self.send_error(player_id, "No valid commodities specified")
                return
            
            # Update subscriptions
            self.market_subscriptions[player_id].update(commodities)
            
            # Send initial market data
            market_data = await self._get_current_market_data(commodities, db)
            await self.send_message(player_id, {
                "type": "market_data",
                "action": "initial",
                "data": market_data
            })
            
            # Start streaming if not already active
            asyncio.create_task(self._stream_market_updates(player_id, db))
            
            logger.info(f"Player {player_id} subscribed to commodities: {commodities}")
            
        except Exception as e:
            logger.error(f"Error handling market subscribe: {e}")
            await self.send_error(player_id, "Failed to subscribe to market data")
    
    async def _handle_market_unsubscribe(self, player_id: str, message: Dict[str, Any]):
        """Unsubscribe from market data"""
        commodities = message.get("commodities", [])
        if commodities:
            for commodity in commodities:
                self.market_subscriptions[player_id].discard(commodity.upper())
        else:
            # Clear all subscriptions
            self.market_subscriptions[player_id].clear()
    
    async def _stream_market_updates(self, player_id: str, db: AsyncSession):
        """Stream real-time market updates to subscribed player using Redis Pub/Sub"""
        
        async def market_callback(update: Dict[str, Any]):
            """Callback for pub/sub service to send updates"""
            try:
                # The update already comes formatted from the pub/sub service
                await self.send_message(player_id, update)
            except Exception as e:
                logger.error(f"Error sending market update to player {player_id}: {e}")
        
        try:
            # Get commodities player is subscribed to
            commodities = list(self.market_subscriptions.get(player_id, []))
            if not commodities:
                return
            
            # Get pub/sub service
            pubsub_service = await get_pubsub_service()
            
            # Subscribe to market updates via Redis pub/sub
            # This will run until the player disconnects
            await pubsub_service.subscribe_to_market_updates(
                commodities=commodities,
                callback=market_callback,
                player_id=player_id
            )
            
        except Exception as e:
            logger.error(f"Error streaming market updates: {e}")
        finally:
            # Clean up subscription when done
            try:
                pubsub_service = await get_pubsub_service()
                await pubsub_service.unsubscribe_player(player_id)
            except:
                pass  # Don't fail on cleanup
    
    async def _get_current_market_data(self, commodities: List[str], db: AsyncSession) -> Dict[str, Any]:
        """Get current market data for specified commodities using RealTimeMarketService"""
        try:
            # Use the market service to get comprehensive market data
            market_data = await self.market_service.get_multi_commodity_data(commodities, db)
            
            # Convert to WebSocket format
            formatted_data = {}
            for commodity, snapshot in market_data.items():
                formatted_data[commodity] = {
                    "current_price": snapshot.current_price,
                    "volume_24h": snapshot.volume_24h,
                    "price_24h_ago": snapshot.current_price - snapshot.price_change_24h,
                    "high_24h": snapshot.high_24h,
                    "low_24h": snapshot.low_24h,
                    "price_change_24h": snapshot.price_change_24h,
                    "price_change_percent": snapshot.price_change_percent,
                    "bid_ask_spread": snapshot.bid_ask_spread,
                    "market_depth": snapshot.market_depth,
                    "sector_prices": snapshot.sector_prices,
                    "last_transaction": snapshot.last_transaction.isoformat(),
                    "prediction": snapshot.ai_prediction
                }
                
                # Add trading signals if available
                signals = await self.market_service.generate_trading_signals(commodity, snapshot)
                if signals:
                    formatted_data[commodity]["signals"] = [
                        {
                            "type": s.signal_type,
                            "strength": s.strength,
                            "reason": s.reason,
                            "target_price": s.target_price,
                            "confidence": s.confidence
                        } for s in signals
                    ]
            
            return formatted_data
            
        except Exception as e:
            logger.error(f"Error getting market data: {e}")
            # Return empty data on error
            return {commodity: {} for commodity in commodities}
    
    async def _get_market_updates(self, commodities: List[str], db: AsyncSession) -> Dict[str, Any]:
        """Get incremental market updates using the market service"""
        # The market service handles caching and change detection
        return await self._get_current_market_data(commodities, db)
    
    # =============================================================================
    # TRADING COMMAND EXECUTION
    # =============================================================================
    
    async def _handle_trading_command(self, player_id: str, message: Dict[str, Any], 
                                    db: AsyncSession, session: WebSocketSession):
        """
        Handle secure trading commands from Foundation Sprint interface
        OWASP A01, A03, A04 compliance
        """
        try:
            # Check trading permission
            if "trading" not in session.permissions:
                await self.send_error(player_id, "Trading permission required")
                return
            
            command_type = message.get("command")
            if command_type not in ["buy", "sell", "cancel"]:
                await self.send_error(player_id, "Invalid trading command")
                return
            
            # Validate trade data
            trade_data = message.get("data", {})
            if not self._validate_trade_data(trade_data, command_type):
                await self.send_error(player_id, "Invalid trade data")
                return
            
            # Initialize trading service if needed
            if not self.trading_service:
                self.trading_service = TradingService(db)
            
            # Execute trade based on command
            if command_type in ["buy", "sell"]:
                result = await self._execute_trade(player_id, command_type, trade_data, db)
            else:  # cancel
                result = await self._cancel_trade(player_id, trade_data.get("trade_id"), db)
            
            # Send result
            await self.send_message(player_id, {
                "type": "trading_result",
                "command": command_type,
                "success": result.get("success", False),
                "data": result
            })
            
            # Broadcast market update if trade succeeded
            if result.get("success") and command_type in ["buy", "sell"]:
                await self._broadcast_trade_update(result.get("trade"), db)
                
        except Exception as e:
            logger.error(f"Error handling trading command: {e}")
            await self.send_error(player_id, "Trading command failed")
    
    async def _execute_trade(self, player_id: str, action: str, trade_data: Dict[str, Any], 
                           db: AsyncSession) -> Dict[str, Any]:
        """Execute buy or sell trade using existing trading logic"""
        try:
            # Import required models
            from src.models.ship import Ship
            from src.models.station import Station
            from src.models.market_transaction import MarketPrice, TransactionType
            
            # Get player
            player = await db.get(Player, player_id)
            if not player:
                return {"success": False, "error": "Player not found"}
            
            # Verify player is docked
            if not player.is_docked:
                return {"success": False, "error": "You must be docked at a port to trade"}
            
            # Get port
            station_id = trade_data.get("station_id")
            if not station_id:
                # If station_id not provided, get from player's current location
                ship = await db.execute(
                    select(Ship).where(
                        Ship.id == player.current_ship_id,
                        Ship.owner_id == player.id
                    )
                )
                ship = ship.scalar_one_or_none()
                if ship and ship.current_port_id:
                    station_id = str(ship.current_port_id)
                else:
                    return {"success": False, "error": "Station ID required or ship must be docked"}
            
            station = await db.get(Station, station_id)
            if not port:
                return {"success": False, "error": "Station not found"}
            
            # Verify player is in the same sector as the port
            if player.current_sector_id != station.sector_id:
                return {"success": False, "error": "You must be in the same sector as the port"}
            
            # Get current ship
            ship = await db.execute(
                select(Ship).where(
                    Ship.id == player.current_ship_id,
                    Ship.owner_id == player.id
                )
            )
            current_ship = ship.scalar_one_or_none()
            if not current_ship:
                return {"success": False, "error": "No active ship found"}
            
            commodity = trade_data.get("commodity")
            quantity = int(trade_data.get("quantity", 0))
            
            # Get market price for this commodity
            market_price_query = await db.execute(
                select(MarketPrice).where(
                    MarketPrice.station_id == station_id,
                    MarketPrice.commodity == commodity
                )
            )
            market_price = market_price_query.scalar_one_or_none()
            if not market_price:
                return {"success": False, "error": "Commodity not available at this port"}
            
            if action == "buy":
                # Check if port has enough quantity
                if market_price.quantity_available < quantity:
                    return {
                        "success": False, 
                        "error": f"Station only has {market_price.quantity_available} units available"
                    }
                
                # Calculate total cost
                total_cost = market_price.buy_price * quantity
                
                # Check if player has enough credits
                if player.credits < total_cost:
                    return {
                        "success": False,
                        "error": f"Insufficient credits. Need {total_cost}, have {player.credits}"
                    }
                
                # Check ship cargo capacity
                current_cargo = sum(current_ship.cargo.values()) if current_ship.cargo else 0
                if current_cargo + quantity > current_ship.cargo_capacity:
                    return {"success": False, "error": "Insufficient cargo space"}
                
                # Execute buy trade
                player.credits -= total_cost
                
                # Update ship cargo
                if not current_ship.cargo:
                    current_ship.cargo = {}
                current_ship.cargo[commodity] = current_ship.cargo.get(commodity, 0) + quantity
                
                # Update market quantity
                market_price.quantity_available -= quantity
                market_price.last_updated = datetime.now(UTC)
                
                price_used = market_price.buy_price
                
            else:  # sell
                # Check if player has the commodity
                if not current_ship.cargo or current_ship.cargo.get(commodity, 0) < quantity:
                    return {"success": False, "error": "Insufficient cargo to sell"}
                
                # Calculate total revenue
                total_revenue = market_price.sell_price * quantity
                
                # Execute sell trade
                player.credits += total_revenue
                
                # Update ship cargo
                current_ship.cargo[commodity] -= quantity
                if current_ship.cargo[commodity] == 0:
                    del current_ship.cargo[commodity]
                
                # Update market quantity
                market_price.quantity_available += quantity
                market_price.last_updated = datetime.now(UTC)
                
                price_used = market_price.sell_price
            
            # Create transaction record
            transaction = MarketTransaction(
                player_id=player.id,
                station_id=station_id,
                transaction_type=TransactionType.BUY if action == "buy" else TransactionType.SELL,
                commodity=commodity,
                quantity=quantity,
                unit_price=price_used,
                total_value=total_cost if action == "buy" else total_revenue,
                timestamp=datetime.now(UTC)
            )
            db.add(transaction)
            
            await db.commit()
            
            return {
                "success": True,
                "trade": {
                    "id": str(transaction.id),
                    "commodity": commodity,
                    "action": action,
                    "quantity": quantity,
                    "price": price_used,
                    "total": total_cost if action == "buy" else total_revenue,
                    "timestamp": transaction.timestamp.isoformat()
                },
                "new_balance": float(player.credits),
                "cargo_update": dict(current_ship.cargo) if current_ship.cargo else {},
                "remaining_cargo_space": current_ship.cargo_capacity - sum(current_ship.cargo.values() if current_ship.cargo else [])
            }
            
        except Exception as e:
            logger.error(f"Trade execution error: {e}")
            await db.rollback()
            return {"success": False, "error": str(e)}
    
    async def _cancel_trade(self, player_id: str, trade_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Cancel pending trade order"""
        # Simplified - would need actual order management
        return {"success": True, "message": "Trade cancelled"}
    
    def _validate_trade_data(self, trade_data: Dict[str, Any], command_type: str) -> bool:
        """Validate trade data structure and values"""
        if command_type in ["buy", "sell"]:
            required = ["commodity", "quantity", "price"]
            if not all(field in trade_data for field in required):
                return False
            
            # Validate quantity and price
            try:
                quantity = int(trade_data.get("quantity", 0))
                price = float(trade_data.get("price", 0))
                if quantity <= 0 or price <= 0:
                    return False
            except (ValueError, TypeError):
                return False
            
            # Validate commodity (canonical from RESOURCE_TYPES.md)
            valid_commodities = [
                "ORE", "BASIC_FOOD", "GOURMET_FOOD", "FUEL",
                "TECHNOLOGY", "EXOTIC_TECHNOLOGY", "LUXURY_GOODS"
            ]
            if trade_data.get("commodity") not in valid_commodities:
                return False
                
        elif command_type == "cancel":
            if not trade_data.get("trade_id"):
                return False
        
        return True
    
    # =============================================================================
    # AI INTEGRATION
    # =============================================================================
    
    async def _handle_ai_request(self, player_id: str, message: Dict[str, Any], 
                               db: AsyncSession, session: WebSocketSession):
        """Handle AI trading intelligence requests"""
        try:
            request_type = message.get("request_type")
            
            # Initialize AI service if needed
            if not self.ai_trading_service:
                self.ai_trading_service = AITradingService()
            
            if request_type == "trading_recommendations":
                recommendations = await self.ai_trading_service.get_trading_recommendations(
                    db, player_id, max_recommendations=5
                )
                
                await self.send_message(player_id, {
                    "type": "ai_recommendations",
                    "data": [rec.to_dict() for rec in recommendations]
                })
                
            elif request_type == "market_prediction":
                commodity = message.get("commodity")
                if commodity:
                    prediction = await self.ai_trading_service.get_market_prediction(
                        db, commodity
                    )
                    
                    await self.send_message(player_id, {
                        "type": "ai_prediction",
                        "commodity": commodity,
                        "data": prediction.to_dict() if prediction else None
                    })
                    
        except Exception as e:
            logger.error(f"Error handling AI request: {e}")
            await self.send_error(player_id, "AI request failed")
    
    async def _handle_aria_chat(self, player_id: str, message: Dict[str, Any], 
                              db: AsyncSession, session: WebSocketSession):
        """Handle ARIA conversational AI requests with full cross-system intelligence"""
        try:
            # Initialize services if needed
            if not self.enhanced_ai_service:
                from src.services.enhanced_ai_service import get_enhanced_ai_service
                self.enhanced_ai_service = await get_enhanced_ai_service(db)
            
            user_input = message.get("content", "")
            conversation_id = message.get("conversation_id", str(uuid4()))
            context_type = message.get("context", "general")  # trading, combat, colony, port, strategic
            
            # Build conversation context with player's current state
            conversation_context = await self._build_aria_context(player_id, context_type, db)

            # Check if this is a market intelligence query
            if any(keyword in user_input.lower() for keyword in ["market", "price", "commodity", "trade"]):
                # Add real-time market data
                subscribed_commodities = list(self.market_subscriptions.get(player_id, []))
                if subscribed_commodities:
                    market_data = await self._get_current_market_data(subscribed_commodities, db)
                    conversation_context["market_data"] = market_data
            
            # Process with ARIA's enhanced intelligence
            response = await self.enhanced_ai_service.process_player_query(
                player_id=player_id,
                query=user_input,
                conversation_id=conversation_id,
                context=conversation_context
            )
            
            # Extract actionable items from ARIA's response
            actions = await self._extract_aria_actions(response, player_id, db)
            
            # Send response with any actions
            await self.send_message(player_id, {
                "type": "aria_response",
                "conversation_id": conversation_id,
                # (send_message stamps a top-level "timestamp" on every outbound
                # frame, so this path already satisfies ARIAResponseMessage.)
                "data": {
                    "message": response.get("response", ""),
                    "confidence": response.get("confidence", 0.95),
                    "context_used": response.get("context_type", context_type),
                    "actions": actions,
                    "suggestions": response.get("suggestions", []),
                    "learning_note": response.get("learning_note", None)
                }
            })
            
            # Log ARIA interaction for learning
            await self._log_aria_interaction(player_id, user_input, response, db)
            
        except Exception as e:
            logger.error(f"Error handling ARIA chat: {e}")
            await self.send_error(player_id, "ARIA is temporarily unavailable")
    
    async def _handle_automation_rule(self, player_id: str, message: Dict[str, Any], 
                                    db: AsyncSession, session: WebSocketSession):
        """Handle trading automation rules"""
        try:
            action = message.get("action")  # create, update, delete, toggle
            rule_data = message.get("rule", {})
            
            # Validate rule structure
            if not self._validate_automation_rule(rule_data):
                await self.send_error(player_id, "Invalid automation rule")
                return
            
            # Process rule action (simplified)
            result = {
                "success": True,
                "action": action,
                "rule_id": rule_data.get("id") or str(uuid4()),
                "message": f"Rule {action}d successfully"
            }
            
            await self.send_message(player_id, {
                "type": "automation_result",
                "data": result
            })
            
        except Exception as e:
            logger.error(f"Error handling automation rule: {e}")
            await self.send_error(player_id, "Automation rule processing failed")
    
    def _validate_automation_rule(self, rule_data: Dict[str, Any]) -> bool:
        """Validate automation rule structure"""
        required = ["name", "conditions", "actions", "enabled"]
        return all(field in rule_data for field in required)
    
    # =============================================================================
    # SECURITY & UTILITIES
    # =============================================================================
    
    def _check_rate_limit(self, session: WebSocketSession, message_type: str) -> bool:
        """Check if message passes rate limiting"""
        now = time.time()
        
        # Determine rate limit based on message type
        if message_type.startswith("trading_"):
            limit_key = "trading"
            max_per_minute = self.rate_limit_config.max_trading_commands_per_minute
            window = 60
        elif message_type.startswith("ai_") or message_type == "aria_chat":
            limit_key = "ai"
            max_per_minute = self.rate_limit_config.max_ai_requests_per_minute
            window = 60
        else:
            limit_key = "general"
            max_per_minute = self.rate_limit_config.max_messages_per_second * 60
            window = 1
        
        # Clean old entries
        cutoff = now - window
        session.rate_limiter[limit_key] = [
            t for t in session.rate_limiter[limit_key] if t > cutoff
        ]
        
        # Check limit
        if len(session.rate_limiter[limit_key]) >= max_per_minute:
            return False
        
        # Add current request
        session.rate_limiter[limit_key].append(now)
        return True
    
    def _validate_message_structure(self, message: Dict[str, Any]) -> bool:
        """Validate basic message structure"""
        required_fields = ["type", "timestamp", "session_id"]
        return all(field in message for field in required_fields)
    
    def _validate_message_signature(self, message: Dict[str, Any]) -> bool:
        """Validate message signature for integrity"""
        signature = message.get("signature")
        if not signature:
            return False
        
        # Create signature from message content
        content = json.dumps({
            "type": message.get("type"),
            "timestamp": message.get("timestamp"),
            "session_id": message.get("session_id")
        }, sort_keys=True)
        
        expected_signature = hmac.new(
            self.message_secret.encode(),
            content.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(signature, expected_signature)
    
    async def _validate_auth_token(self, player_id: str, auth_token: str) -> bool:
        """Validate authentication token using JWT verification"""
        try:
            # Decode and verify the JWT token
            payload = jose_jwt.decode(
                auth_token, settings.JWT_SECRET, algorithms=["HS256"]
            )

            # Verify the token has a subject claim
            user_id: str = payload.get("sub")
            if user_id is None:
                logger.warning(f"JWT token missing 'sub' claim for player {player_id}")
                return False

            # Verify the token hasn't expired (jose handles this, but be explicit)
            exp = payload.get("exp")
            if exp is not None and datetime.fromtimestamp(exp, tz=UTC) < datetime.now(UTC):
                logger.warning(f"JWT token expired for player {player_id}")
                return False

            # Verify the user_id from token maps to the claimed player_id
            async with AsyncSessionLocal() as db:
                stmt = select(Player.id).where(Player.user_id == user_id)
                result = await db.execute(stmt)
                token_player_id = result.scalar_one_or_none()

                if token_player_id is None:
                    logger.warning(f"No player found for user_id {user_id} from token")
                    return False

                if str(token_player_id) != player_id:
                    logger.warning(
                        f"Token user mismatch: token player {token_player_id} != claimed {player_id}"
                    )
                    return False

            return True

        except JWTError as e:
            logger.warning(f"JWT validation failed for player {player_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error validating auth token for player {player_id}: {e}")
            return False

    async def _load_player_permissions(self, player_id: str) -> Set[str]:
        """Load player permissions from database based on player role and status"""
        base_permissions = {"trading"}  # All players can trade
        try:
            async with AsyncSessionLocal() as db:
                stmt = (
                    select(Player, User)
                    .join(User, Player.user_id == User.id)
                    .where(Player.id == player_id)
                )
                result = await db.execute(stmt)
                row = result.first()

                if row is None:
                    logger.warning(f"Player {player_id} not found for permissions lookup")
                    return base_permissions

                player, user = row

                # Grant permissions based on player/user status
                if user.is_admin:
                    base_permissions.add("admin")
                    base_permissions.add("automation")

                if player.is_active:
                    base_permissions.add("ai_access")

                if player.is_galactic_citizen:
                    base_permissions.add("automation")

                return base_permissions

        except Exception as e:
            logger.error(f"Error loading permissions for player {player_id}: {e}")
            return base_permissions

    async def _get_player_username(self, player_id: str) -> str:
        """Get player username from database"""
        try:
            async with AsyncSessionLocal() as db:
                stmt = (
                    select(Player.nickname, User.username)
                    .join(User, Player.user_id == User.id)
                    .where(Player.id == player_id)
                )
                result = await db.execute(stmt)
                row = result.first()

                if row is not None:
                    nickname, username = row
                    return nickname or username

        except Exception as e:
            logger.error(f"Error loading username for player {player_id}: {e}")

        return f"Player_{player_id[:8]}"
    
    # =============================================================================
    # MESSAGE SENDING
    # =============================================================================
    
    async def send_message(self, player_id: str, message: Dict[str, Any]):
        """Send signed message to player"""
        # Add metadata
        message["timestamp"] = datetime.now(UTC).isoformat()
        message["server_version"] = "1.0.0"
        
        # Sign message
        content = json.dumps({
            "type": message.get("type"),
            "timestamp": message.get("timestamp")
        }, sort_keys=True)
        
        message["signature"] = hmac.new(
            self.message_secret.encode(),
            content.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Send via connection manager
        await self.connection_manager.send_personal_message(player_id, message)
    
    async def send_error(self, player_id: str, error_message: str, code: str = "ERROR"):
        """Send error message to player"""
        await self.send_message(player_id, {
            "type": "error",
            "code": code,
            "message": error_message
        })
    
    async def _handle_heartbeat(self, player_id: str):
        """Handle heartbeat message"""
        await self.connection_manager.handle_heartbeat(player_id)
        await self.send_message(player_id, {
            "type": "heartbeat_ack",
            "server_time": datetime.now(UTC).isoformat()
        })
    
    async def _broadcast_trade_update(self, trade: Dict[str, Any], db: AsyncSession):
        """Broadcast trade update via Redis pub/sub for scalability"""
        try:
            # Get pub/sub service
            pubsub_service = await get_pubsub_service()
            
            # Publish trade event
            await pubsub_service.publish_trading_event(
                event_type="trade_executed",
                event_data={
                    "trade": trade,
                    "commodity": trade.get("commodity"),
                    "action": trade.get("action"),
                    "quantity": trade.get("quantity"),
                    "price": trade.get("price"),
                    "player_id": trade.get("player_id"),  # Can be anonymized if needed
                    "timestamp": trade.get("timestamp")
                }
            )
            
            # Also trigger market data update for this commodity
            commodity = trade.get("commodity")
            if commodity:
                # Get fresh market snapshot
                snapshot = await self.market_service.get_market_snapshot(commodity, db)
                # Publish market update
                await self.market_service.publish_market_update(commodity, snapshot)
                
        except Exception as e:
            logger.error(f"Error broadcasting trade update: {e}")
    
    # =============================================================================
    # ARIA HELPER METHODS
    # =============================================================================
    
    async def _build_aria_context(self, player_id: str, context_type: str, 
                                 db: AsyncSession) -> Dict[str, Any]:
        """Build comprehensive context for ARIA based on player's current state"""
        context = {
            "player_id": player_id,
            "context_type": context_type,
            "timestamp": datetime.now(UTC).isoformat()
        }
        
        # Get player and ship info
        player = await db.get(Player, player_id)
        if player:
            context["player_state"] = {
                "credits": float(player.credits),
                "is_docked": player.is_docked,
                "current_sector": player.current_sector_id,
                "team_id": str(player.team_id) if player.team_id else None
            }
            
            # Get current ship
            if player.current_ship_id:
                from src.models.ship import Ship
                ship = await db.get(Ship, player.current_ship_id)
                if ship:
                    context["ship_state"] = {
                        "type": ship.ship_type,
                        "cargo_capacity": ship.cargo_capacity,
                        "cargo": dict(ship.cargo) if ship.cargo else {},
                        "current_port": str(ship.current_port_id) if ship.current_port_id else None
                    }
        
        # Add ARIA personal intelligence summary
        from src.services.aria_personal_intelligence_service import get_aria_intelligence_service
        aria_intel = get_aria_intelligence_service()
        
        # Get exploration summary
        from src.models.aria_personal_intelligence import ARIAExplorationMap
        exploration_count = await db.execute(
            select(func.count(ARIAExplorationMap.id)).where(
                ARIAExplorationMap.player_id == player_id
            )
        )
        context["exploration_summary"] = {
            "sectors_visited": exploration_count.scalar() or 0
        }
        
        return context
    
    async def _extract_aria_actions(self, response: Dict[str, Any], player_id: str,
                                   db: AsyncSession) -> List[Dict[str, Any]]:
        """Extract actionable items from ARIA's response"""
        actions = []
        
        # Check for trading recommendations
        if "recommended_trades" in response:
            for trade in response["recommended_trades"]:
                actions.append({
                    "type": "trade_recommendation",
                    "action": trade.get("action"),
                    "commodity": trade.get("commodity"),
                    "quantity": trade.get("quantity"),
                    "expected_profit": trade.get("expected_profit"),
                    "confidence": trade.get("confidence", 0.8)
                })
        
        # Check for quantum trading suggestions
        if "quantum_opportunities" in response:
            for opp in response["quantum_opportunities"]:
                actions.append({
                    "type": "quantum_trade_opportunity",
                    "commodity": opp.get("commodity"),
                    "superposition_count": opp.get("states", 3),
                    "success_probability": opp.get("probability", 0.7)
                })
        
        # Check for navigation suggestions
        if "navigation_suggestions" in response:
            for nav in response["navigation_suggestions"]:
                actions.append({
                    "type": "navigation",
                    "destination": nav.get("sector"),
                    "reason": nav.get("reason"),
                    "estimated_profit": nav.get("profit_potential")
                })
        
        return actions
    
    async def _log_aria_interaction(self, player_id: str, user_input: str,
                                   response: Dict[str, Any], db: AsyncSession):
        """Log ARIA interaction for learning and improvement"""
        try:
            # Store in ARIA personal memory for learning
            from src.services.aria_personal_intelligence_service import get_aria_intelligence_service
            aria_intel = get_aria_intelligence_service()
            
            # Create memory of this interaction
            memory_content = {
                "type": "conversation",
                "input": user_input,
                "response_summary": response.get("response", "")[:200],  # First 200 chars
                "confidence": response.get("confidence", 0),
                "actions_suggested": len(response.get("actions", [])),
                "timestamp": datetime.now(UTC).isoformat()
            }
            
            # This would store in the ARIA personal memory system
            # await aria_intel._create_memory(player_id, "conversation", memory_content, 0.5, db)
            
            logger.info(f"ARIA interaction logged for player {player_id}")
            
        except Exception as e:
            logger.error(f"Error logging ARIA interaction: {e}")


# Global enhanced service instance
enhanced_websocket_service = None


def get_enhanced_websocket_service(redis_client: redis.Redis = None) -> EnhancedWebSocketService:
    """Get or create enhanced WebSocket service instance"""
    global enhanced_websocket_service
    if enhanced_websocket_service is None:
        enhanced_websocket_service = EnhancedWebSocketService(redis_client)
    return enhanced_websocket_service