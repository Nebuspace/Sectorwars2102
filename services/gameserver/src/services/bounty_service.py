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

# System-generated bounty thresholds based on personal reputation. These define
# WHO the Federation wants (a player must be at or below the shallowest tier,
# -500, to accrue any system bounty) and the per-tier ACCRUAL CAP — the deepest
# matched tier sets the ceiling the stored pot grows toward. (Previously these
# were instantaneous bounty values recomputed on every kill; under WO-BN the pot
# is STORED and GROWS over time, so a tier's figure is now the cap, not the
# constant payout.)
SYSTEM_BOUNTY_TIERS = {
    -500: 5000,    # Criminal: pot caps at 5,000 credits
    -750: 25000,   # Villain low: pot caps at 25,000 credits
    -1000: 100000, # Villain max: pot caps at 100,000 credits
}

# Shallowest criminal threshold — a player whose personal_reputation is strictly
# greater than this is NOT wanted and accrues no system pot.
SYSTEM_BOUNTY_CRIMINAL_THRESHOLD = max(SYSTEM_BOUNTY_TIERS)  # == -500

# --- WO-BN stored-pot model -------------------------------------------------
# The SYSTEM bounty is no longer recomputed on demand; it is a STORED pot per
# criminal that GROWS over time (npc_scheduler accrual sweep) and RESETS to 0
# when a hunter kills+collects. The pot lives in Player.settings JSONB (additive,
# NO migration; mirrors the per-player _daily_stipend / per-ship _passive_income
# anchor convention used by the other economy faucets).
#
# Storage keys (Player.settings):
#   system_bounty_pot         -> int credits currently owed on this criminal's head
#   system_bounty_pot_period  -> canonical-day index of the last accrual (durable
#                                idempotency anchor: a restart / duplicate wake /
#                                re-run within the same canonical day re-reads this
#                                and skips, so the pot NEVER double-accrues)
SYSTEM_BOUNTY_POT_KEY = "system_bounty_pot"
SYSTEM_BOUNTY_POT_PERIOD_KEY = "system_bounty_pot_period"

# ACCRUAL MODEL (NO-CANON — bounties.md gives the tier FIGURES but is silent on
# any growth rate; proposed conservatively and flagged for DECISIONS.md):
#   * base accrual per canonical day for a shallow criminal (-500..-749);
#   * scaled UP by a per-tier "dastardly" multiplier (more-severe criminals
#     accrue FASTER — the deeper the pit, the bigger the daily bounty bump);
#   * each criminal's pot is CAPPED at its deepest-matched tier figure, so a
#     -500 player tops out at 5,000, a -1000 player at 100,000 — the same
#     ceilings the old on-demand model paid, now reached gradually.
# Conservative: at base 250/day a -500 criminal needs ~20 canonical days to fill
# its 5,000 cap; a -1000 criminal at 4x (1,000/day) needs ~100 days to fill
# 100,000 — slow enough that the pot is never a runaway faucet.
SYSTEM_BOUNTY_BASE_ACCRUAL_PER_DAY = 250  # credits/canonical-day, shallow tier
# Per-tier dastardly multiplier on the base daily accrual (keyed by the same
# thresholds as SYSTEM_BOUNTY_TIERS — deepest matched tier wins).
SYSTEM_BOUNTY_ACCRUAL_MULTIPLIER = {
    -500: 1.0,   # Criminal:    250/day
    -750: 2.0,   # Villain low: 500/day
    -1000: 4.0,  # Villain max: 1,000/day
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

    # --- WO-BN stored system-bounty pot (Player.settings JSONB) -------------

    @staticmethod
    def get_system_bounty_pot(player: Player) -> int:
        """Read the stored system-bounty pot (credits) for this criminal.

        The pot is the GROWING-then-RESET value the accrual sweep writes and the
        kill+collect path zeroes. A non-criminal (positive/neutral rep) simply
        never accrues, so its pot stays 0. Robust to a missing/None/garbage
        stored value (treated as 0)."""
        settings = player.settings or {}
        try:
            return max(0, int(settings.get(SYSTEM_BOUNTY_POT_KEY, 0) or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _set_system_bounty_pot(player: Player, value: int) -> None:
        """Write the stored system-bounty pot (clamped >= 0) and flag the JSONB
        column dirty so SQLAlchemy persists the in-place mutation."""
        if player.settings is None:
            player.settings = {}
        player.settings[SYSTEM_BOUNTY_POT_KEY] = max(0, int(value))
        flag_modified(player, "settings")

    @staticmethod
    def is_criminal(player: Player) -> bool:
        """True if this player is wanted by the Federation — i.e. deep enough in
        negative personal reputation to carry a system bounty. Reuses the exact
        threshold the on-demand model used (``personal_reputation <= -500``), so
        WHO accrues is identical to who used to be assigned a system bounty."""
        return (player.personal_reputation or 0) <= SYSTEM_BOUNTY_CRIMINAL_THRESHOLD

    @staticmethod
    def _matched_tier(score: int) -> Optional[int]:
        """The deepest (most-negative) tier threshold this rep score has reached,
        or None if the player is not a criminal. Mirrors _get_system_bounties'
        'deepest matched tier wins' rule."""
        matched = [t for t in SYSTEM_BOUNTY_TIERS if score <= t]
        return min(matched) if matched else None

    @classmethod
    def system_bounty_pot_cap(cls, player: Player) -> int:
        """The ceiling this criminal's pot may grow to — the deepest-matched
        tier's figure (5k / 25k / 100k). 0 for a non-criminal."""
        tier = cls._matched_tier(player.personal_reputation or 0)
        return SYSTEM_BOUNTY_TIERS.get(tier, 0) if tier is not None else 0

    @classmethod
    def system_bounty_daily_accrual(cls, player: Player) -> int:
        """Credits this criminal's pot grows per canonical day — base rate scaled
        by the deepest-matched tier's dastardly multiplier. 0 for a non-criminal
        (so the accrual sweep credits nothing)."""
        tier = cls._matched_tier(player.personal_reputation or 0)
        if tier is None:
            return 0
        mult = SYSTEM_BOUNTY_ACCRUAL_MULTIPLIER.get(tier, 1.0)
        return int(SYSTEM_BOUNTY_BASE_ACCRUAL_PER_DAY * mult)

    @classmethod
    def accrue_system_bounty_pot(cls, player: Player, period: int) -> int:
        """Grow this criminal's stored pot for ``period`` (a canonical-day
        index), idempotently. Returns the credits ADDED (0 on a no-op).

        Idempotency: the durable per-player anchor
        ``settings[SYSTEM_BOUNTY_POT_PERIOD_KEY]`` records the last period
        accrued. We accrue at most ONE period's worth per call and only when the
        anchor is BEHIND ``period`` — a restart, duplicate wake, or re-run within
        the same canonical day re-reads the anchor and skips, so the pot NEVER
        double-accrues. (We deliberately do NOT back-fill multiple missed periods
        in one call: a criminal who was offline for a week shouldn't get a lump
        sum — the cap and the slow daily drip keep the faucet conservative.)

        The caller (the scheduler sweep) owns the lock on this player row and the
        commit; this method only mutates the JSONB on the locked instance."""
        settings = player.settings or {}
        try:
            last_period = int(settings.get(SYSTEM_BOUNTY_POT_PERIOD_KEY))
        except (TypeError, ValueError):
            last_period = None

        # Already accrued this (or a later) period -> idempotent no-op. We still
        # advance a stale/missing anchor below so the next period accrues cleanly.
        if last_period is not None and last_period >= period:
            return 0

        added = 0
        if cls.is_criminal(player):
            daily = cls.system_bounty_daily_accrual(player)
            cap = cls.system_bounty_pot_cap(player)
            current = cls.get_system_bounty_pot(player)
            if daily > 0 and current < cap:
                new_value = min(cap, current + daily)
                added = new_value - current
                cls._set_system_bounty_pot(player, new_value)

        # Advance the durable anchor to this period regardless of whether credits
        # were added (a criminal at cap, or a player who lapsed out of criminal
        # status, still moves the anchor forward so a single period is never
        # re-evaluated). flag_modified covers both the pot and the anchor.
        if player.settings is None:
            player.settings = {}
        player.settings[SYSTEM_BOUNTY_POT_PERIOD_KEY] = int(period)
        flag_modified(player, "settings")
        return added

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

    def cancel_bounty(
        self, placer_id: uuid.UUID, bounty_id: str, target_id: uuid.UUID
    ) -> Dict[str, Any]:
        """Cancel a still-uncollected PLAYER-placed bounty and refund the placer.

        Canon (SYSTEMS/bounty-and-reputation.md#cancellation, invariant #9):
        only the ORIGINAL PLACER may cancel; only a not-yet-collected bounty is
        cancellable; the placer is refunded the escrowed PRINCIPAL (``amount``)
        — the 10% placement fee is NON-refundable. The entry is then removed so
        it can never be collected after the refund.

        Safety (system-economy money — no inflation, no double-refund):

        * Both the placer's Player row AND the target's Player row are
          ``with_for_update``-locked before any mutation. Two concurrent paths
          that could touch the same JSONB pot — a second cancel, or a kill's
          ``collect_bounty`` (which locks the target) — serialize behind this
          lock, so the cancel either runs before the pot is cleared (refund +
          remove) or finds nothing afterwards (clean rejection).
        * The refund equals exactly the escrowed ``amount`` of the located
          entry and nothing else — system/auto (``type == "system"``) bounties
          have no stored principal, are recomputed from reputation, and are NOT
          cancellable/refundable here (they never live in the JSONB pot).
        * Double-cancel guard: a second cancel of the same ``bounty_id`` finds
          no matching entry (the first removed it / collect cleared the pot) and
          returns a clean failure WITHOUT a second credit.
        """
        # Lock placer + target rows. Acquire the target lock as well so a
        # concurrent collect_bounty (which locks the target) cannot clear the
        # pot between our read and our remove — the refund stays exact.
        placer = (
            self.db.query(Player)
            .filter(Player.id == placer_id)
            .with_for_update()
            .first()
        )
        target = (
            self.db.query(Player)
            .filter(Player.id == target_id)
            .with_for_update()
            .first()
        )

        if not placer or not target:
            return {"success": False, "message": "Player not found"}

        bounties = self._get_bounties(target)

        # Locate the entry by id. A missing entry = already cancelled, already
        # collected (pot cleared), or never existed → clean rejection, no credit.
        entry = next((b for b in bounties if str(b.get("id")) == str(bounty_id)), None)
        if entry is None:
            return {
                "success": False,
                "message": "Bounty not found or already resolved",
            }

        # Only the original placer may cancel. System bounties have
        # placed_by == "SYSTEM" and are never stored here, but guard regardless.
        if str(entry.get("placed_by")) != str(placer_id):
            return {
                "success": False,
                "message": "Only the original placer may cancel this bounty",
            }

        if entry.get("type") == "system":
            # Defensive: system bounties are never persisted to the pot, so this
            # should be unreachable — but never refund an unfunded bounty.
            return {
                "success": False,
                "message": "System bounties cannot be cancelled",
            }

        # Refund the escrowed principal only (fee is non-refundable, invariant #9).
        refund = int(entry.get("amount", 0))

        # Remove the entry FIRST so it can never be collected after the refund,
        # then credit. Both happen under the target+placer locks atomically.
        remaining = [b for b in bounties if str(b.get("id")) != str(bounty_id)]
        self._set_bounties(target, remaining)

        placer.credits += refund

        self.db.flush()

        logger.info(
            "Bounty cancelled: %s cancelled bounty %s on %s, refunded %d",
            placer_id, bounty_id, target_id, refund,
        )

        return {
            "success": True,
            "bounty_id": str(bounty_id),
            "target_id": str(target_id),
            "refund": refund,
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

        # had_bounty: did the target carry ANY bounty at all at call time? A
        # non-empty player-placed list, or a non-zero stored system pot, both
        # count. Combat uses this to distinguish "killed an innocent" (no bounty)
        # from "killed a wanted criminal" (paid out). Under WO-BN there is no
        # longer a deduped-but-present case for the SYSTEM pot — a zeroed pot
        # simply returns [] from _get_system_bounties, so an already-collected
        # criminal whose pot hasn't re-accrued reads as had_bounty False (no
        # bounty currently on the head), which is the correct player-facing truth.
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

        # --- System bounty: STORED POT, paid-then-ZEROED (WO-BN) -------------
        # The system bounty is now a STORED pot per criminal (grown over time by
        # the npc_scheduler accrual sweep, capped per tier). The kill+collect
        # pays out whatever the pot currently holds and then RESETS it to 0 — and
        # that reset IS the anti-double-collect: an emptied pot pays nothing until
        # it re-accrues, so the old per-(hunter,target) BountyClaim dedup
        # (_has_paid_system_claim) is gone for SYSTEM bounties. The target row is
        # already with_for_update-locked above, so two hunters can't both drain a
        # full pot — the kill that zeroes it first wins; the second reads 0. We
        # still record a PAID claim row for provenance (audit trail of who turned
        # in this head), but the claim no longer GATES payout.
        total_system = 0
        for b in system_bounties:
            amount = b.get("amount", 0)
            if amount <= 0:
                continue
            total_system += amount
            self._write_claim(
                claimant_id=collector_id,
                target_id=target_id,
                amount=amount,
                bounty_ref=str(b.get("id")),  # "system_pot"
                resolved_at=now,
            )
        if total_system > 0:
            # Empty the pot under the target lock — the reset is the dedup.
            self._set_system_bounty_pot(target, 0)

        total = total_player + total_system

        if total == 0:
            # No payout. Under the stored-pot model this is normally the
            # "no bounty on this head" case (had_bounty False — pot 0 and no
            # player-placed entries). The had_bounty-True-but-total-0 branch is
            # now only reachable defensively (a malformed 0-credit player-placed
            # entry); we preserve the distinction so combat can still tell an
            # innocent kill from a degenerate-but-present bounty.
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
                "Bounty collect: %s killed %s — bounty present but zero net payout",
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

        # Clear player-placed bounties (clearing the JSONB list IS their dedup).
        # The system pot was already zeroed above (its reset is ITS dedup).
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

    def collect_bounty_share(
        self,
        hunter_id: uuid.UUID,
        target_id: uuid.UUID,
        num_participants: int,
        claim_player_pot: bool,
    ) -> Dict[str, Any]:
        """Award ONE fleet member's even share of a kill's bounty (WO-C2 fleet-
        kill-attribution; updated for the WO-BN stored-pot model).

        The fleet helper calls this once per DISTINCT participating player of the
        killing fleet. A fleet kill is ONE kill, so — exactly like the solo path
        — the system pot is paid out ONCE and then RESET to 0; the pot-reset is
        the anti-double-collect (the old per-(hunter,target) ledger dedup is
        gone). Each member receives an even-split share ``pot // n`` of the
        STORED system pot, and the designated member (``claim_player_pot`` True)
        ZEROES the pot after all shares are read.

        Anti-faucet under the stored pot: the target row is locked ONCE by the
        caller for the whole loop, and the pot is zeroed exactly once by the
        designated member — so a concurrent second kill on the same criminal
        serializes behind that lock and reads a 0 pot. Collector rotation across
        alts can no longer re-mint the bounty: the pot is a single shared value
        that empties on this kill, not a per-hunter entitlement that each alt
        re-earns. The integer-floor even split means the fleet total is at most
        the pot (the floor remainder is dropped, never minted) — and may be a
        hair LESS than a solo single-kill, which is acceptable, not a bug to
        "top up".

        ORDERING CONTRACT (caller-enforced): the designated member
        (``claim_player_pot=True``) must be processed LAST, so every other member
        reads the full pre-zero pot for its share before the designated member
        zeroes it. The fleet helper designates ``idx == n - 1`` for exactly this
        reason. A defensive guard still computes the designated member's OWN share
        from the pot value BEFORE zeroing, so even a mis-ordered caller never
        shorts the designated member itself.

        Two pots:

        * SYSTEM pot — STORED, even-split per member, zeroed once (above).
        * PLAYER-placed pot — pay-once-then-cleared (clearing the JSONB list IS
          its dedup). Exactly ONE member (``claim_player_pot`` True) claims the
          whole player-placed pot as an even-split share and clears it; the
          others get a 0 player-placed share. Each paid member records a PAID
          claim row for provenance (the claim no longer GATES payout).

        Locks ONLY this member's Player row (``with_for_update``) before
        crediting. The target row is locked once by the caller (the fleet helper).

        Returns ``{paid, system_paid, player_paid, had_bounty, new_credits}``.
        ``paid`` > 0 ⇒ heroic bounty kill (caller awards the +100 rep);
        ``had_bounty`` reflects whether the target carried ANY bounty at call
        time (so the caller can distinguish innocent-slaughter from a clean kill
        of a criminal whose pot is empty, exactly as the solo path does).
        """
        n = max(1, int(num_participants))

        # Lock THIS member's row before any credit mutation (lost-update guard).
        hunter = (
            self.db.query(Player)
            .filter(Player.id == hunter_id)
            .with_for_update()
            .first()
        )
        # Target is read (and, on the pot zero / player-pot clear, mutated). The
        # caller has already locked the target row; re-query without a redundant
        # lock.
        target = self.db.query(Player).filter(Player.id == target_id).first()

        if not hunter or not target:
            return {
                "success": False,
                "message": "Player not found",
                "paid": 0,
                "system_paid": 0,
                "player_paid": 0,
                "had_bounty": False,
            }

        player_bounties = self._get_bounties(target)
        system_bounties = self._get_system_bounties(target)
        had_bounty = bool(player_bounties) or bool(system_bounties)

        now = datetime.now(UTC)

        # --- SYSTEM pot: this member's even share of the STORED pot ------------
        # Read the pot value (same for every member until the designated member
        # zeroes it LAST). Pay pot // n; record a provenance claim row.
        system_paid = 0
        for b in system_bounties:
            amount = b.get("amount", 0)
            # Even split per distinct participating player. Integer floor; we do
            # NOT chase the remainder across members (no top-up to match solo —
            # the floor remainder stays in nobody's pocket, never minted).
            share = amount // n
            if share <= 0:
                continue
            system_paid += share
            self._write_claim(
                claimant_id=hunter_id,
                target_id=target_id,
                amount=share,
                bounty_ref=str(b.get("id")),  # "system_pot"
                resolved_at=now,
            )
        # The designated member ZEROES the stored system pot once, AFTER reading
        # its own share above — the reset is the anti-double-collect.
        if claim_player_pot and system_bounties:
            self._set_system_bounty_pot(target, 0)

        # --- PLAYER-placed pot: claimed once by the designated member only ------
        player_paid = 0
        if claim_player_pot and player_bounties:
            for b in player_bounties:
                amount = b.get("amount", 0)
                share = amount // n
                if share <= 0:
                    continue
                player_paid += share
                self._write_claim(
                    claimant_id=hunter_id,
                    target_id=target_id,
                    amount=share,
                    bounty_ref=str(b.get("id")),
                    resolved_at=now,
                )
            # Pay-once-then-clear: the JSONB list is the dedup for player-placed
            # bounties, so clear it now that the designated member has claimed it.
            self._set_bounties(target, [])

        total = system_paid + player_paid
        if total > 0:
            hunter.credits += total

        # Flush within the caller's locked transaction (caller owns the commit).
        self.db.flush()

        if total > 0:
            logger.info(
                "Fleet bounty share: %s collected %d (system=%d player=%d) from %s",
                hunter_id, total, system_paid, player_paid, target_id,
            )

        return {
            "success": True,
            "hunter_id": str(hunter_id),
            "target_id": str(target_id),
            "had_bounty": had_bounty,
            "paid": total,
            "system_paid": system_paid,
            "player_paid": player_paid,
            "new_credits": hunter.credits,
        }

    # NOTE (WO-BN): the former ``_has_paid_system_claim`` per-(hunter,target)
    # SYSTEM-bounty dedup is GONE — the stored-pot RESET (collect zeroes the pot;
    # an emptied pot pays nothing until it re-accrues) replaces it. ``_write_claim``
    # below is retained: it still records PAID provenance rows for both system and
    # player-placed payouts (audit trail of who turned in which head), but a claim
    # row no longer GATES any payout.

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
        """Return the criminal's CURRENT system bounty from the STORED, GROWING
        pot (WO-BN) — no longer recomputed on demand from reputation.

        The pot is grown over time by the npc_scheduler accrual sweep
        (``accrue_system_bounty_pot``, scaled by severity, capped per tier) and
        ZEROED on a successful kill+collect. So this read reflects exactly what
        the Federation currently owes on this head: a freshly-claimed (zeroed)
        pot returns NOTHING until it re-accrues, and that pot-reset — not a
        per-(hunter,target) ledger — is now the anti-double-collect (collect
        pays the pot, then empties it).

        Returns a single-entry list (mirroring the prior shape so every caller —
        get_bounties_on_player / collect_bounty / collect_bounty_share /
        get_available_bounties — keeps working unchanged) ONLY when the stored
        pot is > 0; an empty pot returns [] exactly as a non-criminal used to.
        The entry ``id`` is the STABLE per-criminal ``system_<id>`` (used as the
        BountyClaim.bounty_ref provenance tag), no longer the tier-threshold
        string — the pot, not the tier, is now the unit of payout.
        """
        pot = self.get_system_bounty_pot(target)
        if pot <= 0:
            return []
        return [{
            "id": "system_pot",
            "placed_by": "SYSTEM",
            "placed_by_name": "Federation Bounty Board",
            "amount": pot,
            "type": "system",
            "reason": f"Criminal reputation ({target.personal_reputation})",
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
