"""
Personal Reputation Service

8-tier alignment system from Villain (-1000) to Legendary (+1000).
Tracks player morality through combat actions, trade behavior, and diplomacy.
"""

import logging
import uuid
from typing import Dict, Any

from sqlalchemy.orm import Session

from src.models.player import Player
from src.services.wanted_service import recompute_is_wanted

logger = logging.getLogger(__name__)


def _recompute_wanted_defensively(db: Session, player) -> None:
    """Fires the personal_reputation Wanted-trigger recompute (ranking.md
    "Wanted status") without ever breaking the caller. Mirrors ship_service.
    _dispatch_fleet_medals's own defensive-hook convention: adjust_reputation
    is called from dozens of pre-existing sites (combat, bounty, contraband,
    ...), many exercised by tests with lightweight Player stand-ins
    (SimpleNamespace, hand-rolled stubs) that predate this trigger and don't
    carry ``is_wanted``/``wanted_until``/``personal_reputation`` or a
    ``Ship.id``-query-capable fake session. The Wanted flag is a side
    effect of a reputation change, not the reason for it -- a stub gap here
    must never swallow the actual reputation adjustment."""
    try:
        recompute_is_wanted(db, player)
    except Exception as e:  # never let the Wanted-trigger break reputation adjustment
        logger.error("Wanted-trigger recompute failed for player %s: %s", getattr(player, "id", "?"), e)

# 8-tier reputation system: (min_score, max_score, tier_name, color)
REPUTATION_TIERS = [
    (-1000, -750, "Villain", "#FF0000"),
    (-749, -500, "Criminal", "#FF4400"),
    (-499, -250, "Outlaw", "#FF8800"),
    (-249, -1, "Suspicious", "#FFCC00"),
    (0, 0, "Neutral", "#FFFFFF"),
    (1, 249, "Lawful", "#88FF88"),
    (250, 499, "Heroic", "#00FF00"),
    (500, 1000, "Legendary", "#00FFFF"),
]

# Reputation change triggers
REPUTATION_TRIGGERS = {
    "attack_innocent": -100,       # Attack player with no bounty
    "kill_escape_pod": -500,       # Kill player in escape pod
    "defend_against_attacker": 50, # Defend successfully against an aggressor
    "defeat_bounty_target": 100,   # Kill player with active bounty
    "complete_trade": 1,           # Small positive for legitimate trade
    "destroy_pirate_drones": 10,   # Clear dangerous sector drones
}


class PersonalReputationService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _get_tier_for_score(score: int) -> tuple:
        """Return (tier_name, color) for the given reputation score."""
        for min_s, max_s, tier, color in REPUTATION_TIERS:
            if min_s <= score <= max_s:
                return tier, color
        # Clamp extremes
        if score < -1000:
            return "Villain", "#FF0000"
        return "Legendary", "#00FFFF"

    def adjust_reputation(
        self, player_id: uuid.UUID, amount: int, reason: str
    ) -> Dict[str, Any]:
        """Adjust a player's personal reputation by `amount`, clamped to [-1000, +1000]."""
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {"success": False, "message": "Player not found"}

        old_score = player.personal_reputation
        new_score = max(-1000, min(1000, old_score + amount))
        player.personal_reputation = new_score

        tier, color = self._get_tier_for_score(new_score)
        player.reputation_tier = tier
        player.name_color = color

        # Wanted-trigger (ranking.md "Wanted status"): personal_reputation
        # crossing -500 in either direction. This is the only write site
        # for personal_reputation (apply_weekly_decay is the other, below).
        _recompute_wanted_defensively(self.db, player)

        self.db.flush()

        logger.info(
            "Reputation adjusted for player %s: %d -> %d (%s) reason=%s",
            player_id, old_score, new_score, tier, reason,
        )

        return {
            "success": True,
            "old_score": old_score,
            "new_score": new_score,
            "tier": tier,
            "color": color,
            "reason": reason,
        }

    def get_reputation_info(self, player_id: uuid.UUID) -> Dict[str, Any]:
        """Return current reputation score, tier, color, and gameplay effects."""
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {"success": False, "message": "Player not found"}

        score = player.personal_reputation
        tier, color = self._get_tier_for_score(score)

        # Gameplay effects based on alignment
        effects = {}
        if score <= -500:
            effects["station_price_increase"] = 20  # 20% markup at lawful stations
            effects["bounty_hunter_aggro"] = True
        elif score <= -250:
            effects["station_price_increase"] = 10
        elif score >= 500:
            effects["station_price_discount"] = 10  # 10% discount at lawful stations
            effects["faction_standing_bonus"] = 5
        elif score >= 250:
            effects["station_price_discount"] = 5
            effects["faction_standing_bonus"] = 5
        elif score >= 1:
            effects["station_price_discount"] = 5

        return {
            "success": True,
            "player_id": str(player_id),
            "score": score,
            "tier": tier,
            "color": color,
            "effects": effects,
        }

    def apply_weekly_decay(self, player_id: uuid.UUID) -> Dict[str, Any]:
        """Decay reputation toward 0 by 5 points per week for any nonzero
        score (ADR-0025's whole-range ruling) -- not just extreme values."""
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {"success": False, "message": "Player not found"}

        score = player.personal_reputation
        if score == 0:
            return {"success": True, "decayed": False, "score": 0}

        decay = 5
        if score > 0:
            new_score = max(0, score - decay)
        else:
            new_score = min(0, score + decay)

        player.personal_reputation = new_score
        tier, color = self._get_tier_for_score(new_score)
        player.reputation_tier = tier
        player.name_color = color

        _recompute_wanted_defensively(self.db, player)

        self.db.flush()

        return {
            "success": True,
            "decayed": True,
            "old_score": score,
            "new_score": new_score,
            "tier": tier,
        }
