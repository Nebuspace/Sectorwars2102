"""
Personal Reputation Service

8-tier alignment system from Villain (-1000) to Legendary (+1000).
Tracks player morality through combat actions, trade behavior, and diplomacy.
"""

import logging
import math
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from src.models.player import Player
from src.services.trading_service import _PERSONAL_REP_TIER_MULTIPLIERS
from src.services.wanted_service import recompute_is_wanted

# bounty-and-reputation.md:148 — encounter-rate increase at this score.
BOUNTY_HUNTER_AGGRO_THRESHOLD = -500
# ranking.md Villain row lists 1.20 alongside "bounty hunters target".
# Canon states direction ("increased") not a spawn formula; this is that
# table's only numeric encounter-adjacent magnitude, applied at the :148
# threshold (Criminal and Villain both ≤ −500). Not a new band.
BOUNTY_HUNTER_ENCOUNTER_MULTIPLIER = 1.20

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

        # Gameplay effects based on alignment. The price effect is derived
        # from _PERSONAL_REP_TIER_MULTIPLIERS -- the same table
        # compute_player_price_multiplier() actually charges -- so this
        # display never drifts from what the player is really paying (was
        # previously a separately hand-maintained ladder that had drifted:
        # e.g. Suspicious showed "no effect" while actually paying +5%).
        effects = {}
        personal_mult = _PERSONAL_REP_TIER_MULTIPLIERS.get(tier, 1.0)
        if personal_mult > 1.0:
            effects["station_price_increase"] = round((personal_mult - 1.0) * 100)
        elif personal_mult < 1.0:
            effects["station_price_discount"] = round((1.0 - personal_mult) * 100)
        if tier in ("Criminal", "Villain"):
            effects["bounty_hunter_aggro"] = True
        if tier in ("Heroic", "Legendary"):
            effects["faction_standing_bonus"] = 5

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


def bounty_hunter_encounter_multiplier(personal_reputation: Optional[int]) -> float:
    """Spawn/encounter weight for bounty-hunter NPCs.

    1.0 below the Criminal threshold; 1.20 at ``personal_reputation ≤ −500``
    (bounty-and-reputation.md:148 + ranking.md Villain 1.20).
    """
    if personal_reputation is None:
        return 1.0
    if personal_reputation <= BOUNTY_HUNTER_AGGRO_THRESHOLD:
        return BOUNTY_HUNTER_ENCOUNTER_MULTIPLIER
    return 1.0


def bounty_hunter_spawn_count(base: int, personal_reputation: Optional[int]) -> int:
    """Integer Loop-B fill size after applying :func:`bounty_hunter_encounter_multiplier`.

    ``ceil(base * 1.20)`` so a live cadence of 1 becomes 2 at threshold
    (``floor`` would no-op). ``base <= 0`` stays 0.
    """
    if base <= 0:
        return 0
    return max(base, math.ceil(base * bounty_hunter_encounter_multiplier(personal_reputation)))


def lowest_personal_reputation_in_sector(db: Session, sector_id: int) -> Optional[int]:
    """Most-negative ``Player.personal_reputation`` currently in ``sector_id``.

    ``None`` when the sector is empty of players (neutral spawn cadence).
    """
    row = (
        db.query(Player.personal_reputation)
        .filter(Player.current_sector_id == sector_id)
        .order_by(Player.personal_reputation.asc())
        .first()
    )
    if row is None:
        return None
    return row[0]
