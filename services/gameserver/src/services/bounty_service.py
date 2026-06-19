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
from src.models.bounty_claim import BountyClaim, BountyClaimStatus

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
            return {"success": False, "message": "Player not found", "had_bounty": False}

        player_bounties = self._get_bounties(target)
        system_bounties = self._get_system_bounties(target)

        # had_bounty: did the target carry ANY bounty at all at call time,
        # evaluated BEFORE the anti-faucet dedup below? Combat uses this to
        # distinguish "killed an innocent" (no bounty ever) from "killed a known
        # criminal whose head I'd already turned in" (bounty exists but deduped).
        had_bounty = bool(player_bounties) or bool(system_bounties)

        now = datetime.now(UTC)

        # --- Player-placed bounties: pay every entry, record a PAID claim ---
        # These are pay-once-then-cleared (the list is wiped below), so no
        # ledger dedup is needed — clearing the JSONB is the dedup.
        total_player = 0
        for b in player_bounties:
            amount = b.get("amount", 0)
            total_player += amount
            self._write_claim(
                claimant_id=collector_id,
                target_id=target_id,
                amount=amount,
                bounty_ref=str(b.get("id")),
                resolved_at=now,
            )

        # --- System bounty: ledger-based dedup closes the collusion faucet ---
        # The system bounty is recomputed from reputation on EVERY kill and is
        # never "cleared", so without a ledger a hunter could farm it by
        # repeat-killing the same deep-negative-rep accomplice. We make a
        # criminal's head collectible ONCE per (hunter, target) for ANY system
        # tier: if this hunter already has a PAID system claim against this
        # target we skip — this also closes the residual "deepen rep across tier
        # boundaries to mint a fresh bounty_ref" harvest (5k→25k→100k). A
        # DIFFERENT hunter can still collect once. The precise re-collection
        # policy (once-forever vs once-per-rep-recovery-cycle) is NO-CANON and
        # filed for ratification in DECISIONS.md (system-bounty-anti-faucet).
        total_system = 0
        already_claimed_system = self._has_paid_system_claim(collector_id, target_id)
        for b in system_bounties:
            if already_claimed_system:
                # This hunter already turned in this criminal — no second payout.
                continue
            bounty_ref = str(b.get("id"))  # f"system_{threshold}"
            amount = b.get("amount", 0)
            total_system += amount
            self._write_claim(
                claimant_id=collector_id,
                target_id=target_id,
                amount=amount,
                bounty_ref=bounty_ref,
                resolved_at=now,
            )

        total = total_player + total_system

        if total == 0:
            # No NEW payout. This is reached either because there was never a
            # bounty (had_bounty False) or because every bounty was already
            # claimed by this hunter (had_bounty True). Combat needs the
            # distinction, so return success-ish with had_bounty rather than a
            # bare failure when the target genuinely had a (deduped) bounty.
            if not had_bounty:
                return {
                    "success": False,
                    "message": "No bounties on this player",
                    "had_bounty": False,
                    "player_bounties_collected": 0,
                    "system_bounties_collected": 0,
                    "total_collected": 0,
                }
            logger.info(
                "Bounty collect: %s killed %s but all bounties already claimed (faucet deduped)",
                collector_id, target_id,
            )
            return {
                "success": True,
                "collector_id": str(collector_id),
                "target_id": str(target_id),
                "had_bounty": True,
                "player_bounties_collected": 0,
                "system_bounties_collected": 0,
                "total_collected": 0,
                "new_credits": collector.credits,
            }

        # Award credits
        collector.credits += total

        # Clear player bounties (system bounties are not stored; dedup is ledger)
        self._set_bounties(target, [])

        # Flush within the caller's locked transaction (caller owns the commit).
        self.db.flush()

        logger.info(
            "Bounty collected: %s collected %d (player=%d system=%d) from bounties on %s",
            collector_id, total, total_player, total_system, target_id,
        )

        return {
            "success": True,
            "collector_id": str(collector_id),
            "target_id": str(target_id),
            "had_bounty": True,
            "player_bounties_collected": total_player,
            "system_bounties_collected": total_system,
            "total_collected": total,
            "new_credits": collector.credits,
        }

    def _has_paid_system_claim(
        self, claimant_id: uuid.UUID, target_id: uuid.UUID
    ) -> bool:
        """True if this claimant already has ANY PAID *system* bounty claim
        against this target (bounty_ref starts with 'system_'). This is the
        anti-faucet guard: a criminal's head pays each hunter once, regardless of
        how deep the target's reputation later sinks. Purely ledger-based — no
        time window, no magic numbers."""
        return (
            self.db.query(BountyClaim.id)
            .filter(
                BountyClaim.claimant_id == claimant_id,
                BountyClaim.target_id == target_id,
                BountyClaim.bounty_ref.like("system_%"),
                BountyClaim.status == BountyClaimStatus.PAID,
            )
            .first()
            is not None
        )

    def _write_claim(
        self,
        claimant_id: uuid.UUID,
        target_id: uuid.UUID,
        amount: int,
        bounty_ref: str,
        resolved_at: datetime,
    ) -> None:
        """Append a PAID BountyClaim provenance row inside the caller's locked
        transaction. The INSERT is SAVEPOINT-scoped (``begin_nested``): a flush
        failure rolls back ONLY this claim, never the caller's open unit of work
        (collect_bounty runs inside combat's transaction — an unguarded failed
        flush would poison the session and make combat's terminal commit raise
        PendingRollbackError). The savepoint also keeps the row visible to
        subsequent same-txn dedup reads; the caller owns the outer commit."""
        claim = BountyClaim(
            bounty_ref=bounty_ref,
            claimant_id=claimant_id,
            target_id=target_id,
            amount=amount,
            status=BountyClaimStatus.PAID,
            resolved_at=resolved_at,
        )
        with self.db.begin_nested():
            self.db.add(claim)
            self.db.flush()

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
