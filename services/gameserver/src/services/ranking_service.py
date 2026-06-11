"""
Military Ranking Service

Manages the military ranking system for players, including rank definitions,
point awards, promotions, and rank-based bonuses.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import desc

from src.models.player import Player

logger = logging.getLogger(__name__)


# 18-rank spec-compliant definitions ordered by point threshold (ascending)
# Tiers: Enlisted (3), NCO (3), Warrant (2), Officer (5), Flag (5)
RANK_DEFINITIONS: List[Dict[str, Any]] = [
    # Enlisted
    {"name": "Recruit",           "points_required": 0,      "level": 0,  "tier": "Enlisted",  "trading_bonus": 0,  "combat_bonus": 0,  "max_turns_bonus": 0},
    {"name": "Spacer",            "points_required": 50,     "level": 1,  "tier": "Enlisted",  "trading_bonus": 2,  "combat_bonus": 1,  "max_turns_bonus": 5},
    {"name": "Corporal",          "points_required": 150,    "level": 2,  "tier": "Enlisted",  "trading_bonus": 3,  "combat_bonus": 2,  "max_turns_bonus": 10},
    # NCO
    {"name": "Sergeant",          "points_required": 300,    "level": 3,  "tier": "NCO",       "trading_bonus": 5,  "combat_bonus": 4,  "max_turns_bonus": 15},
    {"name": "Staff Sergeant",    "points_required": 500,    "level": 4,  "tier": "NCO",       "trading_bonus": 6,  "combat_bonus": 5,  "max_turns_bonus": 20},
    {"name": "Master Sergeant",   "points_required": 800,    "level": 5,  "tier": "NCO",       "trading_bonus": 7,  "combat_bonus": 6,  "max_turns_bonus": 25},
    # Warrant
    {"name": "Warrant Officer",       "points_required": 1200,  "level": 6,  "tier": "Warrant",  "trading_bonus": 8,   "combat_bonus": 8,   "max_turns_bonus": 30},
    {"name": "Chief Warrant Officer", "points_required": 1800,  "level": 7,  "tier": "Warrant",  "trading_bonus": 10,  "combat_bonus": 10,  "max_turns_bonus": 35},
    # Officer
    {"name": "Ensign",            "points_required": 2500,   "level": 8,  "tier": "Officer",   "trading_bonus": 12,  "combat_bonus": 12,  "max_turns_bonus": 40},
    {"name": "Lieutenant",        "points_required": 3500,   "level": 9,  "tier": "Officer",   "trading_bonus": 15,  "combat_bonus": 14,  "max_turns_bonus": 45},
    {"name": "Commander",         "points_required": 5000,   "level": 10, "tier": "Officer",   "trading_bonus": 18,  "combat_bonus": 16,  "max_turns_bonus": 50},
    {"name": "Captain",           "points_required": 7000,   "level": 11, "tier": "Officer",   "trading_bonus": 20,  "combat_bonus": 18,  "max_turns_bonus": 55},
    {"name": "Senior Captain",    "points_required": 10000,  "level": 12, "tier": "Officer",   "trading_bonus": 22,  "combat_bonus": 20,  "max_turns_bonus": 60},
    # Flag
    {"name": "Commodore",         "points_required": 14000,  "level": 13, "tier": "Flag",      "trading_bonus": 25,  "combat_bonus": 22,  "max_turns_bonus": 70},
    {"name": "Rear Admiral",      "points_required": 20000,  "level": 14, "tier": "Flag",      "trading_bonus": 30,  "combat_bonus": 25,  "max_turns_bonus": 80},
    {"name": "Vice Admiral",      "points_required": 28000,  "level": 15, "tier": "Flag",      "trading_bonus": 35,  "combat_bonus": 28,  "max_turns_bonus": 90},
    {"name": "Admiral",           "points_required": 40000,  "level": 16, "tier": "Flag",      "trading_bonus": 40,  "combat_bonus": 32,  "max_turns_bonus": 100},
    {"name": "Fleet Admiral",     "points_required": 60000,  "level": 17, "tier": "Flag",      "trading_bonus": 50,  "combat_bonus": 40,  "max_turns_bonus": 120},
]

# Legacy rank name mapping for backwards compatibility with existing player records
LEGACY_RANK_MAP: Dict[str, str] = {
    "Private": "Recruit",
    "General": "Admiral",
    "Major": "Commander",
    "Colonel": "Commodore",
}

# Achievement-based rank requirements beyond points
# Ranks not listed have no achievement requirements beyond points
RANK_REQUIREMENTS: Dict[str, Dict[str, int]] = {
    "Sergeant": {"min_trades": 25, "min_sectors_visited": 50},
    "Staff Sergeant": {"min_trades": 50, "min_combat_victories": 10},
    "Master Sergeant": {"min_trades": 100, "min_combat_victories": 25, "min_sectors_visited": 100},
    "Warrant Officer": {"min_combat_victories": 50, "min_trades": 200},
    "Ensign": {"min_combat_victories": 100, "min_planets_owned": 1},
    "Lieutenant": {"min_trades": 500, "min_combat_victories": 200},
    "Commander": {"min_planets_owned": 3, "min_combat_victories": 500},
    "Captain": {"min_trades": 1000, "min_combat_victories": 1000, "min_planets_owned": 5},
}

# Mapping from requirement keys to stat keys for comparison
_REQUIREMENT_TO_STAT = {
    "min_trades": "total_trades",
    "min_combat_victories": "combat_victories",
    "min_sectors_visited": "sectors_visited",
    "min_planets_owned": "planets_owned",
}

# Valid reasons for awarding rank points
VALID_REASONS = {
    "combat_victory",
    "trading_volume",
    "exploration",
    "colony_establishment",
    "admin_grant",
}


class RankingService:
    """Service for managing military ranking and progression."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Core rank helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_rank_for_points(points: int) -> Dict[str, Any]:
        """Return the rank definition that matches the given point total."""
        current_rank = RANK_DEFINITIONS[0]
        for rank_def in RANK_DEFINITIONS:
            if points >= rank_def["points_required"]:
                current_rank = rank_def
            else:
                break
        return current_rank

    @staticmethod
    def get_next_rank(current_rank_name: str) -> Optional[Dict[str, Any]]:
        """Return the next rank definition above the given rank, or None if max."""
        mapped_name = LEGACY_RANK_MAP.get(current_rank_name, current_rank_name)
        for i, rank_def in enumerate(RANK_DEFINITIONS):
            if rank_def["name"] == mapped_name:
                if i + 1 < len(RANK_DEFINITIONS):
                    return RANK_DEFINITIONS[i + 1]
                return None
        return None

    @staticmethod
    def get_rank_level(rank_name: str) -> int:
        """Return the numeric level (0-17) for a given rank name.

        Handles legacy rank names from existing player records via LEGACY_RANK_MAP.
        """
        # Map legacy names to current names
        mapped_name = LEGACY_RANK_MAP.get(rank_name, rank_name)
        for rank_def in RANK_DEFINITIONS:
            if rank_def["name"] == mapped_name:
                return rank_def["level"]
        return 0

    # ------------------------------------------------------------------
    # Rank bonuses
    # ------------------------------------------------------------------

    @staticmethod
    def get_rank_bonuses(rank_name: str) -> Dict[str, Any]:
        """Return the bonuses granted by a given rank.

        Each rank has specific trading, combat, and turn bonuses defined
        in RANK_DEFINITIONS. Legacy rank names are mapped automatically.
        """
        mapped_name = LEGACY_RANK_MAP.get(rank_name, rank_name)
        for rank_def in RANK_DEFINITIONS:
            if rank_def["name"] == mapped_name:
                return {
                    "trading_discount_percent": rank_def["trading_bonus"],
                    "max_turns_bonus": rank_def["max_turns_bonus"],
                    "combat_damage_bonus_percent": rank_def["combat_bonus"],
                }
        # Fallback for unknown rank names
        return {
            "trading_discount_percent": 0,
            "max_turns_bonus": 0,
            "combat_damage_bonus_percent": 0,
        }

    # ------------------------------------------------------------------
    # Turn calculation & refresh
    # ------------------------------------------------------------------

    BASE_TURNS = 1000

    @staticmethod
    def calculate_max_turns(
        player: Player,
        base_turns: int = 1000,
    ) -> int:
        """Calculate the effective max turns for a player.

        Combines the base turn allowance with the military-rank bonus and
        the ARIA consciousness multiplier.

        Formula:
            max_turns = int((base_turns + rank_bonus) * aria_multiplier)

        Parameters
        ----------
        player : Player
            The player whose max turns we are computing.
        base_turns : int, optional
            The game-wide base turn allowance (default 1000).

        Returns
        -------
        int
            The player's effective maximum turns.
        """
        rank_bonuses = RankingService.get_rank_bonuses(player.military_rank)
        rank_bonus = rank_bonuses["max_turns_bonus"]

        # aria_bonus_multiplier is stored on the player (1.0 to 1.5)
        aria_multiplier = getattr(player, "aria_bonus_multiplier", 1.0) or 1.0
        # Clamp to spec range just in case
        aria_multiplier = max(1.0, min(1.5, aria_multiplier))

        return int((base_turns + rank_bonus) * aria_multiplier)

    def refresh_daily_turns(
        self,
        player: Player,
        base_turns: int = 1000,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Reset a player's turns to their calculated max if a daily reset is due.

        The reset happens at most once per calendar day (UTC). The
        ``Player.turn_reset_at`` column tracks when turns were last
        refreshed.  If the player's turns are already *above* the
        calculated max (e.g. from an admin grant), we leave them alone
        unless ``force`` is True.

        Parameters
        ----------
        player : Player
            A loaded Player ORM object (must be attached to the session).
        base_turns : int, optional
            The game-wide base turn allowance (default 1000).
        force : bool, optional
            If True, reset turns even if the daily window has not elapsed.

        Returns
        -------
        dict
            Keys: refreshed (bool), old_turns, new_turns, max_turns,
            rank_bonus, aria_multiplier.
        """
        now = datetime.now(timezone.utc)
        max_turns = self.calculate_max_turns(player, base_turns)

        # Determine whether a refresh is due
        needs_refresh = force
        if not needs_refresh:
            if player.turn_reset_at is None:
                # Player has never had a turn reset — grant one now
                needs_refresh = True
            else:
                # Ensure we compare tz-aware datetimes
                last_reset = player.turn_reset_at
                if last_reset.tzinfo is None:
                    last_reset = last_reset.replace(tzinfo=timezone.utc)
                # Reset is due if the last reset was before the start of the current UTC day
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                needs_refresh = last_reset < today_start

        if not needs_refresh:
            return {
                "refreshed": False,
                "old_turns": player.turns,
                "new_turns": player.turns,
                "max_turns": max_turns,
                "rank_bonus": self.get_rank_bonuses(player.military_rank)["max_turns_bonus"],
                "aria_multiplier": getattr(player, "aria_bonus_multiplier", 1.0) or 1.0,
            }

        old_turns = player.turns

        # Only reset if the player's turns are below the max (don't punish admin grants)
        if player.turns < max_turns or force:
            player.turns = max_turns

        player.turn_reset_at = now
        self.db.flush()

        rank_bonus = self.get_rank_bonuses(player.military_rank)["max_turns_bonus"]
        aria_multiplier = getattr(player, "aria_bonus_multiplier", 1.0) or 1.0

        logger.info(
            "Turn refresh for player %s: %d -> %d (max=%d, rank_bonus=%d, aria=%.2f)",
            player.id, old_turns, player.turns, max_turns, rank_bonus, aria_multiplier,
        )

        return {
            "refreshed": True,
            "old_turns": old_turns,
            "new_turns": player.turns,
            "max_turns": max_turns,
            "rank_bonus": rank_bonus,
            "aria_multiplier": aria_multiplier,
        }

    # ------------------------------------------------------------------
    # Point calculation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_combat_points(
        winner_rank: str, loser_rank: str
    ) -> int:
        """Calculate rank points earned from a combat victory.

        Points range from 10-50 based on the relative rank difference.
        Defeating a higher-ranked opponent grants more points.
        """
        winner_level = RankingService.get_rank_level(winner_rank)
        loser_level = RankingService.get_rank_level(loser_rank)

        base_points = 10
        rank_diff = loser_level - winner_level  # positive = opponent is higher rank

        if rank_diff > 0:
            # Bonus for defeating higher ranked opponent: up to +40
            bonus = min(rank_diff * 10, 40)
        elif rank_diff < 0:
            # Reduced points for defeating lower ranked opponent (minimum 10)
            bonus = max(rank_diff * 5, -5)  # At most lose 5 from base
        else:
            bonus = 5  # Same rank gives a small bonus

        return max(10, min(50, base_points + bonus))

    @staticmethod
    def calculate_trading_points(total_value: int) -> int:
        """Calculate rank points earned from a trade based on total transaction value.

        Points range from 5-20 depending on trade value milestones.
        """
        if total_value >= 100000:
            return 20
        elif total_value >= 50000:
            return 15
        elif total_value >= 10000:
            return 10
        elif total_value >= 1000:
            return 5
        return 0

    @staticmethod
    def calculate_exploration_points() -> int:
        """Points awarded for discovering a new sector. Fixed 3 points."""
        return 3

    @staticmethod
    def calculate_colony_points() -> int:
        """Points awarded for establishing a colony. Fixed 25 points."""
        return 25

    # ------------------------------------------------------------------
    # Player stats & achievement requirements
    # ------------------------------------------------------------------

    def get_player_stats(self, player_id: uuid.UUID) -> Dict[str, int]:
        """Query the DB to build current achievement stats for a player.

        Returns a dict with keys: total_trades, combat_victories,
        sectors_visited, planets_owned.
        """
        from sqlalchemy import func as sa_func, or_, and_

        # --- total_trades: count from enhanced_market_transactions ---
        from src.models.market_transaction import MarketTransaction
        total_trades = (
            self.db.query(sa_func.count(MarketTransaction.id))
            .filter(MarketTransaction.player_id == player_id)
            .scalar()
        ) or 0

        # --- combat_victories: attacker wins + defender wins ---
        from src.models.combat_log import CombatLog
        combat_victories = (
            self.db.query(sa_func.count(CombatLog.id))
            .filter(
                or_(
                    and_(
                        CombatLog.attacker_id == player_id,
                        CombatLog.outcome == "attacker_win",
                        # NPC kills (defender_id NULL) must not farm
                        # achievement victories — canon NPC-kill reward
                        # hooks (npc-scheduler.md KIA step 8) are deferred
                        CombatLog.defender_id.isnot(None),
                    ),
                    and_(
                        CombatLog.defender_id == player_id,
                        CombatLog.outcome == "defender_win",
                    ),
                )
            )
            .scalar()
        ) or 0

        # --- sectors_visited: count unique sectors from ARIA exploration map ---
        from src.models.aria_personal_intelligence import ARIAExplorationMap
        sectors_visited = (
            self.db.query(sa_func.count(ARIAExplorationMap.id))
            .filter(ARIAExplorationMap.player_id == player_id)
            .scalar()
        ) or 0

        # --- planets_owned: count from planets table ---
        from src.models.planet import Planet
        planets_owned = (
            self.db.query(sa_func.count(Planet.id))
            .filter(Planet.owner_id == player_id)
            .scalar()
        ) or 0

        return {
            "total_trades": total_trades,
            "combat_victories": combat_victories,
            "sectors_visited": sectors_visited,
            "planets_owned": planets_owned,
        }

    def check_rank_requirements(
        self, player_id: uuid.UUID, target_rank: str
    ) -> Dict[str, Any]:
        """Check whether a player meets achievement requirements for a rank.

        Returns a dict with keys:
            met (bool), requirements (dict), current_stats (dict), missing (list)
        """
        requirements = RANK_REQUIREMENTS.get(target_rank)
        if not requirements:
            # No achievement requirements for this rank — auto-pass
            return {
                "met": True,
                "requirements": {},
                "current_stats": {},
                "missing": [],
            }

        stats = self.get_player_stats(player_id)
        missing: List[str] = []

        for req_key, threshold in requirements.items():
            stat_key = _REQUIREMENT_TO_STAT.get(req_key, req_key)
            current_value = stats.get(stat_key, 0)
            if current_value < threshold:
                missing.append(
                    f"{req_key}: have {current_value}, need {threshold}"
                )

        return {
            "met": len(missing) == 0,
            "requirements": requirements,
            "current_stats": stats,
            "missing": missing,
        }

    # ------------------------------------------------------------------
    # Core service methods
    # ------------------------------------------------------------------

    def award_rank_points(
        self,
        player_id: uuid.UUID,
        points: int,
        reason: str,
    ) -> Dict[str, Any]:
        """Award rank points to a player and check for promotion.

        Args:
            player_id: UUID of the player to award points to.
            points: Number of points to award (must be positive).
            reason: Reason for the award (must be a valid reason).

        Returns:
            Dict with keys: success, points_awarded, new_total, promoted, rank_info
        """
        if points <= 0:
            return {
                "success": False,
                "message": "Points must be positive",
                "points_awarded": 0,
                "new_total": 0,
                "promoted": False,
                "rank_info": None,
            }

        if reason not in VALID_REASONS:
            logger.warning(
                "Invalid rank point reason '%s' for player %s", reason, player_id
            )
            return {
                "success": False,
                "message": f"Invalid reason: {reason}",
                "points_awarded": 0,
                "new_total": 0,
                "promoted": False,
                "rank_info": None,
            }

        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {
                "success": False,
                "message": "Player not found",
                "points_awarded": 0,
                "new_total": 0,
                "promoted": False,
                "rank_info": None,
            }

        old_rank = player.military_rank
        player.rank_points = (player.rank_points or 0) + points

        # Check and apply promotion
        promotion_result = self._check_and_promote(player)

        self.db.flush()  # flush but let caller decide on commit

        logger.info(
            "Awarded %d rank points to player %s for %s (total: %d, rank: %s)",
            points,
            player_id,
            reason,
            player.rank_points,
            player.military_rank,
        )

        rank_info = self._build_rank_info(player)

        result = {
            "success": True,
            "message": (
                f"Promoted to {player.military_rank}!"
                if promotion_result["promoted"]
                else f"Awarded {points} rank points"
            ),
            "points_awarded": points,
            "new_total": player.rank_points,
            "promoted": promotion_result["promoted"],
            "old_rank": old_rank,
            "new_rank": player.military_rank,
            "rank_info": rank_info,
        }

        # If promotion was blocked by achievement requirements, surface that
        if promotion_result.get("promotion_blocked"):
            result["promotion_blocked"] = True
            result["missing_requirements"] = promotion_result["missing_requirements"]
            result["message"] = (
                f"Awarded {points} rank points — promotion to "
                f"{promotion_result['target_rank']} blocked: "
                f"achievement requirements not met"
            )

        return result

    def check_and_promote(self, player_id: uuid.UUID) -> Dict[str, Any]:
        """Check if a player qualifies for promotion and promote if so.

        Public wrapper that fetches the player by ID.
        """
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {"promoted": False, "message": "Player not found"}

        result = self._check_and_promote(player)
        if result["promoted"]:
            self.db.flush()
        return result

    def _check_and_promote(self, player: Player) -> Dict[str, Any]:
        """Internal promotion check that operates on a loaded Player object.

        In addition to verifying the player has enough rank points, this
        method also checks achievement-based requirements (trades completed,
        combat victories, sectors visited, planets owned) defined in
        RANK_REQUIREMENTS.  If the player has enough points but does not
        meet the achievement thresholds, the promotion is blocked and the
        return dict includes ``promotion_blocked`` and ``missing_requirements``.
        """
        earned_rank = self.get_rank_for_points(player.rank_points or 0)

        if earned_rank["name"] != player.military_rank:
            # Player has enough points for a new rank — verify achievements
            req_check = self.check_rank_requirements(player.id, earned_rank["name"])

            if not req_check["met"]:
                logger.info(
                    "Player %s has points for %s but missing requirements: %s",
                    player.id,
                    earned_rank["name"],
                    req_check["missing"],
                )
                return {
                    "promoted": False,
                    "promotion_blocked": True,
                    "target_rank": earned_rank["name"],
                    "missing_requirements": req_check["missing"],
                    "current_stats": req_check["current_stats"],
                    "message": (
                        f"Points qualify for {earned_rank['name']} but "
                        f"achievement requirements not met"
                    ),
                }

            old_rank = player.military_rank
            player.military_rank = earned_rank["name"]
            logger.info(
                "Player %s promoted from %s to %s (points: %d)",
                player.id,
                old_rank,
                earned_rank["name"],
                player.rank_points,
            )
            return {
                "promoted": True,
                "old_rank": old_rank,
                "new_rank": earned_rank["name"],
                "message": f"Promoted from {old_rank} to {earned_rank['name']}!",
            }

        return {"promoted": False, "message": "No promotion earned yet"}

    def get_rank_info(self, player_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Return detailed rank information for a player."""
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return None
        return self._build_rank_info(player)

    def _build_rank_info(self, player: Player) -> Dict[str, Any]:
        """Build rank info dict from a loaded Player object."""
        current_rank = self.get_rank_for_points(player.rank_points or 0)
        next_rank = self.get_next_rank(current_rank["name"])

        if next_rank:
            points_to_next = next_rank["points_required"] - (player.rank_points or 0)
            progress_percent = (
                ((player.rank_points or 0) - current_rank["points_required"])
                / (next_rank["points_required"] - current_rank["points_required"])
                * 100
            )
            progress_percent = max(0.0, min(100.0, progress_percent))
        else:
            points_to_next = 0
            progress_percent = 100.0

        bonuses = self.get_rank_bonuses(current_rank["name"])

        aria_multiplier = getattr(player, "aria_bonus_multiplier", 1.0) or 1.0
        effective_max_turns = self.calculate_max_turns(player)

        return {
            "player_id": str(player.id),
            "username": player.username,
            "current_rank": current_rank["name"],
            "rank_level": current_rank["level"],
            "rank_tier": current_rank.get("tier", "Enlisted"),
            "rank_points": player.rank_points or 0,
            "points_to_next_rank": points_to_next,
            "next_rank": next_rank["name"] if next_rank else None,
            "next_rank_points_required": next_rank["points_required"] if next_rank else None,
            "progress_percent": round(progress_percent, 1),
            "bonuses": bonuses,
            "is_max_rank": next_rank is None,
            "effective_max_turns": effective_max_turns,
            "aria_multiplier": round(aria_multiplier, 2),
        }

    # ------------------------------------------------------------------
    # Leaderboard
    # ------------------------------------------------------------------

    def get_leaderboard(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return a leaderboard of top-ranked players.

        Args:
            limit: Maximum number of players to return (default 20, max 100).

        Returns:
            List of dicts with player rank info, ordered by rank_points descending.
        """
        limit = max(1, min(100, limit))

        players = (
            self.db.query(Player)
            .filter(Player.is_active == True)
            .order_by(desc(Player.rank_points))
            .limit(limit)
            .all()
        )

        leaderboard = []
        for position, player in enumerate(players, start=1):
            rank_info = self._build_rank_info(player)
            leaderboard.append(
                {
                    "position": position,
                    "player_id": str(player.id),
                    "username": player.username,
                    "military_rank": player.military_rank,
                    "rank_points": player.rank_points or 0,
                    "rank_level": rank_info["rank_level"],
                }
            )

        return leaderboard
