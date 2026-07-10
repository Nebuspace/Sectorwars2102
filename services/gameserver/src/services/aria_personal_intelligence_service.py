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
import heapq
import math
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
from src.models.sector import Sector, sector_warps
from src.models.station import Station
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus
from src.models.market_transaction import MarketTransaction
from src.models.aria_personal_intelligence import (
    ARIAPersonalMemory, ARIAMarketIntelligence, ARIAExplorationMap,
    ARIAQuantumCache, ARIASecurityLog,
    ARIATradingObservation, ObservationAction, ObservationOutcome,
)
# ARIATradingPattern (the GA/"Trade DNA" model) is DEPRECATED -- WO-ARIA-
# GA-CLEANUP removed its only callers (evolve_trading_pattern /
# get_evolved_patterns / _create_trading_pattern / _classify_pattern_type,
# ADR-0038). No longer imported here; see models/aria_personal_intelligence.py's
# own deprecation note on the class.
from src.core.config import settings
from src.core.security import get_password_hash
from src.core.game_time import scaled_elapsed

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

        # Per-player ARIA storage hard cap (NO-CANON: 10 MiB proposed — pure
        # data-maintenance, not dialogue/LLM). When a player's combined
        # ARIAPersonalMemory + ARIAMarketIntelligence row bytes exceed this,
        # the daily pass evicts oldest-first until back under the cap.
        self.MAX_PLAYER_ARIA_BYTES = 10 * 1024 * 1024  # 10 MiB
        
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
        
        # Get port's sector. sector_uuid (not the Integer sector_id -- see
        # station.py:99-100) is the FK-compatible field ARIAMarketIntelligence.
        # sector_id (UUID, NOT NULL) actually needs; a station with no
        # resolved sector_uuid can't be recorded (WO-ARIA-MARKET-OBS).
        station = await db.get(Station, station_id)
        if not station:
            return None
        if station.sector_uuid is None:
            logger.warning(
                "record_market_observation: station %s has no sector_uuid -- skipping",
                station_id,
            )
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
            # Update existing intelligence. REASSIGN (never in-place
            # .append()) -- price_observations is a plain Column(JSON), and
            # in-place mutation of the same list object bypasses SQLAlchemy's
            # attribute-set change tracking entirely, so the append would be
            # silently lost at flush (WO-ARIA-MARKET-OBS finding).
            intelligence.price_observations = intelligence.price_observations + [observation]
            intelligence.data_points += 1
            intelligence.last_visit = datetime.now(UTC)

            # Recalculate statistics
            prices = [obs["price"] for obs in intelligence.price_observations[-50:]]  # Last 50
            intelligence.average_price = statistics.mean(prices)
            intelligence.price_volatility = statistics.stdev(prices) if len(prices) > 1 else 0.0

            # Update patterns if enough data
            if intelligence.data_points >= self.MIN_DATA_POINTS_FOR_PREDICTION:
                patterns = self._identify_price_patterns(
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
                sector_id=station.sector_uuid,
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

    # Canonical dedup window (WO-ARIA-MARKET-OBS, NO-CANON -- flagged for the
    # DECISIONS batch): one market observation per (player, station,
    # commodity) per 10 CANONICAL minutes, spanning every hook site that
    # calls record_market_observation_sync for that station visit (dock,
    # market view, ...). Uses game_time.scaled_elapsed -- CANONICAL, not
    # wall-clock, matching this codebase's established clock-domain
    # convention (docking/construction/ownership durations all compare a
    # scaled-elapsed wall duration against a canonical threshold; see
    # src/core/game_time.py's module docstring).
    MARKET_OBSERVATION_DEDUP_WINDOW = timedelta(minutes=10)

    def record_market_observation_sync(
        self, player_id: str, station_id: str,
        market_prices: List[Dict[str, Any]], db: Session,
    ) -> None:
        """
        Synchronous, multi-commodity twin of ``record_market_observation``
        for sync-Session callers (WO-ARIA-MARKET-OBS) -- trading.py's
        dock / market-view hooks run on a sync Session and need to record a
        whole station visit's price list in one call, not one commodity at
        a time.

        One call per station visit; ``market_prices`` is the FULL commodity
        price list observed at ``station_id`` this visit. Each entry:
        ``{"commodity": str, "price": float | None, "quantity": int}``
        (``quantity`` optional, defaults to 0).

        Per-(player, station, commodity) upsert, gated by
        ``MARKET_OBSERVATION_DEDUP_WINDOW`` on that row's own ``last_visit``
        -- naturally covers "spans both hook sites" (whichever hook fires
        first sets ``last_visit``; a second hook re-submitting the same
        commodity within the window is a no-op for that commodity) while
        still recording any commodity that's genuinely new to this player+
        station regardless of what else in the same payload was just seen.

        FLUSH-ONLY: only ``db.add()``s / mutates already-attached rows; the
        CALLER owns the commit (folds into the route's single commit).
        Never raises -- an ARIA market-observation hiccup must never break
        docking or the market view.

        WO-SWEEP-ARIA-MI-COLUMN: each commodity's read+write is now its own
        SAVEPOINT (``db.begin_nested()``) -- a DB-level failure (the
        phantom-column defect this WO fixed, or any future one) rolls back
        ONLY that commodity, never poisons the session for the rest of this
        station visit's payload or the caller's own commit. Mirrors
        bounty_service.py:774 / combat_service.py:4382 /
        faction_service.py:212's add+flush-inside-begin_nested idiom, widened
        to cover the SELECT too: a failed SELECT aborts the current
        transaction in Postgres exactly like a failed flush does, and here
        the SELECT (not just the INSERT/UPDATE) is the actual failure point
        the phantom-column defect hit.
        """
        try:
            if not self._validate_player_at_port_sync(player_id, station_id, db):
                self._log_security_event_sync(
                    player_id, "invalid_market_observation", "warning",
                    {"station_id": station_id, "commodity_count": len(market_prices or [])},
                    db,
                )
                return

            station = db.query(Station).filter(Station.id == station_id).first()
            if not station:
                logger.warning(
                    "record_market_observation_sync: station %s not found -- skipping",
                    station_id,
                )
                return
            if station.sector_uuid is None:
                logger.warning(
                    "record_market_observation_sync: station %s has no sector_uuid -- skipping",
                    station_id,
                )
                return

            if not market_prices:
                return  # empty market -> 0 writes, no error

            now = datetime.now(UTC)

            for entry in market_prices:
                commodity = entry.get("commodity")
                if not commodity:
                    logger.warning(
                        "record_market_observation_sync: entry with no commodity for "
                        "player %s at station %s -- skipping", player_id, station_id,
                    )
                    continue

                price = entry.get("price")
                if price is None:
                    logger.info(
                        "record_market_observation_sync: %s at station %s has no "
                        "price -- skipping", commodity, station_id,
                    )
                    continue  # price 0 is a real market state and IS recorded

                quantity = entry.get("quantity", 0)

                try:
                    with db.begin_nested():
                        intelligence = (
                            db.query(ARIAMarketIntelligence)
                            .filter(
                                ARIAMarketIntelligence.player_id == player_id,
                                ARIAMarketIntelligence.station_id == station_id,
                                ARIAMarketIntelligence.commodity == commodity,
                            )
                            .first()
                        )

                        observation = {
                            "price": price,
                            "quantity": quantity,
                            "timestamp": now.isoformat(),
                        }

                        if intelligence is not None:
                            if (
                                intelligence.last_visit is not None
                                and scaled_elapsed(intelligence.last_visit, now)
                                < self.MARKET_OBSERVATION_DEDUP_WINDOW
                            ):
                                continue  # within the dedup window -- no-op for this commodity

                            # REASSIGN, never in-place .append() -- price_observations
                            # is a plain Column(JSON); in-place mutation of the same
                            # list object bypasses SQLAlchemy's change tracking and
                            # would be silently lost at flush.
                            intelligence.price_observations = intelligence.price_observations + [observation]
                            intelligence.data_points += 1
                            intelligence.last_visit = now

                            prices = [obs["price"] for obs in intelligence.price_observations[-50:]]
                            intelligence.average_price = statistics.mean(prices)
                            intelligence.price_volatility = statistics.stdev(prices) if len(prices) > 1 else 0.0

                            if intelligence.data_points >= self.MIN_DATA_POINTS_FOR_PREDICTION:
                                intelligence.identified_patterns = self._identify_price_patterns(
                                    intelligence.price_observations
                                )
                                intelligence.prediction_confidence = min(
                                    intelligence.data_points / 20, 0.95
                                )
                        else:
                            intelligence = ARIAMarketIntelligence(
                                player_id=player_id,
                                station_id=station_id,
                                sector_id=station.sector_uuid,
                                commodity=commodity,
                                price_observations=[observation],
                                average_price=price,
                                price_volatility=0.0,
                                data_points=1,
                                last_visit=now,
                                intelligence_quality=0.1,
                            )
                            db.add(intelligence)

                        intelligence.intelligence_quality = self._calculate_intelligence_quality(
                            intelligence.data_points,
                            intelligence.last_visit,
                            intelligence.price_volatility,
                        )
                        db.flush()
                except Exception as row_err:
                    logger.warning(
                        "record_market_observation_sync: commodity %s at station %s "
                        "failed (isolated to this commodity, rest of the visit's "
                        "payload is unaffected): %s",
                        commodity, station_id, row_err,
                    )
                    continue
        except Exception as e:
            logger.warning(
                "record_market_observation_sync failed for player %s at station %s: %s",
                player_id, station_id, e,
            )

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

    def record_trade_memory_sync(self, player_id: str, trade_data: dict,
                                 db: Session) -> None:
        """Synchronous twin of ``record_trade_memory`` for sync-Session callers.

        trading.py's buy/sell routes run entirely on a synchronous SQLAlchemy
        ``Session`` (WO-ARIA-OBS-LOG addendum): the async ``record_trade_memory``
        internally ``await``s ``db.execute(select(...))``, which raises against a
        sync ``Session`` -- swallowed by its own except, so zero
        ``ARIAPersonalMemory`` rows ever persisted through the trade path. This
        method records the exact same ``trade_transaction`` memory shape via the
        sync session: identical encryption, content schema, importance formula,
        and dedup-by-hash logic as ``record_trade_memory`` -- only the DB calls
        differ (sync ``query``/``add`` instead of ``await db.execute``/``select``),
        mirroring ``record_combat_memory_sync``'s existing precedent exactly.

        FLUSH-FREE: only ``db.add``s the memory; the CALLER owns the commit (so
        it folds into the trade's single commit). Never raises -- an ARIA
        memory hiccup must never break a real trade.

        Args:
            player_id: the player whose ARIA should remember this trade.
            trade_data: same keys as ``record_trade_memory`` -- ``station_name``,
                ``action``, ``commodity``, ``quantity``, ``total_value``,
                ``profit``.
            db: synchronous database session (caller commits).
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
                return  # Memory already exists (dedup -- also our double-fire guard)

            memory = ARIAPersonalMemory(
                player_id=player_id,
                memory_type="market",
                importance_score=importance,
                memory_content={"encrypted": encrypted_content},
                memory_hash=memory_hash,
                confidence_level=0.9,
                decay_rate=self.MEMORY_DECAY_RATE,
            )
            db.add(memory)
            self.memories_created += 1

            logger.info(
                "Recorded trade memory (sync) for player %s: %s %s at %s",
                player_id, action, commodity,
                trade_data.get("station_name", "Unknown"),
            )
        except Exception as e:
            logger.warning(
                "Failed to record trade memory (sync) for player %s: %s",
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
    
    async def _validate_player_at_port(self, player_id: str, station_id: str,
                                     db: AsyncSession) -> bool:
        """Validate player is docked at this station.

        WO-ARIA-MARKET-OBS fix: the previous implementation queried
        ``Ship.player_id`` / ``Ship.current_port_id`` -- neither column
        exists on the ``Ship`` model (see models/ship.py; it has
        ``owner_id`` and no port reference at all). Every call raised
        ``AttributeError`` building the ``select()``, silently swallowed by
        this method's callers, so this gate has ALWAYS returned a false
        negative in practice -- a total, silent no-op. Docking state
        actually lives on ``Player``: ``is_docked`` (bool) +
        ``current_sector_id``, checked against the station's sector -- the
        exact convention every trading.py route already uses (see
        buy_resource/sell_resource's "must be docked" + "must be in the
        same sector" checks).
        """
        from src.models.player import Player

        stmt = select(Player).where(Player.id == player_id)
        result = await db.execute(stmt)
        player = result.scalar_one_or_none()
        if player is None or not player.is_docked:
            return False

        station = await db.get(Station, station_id)
        if station is None:
            return False

        return player.current_sector_id == station.sector_id

    def _validate_player_at_port_sync(self, player_id: str, station_id: str,
                                      db: Session) -> bool:
        """Synchronous twin of ``_validate_player_at_port`` for sync-Session
        callers (WO-ARIA-MARKET-OBS) -- same Player.is_docked +
        current_sector_id-vs-station.sector_id check, same bug-fix
        rationale as the async version above."""
        from src.models.player import Player

        player = db.query(Player).filter(Player.id == player_id).first()
        if player is None or not player.is_docked:
            return False

        station = db.query(Station).filter(Station.id == station_id).first()
        if station is None:
            return False

        return player.current_sector_id == station.sector_id

    async def _log_security_event(self, player_id: str, event_type: str,
                                severity: str, event_data: Dict[str, Any],
                                db: AsyncSession):
        """Log security events for audit (OWASP A09)"""
        # Calculate anomaly score
        anomaly_score = self._calculate_anomaly_score(
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

    def _log_security_event_sync(self, player_id: str, event_type: str,
                                 severity: str, event_data: Dict[str, Any],
                                 db: Session) -> None:
        """Synchronous twin of ``_log_security_event`` for sync-Session
        callers (WO-ARIA-MARKET-OBS). FLUSH-ONLY like every other sync twin
        in this class -- the caller owns the commit; the append to
        ``security_flags`` below is safe in-place mutation on a brand-new,
        not-yet-added ``log_entry`` (there is no prior committed baseline to
        lose), unlike the price_observations bug this WO also fixes."""
        anomaly_score = self._calculate_anomaly_score(
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

        if anomaly_score > self.anomaly_threshold:
            log_entry.security_flags.append("high_anomaly_score")
            log_entry.action_taken = "flagged_for_review"
            logger.warning(f"High anomaly score {anomaly_score} for player {player_id}")

        db.add(log_entry)

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
    
    def _identify_price_patterns(self, observations: List[Dict]) -> List[str]:
        """Identify patterns in price history.

        Pure computation, no I/O -- made sync (WO-ARIA-MARKET-OBS) so both
        the async record_market_observation and the sync
        record_market_observation_sync can share this one implementation
        instead of duplicating it. Its one caller previously used ``await``
        for no reason (nothing inside this method ever awaited anything).
        """
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
    
    def _calculate_anomaly_score(self, player_id: str, event_type: str,
                                event_data: Dict[str, Any]) -> float:
        """Calculate anomaly score for security monitoring.

        Pure computation, no I/O -- made sync (WO-ARIA-MARKET-OBS) so both
        _log_security_event (async) and its new sync twin can share this one
        implementation.
        """
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
    
    async def _build_explored_adjacency(
        self, explored_sector_ids: Set[str], db: AsyncSession,
    ) -> Dict[str, List[Tuple[str, int]]]:
        """Adjacency list (sector UUID -> [(neighbour UUID, turn_cost), ...])
        restricted to ``explored_sector_ids`` on BOTH endpoints -- ADR-0075
        ("route candidates limited to visited + charted sectors"). Mirrors
        ``nav_service.NavService._build_known_graph``'s exact edge-source
        selection (``sector_warps`` association table + ACTIVE ``WarpTunnel``
        rows), adapted to ``AsyncSession``.

        No UUID<->global-int conversion is needed here, unlike NavService
        (which keys its graph by the human-readable global ``Sector.sector_id``
        integer): ``ARIAExplorationMap.sector_id`` -- and therefore every key
        in ``trade_graph`` -- IS the ``sectors.id`` UUID already, the exact
        type ``sector_warps``/``WarpTunnel`` key on.
        """
        if not explored_sector_ids:
            return {}
        ids = list(explored_sector_ids)
        graph: Dict[str, List[Tuple[str, int]]] = {sid: [] for sid in explored_sector_ids}

        warp_rows = (
            await db.execute(sector_warps.select().where(sector_warps.c.source_sector_id.in_(ids)))
        ).fetchall()
        for row in warp_rows:
            src, dst = row.source_sector_id, row.destination_sector_id
            if src not in explored_sector_ids or dst not in explored_sector_ids:
                continue
            tc = row.turn_cost or 1
            graph[src].append((dst, tc))
            if row.is_bidirectional and src != dst:
                graph[dst].append((src, tc))

        tunnel_stmt = select(WarpTunnel).where(
            and_(
                WarpTunnel.status == WarpTunnelStatus.ACTIVE,
                WarpTunnel.origin_sector_id.in_(ids),
            )
        )
        tunnel_rows = (await db.execute(tunnel_stmt)).scalars().all()
        for tunnel in tunnel_rows:
            origin, dest = tunnel.origin_sector_id, tunnel.destination_sector_id
            if origin not in explored_sector_ids or dest not in explored_sector_ids:
                continue
            tc = tunnel.turn_cost or 1
            graph[origin].append((dest, tc))
            if tunnel.is_bidirectional:
                graph[dest].append((origin, tc))

        return graph

    def _dijkstra_hop_distances(
        self, graph: Dict[str, List[Tuple[str, int]]], start_sector_id: str, max_jumps: int,
    ) -> Dict[str, int]:
        """All-destinations shortest HOP COUNT from ``start_sector_id``,
        pruned to ``max_jumps``. Returns ``{sector_id: hop_count}`` for
        every reachable sector within budget (including the start sector
        itself, at 0).

        Unweighted (every edge costs exactly 1 hop) -- canon's
        ``max_jumps`` is explicitly a HOP-COUNT budget
        (aria-companion.md:38, ``Input: ... max_jumps``), not a turn-cost
        budget like NavService's turn-cost-weighted Dijkstra. Weighting by
        real ``turn_cost``/tunnel stability here would let a low-turn-cost
        multi-hop route "beat" a genuinely-fewer-hop route on weighted
        cost while still violating the hop budget -- not the semantics
        canon asks for. Still implemented as a min-heap Dijkstra (mirrors
        ``nav_service.NavService._dijkstra``'s shape) rather than a plain
        BFS queue, even though the two are equivalent at uniform edge
        weight -- this stays trivially extensible if a future WO ever
        needs weighted hops.

        A* was considered and rejected: this is a single-source
        ALL-destinations search (every other explored sector is a
        candidate sell leg, not one fixed target) -- exactly Dijkstra/
        BFS's shape. A* only pays off for a single-source SINGLE-target
        search with a goal-directed heuristic, which doesn't apply here.
        """
        dist: Dict[str, int] = {start_sector_id: 0}
        pq: List[Tuple[int, str]] = [(0, start_sector_id)]
        while pq:
            d, node = heapq.heappop(pq)
            if d > dist.get(node, math.inf):
                continue
            if d >= max_jumps:
                continue  # do not expand past the jump budget
            for neighbour, _turn_cost in graph.get(node, []):
                nd = d + 1
                if nd < dist.get(neighbour, math.inf):
                    dist[neighbour] = nd
                    heapq.heappush(pq, (nd, neighbour))
        return dist

    async def _find_profitable_paths(self, player_id: str, start_sector: str,
                                   trade_graph: Dict[str, Any], target_profit: float,
                                   max_jumps: int, db: AsyncSession) -> List[Dict[str, Any]]:
        """Find profitable trade paths through EXPLORED space only
        (ADR-0075; aria-companion.md:33-50). Two-leg cascades: buy at a
        station in ``start_sector``, sell the SAME commodity at a station
        in another explored sector reachable within ``max_jumps`` real
        warp/tunnel hops.

        [NO-CANON] Profit-scoring: canon's ``plan_trade_cascade`` input
        has no cargo/quantity parameter, so ``total_profit`` here is the
        PER-UNIT price differential (sell avg_price - buy avg_price) from
        ``trade_graph``'s ``ARIAMarketIntelligence`` observations -- real,
        populated data (per the WO's own guidance). The alternative
        signal, ``get_top_routes``/the SQL-aggregate recommendation
        engine (``ARIATradingObservation``-backed, real completed-trade
        profit), is DELIBERATELY NOT used here: that engine is SYNC
        (``Session``, WO-ARIA-OBS-LOG's own documented split matching
        trading.py's sync buy/sell path) while this entire call chain is
        ``AsyncSession``-based -- bridging would need
        ``AsyncSession.run_sync(...)`` (this file's own OBS-LOG section
        docstring names that as the future connector for "a future async
        caller"), which this WO does not need to introduce for a
        secondary signal the WO's own brief already flags as sparse
        (sell-leg ``profit`` is ``None`` until the cost-basis WO lands).
        ``confidence`` is the MIN of the two legs' ``prediction_confidence``
        (conservative combination, not an average) -- flagged, not canon.

        Degrades honestly: returns ``[]`` when the start sector has no
        market intelligence, no other explored sector is reachable within
        ``max_jumps``, or no commodity clears ``target_profit`` -- never a
        fabricated result.
        """
        start_ports = trade_graph.get(start_sector, {}).get("ports") or {}
        if not start_ports:
            return []

        explored_ids: Set[str] = set(trade_graph.keys())
        adjacency = await self._build_explored_adjacency(explored_ids, db)
        hop_distances = self._dijkstra_hop_distances(adjacency, start_sector, max_jumps)

        profitable_paths: List[Dict[str, Any]] = []
        for sell_sector_id, hops in hop_distances.items():
            if sell_sector_id == start_sector or hops <= 0 or hops > max_jumps:
                continue
            sell_ports = trade_graph.get(sell_sector_id, {}).get("ports") or {}
            if not sell_ports:
                continue

            for buy_station_id, buy_commodities in start_ports.items():
                for commodity, buy_intel in buy_commodities.items():
                    buy_price = buy_intel.get("avg_price")
                    if buy_price is None:
                        continue

                    for sell_station_id, sell_commodities in sell_ports.items():
                        sell_intel = sell_commodities.get(commodity)
                        if sell_intel is None:
                            continue
                        sell_price = sell_intel.get("avg_price")
                        if sell_price is None:
                            continue

                        profit = sell_price - buy_price
                        if profit < target_profit:
                            continue

                        confidence = min(
                            buy_intel.get("confidence", 0.0),
                            sell_intel.get("confidence", 0.0),
                        )
                        profitable_paths.append({
                            "total_profit": profit,
                            "jumps": hops,
                            "profit_per_jump": profit / hops,
                            "confidence": confidence,
                            "path": [
                                {
                                    "sector_id": start_sector,
                                    "station_id": buy_station_id,
                                    "action": "buy",
                                    "commodity": commodity,
                                    "expected_price": buy_price,
                                    "confidence": buy_intel.get("confidence", 0.0),
                                    "observations": buy_intel.get("observations", 0),
                                },
                                {
                                    "sector_id": sell_sector_id,
                                    "station_id": sell_station_id,
                                    "action": "sell",
                                    "commodity": commodity,
                                    "expected_price": sell_price,
                                    "confidence": sell_intel.get("confidence", 0.0),
                                    "observations": sell_intel.get("observations", 0),
                                },
                            ],
                        })

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

    # WO-ARIA-PROGRESSION: the single canonical copy of the (interactions ->
    # (level, multiplier)) mapping that was previously duplicated verbatim
    # across movement_service.py, combat_service.py, and trading.py (buy +
    # sell) -- all four now call update_consciousness_and_relationship[_sync]
    # instead of re-declaring this dict. Paired with CONSCIOUSNESS_THRESHOLDS
    # above for the memory-side gate (aria-companion.md:128: "Both thresholds
    # ... must be met to advance").
    CONSCIOUSNESS_INTERACTION_THRESHOLDS = {50: (2, 1.1), 150: (3, 1.2), 400: (4, 1.35), 1000: (5, 1.5)}

    def _apply_consciousness_and_relationship(
        self, player, total_memories: int,
    ) -> Dict[str, Any]:
        """
        Pure state-mutation core shared by ``update_consciousness_and_
        relationship`` (async) and ``update_consciousness_and_relationship_
        sync`` -- mutates ``player`` in place; no I/O, no DB access. See the
        async method's docstring for the full canon citation and the
        memory-diversity interpretation this WO flags.
        """
        old_level = player.aria_consciousness_level
        old_relationship = player.aria_relationship_score or 0

        # aria-companion.md:139 -- "Rises +1 per significant interaction
        # (capped at 100)". This call itself IS the significant interaction
        # (NO-CANON, flagged for DECISIONS: "significant interaction" =
        # exactly the call sites that already bump aria_total_interactions
        # today -- movement, combat victory, buy, sell).
        player.aria_total_interactions = (player.aria_total_interactions or 0) + 1
        player.aria_relationship_score = min(100, old_relationship + 1)
        total_interactions = player.aria_total_interactions

        # aria-companion.md:128 -- both thresholds must be met to advance.
        # Ascending walk over CONSCIOUSNESS_INTERACTION_THRESHOLDS (50, 150,
        # 400, 1000): each time both gates clear, new_level is overwritten,
        # so the loop's final value is the HIGHEST qualifying level.
        new_level = 1
        new_multiplier = self.CONSCIOUSNESS_BONUSES[1]
        for threshold, (level, multiplier) in self.CONSCIOUSNESS_INTERACTION_THRESHOLDS.items():
            memories_needed = self.CONSCIOUSNESS_THRESHOLDS[level]["memories"]
            if total_interactions >= threshold and total_memories >= memories_needed:
                new_level = level
                new_multiplier = multiplier

        leveled_up = new_level > old_level
        if leveled_up:
            player.aria_consciousness_level = new_level
            player.aria_bonus_multiplier = new_multiplier
            logger.info(
                "Player %s ARIA consciousness evolved: %s (%d) -> %s (%d), "
                "relationship %d -> %d, multiplier %.2f",
                getattr(player, "id", "?"),
                self.CONSCIOUSNESS_LEVEL_NAMES.get(old_level, "Unknown"), old_level,
                self.CONSCIOUSNESS_LEVEL_NAMES.get(new_level, "Unknown"), new_level,
                old_relationship, player.aria_relationship_score, player.aria_bonus_multiplier,
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
    # STORAGE MAINTENANCE (size-cap prune — NOT dialogue/LLM)
    # =============================================================================

    @staticmethod
    def _row_byte_size(value: Any) -> int:
        """
        Estimate the on-disk JSON byte size of a single ARIA row's payload.

        Pure size proxy: the UTF-8 byte length of the row's JSON content
        (memory_content for memories, price_observations for market
        intelligence). Defensive — a non-serialisable payload falls back to
        ``len(str(value))`` so one bad row can never derail the whole prune.
        """
        if value is None:
            return 0
        try:
            return len(json.dumps(value, default=str).encode("utf-8"))
        except Exception:
            try:
                return len(str(value).encode("utf-8"))
            except Exception:
                return 0

    async def prune_player_storage(
        self, player_id: str, db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Daily-pass-callable storage prune for ONE player's ARIA data.

        Computes the combined JSON byte size of the player's
        ``ARIAPersonalMemory`` rows (``memory_content``) and
        ``ARIAMarketIntelligence`` rows (``price_observations`` — the player's
        personal trading-observation log). If the total exceeds the
        ``MAX_PLAYER_ARIA_BYTES`` hard cap (NO-CANON: 10 MiB), the OLDEST rows
        across BOTH tables are evicted first until the total is back under the
        cap. Under-cap players are left completely untouched.

        This is pure data maintenance — it deletes whole stale rows by age and
        size. It does NOT read, write, decrypt, or otherwise touch any
        LLM/dialogue/prompt logic; the memories' encrypted contents are never
        opened. Ordering keys:
          - ARIAPersonalMemory:       ``created_at``
          - ARIAMarketIntelligence:   ``last_visit`` (NULL sorts oldest)

        Returns a summary dict (no raise on the happy path); fully defensive so
        a hiccup in the nightly sweep can never break the pass for other players.

        Args:
            player_id: the player whose ARIA storage to size-check and prune.
            db: async database session (this method owns its single commit).

        Returns:
            {success, over_cap, bytes_before, bytes_after, cap_bytes,
             evicted_memories, evicted_intelligence, evicted_total}
        """
        cap = self.MAX_PLAYER_ARIA_BYTES

        try:
            # --- Load the player's rows from both tables ---
            mem_result = await db.execute(
                select(ARIAPersonalMemory).where(
                    ARIAPersonalMemory.player_id == player_id
                )
            )
            memories = mem_result.scalars().all()

            intel_result = await db.execute(
                select(ARIAMarketIntelligence).where(
                    ARIAMarketIntelligence.player_id == player_id
                )
            )
            intelligences = intel_result.scalars().all()

            # --- Build an age-sortable, size-tagged eviction list ---
            # Each entry: (sort_key_datetime, byte_size, kind, orm_obj)
            # Use a tz-aware epoch floor so NULL timestamps sort OLDEST and
            # naive/aware mixes never raise on comparison.
            epoch = datetime(1970, 1, 1, tzinfo=UTC)

            def _as_aware(dt: Optional[datetime]) -> datetime:
                if dt is None:
                    return epoch
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=UTC)
                return dt

            entries: List[Tuple[datetime, int, str, Any]] = []
            total_bytes = 0

            for m in memories:
                size = self._row_byte_size(m.memory_content)
                total_bytes += size
                entries.append(
                    (_as_aware(m.created_at), size, "memory", m)
                )

            for intel in intelligences:
                size = self._row_byte_size(intel.price_observations)
                total_bytes += size
                entries.append(
                    (_as_aware(intel.last_visit), size, "intelligence", intel)
                )

            bytes_before = total_bytes

            # --- Under cap: untouched ---
            if total_bytes <= cap:
                return {
                    "success": True,
                    "over_cap": False,
                    "bytes_before": bytes_before,
                    "bytes_after": bytes_before,
                    "cap_bytes": cap,
                    "evicted_memories": 0,
                    "evicted_intelligence": 0,
                    "evicted_total": 0,
                }

            # --- Over cap: evict OLDEST-first across both tables ---
            entries.sort(key=lambda e: e[0])  # oldest first

            evicted_memories = 0
            evicted_intelligence = 0
            for _sort_key, size, kind, obj in entries:
                if total_bytes <= cap:
                    break
                await db.delete(obj)
                total_bytes -= size
                if kind == "memory":
                    evicted_memories += 1
                else:
                    evicted_intelligence += 1

            await db.commit()

            evicted_total = evicted_memories + evicted_intelligence
            logger.info(
                "ARIA storage prune for player %s: %d -> %d bytes (cap %d), "
                "evicted %d rows (%d memories, %d intelligence)",
                player_id, bytes_before, total_bytes, cap,
                evicted_total, evicted_memories, evicted_intelligence,
            )

            return {
                "success": True,
                "over_cap": True,
                "bytes_before": bytes_before,
                "bytes_after": total_bytes,
                "cap_bytes": cap,
                "evicted_memories": evicted_memories,
                "evicted_intelligence": evicted_intelligence,
                "evicted_total": evicted_total,
            }
        except Exception as e:
            logger.warning(
                "ARIA storage prune failed for player %s: %s", player_id, e
            )
            try:
                await db.rollback()
            except Exception:
                logger.debug("ARIA storage prune: db.rollback failed for player %s", player_id, exc_info=True)
            return {
                "success": False,
                "over_cap": False,
                "bytes_before": 0,
                "bytes_after": 0,
                "cap_bytes": cap,
                "evicted_memories": 0,
                "evicted_intelligence": 0,
                "evicted_total": 0,
                "error": str(e),
            }

    # =============================================================================
    # CONSCIOUSNESS EVOLUTION & GAMEPLAY INTEGRATION
    # =============================================================================

    async def update_consciousness_and_relationship(
        self, player_id: str, db: AsyncSession
    ) -> Dict[str, Any]:
        """
        THE canonical consciousness + relationship promotion path
        (sw2102-docs/FEATURES/gameplay/aria-companion.md:118-128, :139-144 --
        canon explicitly names this method as the promotion path). Call once
        per "significant interaction" to keep the consciousness system
        actively evolving alongside gameplay (dialogue, trade, combat,
        movement, ...).

        WO-ARIA-PROGRESSION consolidation: this is now the SINGLE source of
        truth, replacing the four duplicated inline threshold blocks
        (movement_service.py, combat_service.py, trading.py buy + sell) AND
        the two now-removed redundant siblings (update_consciousness_level /
        update_relationship_score, both zero-caller dead code before this
        WO). See update_consciousness_and_relationship_sync for the
        sync-Session twin the three sync call sites use.

        Per call: +1 aria_total_interactions, +1 aria_relationship_score
        (capped 100) -- aria-companion.md:139 "Rises +1 per significant
        interaction (capped at 100)". NO-CANON (flagged for DECISIONS):
        "significant interaction" = exactly the event set that already
        bumps aria_total_interactions today (movement, combat victory, buy,
        sell) -- not silently redefined by this WO.

        Then checks BOTH promotion thresholds -- aria-companion.md:128
        "Both thresholds (interactions and unique-type memory diversity)
        must be met to advance" -- and promotes + sets the new tier's bonus
        multiplier if newly qualified.

        FLAGGED, NOT SILENTLY RESOLVED (dispatch's explicit ask): canon's
        memory-side gate is worded "unique-type memory diversity". A LITERAL
        distinct ARIAPersonalMemory.memory_type COUNT is mathematically
        incapable of ever promoting past level 1 (Dormant) -- only THREE
        memory_type values are ever actually written anywhere in this
        codebase (combat / market / exploration; "social" is named in a
        docstring but never instantiated by any caller), and even the
        LOWEST threshold (level 2) requires 10. This method therefore uses
        the RAW TOTAL memory count (matching this method's own pre-existing
        arithmetic before this WO), not distinct-type cardinality -- see the
        dispatch report for the full proof and a dedicated falsifying test.
        The threshold NUMBERS themselves (10/30/75/150) are unchanged.
        """
        from src.models.player import Player

        stmt = select(Player).where(Player.id == player_id)
        result = await db.execute(stmt)
        player = result.scalar_one_or_none()
        if not player:
            return {"success": False, "message": "Player not found"}

        memory_stmt = select(func.count(ARIAPersonalMemory.id)).where(
            ARIAPersonalMemory.player_id == player_id
        )
        memory_result = await db.execute(memory_stmt)
        total_memories = memory_result.scalar() or 0

        return self._apply_consciousness_and_relationship(player, total_memories)

    def update_consciousness_and_relationship_sync(
        self, player_id: str, db: Session,
    ) -> Dict[str, Any]:
        """
        Synchronous twin of ``update_consciousness_and_relationship`` for
        the three sync-Session call sites (WO-ARIA-PROGRESSION --
        movement_service.py, combat_service.py, trading.py buy + sell all
        run on a sync Session, exactly the record_trade_memory_sync
        precedent). Same canonical core (``_apply_consciousness_and_
        relationship``), same canon citations -- see the async method's
        docstring. Never raises -- an ARIA progression hiccup must never
        break movement, combat, or a trade.
        """
        try:
            from src.models.player import Player

            player = db.query(Player).filter(Player.id == player_id).first()
            if not player:
                return {"success": False, "message": "Player not found"}

            total_memories = (
                db.query(func.count(ARIAPersonalMemory.id))
                .filter(ARIAPersonalMemory.player_id == player_id)
                .scalar()
            ) or 0

            return self._apply_consciousness_and_relationship(player, total_memories)
        except Exception as e:
            logger.warning(
                "update_consciousness_and_relationship_sync failed for player %s: %s",
                player_id, e,
            )
            return {"success": False, "message": str(e)}

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

    # =============================================================================
    # OBSERVATION LOG + RECOMMENDATION ENGINE (WO-ARIA-OBS-LOG, ADR-0038)
    # =============================================================================
    #
    # Append-only per-trade observation log mined by SQL aggregates -- the
    # genetic-algorithm framing (evolve_trading_pattern / get_evolved_
    # patterns / ARIATradingPattern) was REMOVED (WO-ARIA-GA-CLEANUP,
    # ADR-0038, zero live callers) rather than merely retired; this section
    # is its replacement recommendation engine.
    #
    # DELIBERATELY SYNC (Session, not AsyncSession): the intended write-path
    # caller is trading.py's buy/sell routes (lane C of this WO, deferred --
    # this table has zero writers until that follow-up lands), which run
    # entirely on a synchronous Session, exactly like this class's existing
    # record_combat_memory_sync twin. Rather than splitting one substrate
    # across two session types, both the write and the read/aggregate side
    # stay sync; a future async caller (e.g. an ARIA-recommendations route)
    # bridges in via AsyncSession.run_sync(...) -- the same established
    # pattern this codebase already uses for sync compute under async
    # callers (see turn_service's regen bridge) -- rather than this one
    # table inventing a second, mixed-session surface.

    # aria.md:220 / ADR-0038 Anti-gaming -- the wash-trade floor. A trade is
    # only counted as a "success" toward any aggregate below if its profit
    # clears this bar; near-zero-margin wash trades and small real losses
    # both land on the "not successful" side of every ratio computed here.
    MIN_SIGNIFICANT_PROFIT_CR = 100

    MIN_ROUTE_SAMPLES = 3           # aria.md:210 "count >= 3"
    MIN_RELIABLE_SAMPLES = 5        # aria.md:211 "count >= 5"
    MIN_WATCHOUT_SAMPLES = 5        # aria.md:213 "count >= 5"
    RELIABLE_SUCCESS_RATE = 0.7     # aria.md:211
    WATCHOUT_SUCCESS_RATE = 0.3     # aria.md:213
    AGGREGATE_CACHE_TTL = timedelta(hours=4)  # aria.md:218

    _RECOMMENDATION_CACHE_KEY = "recommendation_aggregates"
    _AGGREGATE_CACHE_SCOPE_COMMODITY = "__ALL__"

    def record_trade_observation(
        self, player_id: str, trade_result: Dict[str, Any], db: Session,
    ) -> Optional[ARIATradingObservation]:
        """
        Insert one ARIATradingObservation row for a completed trade leg.

        Canonical entry-point name/signature per OPERATIONS/aria.md:222
        ("aria_personal_intelligence_service.record_trade_observation
        (player_id, trade_result)"). SYNC on purpose -- see section
        docstring above.

        FLUSH-FREE like record_combat_memory_sync: only db.add()s; the
        CALLER owns the commit (folds into the trade's single commit, same
        convention as trading.py's existing pending_aria_memories append).
        Never raises -- an ARIA logging hiccup must never break a real
        trade (single insert, non-blocking).

        trade_result keys: commodity, action ("buy"|"sell"),
        source_station_id, dest_station_id (optional, buy-only omits it),
        source_sector_id / dest_sector_id (optional), quantity, unit_price,
        total_credits, profit (optional, sell-leg only), hours_held
        (optional, sell-leg only), trade_id / matched_market_intel_id /
        recommendation_id (all optional FKs).

        Returns the inserted (unflushed) row, or None on any invalid input
        or failure -- callers should treat this as best-effort telemetry,
        never a return value that gates trade logic.
        """
        try:
            try:
                action = ObservationAction(trade_result.get("action"))
            except ValueError:
                logger.warning(
                    "record_trade_observation: unrecognised action %r for player %s",
                    trade_result.get("action"), player_id,
                )
                return None

            profit = trade_result.get("profit")
            outcome = None
            if action is ObservationAction.sell and profit is not None:
                if profit > 0:
                    outcome = ObservationOutcome.profit
                elif profit == 0:
                    outcome = ObservationOutcome.break_even
                else:
                    outcome = ObservationOutcome.loss

            observation = ARIATradingObservation(
                player_id=player_id,
                trade_id=trade_result.get("trade_id"),
                commodity=trade_result["commodity"],
                action=action,
                source_station_id=trade_result["source_station_id"],
                dest_station_id=trade_result.get("dest_station_id"),
                source_sector_id=trade_result.get("source_sector_id"),
                dest_sector_id=trade_result.get("dest_sector_id"),
                quantity=trade_result["quantity"],
                unit_price=trade_result["unit_price"],
                total_credits=trade_result["total_credits"],
                profit=profit,
                hours_held=trade_result.get("hours_held"),
                outcome_classification=outcome,
                observed_at=datetime.now(UTC),
                matched_market_intel_id=trade_result.get("matched_market_intel_id"),
                recommendation_id=trade_result.get("recommendation_id"),
            )
            db.add(observation)

            # aria.md:222 -- new observations invalidate cached aggregates.
            self._invalidate_aggregate_cache_sync(player_id, db)

            return observation
        except Exception as e:
            logger.warning(
                "record_trade_observation failed for player %s: %s", player_id, e,
            )
            return None

    def get_top_routes(
        self, player_id: str, db: Session, limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Top profitable repeated routes. OPERATIONS/aria.md:210:
        ``GROUP BY (commodity, source_station, dest_station) HAVING count
        >= 3 ORDER BY avg_profit DESC LIMIT 5``. Surfaced on the "Suggested
        trades" panel.

        SQL does the GROUP BY / HAVING(count) / SUM(profit) reduction;
        avg-profit ranking + LIMIT happen in Python over the (small,
        already-reduced) per-player group set -- see section docstring for
        why this avoids ORDER BY on a derived expression.
        """
        rows = (
            db.query(
                ARIATradingObservation.commodity,
                ARIATradingObservation.source_station_id,
                ARIATradingObservation.dest_station_id,
                func.sum(ARIATradingObservation.profit),
                func.count(ARIATradingObservation.id),
            )
            .filter(
                ARIATradingObservation.player_id == player_id,
                ARIATradingObservation.action == ObservationAction.sell,
                ARIATradingObservation.profit >= self.MIN_SIGNIFICANT_PROFIT_CR,
                ARIATradingObservation.dest_station_id.isnot(None),
            )
            .group_by(
                ARIATradingObservation.commodity,
                ARIATradingObservation.source_station_id,
                ARIATradingObservation.dest_station_id,
            )
            .having(func.count(ARIATradingObservation.id) >= self.MIN_ROUTE_SAMPLES)
            .all()
        )

        routes = []
        for commodity, source_station_id, dest_station_id, total_profit, sample_count in rows:
            avg_profit = total_profit / sample_count
            routes.append({
                "commodity": commodity,
                "source_station_id": str(source_station_id),
                "dest_station_id": str(dest_station_id),
                "avg_profit": avg_profit,
                "sample_count": sample_count,
                "explanation": (
                    f"You've made an average of {avg_profit:,.0f} cr trading "
                    f"{commodity} on this route over your last {sample_count} trades."
                ),
            })
        routes.sort(key=lambda r: r["avg_profit"], reverse=True)
        return routes[:limit]

    def _commodity_success_rate_aggregate(
        self, player_id: str, db: Session, group_cols: list,
        min_samples: int, rate_ok,
    ) -> List[Dict[str, Any]]:
        """
        Shared SQL-aggregate core for get_reliable_commodities /
        get_watch_out_commodities -- both are "group by N columns, HAVING
        count >= min_samples, then keep groups whose success_rate clears
        rate_ok" over sell-leg observations. Two grouped COUNT queries
        (total samples, then samples clearing MIN_SIGNIFICANT_PROFIT_CR)
        rather than one query with a CASE-WHEN, matching this codebase's
        established sum/count-only aggregate-query convention.

        Returns a list of {"group_key": <tuple>, "sample_count": int,
        "success_rate": float} dicts for groups where rate_ok(rate) is True.
        """
        base_filter = (
            ARIATradingObservation.player_id == player_id,
            ARIATradingObservation.action == ObservationAction.sell,
            ARIATradingObservation.profit.isnot(None),
        )

        total_rows = (
            db.query(*group_cols, func.count(ARIATradingObservation.id))
            .filter(*base_filter)
            .group_by(*group_cols)
            .having(func.count(ARIATradingObservation.id) >= min_samples)
            .all()
        )
        if not total_rows:
            return []

        totals = {tuple(row[:-1]): row[-1] for row in total_rows}

        success_rows = (
            db.query(*group_cols, func.count(ARIATradingObservation.id))
            .filter(*base_filter, ARIATradingObservation.profit >= self.MIN_SIGNIFICANT_PROFIT_CR)
            .group_by(*group_cols)
            .all()
        )
        successes = {tuple(row[:-1]): row[-1] for row in success_rows}

        results = []
        for key, total in totals.items():
            success = successes.get(key, 0)
            rate = success / total if total else 0.0
            if rate_ok(rate):
                results.append({"group_key": key, "sample_count": total, "success_rate": rate})
        return results

    def get_reliable_commodities(self, player_id: str, db: Session) -> List[Dict[str, Any]]:
        """
        Reliable commodities by station. OPERATIONS/aria.md:211:
        ``GROUP BY (commodity, source_station) HAVING count >= 5 AND
        success_rate >= 0.7``. Surfaced on the "Stations to revisit" panel.
        """
        raw = self._commodity_success_rate_aggregate(
            player_id, db,
            group_cols=[ARIATradingObservation.commodity, ARIATradingObservation.source_station_id],
            min_samples=self.MIN_RELIABLE_SAMPLES,
            rate_ok=lambda rate: rate >= self.RELIABLE_SUCCESS_RATE,
        )
        out = []
        for r in raw:
            commodity, station_id = r["group_key"]
            out.append({
                "commodity": commodity,
                "source_station_id": str(station_id),
                "sample_count": r["sample_count"],
                "success_rate": r["success_rate"],
                "explanation": (
                    f"{r['success_rate'] * 100:.0f}% of your {commodity} sales from "
                    f"this station over {r['sample_count']} trades cleared a profit."
                ),
            })
        return out

    def get_watch_out_commodities(self, player_id: str, db: Session) -> List[Dict[str, Any]]:
        """
        Watch-out commodities. OPERATIONS/aria.md:213: ``GROUP BY
        (commodity) HAVING count >= 5 AND success_rate <= 0.3``. Surfaced
        as a warning on the "Caution" panel -- never a positive
        recommendation.
        """
        raw = self._commodity_success_rate_aggregate(
            player_id, db,
            group_cols=[ARIATradingObservation.commodity],
            min_samples=self.MIN_WATCHOUT_SAMPLES,
            rate_ok=lambda rate: rate <= self.WATCHOUT_SUCCESS_RATE,
        )
        out = []
        for r in raw:
            (commodity,) = r["group_key"]
            out.append({
                "commodity": commodity,
                "sample_count": r["sample_count"],
                "success_rate": r["success_rate"],
                "explanation": (
                    f"Only {r['success_rate'] * 100:.0f}% of your {commodity} trades "
                    f"over {r['sample_count']} attempts cleared a profit -- consider "
                    f"avoiding it."
                ),
            })
        return out

    def compute_recommendation_aggregates(
        self, player_id: str, db: Session, force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Umbrella entry point: returns {top_routes, reliable_commodities,
        watch_out_commodities, computed_at}, backed by a 4-hour
        stale-while-revalidate cache in ARIAQuantumCache (repurposed per
        ADR-0038's Caching section -- aria.md:218; the whole bundle is
        cached as ONE row per player rather than partitioned per
        (commodity, station_id) -- see _invalidate_aggregate_cache_sync for
        why).

        NOTE (flagged, not guessed): aria.md's recommendation-engine table
        (:204-222) has two further rows -- "routes within explored space"
        (filtering top routes by ARIAExplorationMap + a distance <=
        max_jumps graph traversal) and "off-peak buy windows" (hour-of-day
        grouping). Neither is implemented in this WO: this WO's explicit
        method list and D-tests' acceptance criteria named only top routes/
        reliable/watch-out, and "routes within explored space" needs a
        warp-graph distance utility this WO doesn't define or own. Left for
        a follow-up WO once that utility question is resolved.
        """
        if not force_refresh:
            cached = self._get_cached_aggregates_sync(player_id, db)
            if cached is not None:
                return cached

        bundle = {
            "top_routes": self.get_top_routes(player_id, db),
            "reliable_commodities": self.get_reliable_commodities(player_id, db),
            "watch_out_commodities": self.get_watch_out_commodities(player_id, db),
            "computed_at": datetime.now(UTC).isoformat(),
        }
        self._cache_aggregates_sync(player_id, bundle, db)
        return bundle

    def _get_cached_aggregates_sync(self, player_id: str, db: Session) -> Optional[Dict[str, Any]]:
        entry = (
            db.query(ARIAQuantumCache)
            .filter(
                ARIAQuantumCache.player_id == player_id,
                ARIAQuantumCache.cache_key == self._RECOMMENDATION_CACHE_KEY,
                ARIAQuantumCache.expires_at > datetime.now(UTC),
            )
            .first()
        )
        if entry is None:
            return None
        entry.hit_count = (entry.hit_count or 0) + 1
        return entry.ghost_results

    def _cache_aggregates_sync(self, player_id: str, bundle: Dict[str, Any], db: Session) -> None:
        existing = (
            db.query(ARIAQuantumCache)
            .filter(
                ARIAQuantumCache.player_id == player_id,
                ARIAQuantumCache.cache_key == self._RECOMMENDATION_CACHE_KEY,
            )
            .first()
        )
        expires_at = datetime.now(UTC) + self.AGGREGATE_CACHE_TTL
        if existing is not None:
            existing.ghost_results = bundle
            existing.expires_at = expires_at
        else:
            db.add(ARIAQuantumCache(
                player_id=player_id,
                cache_key=self._RECOMMENDATION_CACHE_KEY,
                commodity=self._AGGREGATE_CACHE_SCOPE_COMMODITY,
                station_id=None,
                sector_id=None,
                quantum_states=[],  # unused for this repurposed cache use
                ghost_results=bundle,
                expected_value=0.0,  # unused for this repurposed cache use
                confidence_interval=[0, 0],  # unused for this repurposed cache use
                expires_at=expires_at,
            ))

    def _invalidate_aggregate_cache_sync(self, player_id: str, db: Session) -> None:
        """
        New-observation cache invalidation (aria.md:218's "new observations
        invalidate cache entries that touch the same (commodity,
        station_id) tuple" rule). This WO caches the full recommendation
        bundle as ONE row per player rather than partitioning per
        (commodity, station_id) pair -- so any new observation invalidates
        the whole bundle. This is the conservative superset of aria.md's
        per-tuple rule (correct, slightly less cache-efficient); flagged in
        the dispatch report as a deliberate simplification given the
        observation-log's per-player scale doesn't warrant a finer
        partition yet.

        WO-SWEEP-QUANTUM-CACHE-COLUMN: the DELETE itself was ALREADY safe
        from the aria_quantum_cache.port_id/station_id defect (a `.filter(
        ...).delete()` bulk statement only references its WHERE-clause
        columns -- player_id/cache_key here -- never the full column list a
        plain SELECT would), but this is the ONE live, production-reachable
        write against this table (record_trade_observation ->
        trading.py:414, folded into the SAME trade commit per that
        method's own "CALLER owns the commit" contract) with NO savepoint
        protection -- ANY DB-level failure here (this defect or a future
        one) would poison the session and take the real trade's own commit
        down with it. Mirrors record_market_observation_sync's per-write
        savepoint discipline (WO-SWEEP-ARIA-MI-COLUMN) — the caller's own
        broad try/except catches the Python-level exception either way,
        but only a SAVEPOINT actually protects the transaction itself.
        """
        with db.begin_nested():
            db.query(ARIAQuantumCache).filter(
                ARIAQuantumCache.player_id == player_id,
                ARIAQuantumCache.cache_key == self._RECOMMENDATION_CACHE_KEY,
            ).delete(synchronize_session=False)


# Singleton instance
_aria_intelligence_service = None


def get_aria_intelligence_service() -> ARIAPersonalIntelligenceService:
    """Get or create ARIA intelligence service instance"""
    global _aria_intelligence_service
    if _aria_intelligence_service is None:
        _aria_intelligence_service = ARIAPersonalIntelligenceService()
    return _aria_intelligence_service
