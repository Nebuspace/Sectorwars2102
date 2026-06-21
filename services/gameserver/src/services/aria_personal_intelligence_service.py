"""
ARIA Personal Intelligence Service
Manages each player's unique ARIA knowledge base and learning

OWASP Security Implementation:
- A01: Personal data isolation between players
- A02: Cryptographic protection of memories
- A03: Input validation on all learning data
- A04: Rate limiting on intelligence queries
- A07: Player-specific authentication required
- A08: Data integrity verification
- A09: Comprehensive audit logging
- A10: Security monitoring for anomalies
"""

import json
import hashlib
import hmac
from typing import Dict, List, Any, Optional, Tuple, Set
from datetime import datetime, timedelta, UTC
from decimal import Decimal
import statistics
import numpy as np
from collections import defaultdict, deque
import logging
from cryptography.fernet import Fernet
import base64

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, update
from sqlalchemy.orm import selectinload, Session

from src.models.player import Player
from src.models.sector import Sector
from src.models.station import Station
from src.models.market_transaction import MarketTransaction
from src.models.aria_personal_intelligence import (
    ARIAPersonalMemory, ARIAMarketIntelligence, ARIAExplorationMap,
    ARIATradingPattern, ARIAQuantumCache, ARIASecurityLog
)
from src.core.config import settings
from src.core.security import get_password_hash

logger = logging.getLogger(__name__)


class ARIAPersonalIntelligenceService:
    """
    Manages personal ARIA intelligence for each player
    All predictions based solely on player's own exploration and experience
    """
    
    def __init__(self):
        # Encryption for personal memories (OWASP A02)
        self.cipher_suite = self._initialize_encryption()
        
        # Rate limiting per player (OWASP A04)
        self.query_limits = defaultdict(lambda: deque(maxlen=100))
        self.max_queries_per_minute = 60
        
        # Pattern recognition thresholds
        self.MIN_DATA_POINTS_FOR_PREDICTION = 5  # Need at least 5 visits
        self.CONFIDENCE_THRESHOLD = 0.6  # Minimum confidence for predictions
        self.MEMORY_DECAY_RATE = 0.001  # How fast old memories fade
        
        # Security monitoring (OWASP A09)
        self.anomaly_threshold = 0.8
        self.manipulation_patterns = self._load_manipulation_patterns()
        
        # Performance tracking
        self.predictions_made = 0
        self.memories_created = 0
        self.patterns_evolved = 0
        
        logger.info("ARIA Personal Intelligence Service initialized")
    
    # =============================================================================
    # EXPLORATION & MEMORY CREATION
    # =============================================================================
    
    async def record_sector_visit(self, player_id: str, sector_id: str, 
                                 ship_id: str, db: AsyncSession) -> ARIAExplorationMap:
        """
        Record that a player visited a sector
        This is the foundation of all ARIA knowledge
        """
        # Security: Validate player owns the ship (OWASP A01)
        if not await self._validate_player_ship(player_id, ship_id, db):
            await self._log_security_event(
                player_id, "unauthorized_visit_attempt", "critical",
                {"ship_id": ship_id, "sector_id": sector_id}, db
            )
            raise ValueError("Unauthorized ship access")
        
        # Check if we've visited this sector before
        stmt = select(ARIAExplorationMap).where(
            and_(
                ARIAExplorationMap.player_id == player_id,
                ARIAExplorationMap.sector_id == sector_id
            )
        )
        result = await db.execute(stmt)
        exploration = result.scalar_one_or_none()
        
        if exploration:
            # Update existing exploration record
            exploration.last_visit = datetime.now(UTC)
            exploration.visit_count += 1
            
            # Decay old market intelligence
            await self._decay_sector_intelligence(player_id, sector_id, db)
        else:
            # First visit to this sector!
            exploration = ARIAExplorationMap(
                player_id=player_id,
                sector_id=sector_id,
                first_visit=datetime.now(UTC),
                last_visit=datetime.now(UTC),
                visit_count=1
            )
            db.add(exploration)
            
            # Create memory of first exploration
            await self._create_memory(
                player_id,
                "exploration",
                {
                    "event": "first_sector_visit",
                    "sector_id": sector_id,
                    "timestamp": datetime.now(UTC).isoformat()
                },
                importance=0.8,  # First visits are important
                db=db
            )
        
        await db.commit()
        
        logger.info(f"Player {player_id} visited sector {sector_id} (visit #{exploration.visit_count})")
        return exploration
    
    async def record_market_observation(self, player_id: str, station_id: str,
                                      commodity: str, price: float, quantity: int,
                                      db: AsyncSession) -> ARIAMarketIntelligence:
        """
        Record a market price observation at a port
        This builds the player's personal price history
        """
        # Validate the player is at this port (OWASP A04)
        if not await self._validate_player_at_port(player_id, station_id, db):
            await self._log_security_event(
                player_id, "invalid_market_observation", "warning",
                {"station_id": station_id, "commodity": commodity}, db
            )
            return None
        
        # Get port's sector
        station = await db.get(Station, station_id)
        if not station:
            return None

        # Check existing intelligence
        stmt = select(ARIAMarketIntelligence).where(
            and_(
                ARIAMarketIntelligence.player_id == player_id,
                ARIAMarketIntelligence.station_id == station_id,
                ARIAMarketIntelligence.commodity == commodity
            )
        )
        result = await db.execute(stmt)
        intelligence = result.scalar_one_or_none()
        
        observation = {
            "price": price,
            "quantity": quantity,
            "timestamp": datetime.now(UTC).isoformat()
        }
        
        if intelligence:
            # Update existing intelligence
            intelligence.price_observations.append(observation)
            intelligence.data_points += 1
            intelligence.last_visit = datetime.now(UTC)
            
            # Recalculate statistics
            prices = [obs["price"] for obs in intelligence.price_observations[-50:]]  # Last 50
            intelligence.average_price = statistics.mean(prices)
            intelligence.price_volatility = statistics.stdev(prices) if len(prices) > 1 else 0.0
            
            # Update patterns if enough data
            if intelligence.data_points >= self.MIN_DATA_POINTS_FOR_PREDICTION:
                patterns = await self._identify_price_patterns(
                    intelligence.price_observations
                )
                intelligence.identified_patterns = patterns
                intelligence.prediction_confidence = min(
                    intelligence.data_points / 20, 0.95  # Max 95% confidence
                )
        else:
            # First observation of this commodity at this port
            intelligence = ARIAMarketIntelligence(
                player_id=player_id,
                station_id=station_id,
                sector_id=station.sector_id,
                commodity=commodity,
                price_observations=[observation],
                average_price=price,
                price_volatility=0.0,
                data_points=1,
                last_visit=datetime.now(UTC),
                intelligence_quality=0.1  # Low quality with just 1 data point
            )
            db.add(intelligence)
        
        # Update intelligence quality
        intelligence.intelligence_quality = self._calculate_intelligence_quality(
            intelligence.data_points,
            intelligence.last_visit,
            intelligence.price_volatility
        )
        
        await db.commit()
        
        # Create memory of significant price changes
        if intelligence.data_points > 1:
            price_change = abs(price - intelligence.average_price) / intelligence.average_price
            if price_change > 0.2:  # 20% change
                await self._create_memory(
                    player_id,
                    "market",
                    {
                        "event": "significant_price_change",
                        "commodity": commodity,
                        "station_id": station_id,
                        "old_price": intelligence.average_price,
                        "new_price": price,
                        "change_percent": price_change * 100
                    },
                    importance=0.7,
                    db=db
                )
        
        return intelligence
    
    # =============================================================================
    # CONVENIENCE MEMORY RECORDERS (Combat, Trade, Exploration)
    # =============================================================================

    async def record_combat_memory(self, player_id: str, combat_data: dict,
                                   db: AsyncSession) -> None:
        """
        Record a combat encounter as an ARIA memory.

        Args:
            player_id: The player whose ARIA should remember this combat.
            combat_data: Dict with keys like opponent_name, outcome, sector_id,
                         attacker_ship, defender_ship, cargo_stolen, reputation_change.
            db: Async database session.
        """
        try:
            outcome = combat_data.get("outcome", "unknown")
            opponent = combat_data.get("opponent_name", "Unknown")

            # Higher importance for victories and first-time encounters
            importance = 0.8 if outcome == "victory" else 0.7

            content = {
                "event": "combat_encounter",
                "opponent_name": str(opponent),
                "outcome": str(outcome),
                "sector_id": combat_data.get("sector_id"),
                "attacker_ship": combat_data.get("attacker_ship"),
                "defender_ship": combat_data.get("defender_ship"),
                "cargo_stolen": combat_data.get("cargo_stolen"),
                "reputation_change": combat_data.get("reputation_change"),
                "timestamp": datetime.now(UTC).isoformat(),
            }

            await self._create_memory(
                player_id,
                "combat",
                content,
                importance=importance,
                db=db,
            )

            logger.info(
                "Recorded combat memory for player %s: %s vs %s",
                player_id, outcome, opponent,
            )
        except Exception as e:
            logger.warning(
                "Failed to record combat memory for player %s: %s",
                player_id, e,
            )

    def record_combat_memory_sync(self, player_id: str, combat_data: dict,
                                  db: Session) -> None:
        """Synchronous twin of ``record_combat_memory`` for sync-Session callers.

        ``combat_service`` runs entirely on a synchronous SQLAlchemy ``Session``
        (it never awaits), so it cannot call the async ``record_combat_memory``.
        This method records the exact same ``combat_encounter`` memory shape via
        the sync session: it reuses the identical encryption, content schema, and
        dedup-by-hash logic — only the DB calls differ (sync ``query``/``add``
        instead of ``await db.execute``/``select``).

        FLUSH-FREE: like the async ``_create_memory``, it only ``db.add``s the
        memory; the CALLER owns the commit (so it folds into combat's single
        commit). Never raises — an ARIA memory hiccup must never break combat.

        Args:
            player_id: the player whose ARIA should remember this combat.
            combat_data: same keys as ``record_combat_memory`` — ``outcome``,
                ``opponent_name``, ``sector_id``, ``attacker_ship``,
                ``defender_ship``, ``cargo_stolen``, ``reputation_change``, plus
                an optional ``event`` override (defaults ``combat_encounter``).
            db: synchronous database session (caller commits).
        """
        try:
            outcome = combat_data.get("outcome", "unknown")
            opponent = combat_data.get("opponent_name", "Unknown")

            # Higher importance for victories and first-time encounters
            importance = 0.8 if outcome == "victory" else 0.7

            content = {
                "event": combat_data.get("event", "combat_encounter"),
                "opponent_name": str(opponent),
                "outcome": str(outcome),
                "sector_id": combat_data.get("sector_id"),
                "attacker_ship": combat_data.get("attacker_ship"),
                "defender_ship": combat_data.get("defender_ship"),
                "cargo_stolen": combat_data.get("cargo_stolen"),
                "reputation_change": combat_data.get("reputation_change"),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            # Preserve any extra structured detail the caller supplies (e.g.
            # planet_id / planet_name on a capture) without colliding with the
            # canonical keys above.
            for k, v in combat_data.items():
                if k not in content and k not in (
                    "event", "opponent_name", "outcome", "sector_id",
                    "attacker_ship", "defender_ship", "cargo_stolen",
                    "reputation_change",
                ):
                    content[k] = v

            encrypted_content = self._encrypt_memory(content)
            content_str = json.dumps(content, sort_keys=True)
            memory_hash = hashlib.sha256(content_str.encode()).hexdigest()

            existing = (
                db.query(ARIAPersonalMemory)
                .filter(
                    ARIAPersonalMemory.player_id == player_id,
                    ARIAPersonalMemory.memory_hash == memory_hash,
                )
                .first()
            )
            if existing is not None:
                return  # Memory already exists (dedup — also our double-fire guard)

            memory = ARIAPersonalMemory(
                player_id=player_id,
                memory_type="combat",
                importance_score=importance,
                memory_content={"encrypted": encrypted_content},
                memory_hash=memory_hash,
                confidence_level=0.9,
                decay_rate=self.MEMORY_DECAY_RATE,
            )
            db.add(memory)
            self.memories_created += 1

            logger.info(
                "Recorded combat memory (sync) for player %s: %s vs %s",
                player_id, outcome, opponent,
            )
        except Exception as e:
            logger.warning(
                "Failed to record combat memory (sync) for player %s: %s",
                player_id, e,
            )

    async def record_trade_memory(self, player_id: str, trade_data: dict,
                                  db: AsyncSession) -> None:
        """
        Record a trading event as an ARIA memory.

        Args:
            player_id: The player whose ARIA should remember this trade.
            trade_data: Dict with keys like station_name, action, commodity,
                        quantity, total_value, profit.
            db: Async database session.
        """
        try:
            action = trade_data.get("action", "unknown")
            commodity = trade_data.get("commodity", "unknown")
            profit = trade_data.get("profit")

            # Profitable trades are more memorable
            if profit is not None and profit > 0:
                importance = min(0.5 + (profit / 10000), 0.9)
            else:
                importance = 0.5

            content = {
                "event": "trade_transaction",
                "station_name": str(trade_data.get("station_name", "Unknown")),
                "action": str(action),
                "commodity": str(commodity),
                "quantity": trade_data.get("quantity"),
                "total_value": trade_data.get("total_value"),
                "profit": profit,
                "timestamp": datetime.now(UTC).isoformat(),
            }

            await self._create_memory(
                player_id,
                "market",
                content,
                importance=importance,
                db=db,
            )

            logger.info(
                "Recorded trade memory for player %s: %s %s at %s",
                player_id, action, commodity,
                trade_data.get("station_name", "Unknown"),
            )
        except Exception as e:
            logger.warning(
                "Failed to record trade memory for player %s: %s",
                player_id, e,
            )

    async def record_exploration_memory(self, player_id: str, exploration_data: dict,
                                        db: AsyncSession) -> None:
        """
        Record a sector exploration event as an ARIA memory.

        Args:
            player_id: The player whose ARIA should remember this exploration.
            exploration_data: Dict with keys like sector_id, sector_name, discovery.
            db: Async database session.
        """
        try:
            discovery = exploration_data.get("discovery", "revisit")

            # New discoveries are more important than revisits
            importance = 0.8 if discovery == "new_sector" else 0.4

            content = {
                "event": "sector_exploration",
                "sector_id": exploration_data.get("sector_id"),
                "sector_name": str(exploration_data.get("sector_name", "Unknown Sector")),
                "discovery": str(discovery),
                "timestamp": datetime.now(UTC).isoformat(),
            }

            await self._create_memory(
                player_id,
                "exploration",
                content,
                importance=importance,
                db=db,
            )

            logger.info(
                "Recorded exploration memory for player %s: %s sector %s",
                player_id, discovery,
                exploration_data.get("sector_id"),
            )
        except Exception as e:
            logger.warning(
                "Failed to record exploration memory for player %s: %s",
                player_id, e,
            )

    # =============================================================================
    # QUANTUM PREDICTIONS (Personal Data Only)
    # =============================================================================
    
    async def generate_quantum_states(self, player_id: str, station_id: str,
                                    commodity: str, quantity: int,
                                    db: AsyncSession) -> Optional[List[Dict[str, Any]]]:
        """
        Generate quantum states based ONLY on player's personal market intelligence
        Returns None if player has insufficient data
        """
        # Check if player has visited this port
        station = await db.get(Station, station_id)
        if not station:
            return None

        exploration = await self._get_sector_exploration(player_id, station.sector_id, db)
        if not exploration:
            logger.info(f"Player {player_id} has never visited sector {station.sector_id}")
            return None
        
        # Get player's market intelligence for this commodity/port
        intelligence = await self._get_market_intelligence(
            player_id, station_id, commodity, db
        )
        
        if not intelligence or intelligence.data_points < self.MIN_DATA_POINTS_FOR_PREDICTION:
            logger.info(f"Insufficient data for {commodity} at port {station_id} (need {self.MIN_DATA_POINTS_FOR_PREDICTION} observations)")
            return None
        
        # Generate states based on personal observations
        states = []
        
        # Use player's observed price range
        prices = [obs["price"] for obs in intelligence.price_observations[-20:]]
        avg_price = statistics.mean(prices)
        std_dev = statistics.stdev(prices) if len(prices) > 1 else avg_price * 0.1
        
        # State 1: Optimistic (based on player's best observed prices)
        best_price = max(prices) if commodity in ["ORE", "ORGANICS", "FUEL"] else min(prices)
        states.append({
            "state_id": "optimistic",
            "probability": 0.25,
            "price": best_price,
            "confidence": intelligence.prediction_confidence,
            "based_on": f"{len(prices)} personal observations"
        })
        
        # State 2: Expected (based on identified patterns)
        if intelligence.identified_patterns:
            pattern_price = await self._predict_from_patterns(
                intelligence, datetime.now(UTC)
            )
            if pattern_price:
                states.append({
                    "state_id": "pattern_based",
                    "probability": 0.45,
                    "price": pattern_price,
                    "confidence": intelligence.pattern_confidence.get(
                        intelligence.identified_patterns[0], 0.5
                    ),
                    "pattern": intelligence.identified_patterns[0]
                })
        else:
            # No patterns, use average
            states.append({
                "state_id": "average",
                "probability": 0.45,
                "price": avg_price,
                "confidence": intelligence.prediction_confidence * 0.8,
                "based_on": "historical average"
            })
        
        # State 3: Pessimistic (based on player's worst observed prices)
        worst_price = min(prices) if commodity in ["ORE", "ORGANICS", "FUEL"] else max(prices)
        states.append({
            "state_id": "pessimistic",
            "probability": 0.25,
            "price": worst_price,
            "confidence": intelligence.prediction_confidence,
            "based_on": f"{len(prices)} personal observations"
        })
        
        # State 4: Unknown (beyond player's experience)
        states.append({
            "state_id": "unknown",
            "probability": 0.05,
            "price": avg_price * (0.5 if np.random.random() < 0.5 else 1.5),
            "confidence": 0.1,  # Very low confidence
            "based_on": "unexplored market conditions"
        })
        
        # Log quantum generation for security
        await self._log_security_event(
            player_id, "quantum_generation", "info",
            {
                "station_id": station_id,
                "commodity": commodity,
                "states_generated": len(states),
                "data_points": intelligence.data_points
            }, db
        )
        
        return states
    
    async def get_ghost_trade_prediction(self, player_id: str, station_id: str,
                                       commodity: str, action: str, quantity: int,
                                       db: AsyncSession) -> Optional[Dict[str, Any]]:
        """
        Generate ghost trade prediction based on personal experience only
        """
        # Check cache first
        cache_key = self._generate_cache_key(player_id, station_id, commodity, action, quantity)
        cached = await self._get_quantum_cache(player_id, cache_key, db)
        if cached:
            return cached
        
        # Generate quantum states from personal data
        states = await self.generate_quantum_states(
            player_id, station_id, commodity, quantity, db
        )
        
        if not states:
            return {
                "error": "insufficient_data",
                "message": "You need to visit this port more times to generate predictions",
                "required_visits": self.MIN_DATA_POINTS_FOR_PREDICTION
            }
        
        # Calculate expected outcomes
        outcomes = []
        for state in states:
            if action == "buy":
                cost = quantity * state["price"]
                outcome = {
                    "state": state["state_id"],
                    "probability": state["probability"],
                    "cost": cost,
                    "confidence": state["confidence"],
                    "based_on": state.get("based_on", "personal experience")
                }
            else:  # sell
                revenue = quantity * state["price"]
                outcome = {
                    "state": state["state_id"],
                    "probability": state["probability"],
                    "revenue": revenue,
                    "confidence": state["confidence"],
                    "based_on": state.get("based_on", "personal experience")
                }
            outcomes.append(outcome)
        
        # Calculate expected value
        if action == "buy":
            expected_cost = sum(o["cost"] * o["probability"] for o in outcomes)
            result = {
                "action": "buy",
                "expected_cost": expected_cost,
                "outcomes": outcomes,
                "recommendation": self._generate_recommendation(expected_cost, action, commodity)
            }
        else:
            expected_revenue = sum(o["revenue"] * o["probability"] for o in outcomes)
            result = {
                "action": "sell",
                "expected_revenue": expected_revenue,
                "outcomes": outcomes,
                "recommendation": self._generate_recommendation(expected_revenue, action, commodity)
            }
        
        # Cache result
        await self._cache_quantum_result(player_id, cache_key, result, db)
        
        return result
    
    # =============================================================================
    # TRADE DNA EVOLUTION (Personal Patterns)
    # =============================================================================
    
    async def evolve_trading_pattern(self, player_id: str, trade_result: Dict[str, Any],
                                   db: AsyncSession):
        """
        Update a player's personal trading-pattern observation accounting.

        Per ADR-0038 (Accepted) ARIA learning is a purely append-only
        observation log: this records plain performance metrics
        (times_used / success_rate / average_profit / best_profit /
        worst_loss / last_used) for the pattern derived from this trade.
        There is no genetic-algorithm evolution — no fitness-driven
        mutation or offspring. The method name is retained for caller
        compatibility.
        """
        pattern_id = trade_result.get("pattern_id")
        if not pattern_id:
            # Create new pattern from this trade
            pattern_id = await self._create_trading_pattern(player_id, trade_result, db)
        
        # Get pattern
        stmt = select(ARIATradingPattern).where(
            and_(
                ARIATradingPattern.player_id == player_id,
                ARIATradingPattern.pattern_id == pattern_id
            )
        )
        result = await db.execute(stmt)
        pattern = result.scalar_one_or_none()
        
        if not pattern:
            return
        
        # Update performance metrics
        pattern.times_used += 1
        pattern.last_used = datetime.now(UTC)
        
        profit = trade_result.get("profit", 0)
        if profit > 0:
            pattern.success_rate = (
                (pattern.success_rate * (pattern.times_used - 1) + 1) / pattern.times_used
            )
            pattern.average_profit = (
                (pattern.average_profit * (pattern.times_used - 1) + profit) / pattern.times_used
            )
            pattern.best_profit = max(pattern.best_profit, profit)
        else:
            pattern.success_rate = (
                (pattern.success_rate * (pattern.times_used - 1)) / pattern.times_used
            )
            pattern.worst_loss = min(pattern.worst_loss, profit)

        await db.commit()
        self.patterns_evolved += 1
    
    async def get_evolved_patterns(self, player_id: str,
                                 db: AsyncSession,
                                 pattern_type: Optional[str] = None) -> List[ARIATradingPattern]:
        """
        Get a player's recorded trading patterns, most-used first.

        Ordered by observation accounting (times_used) rather than the legacy
        GA fitness score, per ADR-0038's append-only observation-log model.
        """
        stmt = select(ARIATradingPattern).where(
            ARIATradingPattern.player_id == player_id
        )

        if pattern_type:
            stmt = stmt.where(ARIATradingPattern.pattern_type == pattern_type)

        stmt = stmt.order_by(ARIATradingPattern.times_used.desc()).limit(10)

        result = await db.execute(stmt)
        return result.scalars().all()
    
    # =============================================================================
    # CASCADE PLANNING (Through Explored Territory Only)
    # =============================================================================
    
    async def plan_trade_cascade(self, player_id: str, start_sector_id: str,
                               target_profit: float, max_jumps: int,
                               db: AsyncSession) -> Optional[Dict[str, Any]]:
        """
        Plan a trade cascade through ONLY explored sectors
        """
        # Get player's exploration map
        explored_sectors = await self._get_explored_sectors(player_id, db)
        if not explored_sectors:
            return None
        
        # Build graph of known trade routes
        trade_graph = await self._build_personal_trade_graph(
            player_id, explored_sectors, db
        )
        
        if not trade_graph:
            return {
                "error": "insufficient_exploration",
                "message": "Explore more sectors to plan trade routes",
                "explored_sectors": len(explored_sectors)
            }
        
        # Find profitable paths within jump limit
        profitable_paths = await self._find_profitable_paths(
            player_id, start_sector_id, trade_graph, 
            target_profit, max_jumps, db
        )
        
        if not profitable_paths:
            return {
                "error": "no_profitable_routes",
                "message": "No profitable routes found in explored territory",
                "suggestion": "Explore new sectors or lower profit target"
            }
        
        # Select best cascade
        best_cascade = max(profitable_paths, key=lambda x: x["profit_per_jump"])
        
        # Generate detailed cascade plan
        cascade_plan = {
            "cascade_id": self._generate_cascade_id(),
            "player_id": player_id,
            "total_profit": best_cascade["total_profit"],
            "total_jumps": best_cascade["jumps"],
            "profit_per_jump": best_cascade["profit_per_jump"],
            "confidence": best_cascade["confidence"],
            "steps": []
        }
        
        # Detail each step
        for i, step in enumerate(best_cascade["path"]):
            cascade_plan["steps"].append({
                "step": i + 1,
                "sector": step["sector_id"],
                "station": step["station_id"],
                "action": step["action"],
                "commodity": step["commodity"],
                "expected_price": step["expected_price"],
                "confidence": step["confidence"],
                "based_on": f"{step['observations']} observations"
            })
        
        return cascade_plan
    
    # =============================================================================
    # SECURITY & PRIVACY (OWASP Implementation)
    # =============================================================================
    
    async def _validate_player_ship(self, player_id: str, ship_id: str, 
                                  db: AsyncSession) -> bool:
        """Validate player owns the ship (OWASP A01)"""
        from src.models.ship import Ship
        
        stmt = select(Ship).where(
            and_(
                Ship.id == ship_id,
                Ship.player_id == player_id
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none() is not None
    
    async def _validate_player_at_port(self, player_id: str, station_id: str,
                                     db: AsyncSession) -> bool:
        """Validate player has a ship at the port"""
        from src.models.ship import Ship
        
        stmt = select(Ship).where(
            and_(
                Ship.player_id == player_id,
                Ship.current_port_id == station_id
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none() is not None
    
    async def _log_security_event(self, player_id: str, event_type: str,
                                severity: str, event_data: Dict[str, Any],
                                db: AsyncSession):
        """Log security events for audit (OWASP A09)"""
        # Calculate anomaly score
        anomaly_score = await self._calculate_anomaly_score(
            player_id, event_type, event_data
        )
        
        log_entry = ARIASecurityLog(
            player_id=player_id,
            event_type=event_type,
            event_severity=severity,
            event_data=event_data,
            anomaly_score=anomaly_score,
            created_at=datetime.now(UTC)
        )
        
        # Take action if anomaly detected
        if anomaly_score > self.anomaly_threshold:
            log_entry.security_flags.append("high_anomaly_score")
            log_entry.action_taken = "flagged_for_review"
            logger.warning(f"High anomaly score {anomaly_score} for player {player_id}")
        
        db.add(log_entry)
        await db.commit()
    
    def _initialize_encryption(self) -> Fernet:
        """Initialize encryption for personal memories (OWASP A02)"""
        # In production, load from secure key management
        key = settings.ARIA_ENCRYPTION_KEY if hasattr(settings, 'ARIA_ENCRYPTION_KEY') else Fernet.generate_key()
        return Fernet(key)
    
    def _encrypt_memory(self, content: Dict[str, Any]) -> str:
        """Encrypt memory content"""
        json_content = json.dumps(content)
        encrypted = self.cipher_suite.encrypt(json_content.encode())
        return base64.b64encode(encrypted).decode()
    
    def _decrypt_memory(self, encrypted_content: str) -> Dict[str, Any]:
        """Decrypt memory content"""
        encrypted = base64.b64decode(encrypted_content.encode())
        decrypted = self.cipher_suite.decrypt(encrypted)
        return json.loads(decrypted.decode())
    
    # =============================================================================
    # HELPER METHODS
    # =============================================================================
    
    async def _create_memory(self, player_id: str, memory_type: str,
                           content: Dict[str, Any], importance: float,
                           db: AsyncSession):
        """Create a new ARIA memory"""
        # Encrypt content
        encrypted_content = self._encrypt_memory(content)
        
        # Generate hash for deduplication
        content_str = json.dumps(content, sort_keys=True)
        memory_hash = hashlib.sha256(content_str.encode()).hexdigest()
        
        # Check if memory already exists
        existing = await db.execute(
            select(ARIAPersonalMemory).where(
                and_(
                    ARIAPersonalMemory.player_id == player_id,
                    ARIAPersonalMemory.memory_hash == memory_hash
                )
            )
        )
        if existing.scalar_one_or_none():
            return  # Memory already exists
        
        memory = ARIAPersonalMemory(
            player_id=player_id,
            memory_type=memory_type,
            importance_score=importance,
            memory_content={"encrypted": encrypted_content},
            memory_hash=memory_hash,
            confidence_level=0.9,  # High confidence for direct observations
            decay_rate=self.MEMORY_DECAY_RATE
        )
        
        db.add(memory)
        self.memories_created += 1
    
    async def _decay_sector_intelligence(self, player_id: str, sector_id: str,
                                       db: AsyncSession):
        """Decay old intelligence as market conditions change"""
        stmt = select(ARIAMarketIntelligence).where(
            and_(
                ARIAMarketIntelligence.player_id == player_id,
                ARIAMarketIntelligence.sector_id == sector_id
            )
        )
        result = await db.execute(stmt)
        intelligences = result.scalars().all()
        
        for intel in intelligences:
            # Reduce confidence based on time since last visit
            days_old = (datetime.now(UTC) - intel.last_visit).days
            decay_factor = 0.95 ** days_old  # 5% decay per day
            intel.prediction_confidence *= decay_factor
            intel.intelligence_quality *= decay_factor
    
    def _calculate_intelligence_quality(self, data_points: int, 
                                      last_visit: datetime,
                                      volatility: float) -> float:
        """Calculate quality score for market intelligence"""
        # More data = higher quality
        data_score = min(data_points / 50, 1.0)
        
        # Recent data = higher quality
        days_old = (datetime.now(UTC) - last_visit).days
        recency_score = max(0, 1 - (days_old / 30))  # 30 days = 0 quality
        
        # Lower volatility = higher quality (more predictable)
        volatility_score = max(0, 1 - (volatility / 100))
        
        # Weighted average
        quality = (data_score * 0.4 + recency_score * 0.4 + volatility_score * 0.2)
        
        return min(quality, 0.99)  # Cap at 99%
    
    async def _identify_price_patterns(self, observations: List[Dict]) -> List[str]:
        """Identify patterns in price history"""
        if len(observations) < 10:
            return []
        
        patterns = []
        prices = [obs["price"] for obs in observations[-30:]]
        times = [datetime.fromisoformat(obs["timestamp"]) for obs in observations[-30:]]
        
        # Pattern 1: Time-based patterns
        hour_prices = defaultdict(list)
        for i, time in enumerate(times):
            hour_prices[time.hour].append(prices[i])
        
        # Check for hourly patterns
        for hour, hour_price_list in hour_prices.items():
            if len(hour_price_list) >= 3:
                avg = statistics.mean(hour_price_list)
                overall_avg = statistics.mean(prices)
                if avg > overall_avg * 1.1:
                    patterns.append(f"high_hour_{hour}")
                elif avg < overall_avg * 0.9:
                    patterns.append(f"low_hour_{hour}")
        
        # Pattern 2: Trend patterns
        if len(prices) >= 5:
            recent_trend = np.polyfit(range(5), prices[-5:], 1)[0]
            if recent_trend > 0.5:
                patterns.append("rising_trend")
            elif recent_trend < -0.5:
                patterns.append("falling_trend")
            else:
                patterns.append("stable")
        
        # Pattern 3: Volatility patterns
        if len(prices) >= 10:
            volatility = statistics.stdev(prices[-10:])
            avg_price = statistics.mean(prices[-10:])
            volatility_ratio = volatility / avg_price
            
            if volatility_ratio > 0.2:
                patterns.append("high_volatility")
            elif volatility_ratio < 0.05:
                patterns.append("low_volatility")
        
        return patterns[:5]  # Max 5 patterns
    
    async def _predict_from_patterns(self, intelligence: ARIAMarketIntelligence,
                                   target_time: datetime) -> Optional[float]:
        """Predict price based on identified patterns"""
        if not intelligence.identified_patterns:
            return None
        
        base_price = intelligence.average_price
        adjustments = []
        
        for pattern in intelligence.identified_patterns:
            confidence = intelligence.pattern_confidence.get(pattern, 0.5)
            
            if pattern.startswith("high_hour_"):
                hour = int(pattern.split("_")[2])
                if target_time.hour == hour:
                    adjustments.append(("hour_high", 1.1, confidence))
                    
            elif pattern.startswith("low_hour_"):
                hour = int(pattern.split("_")[2])
                if target_time.hour == hour:
                    adjustments.append(("hour_low", 0.9, confidence))
                    
            elif pattern == "rising_trend":
                adjustments.append(("trend", 1.05, confidence))
                
            elif pattern == "falling_trend":
                adjustments.append(("trend", 0.95, confidence))
        
        # Apply adjustments
        final_price = base_price
        for name, factor, confidence in adjustments:
            # Weight adjustment by confidence
            weighted_factor = 1 + (factor - 1) * confidence
            final_price *= weighted_factor
        
        return final_price
    
    def _generate_cache_key(self, player_id: str, station_id: str, 
                          commodity: str, action: str, quantity: int) -> str:
        """Generate cache key for quantum calculations"""
        components = f"{player_id}:{station_id}:{commodity}:{action}:{quantity}"
        return hashlib.sha256(components.encode()).hexdigest()
    
    def _generate_cascade_id(self) -> str:
        """Generate unique cascade ID"""
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        random_component = hashlib.sha256(str(np.random.random()).encode()).hexdigest()[:8]
        return f"cascade_{timestamp}_{random_component}"
    
    def _generate_recommendation(self, value: float, action: str,
                               commodity: str) -> str:
        """Generate trading recommendation"""
        if action == "buy":
            if value < 1000:
                return f"Low cost opportunity for {commodity}"
            elif value < 5000:
                return f"Moderate investment in {commodity}"
            else:
                return f"High investment required for {commodity}"
        else:  # sell
            if value > 5000:
                return f"Excellent selling opportunity for {commodity}"
            elif value > 1000:
                return f"Good selling opportunity for {commodity}"
            else:
                return f"Limited profit potential for {commodity}"
    
    async def _calculate_anomaly_score(self, player_id: str, event_type: str,
                                     event_data: Dict[str, Any]) -> float:
        """Calculate anomaly score for security monitoring"""
        # Simple anomaly detection - in production would use ML
        score = 0.0
        
        # Check for unusual patterns
        if event_type == "quantum_generation":
            # Unusual if generating predictions for many commodities rapidly
            if event_data.get("states_generated", 0) > 10:
                score += 0.3
                
        elif event_type == "unauthorized_visit_attempt":
            # High anomaly for authorization failures
            score += 0.8
            
        elif event_type == "manipulation_detected":
            # Very high for market manipulation
            score += 0.9
        
        return min(score, 1.0)
    
    def _load_manipulation_patterns(self) -> List[Dict[str, Any]]:
        """Load market manipulation patterns"""
        return [
            {
                "pattern": "rapid_price_change",
                "threshold": 0.5,  # 50% price change
                "window": timedelta(hours=1)
            },
            {
                "pattern": "volume_spike", 
                "threshold": 10,  # 10x normal volume
                "window": timedelta(hours=2)
            },
            {
                "pattern": "circular_trading",
                "threshold": 0.7,  # 70% trades between same players
                "window": timedelta(hours=4)
            }
        ]
    
    async def _get_sector_exploration(self, player_id: str, sector_id: str,
                                    db: AsyncSession) -> Optional[ARIAExplorationMap]:
        """Get player's exploration data for a sector"""
        stmt = select(ARIAExplorationMap).where(
            and_(
                ARIAExplorationMap.player_id == player_id,
                ARIAExplorationMap.sector_id == sector_id
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def _get_market_intelligence(self, player_id: str, station_id: str,
                                     commodity: str, 
                                     db: AsyncSession) -> Optional[ARIAMarketIntelligence]:
        """Get player's market intelligence for a commodity at a port"""
        stmt = select(ARIAMarketIntelligence).where(
            and_(
                ARIAMarketIntelligence.player_id == player_id,
                ARIAMarketIntelligence.station_id == station_id,
                ARIAMarketIntelligence.commodity == commodity
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def _get_explored_sectors(self, player_id: str, 
                                  db: AsyncSession) -> List[ARIAExplorationMap]:
        """Get all sectors explored by player"""
        stmt = select(ARIAExplorationMap).where(
            ARIAExplorationMap.player_id == player_id
        )
        result = await db.execute(stmt)
        return result.scalars().all()
    
    async def _build_personal_trade_graph(self, player_id: str,
                                        explored_sectors: List[ARIAExplorationMap],
                                        db: AsyncSession) -> Dict[str, Any]:
        """Build graph of known trade routes from personal data"""
        graph = {}
        
        for exploration in explored_sectors:
            sector_id = exploration.sector_id
            
            # Get market intelligence for this sector
            stmt = select(ARIAMarketIntelligence).where(
                and_(
                    ARIAMarketIntelligence.player_id == player_id,
                    ARIAMarketIntelligence.sector_id == sector_id
                )
            )
            result = await db.execute(stmt)
            intelligences = result.scalars().all()
            
            if intelligences:
                graph[sector_id] = {
                    "ports": defaultdict(dict),
                    "connections": [],  # Would get from warp tunnel data
                    "visit_count": exploration.visit_count,
                    "trade_opportunity": exploration.trade_opportunity_score
                }
                
                for intel in intelligences:
                    if intel.station_id:
                        graph[sector_id]["ports"][intel.station_id][intel.commodity] = {
                            "avg_price": intel.average_price,
                            "volatility": intel.price_volatility,
                            "confidence": intel.prediction_confidence,
                            "observations": intel.data_points
                        }
        
        return graph
    
    async def _find_profitable_paths(self, player_id: str, start_sector: str,
                                   trade_graph: Dict[str, Any], target_profit: float,
                                   max_jumps: int, db: AsyncSession) -> List[Dict[str, Any]]:
        """Find profitable trade paths through known space"""
        # Simplified pathfinding - in production would use A* or similar
        profitable_paths = []
        
        # This is a placeholder for the actual pathfinding algorithm
        # Would implement proper graph traversal here
        
        return profitable_paths
    
    async def _get_quantum_cache(self, player_id: str, cache_key: str,
                               db: AsyncSession) -> Optional[Dict[str, Any]]:
        """Get cached quantum calculation"""
        stmt = select(ARIAQuantumCache).where(
            and_(
                ARIAQuantumCache.player_id == player_id,
                ARIAQuantumCache.cache_key == cache_key,
                ARIAQuantumCache.expires_at > datetime.now(UTC)
            )
        )
        result = await db.execute(stmt)
        cache_entry = result.scalar_one_or_none()
        
        if cache_entry:
            cache_entry.hit_count += 1
            await db.commit()
            return cache_entry.ghost_results
        
        return None
    
    async def _cache_quantum_result(self, player_id: str, cache_key: str,
                                  result: Dict[str, Any], db: AsyncSession):
        """Cache quantum calculation result"""
        # Calculate expiry based on market volatility
        # More volatile = shorter cache
        expiry = datetime.now(UTC) + timedelta(minutes=15)
        
        cache_entry = ARIAQuantumCache(
            player_id=player_id,
            cache_key=cache_key,
            commodity=result.get("commodity", "UNKNOWN"),
            quantum_states=[],  # Would store actual states
            ghost_results=result,
            expected_value=result.get("expected_cost", result.get("expected_revenue", 0)),
            confidence_interval=[0, 0],  # Would calculate
            expires_at=expiry
        )
        
        db.add(cache_entry)
        await db.commit()
    
    async def _create_trading_pattern(self, player_id: str, trade_result: Dict[str, Any],
                                    db: AsyncSession) -> str:
        """Create new trading pattern from successful trade"""
        # Generate pattern DNA from trade characteristics
        pattern_dna = {
            "commodity": trade_result.get("commodity"),
            "action": trade_result.get("action"),
            "time_preference": datetime.now(UTC).hour,
            "quantity_range": trade_result.get("quantity"),
            "risk_tolerance": 0.5  # Would calculate from player profile
        }
        
        # Generate pattern ID
        pattern_id = hashlib.sha256(
            json.dumps(pattern_dna, sort_keys=True).encode()
        ).hexdigest()[:16]
        
        pattern = ARIATradingPattern(
            player_id=player_id,
            pattern_id=pattern_id,
            pattern_type=self._classify_pattern_type(pattern_dna),
            pattern_dna=pattern_dna,
            generation=1,
            discovered_at=datetime.now(UTC)
        )
        
        db.add(pattern)
        await db.commit()
        
        return pattern_id
    
    def _classify_pattern_type(self, pattern_dna: Dict[str, Any]) -> str:
        """Classify trading pattern type"""
        commodity = pattern_dna.get("commodity", "")
        
        if commodity in ["ORE", "ORGANICS", "FUEL"]:
            return "bulk_trading"
        elif commodity in ["LUXURY", "TECHNOLOGY"]:
            return "high_value"
        else:
            return "general"
    
    # =============================================================================
    # CONSCIOUSNESS & RELATIONSHIP TRACKING
    # =============================================================================

    # 5-tier bonus system: consciousness_level + interaction thresholds -> multiplier
    CONSCIOUSNESS_LEVEL_NAMES = {
        1: "Dormant",
        2: "Aware",
        3: "Awakened",
        4: "Sentient",
        5: "Transcendent",
    }

    CONSCIOUSNESS_BONUSES = {
        1: 1.0,   # Dormant
        2: 1.1,   # Aware (50+ interactions, 10+ memories)
        3: 1.2,   # Awakened (150+ interactions, 30+ memories)
        4: 1.35,  # Sentient (400+ interactions, 75+ memories)
        5: 1.5,   # Transcendent (1000+ interactions, 150+ memories)
    }

    CONSCIOUSNESS_THRESHOLDS = {
        2: {"interactions": 50, "memories": 10},
        3: {"interactions": 150, "memories": 30},
        4: {"interactions": 400, "memories": 75},
        5: {"interactions": 1000, "memories": 150},
    }

    async def update_consciousness_level(self, player_id: str, db: AsyncSession) -> Dict[str, Any]:
        """
        Update a player's ARIA consciousness level based on memory count and interaction quality.
        Called after significant ARIA interactions.
        """
        from src.models.player import Player

        stmt = select(Player).where(Player.id == player_id)
        result = await db.execute(stmt)
        player = result.scalar_one_or_none()
        if not player:
            return {"success": False, "message": "Player not found"}

        # Count player's ARIA memories
        memory_stmt = select(func.count(ARIAPersonalMemory.id)).where(
            ARIAPersonalMemory.player_id == player_id
        )
        memory_result = await db.execute(memory_stmt)
        memory_count = memory_result.scalar() or 0

        total_interactions = player.aria_total_interactions
        current_level = player.aria_consciousness_level

        # Check if player qualifies for a higher level
        new_level = 1
        for level in range(5, 1, -1):
            thresholds = self.CONSCIOUSNESS_THRESHOLDS[level]
            if total_interactions >= thresholds["interactions"] and memory_count >= thresholds["memories"]:
                new_level = level
                break

        leveled_up = new_level > current_level
        if leveled_up:
            player.aria_consciousness_level = new_level
            player.aria_bonus_multiplier = self.CONSCIOUSNESS_BONUSES[new_level]
            logger.info(
                "Player %s ARIA consciousness upgraded: %d -> %d (multiplier: %.2f)",
                player_id, current_level, new_level, player.aria_bonus_multiplier,
            )

        return {
            "success": True,
            "consciousness_level": player.aria_consciousness_level,
            "bonus_multiplier": player.aria_bonus_multiplier,
            "total_interactions": total_interactions,
            "memory_count": memory_count,
            "leveled_up": leveled_up,
        }

    async def update_relationship_score(self, player_id: str, db: AsyncSession, increment: int = 1) -> Dict[str, Any]:
        """
        Update the ARIA relationship score for a player.
        Score increases on interaction, decays on inactivity.
        """
        from src.models.player import Player

        stmt = select(Player).where(Player.id == player_id)
        result = await db.execute(stmt)
        player = result.scalar_one_or_none()
        if not player:
            return {"success": False, "message": "Player not found"}

        # Increment interaction count
        player.aria_total_interactions = (player.aria_total_interactions or 0) + 1

        # Update relationship score (clamped 0-100)
        old_score = player.aria_relationship_score
        player.aria_relationship_score = min(100, max(0, old_score + increment))

        return {
            "success": True,
            "relationship_score": player.aria_relationship_score,
            "total_interactions": player.aria_total_interactions,
            "score_change": player.aria_relationship_score - old_score,
        }

    async def apply_inactivity_decay(self, player_id: str, db: AsyncSession, days_inactive: int) -> Dict[str, Any]:
        """
        Decay relationship score based on days of inactivity.
        -1 point per day inactive, minimum 0.
        """
        from src.models.player import Player

        stmt = select(Player).where(Player.id == player_id)
        result = await db.execute(stmt)
        player = result.scalar_one_or_none()
        if not player:
            return {"success": False, "message": "Player not found"}

        decay = min(days_inactive, player.aria_relationship_score)
        player.aria_relationship_score = max(0, player.aria_relationship_score - decay)

        return {
            "success": True,
            "relationship_score": player.aria_relationship_score,
            "decay_applied": decay,
        }

    # =============================================================================
    # CONSCIOUSNESS EVOLUTION & GAMEPLAY INTEGRATION
    # =============================================================================

    async def update_consciousness_and_relationship(
        self, player_id: str, db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Holistic consciousness and relationship update that factors in memory
        diversity, total interactions, and consciousness level to drive both
        the relationship score and potential level-ups.

        This is the primary method that should be called after meaningful ARIA
        interactions (dialogue, trade analysis, combat debrief, etc.) to keep
        the consciousness system actively evolving alongside gameplay.

        Returns dict with old/new levels, relationship score, and memory counts.
        """
        from src.models.player import Player

        # Load the player
        stmt = select(Player).where(Player.id == player_id)
        result = await db.execute(stmt)
        player = result.scalar_one_or_none()
        if not player:
            return {"success": False, "message": "Player not found"}

        # Count total memories grouped by type
        memory_type_stmt = (
            select(
                ARIAPersonalMemory.memory_type,
                func.count(ARIAPersonalMemory.id).label("cnt"),
            )
            .where(ARIAPersonalMemory.player_id == player_id)
            .group_by(ARIAPersonalMemory.memory_type)
        )
        type_result = await db.execute(memory_type_stmt)
        memory_counts: Dict[str, int] = {}
        total_memories = 0
        for row in type_result:
            memory_counts[row.memory_type] = row.cnt
            total_memories += row.cnt

        # Capture the old state for the diff
        old_level = player.aria_consciousness_level
        old_relationship = player.aria_relationship_score
        total_interactions = player.aria_total_interactions or 0

        # --- Relationship score ---
        # Formula: base 25 + contribution from memories + contribution from
        # consciousness depth.  Memories represent breadth of shared experience;
        # consciousness level represents depth of bond.
        new_relationship = min(
            100,
            int(25 + (total_memories * 0.5) + (player.aria_consciousness_level * 10)),
        )
        player.aria_relationship_score = new_relationship

        # --- Consciousness level check ---
        # Walk thresholds from highest to lowest; first match wins.
        new_level = 1
        for level in range(5, 1, -1):
            thresholds = self.CONSCIOUSNESS_THRESHOLDS[level]
            if (
                total_interactions >= thresholds["interactions"]
                and total_memories >= thresholds["memories"]
            ):
                new_level = level
                break

        leveled_up = new_level > old_level
        if leveled_up:
            player.aria_consciousness_level = new_level
            player.aria_bonus_multiplier = self.CONSCIOUSNESS_BONUSES[new_level]
            # Re-calculate relationship with the *new* consciousness level
            player.aria_relationship_score = min(
                100,
                int(25 + (total_memories * 0.5) + (new_level * 10)),
            )
            logger.info(
                "Player %s ARIA consciousness evolved: %s (%d) -> %s (%d), "
                "relationship %d -> %d, multiplier %.2f",
                player_id,
                self.CONSCIOUSNESS_LEVEL_NAMES.get(old_level, "Unknown"),
                old_level,
                self.CONSCIOUSNESS_LEVEL_NAMES.get(new_level, "Unknown"),
                new_level,
                old_relationship,
                player.aria_relationship_score,
                player.aria_bonus_multiplier,
            )

        return {
            "success": True,
            "old_level": old_level,
            "new_level": player.aria_consciousness_level,
            "old_level_name": self.CONSCIOUSNESS_LEVEL_NAMES.get(old_level, "Unknown"),
            "new_level_name": self.CONSCIOUSNESS_LEVEL_NAMES.get(
                player.aria_consciousness_level, "Unknown"
            ),
            "leveled_up": leveled_up,
            "relationship_score": player.aria_relationship_score,
            "old_relationship_score": old_relationship,
            "bonus_multiplier": float(player.aria_bonus_multiplier),
            "total_interactions": total_interactions,
            "total_memories": total_memories,
            "memory_counts": memory_counts,
        }

    async def _resolve_player_language(
        self, player_id: str, db: AsyncSession
    ) -> str:
        """
        Resolve a player's preferred language code via an async lookup against
        the user-language-preference table. Defensive: returns "en" on any
        miss or error. Kept here (async) so we never mix ARIA's AsyncSession
        with the sync TranslationService session.
        """
        try:
            from src.models.player import Player
            from src.models.translation import UserLanguagePreference, Language

            stmt = (
                select(Language.code)
                .select_from(Player)
                .join(UserLanguagePreference, UserLanguagePreference.user_id == Player.user_id)
                .join(Language, Language.id == UserLanguagePreference.language_id)
                .where(Player.id == player_id)
            )
            result = await db.execute(stmt)
            code = result.scalar_one_or_none()
            return code or "en"
        except Exception as e:
            logger.warning(
                "Failed to resolve language for player %s: %s", player_id, e
            )
            return "en"

    async def _localize_recommendations(
        self, player_id: str, recommendations: List[str], db: AsyncSession
    ) -> List[str]:
        """
        Translate ARIA recommendation strings into the player's preferred
        language. Fully defensive: any failure (or an English preference)
        yields the original English recommendations unchanged.
        """
        if not recommendations:
            return recommendations
        try:
            target_language = await self._resolve_player_language(player_id, db)
            if not target_language or target_language.split("-")[0].lower() == "en":
                return recommendations

            from src.services.multilingual_ai_service import MultilingualAIService
            from src.services.ai_dialogue_service import AIDialogueService
            from src.services.translation_service import TranslationService

            ai_service = AIDialogueService()
            if not ai_service.is_available():
                return recommendations

            # translate_text() needs no DB; translation_service is only held as
            # a collaborator and unused on this path, so a None session is safe.
            multilingual = MultilingualAIService(None, ai_service, TranslationService(None))

            localized: List[str] = []
            for rec in recommendations:
                localized.append(await multilingual.translate_text(rec, target_language))
            return localized
        except Exception as e:
            logger.warning(
                "Failed to localize ARIA recommendations for player %s: %s",
                player_id, e,
            )
            return recommendations

    async def get_gameplay_recommendations(
        self, player_id: str, db: AsyncSession
    ) -> List[str]:
        """
        Public entry point: build rule-based gameplay recommendations and then
        localize them into the player's preferred language (defensive — falls
        back to English on any translation failure).
        """
        recommendations = await self._build_gameplay_recommendations(player_id, db)
        return await self._localize_recommendations(player_id, recommendations, db)

    async def _build_gameplay_recommendations(
        self, player_id: str, db: AsyncSession
    ) -> List[str]:
        """
        Generate rule-based gameplay recommendations that scale with the
        player's ARIA consciousness level.

        Lower levels produce generic tips.  Higher levels incorporate the
        player's own memory history to offer contextualised strategic advice.

        Returns a list of 1-3 recommendation strings (in English).
        """
        from src.models.player import Player

        stmt = select(Player).where(Player.id == player_id)
        result = await db.execute(stmt)
        player = result.scalar_one_or_none()
        if not player:
            return ["Unable to generate recommendations — player not found."]

        consciousness_level = player.aria_consciousness_level or 1
        recommendations: List[str] = []

        # ------------------------------------------------------------------
        # Level 1-2: Generic starter tips (no memory analysis needed)
        # ------------------------------------------------------------------
        if consciousness_level <= 2:
            recommendations.append(
                "Try trading at nearby stations to build up credits and market knowledge."
            )
            recommendations.append(
                "Explore more sectors — each new sector you visit expands my awareness."
            )
            if consciousness_level == 2:
                recommendations.append(
                    "You are forming a bond with me. Keep interacting and I will "
                    "unlock deeper strategic insights."
                )
            return recommendations[:3]

        # ------------------------------------------------------------------
        # Level 3+: Memory-aware recommendations
        # ------------------------------------------------------------------
        # Fetch recent memories to contextualise advice
        recent_memories_stmt = (
            select(ARIAPersonalMemory)
            .where(ARIAPersonalMemory.player_id == player_id)
            .order_by(ARIAPersonalMemory.created_at.desc())
            .limit(50)
        )
        mem_result = await db.execute(recent_memories_stmt)
        recent_memories = mem_result.scalars().all()

        # Bucket memories by type for analysis
        combat_count = 0
        market_count = 0
        exploration_count = 0
        for mem in recent_memories:
            if mem.memory_type == "combat":
                combat_count += 1
            elif mem.memory_type == "market":
                market_count += 1
            elif mem.memory_type == "exploration":
                exploration_count += 1

        # ------------------------------------------------------------------
        # Level 3: Market-aware observations
        # ------------------------------------------------------------------
        if consciousness_level == 3:
            if market_count > combat_count and market_count > exploration_count:
                recommendations.append(
                    "Based on your trading pattern, organics are profitable in "
                    "this region. Consider diversifying commodities to reduce risk."
                )
            elif exploration_count > market_count:
                recommendations.append(
                    "You have been exploring extensively. Visit stations in the "
                    "sectors you discovered to capitalise on fresh market data."
                )
            else:
                recommendations.append(
                    "Your activity is well-balanced. Focus on sectors where your "
                    "market intelligence is strongest for the best trade margins."
                )

            if combat_count > 0:
                recommendations.append(
                    "I have recorded combat encounters in your history. Consider "
                    "upgrading shields before venturing into contested sectors."
                )
            else:
                recommendations.append(
                    "You have avoided combat so far. If you plan to enter "
                    "dangerous sectors, prepare defensive equipment first."
                )
            return recommendations[:3]

        # ------------------------------------------------------------------
        # Level 4-5: Deep strategic advice
        # ------------------------------------------------------------------
        # Determine the player's dominant play-style from memory ratios
        total = combat_count + market_count + exploration_count
        if total > 0:
            combat_ratio = combat_count / total
            market_ratio = market_count / total
            exploration_ratio = exploration_count / total
        else:
            combat_ratio = market_ratio = exploration_ratio = 0.0

        if combat_ratio >= 0.4:
            recommendations.append(
                "Your combat record suggests focusing on bounty hunting for "
                "faster rank progression. Target high-value sectors you already "
                "know well."
            )
        elif market_ratio >= 0.4:
            recommendations.append(
                "Your trading expertise is your strongest asset. Build trade "
                "cascades through explored sectors to maximise profit per turn."
            )
        elif exploration_ratio >= 0.4:
            recommendations.append(
                "Your exploration data is extensive. Leverage it by selling route "
                "intelligence to team-mates or planning multi-hop trade cascades."
            )
        else:
            recommendations.append(
                "Your versatile approach gives you strategic flexibility. "
                "Consider specialising in one area to accelerate rank progression."
            )

        # Consciousness level 5 gets an additional transcendent-tier insight
        if consciousness_level >= 5:
            recommendations.append(
                "At transcendent awareness, I can perceive patterns across all "
                "your experiences. Your optimal path forward combines your "
                "strongest trading routes with strategic combat positioning."
            )
        else:
            # Level 4 gets a growth nudge
            recommendations.append(
                "Continue deepening our bond. At the next consciousness tier I "
                "will be able to synthesise cross-domain strategies for you."
            )

        # A third recommendation based on memory gaps
        if combat_count == 0:
            recommendations.append(
                "You have no combat memories yet. Even a single encounter "
                "would let me factor defence into my strategic models."
            )
        elif market_count == 0:
            recommendations.append(
                "I have no market data from you. Visit a station and trade — "
                "even a small transaction will seed my economic models."
            )
        elif exploration_count == 0:
            recommendations.append(
                "Exploring new sectors is the fastest way to unlock hidden "
                "trade routes and expand my situational awareness."
            )

        return recommendations[:3]

    async def get_consciousness_status(
        self, player_id: str, db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Return a comprehensive snapshot of the player's ARIA consciousness
        state, suitable for rendering in the player UI.

        Includes current level/name, relationship score, bonus multiplier,
        interaction count, memory breakdown, next-level requirements, and
        progress percentage toward the next tier.
        """
        from src.models.player import Player

        stmt = select(Player).where(Player.id == player_id)
        result = await db.execute(stmt)
        player = result.scalar_one_or_none()
        if not player:
            return {"success": False, "message": "Player not found"}

        consciousness_level = player.aria_consciousness_level or 1
        total_interactions = player.aria_total_interactions or 0

        # Count memories by type
        memory_type_stmt = (
            select(
                ARIAPersonalMemory.memory_type,
                func.count(ARIAPersonalMemory.id).label("cnt"),
            )
            .where(ARIAPersonalMemory.player_id == player_id)
            .group_by(ARIAPersonalMemory.memory_type)
        )
        type_result = await db.execute(memory_type_stmt)
        memory_counts: Dict[str, int] = {}
        total_memories = 0
        for row in type_result:
            memory_counts[row.memory_type] = row.cnt
            total_memories += row.cnt

        # Ensure common types are present even if zero
        for mtype in ("combat", "market", "exploration"):
            memory_counts.setdefault(mtype, 0)

        # Next level requirements and progress calculation
        next_level = consciousness_level + 1 if consciousness_level < 5 else None
        next_level_requirements: Optional[Dict[str, int]] = None
        progress_to_next: float = 1.0  # Default to 100% if already max

        if next_level and next_level in self.CONSCIOUSNESS_THRESHOLDS:
            thresholds = self.CONSCIOUSNESS_THRESHOLDS[next_level]
            next_level_requirements = {
                "interactions": thresholds["interactions"],
                "memories": thresholds["memories"],
            }

            # Progress is the average of interaction progress and memory progress,
            # each capped at 1.0 individually so one dimension can't inflate the other.
            interaction_progress = min(
                1.0, total_interactions / thresholds["interactions"]
            ) if thresholds["interactions"] > 0 else 1.0
            memory_progress = min(
                1.0, total_memories / thresholds["memories"]
            ) if thresholds["memories"] > 0 else 1.0
            progress_to_next = round(
                (interaction_progress + memory_progress) / 2.0, 2
            )

        return {
            "success": True,
            "level": consciousness_level,
            "level_name": self.CONSCIOUSNESS_LEVEL_NAMES.get(
                consciousness_level, "Unknown"
            ),
            "relationship_score": player.aria_relationship_score,
            "bonus_multiplier": float(player.aria_bonus_multiplier),
            "total_interactions": total_interactions,
            "total_memories": total_memories,
            "memory_counts": memory_counts,
            "next_level": next_level,
            "next_level_name": self.CONSCIOUSNESS_LEVEL_NAMES.get(next_level)
            if next_level
            else None,
            "next_level_requirements": next_level_requirements,
            "progress_to_next": progress_to_next,
        }


# Singleton instance
_aria_intelligence_service = None


def get_aria_intelligence_service() -> ARIAPersonalIntelligenceService:
    """Get or create ARIA intelligence service instance"""
    global _aria_intelligence_service
    if _aria_intelligence_service is None:
        _aria_intelligence_service = ARIAPersonalIntelligenceService()
    return _aria_intelligence_service