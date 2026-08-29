"""
Player Activity Tracking Service (Redis-backed, with a durable Postgres
session/activity mirror — WO-BUILD-RETENTION-SIGNALS-WRITEBACK).

Tracks player session activity, key events, and analytics summaries using
Redis with TTL-based expiration (fast reads for online-presence / current
session UI). At the two existing session boundaries -- login and logout --
this also durably mirrors into ``PlayerSession`` / ``PlayerActivity``
(Postgres), the tables ``RetentionService`` reads for the
``declining_session_length`` / ``early_logout_streak`` at-risk signals and
the tables the WO-G18 governance sweep reads for ``Region.active_players_30d``.
Both tables already existed (created for this purpose) but had zero writers
until this writeback -- see ``retention_service.py``'s "SIGNAL DATA-SOURCE
STATUS" note.

The mirror writes at LOGIN and LOGOUT (session boundaries) and, as of
WO-BUILD-RETENTION-SIGNALS-TRADE-SQL-INSERT, on ``trade_buy`` / ``trade_sell``
when the caller passes a sync ``Session`` — so ``RetentionService``'s
``economic_loss_streak`` SELECT is no longer structurally empty. A synchronous
``Session`` (not ``AsyncSession``) is accepted because production callers
(``auth.py``, ``trading.py``) already hold sync ``Session`` from ``get_db``.
Writebacks are best-effort/non-fatal (savepoint on the trade path so a failed
activity insert cannot poison the trade transaction).
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.models.player import Player
from src.services.redis_service import RedisService, get_redis_service

logger = logging.getLogger(__name__)


# Key prefixes for Redis
_SESSION_KEY = "activity:session:{player_id}"          # current session data
_EVENTS_KEY = "activity:events:{player_id}"            # recent event list
_SUMMARY_KEY = "activity:summary:{player_id}"          # rolling summary
_DAILY_KEY = "activity:daily:{player_id}:{date}"       # daily aggregates
_GLOBAL_ONLINE_KEY = "activity:online_players"         # set of currently online player ids

# TTLs in seconds
_SESSION_TTL = 60 * 60 * 24       # 24 hours
_EVENTS_TTL = 60 * 60 * 24 * 7   # 7 days
_SUMMARY_TTL = 60 * 60 * 24 * 30 # 30 days
_DAILY_TTL = 60 * 60 * 24 * 14   # 14 days


class ActivityEventType:
    """Constants for trackable event types."""
    LOGIN = "login"
    LOGOUT = "logout"
    TRADE_BUY = "trade_buy"
    TRADE_SELL = "trade_sell"
    COMBAT_ATTACK = "combat_attack"
    COMBAT_DEFEND = "combat_defend"
    SECTOR_MOVE = "sector_move"
    DOCK = "dock"
    UNDOCK = "undock"
    PLANET_LAND = "planet_land"
    WARP = "warp"


class PlayerActivityService:
    """
    Service for tracking player activity without new DB tables.

    All data is stored in Redis with automatic TTL expiration.
    Provides methods to:
    - Track login / logout and compute session duration
    - Record gameplay events (trades, combat, movement)
    - Produce per-player and global activity summaries
    """

    def __init__(self, redis: Optional[RedisService] = None):
        self._redis = redis

    async def _get_redis(self) -> RedisService:
        """Lazy-load the Redis service if not injected."""
        if self._redis is None:
            self._redis = await get_redis_service()
        return self._redis

    # ------------------------------------------------------------------
    # Session tracking
    # ------------------------------------------------------------------

    async def track_login(
        self,
        player_id: str,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """
        Record a player login event and start a session.

        Optionally updates Player.last_game_login and (if ``db`` is provided)
        durably opens a ``PlayerSession`` row + a "login" ``PlayerActivity``
        row, mirroring the Redis session so the retention sweep's
        session-length signals and the WO-G18 region-activity recompute have
        real data. The new ``PlayerSession.id`` is stashed on the Redis
        session dict (``db_session_id``) so ``track_logout`` can complete it.
        """
        redis = await self._get_redis()
        now = datetime.utcnow()
        session_key = _SESSION_KEY.format(player_id=player_id)

        session_data = {
            "player_id": player_id,
            "login_at": now.isoformat(),
            "last_activity_at": now.isoformat(),
            "actions_count": 0,
            "trades_count": 0,
            "trade_volume": 0,
            "combat_events": 0,
            "sectors_visited": [],
        }

        # Optionally update DB + open the durable session/activity mirror.
        # Done BEFORE the Redis cache_set below so db_session_id (if created)
        # is included in the persisted session dict.
        if db:
            try:
                from src.models.player_analytics import PlayerActivity, PlayerSession

                player = db.query(Player).filter(Player.id == player_id).first()
                if player:
                    player.last_game_login = now

                db_session = PlayerSession(player_id=player_id, start_time=now)
                db.add(db_session)
                db.flush()  # populate db_session.id (client-side default)
                session_data["db_session_id"] = str(db_session.id)

                db.add(PlayerActivity(
                    player_id=player_id,
                    session_id=db_session.id,
                    activity_type=ActivityEventType.LOGIN,
                    sector_id=player.current_sector_id if player else None,
                    timestamp=now,
                ))
                db.commit()
            except Exception as e:
                logger.warning(f"Could not persist login session for {player_id}: {e}")
                try:
                    db.rollback()
                except Exception:
                    pass

        await redis.cache_set(session_key, session_data, ttl=_SESSION_TTL)
        await self._record_event(player_id, ActivityEventType.LOGIN)

        # Mark player as online
        if redis.redis_pool:
            await redis.redis_pool.sadd(_GLOBAL_ONLINE_KEY, player_id)

        logger.info(f"Player {player_id} logged in")
        return session_data

    async def track_logout(
        self,
        player_id: str,
        db: Optional[Session] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Record a player logout event and finalise the session summary.

        If ``db`` is provided and the Redis session carries a
        ``db_session_id`` (set by ``track_login``), also durably completes
        the matching ``PlayerSession`` row (end_time/duration_minutes/
        actions/sectors) and writes a "logout" ``PlayerActivity`` row --
        closing the retention-sweep session-length signals and feeding
        WO-G18's ``Region.active_players_30d`` recompute.

        Returns the completed session summary including duration.
        """
        redis = await self._get_redis()
        now = datetime.utcnow()
        session_key = _SESSION_KEY.format(player_id=player_id)

        # Retrieve current session
        session_data = await redis.cache_get(session_key)
        if not session_data:
            logger.debug(f"No active session found for player {player_id}")
            session_data = {"login_at": now.isoformat(), "actions_count": 0}

        # Compute session duration
        login_at = datetime.fromisoformat(session_data.get("login_at", now.isoformat()))
        duration_seconds = (now - login_at).total_seconds()
        session_data["logout_at"] = now.isoformat()
        session_data["duration_seconds"] = duration_seconds

        # Complete the durable session/activity mirror opened at login.
        if db:
            db_session_id = session_data.get("db_session_id")
            if db_session_id:
                try:
                    from src.models.player_analytics import PlayerActivity, PlayerSession

                    db_session = (
                        db.query(PlayerSession)
                        .filter(PlayerSession.id == db_session_id)
                        .first()
                    )
                    if db_session is not None:
                        db_session.end_time = now
                        db_session.duration_minutes = int(duration_seconds // 60)
                        db_session.actions_performed = session_data.get("actions_count", 0)
                        db_session.sectors_visited = session_data.get("sectors_visited", [])

                    player = db.query(Player).filter(Player.id == player_id).first()
                    db.add(PlayerActivity(
                        player_id=player_id,
                        session_id=db_session_id,
                        activity_type=ActivityEventType.LOGOUT,
                        sector_id=player.current_sector_id if player else None,
                        timestamp=now,
                    ))
                    db.commit()
                except Exception as e:
                    logger.warning(f"Could not persist logout session for {player_id}: {e}")
                    try:
                        db.rollback()
                    except Exception:
                        pass
            else:
                logger.debug(
                    f"No db_session_id on Redis session for {player_id}; "
                    "skipping durable session completion"
                )

        # Persist session to rolling summary
        await self._update_summary(player_id, session_data)
        await self._update_daily(player_id, session_data)
        await self._record_event(player_id, ActivityEventType.LOGOUT, {
            "duration_seconds": duration_seconds,
        })

        # Remove from online set & delete session key
        if redis.redis_pool:
            await redis.redis_pool.srem(_GLOBAL_ONLINE_KEY, player_id)
        await redis.cache_delete(session_key)

        logger.info(
            f"Player {player_id} logged out after {duration_seconds:.0f}s, "
            f"{session_data.get('actions_count', 0)} actions"
        )
        return session_data

    # ------------------------------------------------------------------
    # Event tracking
    # ------------------------------------------------------------------

    async def track_activity(
        self,
        player_id: str,
        event_type: str,
        details: Optional[Dict[str, Any]] = None,
        db: Optional[Session] = None,
    ) -> None:
        """
        Record a gameplay event and update the running session counters.

        Parameters
        ----------
        player_id : str
            Player UUID.
        event_type : str
            One of ActivityEventType constants.
        details : dict, optional
            Additional context (e.g. commodity, quantity, sector_id).
        db : Session, optional
            Sync SQLAlchemy session. When provided for trade or gameplay
            event types (combat/move/dock/land/warp), also inserts a durable
            ``PlayerActivity`` row (LEG-338) so retention/region activity is
            not Redis-only. Caller owns commit; insert uses a savepoint so
            failure cannot poison the caller's transaction.
        """
        redis = await self._get_redis()

        # Update session counters
        session_key = _SESSION_KEY.format(player_id=player_id)
        session = await redis.cache_get(session_key)
        if session:
            session["actions_count"] = session.get("actions_count", 0) + 1
            session["last_activity_at"] = datetime.utcnow().isoformat()

            if event_type in (ActivityEventType.TRADE_BUY, ActivityEventType.TRADE_SELL):
                session["trades_count"] = session.get("trades_count", 0) + 1
                volume = (details or {}).get("total_value", 0)
                session["trade_volume"] = session.get("trade_volume", 0) + volume

            if event_type in (ActivityEventType.COMBAT_ATTACK, ActivityEventType.COMBAT_DEFEND):
                session["combat_events"] = session.get("combat_events", 0) + 1

            if event_type in (
                ActivityEventType.SECTOR_MOVE,
                ActivityEventType.WARP,
            ):
                sector = (details or {}).get("sector_id")
                visited = session.get("sectors_visited", [])
                if sector and str(sector) not in visited:
                    visited.append(str(sector))
                    # Keep list bounded
                    session["sectors_visited"] = visited[-100:]

            await redis.cache_set(session_key, session, ttl=_SESSION_TTL)

        # Record individual event (Redis)
        await self._record_event(player_id, event_type, details)

        # Durable mirror for trade + gameplay events (Postgres)
        _DURABLE_EVENT_TYPES = (
            ActivityEventType.TRADE_BUY,
            ActivityEventType.TRADE_SELL,
            ActivityEventType.COMBAT_ATTACK,
            ActivityEventType.COMBAT_DEFEND,
            ActivityEventType.SECTOR_MOVE,
            ActivityEventType.DOCK,
            ActivityEventType.UNDOCK,
            ActivityEventType.PLANET_LAND,
            ActivityEventType.WARP,
        )
        if db is not None and event_type in _DURABLE_EVENT_TYPES:
            try:
                from src.models.player_analytics import PlayerActivity

                credits = int((details or {}).get("total_value", 0) or 0)
                session_uuid = None
                if session and session.get("db_session_id"):
                    try:
                        import uuid as _uuid
                        session_uuid = _uuid.UUID(str(session["db_session_id"]))
                    except (ValueError, TypeError):
                        session_uuid = None
                sector_id = (details or {}).get("sector_id")
                if sector_id is not None:
                    try:
                        sector_id = int(sector_id)
                    except (TypeError, ValueError):
                        sector_id = None
                trade_item_keys = ("commodity", "quantity", "station_id")
                gameplay_item_keys = (
                    "target_type",
                    "planet_id",
                    "station_id",
                    "travel_mode",
                )
                item_keys = (
                    trade_item_keys
                    if event_type
                    in (ActivityEventType.TRADE_BUY, ActivityEventType.TRADE_SELL)
                    else gameplay_item_keys
                )
                with db.begin_nested():
                    db.add(
                        PlayerActivity(
                            player_id=player_id,
                            session_id=session_uuid,
                            activity_type=event_type,
                            sector_id=sector_id,
                            credits_involved=credits,
                            items_involved={
                                k: (details or {}).get(k)
                                for k in item_keys
                                if (details or {}).get(k) is not None
                            }
                            or None,
                            timestamp=datetime.utcnow(),
                        )
                    )
            except Exception as e:
                logger.warning(
                    "Could not persist PlayerActivity (%s) for %s: %s",
                    event_type,
                    player_id,
                    e,
                )

    # ------------------------------------------------------------------
    # Analytics queries
    # ------------------------------------------------------------------

    async def get_player_session(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Return current session data for a player (or None if offline)."""
        redis = await self._get_redis()
        session_key = _SESSION_KEY.format(player_id=player_id)
        return await redis.cache_get(session_key)

    async def get_player_summary(self, player_id: str) -> Dict[str, Any]:
        """
        Return the rolling activity summary for a player.

        Includes total sessions, playtime, trade volume, combat events, etc.
        """
        redis = await self._get_redis()
        summary_key = _SUMMARY_KEY.format(player_id=player_id)
        summary = await redis.cache_get(summary_key)
        if not summary:
            return {
                "player_id": player_id,
                "total_sessions": 0,
                "total_playtime_seconds": 0,
                "total_actions": 0,
                "total_trades": 0,
                "total_trade_volume": 0,
                "total_combat_events": 0,
                "unique_sectors_visited": 0,
            }
        return summary

    async def get_recent_events(
        self, player_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Return the most recent events for a player.
        """
        redis = await self._get_redis()
        events_key = _EVENTS_KEY.format(player_id=player_id)

        if redis.redis_pool:
            raw = await redis.redis_pool.lrange(events_key, 0, limit - 1)
            return [json.loads(r) for r in raw] if raw else []
        return []

    async def get_daily_stats(
        self, player_id: str, date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Return activity stats for a specific day (defaults to today).
        """
        redis = await self._get_redis()
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        daily_key = _DAILY_KEY.format(player_id=player_id, date=date)
        stats = await redis.cache_get(daily_key)
        if not stats:
            return {
                "player_id": player_id,
                "date": date,
                "sessions": 0,
                "playtime_seconds": 0,
                "actions": 0,
                "trades": 0,
                "trade_volume": 0,
                "combat_events": 0,
            }
        return stats

    async def get_online_player_count(self) -> int:
        """Return count of currently online players."""
        redis = await self._get_redis()
        if redis.redis_pool:
            return await redis.redis_pool.scard(_GLOBAL_ONLINE_KEY)
        return 0

    async def get_online_player_ids(self) -> List[str]:
        """Return list of currently online player IDs."""
        redis = await self._get_redis()
        if redis.redis_pool:
            members = await redis.redis_pool.smembers(_GLOBAL_ONLINE_KEY)
            return list(members) if members else []
        return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _record_event(
        self,
        player_id: str,
        event_type: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Push an event onto the player's event list in Redis."""
        redis = await self._get_redis()
        events_key = _EVENTS_KEY.format(player_id=player_id)

        event = {
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "details": details or {},
        }

        if redis.redis_pool:
            await redis.redis_pool.lpush(events_key, json.dumps(event))
            await redis.redis_pool.ltrim(events_key, 0, 499)  # keep last 500 events
            await redis.redis_pool.expire(events_key, _EVENTS_TTL)

    async def _update_summary(
        self,
        player_id: str,
        session_data: Dict[str, Any],
    ) -> None:
        """Merge completed session data into the rolling summary."""
        redis = await self._get_redis()
        summary_key = _SUMMARY_KEY.format(player_id=player_id)

        summary = await redis.cache_get(summary_key)
        if not summary:
            summary = {
                "player_id": player_id,
                "total_sessions": 0,
                "total_playtime_seconds": 0,
                "total_actions": 0,
                "total_trades": 0,
                "total_trade_volume": 0,
                "total_combat_events": 0,
                "unique_sectors_visited": 0,
                "all_sectors": [],
            }

        summary["total_sessions"] = summary.get("total_sessions", 0) + 1
        summary["total_playtime_seconds"] = (
            summary.get("total_playtime_seconds", 0)
            + session_data.get("duration_seconds", 0)
        )
        summary["total_actions"] = (
            summary.get("total_actions", 0) + session_data.get("actions_count", 0)
        )
        summary["total_trades"] = (
            summary.get("total_trades", 0) + session_data.get("trades_count", 0)
        )
        summary["total_trade_volume"] = (
            summary.get("total_trade_volume", 0) + session_data.get("trade_volume", 0)
        )
        summary["total_combat_events"] = (
            summary.get("total_combat_events", 0) + session_data.get("combat_events", 0)
        )

        # Merge visited sectors
        existing = set(summary.get("all_sectors", []))
        new_sectors = session_data.get("sectors_visited", [])
        existing.update(new_sectors)
        summary["all_sectors"] = list(existing)[-200:]  # cap at 200
        summary["unique_sectors_visited"] = len(summary["all_sectors"])
        summary["last_updated"] = datetime.utcnow().isoformat()

        await redis.cache_set(summary_key, summary, ttl=_SUMMARY_TTL)

    async def _update_daily(
        self,
        player_id: str,
        session_data: Dict[str, Any],
    ) -> None:
        """Update the daily aggregate stats."""
        redis = await self._get_redis()
        date = datetime.utcnow().strftime("%Y-%m-%d")
        daily_key = _DAILY_KEY.format(player_id=player_id, date=date)

        stats = await redis.cache_get(daily_key)
        if not stats:
            stats = {
                "player_id": player_id,
                "date": date,
                "sessions": 0,
                "playtime_seconds": 0,
                "actions": 0,
                "trades": 0,
                "trade_volume": 0,
                "combat_events": 0,
            }

        stats["sessions"] = stats.get("sessions", 0) + 1
        stats["playtime_seconds"] = (
            stats.get("playtime_seconds", 0)
            + session_data.get("duration_seconds", 0)
        )
        stats["actions"] = (
            stats.get("actions", 0) + session_data.get("actions_count", 0)
        )
        stats["trades"] = (
            stats.get("trades", 0) + session_data.get("trades_count", 0)
        )
        stats["trade_volume"] = (
            stats.get("trade_volume", 0) + session_data.get("trade_volume", 0)
        )
        stats["combat_events"] = (
            stats.get("combat_events", 0) + session_data.get("combat_events", 0)
        )

        await redis.cache_set(daily_key, stats, ttl=_DAILY_TTL)


# ------------------------------------------------------------------
# Module-level convenience accessor
# ------------------------------------------------------------------

_service_instance: Optional[PlayerActivityService] = None


async def get_player_activity_service() -> PlayerActivityService:
    """Get or create the singleton PlayerActivityService."""
    global _service_instance
    if _service_instance is None:
        _service_instance = PlayerActivityService()
    return _service_instance
