"""
Bounty Service

Player-placed and system-generated bounties.
Uses Player.settings["bounties"] JSONB — no new database table required.
"""

import logging
import uuid
from datetime import datetime, UTC
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player

logger = logging.getLogger(__name__)

BOUNTY_MIN_AMOUNT = 1000
BOUNTY_PLACEMENT_FEE = 0.10  # 10% fee

# System-generated bounty thresholds based on personal reputation
SYSTEM_BOUNTY_TIERS = {
    -500: 5000,    # Criminal: 5,000 credit bounty
    -750: 25000,   # Villain low: 25,000 credit bounty
    -1000: 100000, # Villain max: 100,000 credit bounty
}


class BountyService:
    def __init__(self, db: Session):
        self.db = db

    def _get_bounties(self, player: Player) -> List[Dict[str, Any]]:
        """Read bounties list from player settings JSONB."""
        settings = player.settings or {}
        return settings.get("bounties", [])

    def _set_bounties(self, player: Player, bounties: List[Dict[str, Any]]) -> None:
        """Write bounties list to player settings JSONB."""
        if player.settings is None:
            player.settings = {}
        player.settings["bounties"] = bounties
        flag_modified(player, "settings")

    def place_bounty(
        self, placer_id: uuid.UUID, target_id: uuid.UUID, amount: int
    ) -> Dict[str, Any]:
        """Place a bounty on a target player. Placer pays amount + 10% fee."""
        if amount < BOUNTY_MIN_AMOUNT:
            return {
                "success": False,
                "message": f"Minimum bounty is {BOUNTY_MIN_AMOUNT} credits",
            }

        # Lock placer row to prevent concurrent bounty placement race conditions
        placer = self.db.query(Player).filter(Player.id == placer_id).with_for_update().first()
        target = self.db.query(Player).filter(Player.id == target_id).first()

        if not placer or not target:
            return {"success": False, "message": "Player not found"}

        if placer_id == target_id:
            return {"success": False, "message": "Cannot place a bounty on yourself"}

        fee = int(amount * BOUNTY_PLACEMENT_FEE)
        total_cost = amount + fee

        if placer.credits < total_cost:
            return {
                "success": False,
                "message": f"Need {total_cost} credits ({amount} + {fee} fee), have {placer.credits}",
            }

        # Deduct credits from placer
        placer.credits -= total_cost

        # Add bounty to target's settings
        bounties = self._get_bounties(target)
        bounty_entry = {
            "id": str(uuid.uuid4()),
            "placed_by": str(placer_id),
            "placed_by_name": placer.nickname or "Anonymous",
            "amount": amount,
            "placed_at": datetime.now(UTC).isoformat(),
            "type": "player",
        }
        bounties.append(bounty_entry)
        self._set_bounties(target, bounties)

        self.db.flush()

        logger.info(
            "Bounty placed: %s placed %d on %s (fee: %d)",
            placer_id, amount, target_id, fee,
        )

        return {
            "success": True,
            "bounty_id": bounty_entry["id"],
            "target_id": str(target_id),
            "amount": amount,
            "fee": fee,
            "total_cost": total_cost,
            "remaining_credits": placer.credits,
        }

    def get_bounties_on_player(self, target_id: uuid.UUID) -> Dict[str, Any]:
        """List all active bounties on a player."""
        target = self.db.query(Player).filter(Player.id == target_id).first()
        if not target:
            return {"success": False, "message": "Player not found"}

        player_bounties = self._get_bounties(target)

        # Include system bounties based on reputation
        system_bounties = self._get_system_bounties(target)

        return {
            "success": True,
            "target_id": str(target_id),
            "target_name": target.nickname,
            "player_bounties": player_bounties,
            "system_bounties": system_bounties,
            "total_value": sum(b["amount"] for b in player_bounties) + sum(
                b["amount"] for b in system_bounties
            ),
        }

    def collect_bounty(
        self, collector_id: uuid.UUID, target_id: uuid.UUID
    ) -> Dict[str, Any]:
        """Award all bounties on target to collector (called on kill)."""
        # Lock both rows to prevent double-collection race condition
        collector = self.db.query(Player).filter(Player.id == collector_id).with_for_update().first()
        target = self.db.query(Player).filter(Player.id == target_id).with_for_update().first()

        if not collector or not target:
            return {"success": False, "message": "Player not found"}

        player_bounties = self._get_bounties(target)
        system_bounties = self._get_system_bounties(target)

        total_player = sum(b["amount"] for b in player_bounties)
        total_system = sum(b["amount"] for b in system_bounties)
        total = total_player + total_system

        if total == 0:
            return {"success": False, "message": "No bounties on this player"}

        # Award credits
        collector.credits += total

        # Clear player bounties
        self._set_bounties(target, [])

        self.db.flush()

        logger.info(
            "Bounty collected: %s collected %d from bounties on %s",
            collector_id, total, target_id,
        )

        return {
            "success": True,
            "collector_id": str(collector_id),
            "target_id": str(target_id),
            "player_bounties_collected": total_player,
            "system_bounties_collected": total_system,
            "total_collected": total,
            "new_credits": collector.credits,
        }

    def _get_system_bounties(self, target: Player) -> List[Dict[str, Any]]:
        """Generate the system bounty based on the target's personal reputation.

        Canon (FEATURES/gameplay/bounties.md#system-bounty-tiers): only the
        single HIGHEST matched tier is active — the deepest pit pays out,
        lower-tier bounties don't stack on top of it. (Appending every
        matched tier let a -1000 rep player carry 5k+25k+100k = 130k.)
        """
        score = target.personal_reputation
        matched = [t for t in SYSTEM_BOUNTY_TIERS if score <= t]
        if not matched:
            return []
        # Deepest matched threshold == highest-tier (largest) bounty
        threshold = min(matched)
        return [{
            "id": f"system_{threshold}",
            "placed_by": "SYSTEM",
            "placed_by_name": "Federation Bounty Board",
            "amount": SYSTEM_BOUNTY_TIERS[threshold],
            "type": "system",
            "reason": f"Criminal reputation ({score})",
        }]

    def get_available_bounties(self, limit: int = 20) -> Dict[str, Any]:
        """List all players who currently have bounties on them."""
        # Find all players with non-empty bounties in settings
        players = self.db.query(Player).filter(
            Player.is_active == True
        ).all()

        bounty_targets = []
        for player in players:
            player_bounties = self._get_bounties(player)
            system_bounties = self._get_system_bounties(player)
            total = sum(b["amount"] for b in player_bounties) + sum(
                b["amount"] for b in system_bounties
            )
            if total > 0:
                bounty_targets.append({
                    "player_id": str(player.id),
                    "player_name": player.nickname,
                    "reputation_tier": player.reputation_tier,
                    "total_bounty": total,
                    "bounty_count": len(player_bounties) + len(system_bounties),
                    "current_sector": player.current_sector_id,
                })

        # Sort by total bounty descending
        bounty_targets.sort(key=lambda x: x["total_bounty"], reverse=True)

        return {
            "success": True,
            "bounties": bounty_targets[:limit],
            "total_targets": len(bounty_targets),
        }
