"""
Bounty Service

Player-placed and system-generated bounties.
Uses Player.settings["bounties"] JSONB — no new database table required.
"""

import logging
import uuid
from datetime import datetime, timedelta, UTC
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.bounty_claim import BountyClaim, BountyClaimStatus
from src.models.faction import Faction, FactionType
from src.models.reputation import Reputation, ReputationLevel

logger = logging.getLogger(__name__)

BOUNTY_MIN_AMOUNT = 1000
BOUNTY_PLACEMENT_FEE = 0.10  # 10% fee

# Optional bounty expiry (bounty-and-reputation.md 📐 "optional expiry
# timestamp on placement (expires_at); auto-refund-minus-fee on expiry" —
# design-only, no ratified duration; canon's own example, "a 48-hour
# vendetta," is illustrative not normative). Opt-in only: place_bounty's
# default (duration_days=None) sets no expires_at, preserving the documented
# baseline "bounties do NOT auto-expire." NO-CANON bounds pending a
# DECISIONS.md magnitude ruling — conservative clamp so a placer can't set
# an effectively-permanent or effectively-instant expiry.
BOUNTY_MIN_DURATION_DAYS = 1
BOUNTY_MAX_DURATION_DAYS = 90

# Soft cap on a target's bounty-entry list (bounty-and-reputation.md:192,
# ratified number — "50 entries"). Collapse (not deletion) via
# collapse_excess_bounties: over-cap entries merge per-placer, summing
# amount, into one entry each — no credits lost, list length bounded.
BOUNTY_SOFT_CAP_ENTRIES = 50

# System-generated bounty thresholds based on personal reputation. These define
# WHO the Federation wants (a player must be at or below the shallowest tier,
# -500, to accrue any system bounty) and the per-tier ACCRUAL CAP — the deepest
# matched tier sets the ceiling the stored pot grows toward. (Previously these
# were instantaneous bounty values recomputed on every kill; under WO-BN the pot
# is STORED and GROWS over time, so a tier's figure is now the cap, not the
# constant payout.)
#
# WO-DBB-EC1 (canon §1.3, lifecycle.md): "Federation Bounty Board payouts are
# minted ... Target: 5,000–250,000 cr per kill scaling with target's personal-rep
# tier." This per-criminal tier ceiling IS the Federation payout scale — the
# stored pot grows toward, and a kill pays out, the deepest-matched tier's figure,
# so the maximum a Federation kill can mint now scales monotonically from 5,000
# (shallowest criminal tier) up to the canon band's 250,000 ceiling (deepest).
# PLAYER-PLACED (zero-sum) bounties are untouched by this table — they pay their
# own escrowed `amount` from Player.settings["bounties"].
#
# Canon §1.3 gives the 5,000–250,000 band; the intermediate rung (-750 ->
# 75,000) is a monotonic interpolation across the three existing criminal
# thresholds, anchored to the canon endpoints (5,000 at the shallowest tier,
# 250,000 at the deepest). Ratified as canon 2026-08-09 (DECISIONS.md
# `bounty-pot-cap-750-interpolation`).
SYSTEM_BOUNTY_TIERS = {
    -500: 5000,     # Criminal: pot caps at 5,000 credits (canon band floor)
    -750: 75000,    # Villain low: pot caps at 75,000 credits (ratified interp)
    -1000: 250000,  # Villain max: pot caps at 250,000 credits (canon band ceiling)
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
#   * each criminal's pot is CAPPED at its deepest-matched tier figure (the
#     WO-DBB-EC1 canon §1.3 payout scale), so a -500 player tops out at 5,000,
#     a -1000 player at 250,000 — reached gradually by the daily drip.
# Conservative: at base 250/day a -500 criminal needs ~20 canonical days to fill
# its 5,000 cap; a -1000 criminal at 4x (1,000/day) needs ~250 days to fill its
# 250,000 cap — slow enough that the pot is never a runaway faucet (the cap, not
# the drip rate, was raised by WO-DBB-EC1; the accrual multipliers below are the
# pre-existing NO-CANON growth model, unchanged).
SYSTEM_BOUNTY_BASE_ACCRUAL_PER_DAY = 250  # credits/canonical-day, shallow tier
# Per-tier dastardly multiplier on the base daily accrual (keyed by the same
# thresholds as SYSTEM_BOUNTY_TIERS — deepest matched tier wins).
SYSTEM_BOUNTY_ACCRUAL_MULTIPLIER = {
    -500: 1.0,   # Criminal:    250/day
    -750: 2.0,   # Villain low: 500/day
    -1000: 4.0,  # Villain max: 1,000/day
}

# --- Faction-issued bounties (bounties.md:26, 📐 Design-only until this WO) -
# "The Federation putting a bounty on a specific pirate captain that pays out
# only to faction members." Distinct from the player-placed pot (target is a
# PLAYER, escrowed in Player.settings) and the system pot (auto-accrued from
# personal_reputation): a faction bounty is PLACED by faction fiat on an NPC
# pirate captain (NPCCharacter — HOSTILE_RAIDER archetype), not a player, so it
# cannot live in Player.settings["bounties"] (no Player row for an NPC). Reuses
# NPCCharacter.backstory JSONB (schema:no — no new column, no new table),
# mirroring the single-value system-bounty-pot pattern rather than a list: one
# active faction bounty per NPC at a time.
#
# Storage key (NPCCharacter.backstory):
#   faction_bounty -> {faction_type, amount, reason, placed_at}
FACTION_BOUNTY_KEY = "faction_bounty"

# Payout gated on the collector's OWN standing with the issuing faction being
# at least RECOGNIZED — mirrors contraband_service.GATE_MIN_LEVEL exactly (the
# established "faction membership" proxy in this codebase: there is no
# discrete membership row, only a Reputation tier per faction). A collector
# below the gate still gets the kill; they simply don't get paid — "pays out
# only to faction members" per bounties.md.
FACTION_BOUNTY_GATE_LEVEL = ReputationLevel.RECOGNIZED

# Ordered rank table — same values as contraband_service._level_rank (kept as
# a local copy rather than a cross-module import: a lightweight ordinal
# lookup, not shared business logic, and bounty_service should not depend on
# contraband_service for it).
_REPUTATION_LEVEL_RANK = {
    ReputationLevel.PUBLIC_ENEMY: -8,
    ReputationLevel.CRIMINAL: -7,
    ReputationLevel.OUTLAW: -6,
    ReputationLevel.PIRATE: -5,
    ReputationLevel.SMUGGLER: -4,
    ReputationLevel.UNTRUSTWORTHY: -3,
    ReputationLevel.SUSPICIOUS: -2,
    ReputationLevel.QUESTIONABLE: -1,
    ReputationLevel.NEUTRAL: 0,
    ReputationLevel.RECOGNIZED: 1,
    ReputationLevel.ACKNOWLEDGED: 2,
    ReputationLevel.TRUSTED: 3,
    ReputationLevel.RESPECTED: 4,
    ReputationLevel.VALUED: 5,
    ReputationLevel.HONORED: 6,
    ReputationLevel.REVERED: 7,
    ReputationLevel.EXALTED: 8,
}


def place_faction_bounty(
    db: Session, npc, faction_type: FactionType, amount: int, reason: str,
) -> Dict[str, Any]:
    """Place (or replace) a faction-issued bounty on an NPC pirate captain.

    One active faction bounty per NPC — a second placement overwrites the
    first rather than stacking (mirrors the system-bounty-pot's single-value
    shape, not the player-placed list's append shape, since there's exactly
    one issuing faction per call and no per-placer escrow to track)."""
    if amount < BOUNTY_MIN_AMOUNT:
        return {"success": False, "message": f"Minimum bounty is {BOUNTY_MIN_AMOUNT} credits"}

    backstory = dict(npc.backstory or {})
    backstory[FACTION_BOUNTY_KEY] = {
        "faction_type": faction_type.value,
        "amount": int(amount),
        "reason": reason,
        "placed_at": datetime.now(UTC).isoformat(),
    }
    npc.backstory = backstory
    flag_modified(npc, "backstory")

    logger.info(
        "Faction bounty placed: %s put %d on NPC %s (%s)",
        faction_type.value, amount, npc.id, reason,
    )
    return {
        "success": True,
        "npc_id": str(npc.id),
        "faction_type": faction_type.value,
        "amount": int(amount),
    }


def _collector_passes_faction_gate(db: Session, collector: Player, faction_type: FactionType) -> bool:
    """True iff effective standing with the issuing faction is at least
    FACTION_BOUNTY_GATE_LEVEL (RECOGNIZED). Soft-ORDER #1964: teamed collectors
    use ``resolve_effective_faction_standing_value`` (team aggregate); solo
    still personal. Missing faction fails closed. Missing standing reads as
    value 0 (NEUTRAL) via the resolver — below gate. Tier compare maps the
    continuous value through ``FactionService._calculate_reputation_level``
    then ordinal-rank (same pattern as Fringe Soft-ORDER #1971)."""
    from src.services.faction_service import (
        FactionService,
        resolve_effective_faction_standing_value,
    )

    faction = db.query(Faction).filter(Faction.faction_type == faction_type).first()
    if faction is None:
        return False
    value, _source = resolve_effective_faction_standing_value(
        db, collector.id, faction.id, team_id=collector.team_id
    )
    level = FactionService(db)._calculate_reputation_level(value)
    return (
        _REPUTATION_LEVEL_RANK.get(level, -99)
        >= _REPUTATION_LEVEL_RANK[FACTION_BOUNTY_GATE_LEVEL]
    )


def collect_faction_bounty(db: Session, npc, collector: Player) -> Optional[Dict[str, Any]]:
    """Pay out and clear an NPC's faction bounty on kill, iff the collector
    passes the issuing faction's standing gate.

    Returns None when the NPC carries no faction bounty (nothing to do — the
    caller's kill-resolution flow should not treat this as an error). Returns
    a dict with ``paid: 0`` (bounty existed but the collector failed the
    gate — bounty is left standing, uncleared, for a future eligible hunter)
    or ``paid: <amount>`` (gate passed — bounty cleared, credited)."""
    backstory = npc.backstory or {}
    entry = backstory.get(FACTION_BOUNTY_KEY)
    if not entry:
        return None

    try:
        faction_type = FactionType(entry.get("faction_type"))
    except ValueError:
        logger.error("Faction bounty on NPC %s has an unrecognized faction_type %r", npc.id, entry.get("faction_type"))
        return None
    amount = int(entry.get("amount", 0) or 0)

    if not _collector_passes_faction_gate(db, collector, faction_type):
        logger.info(
            "Faction bounty on NPC %s NOT paid: collector %s below %s standing with %s",
            npc.id, collector.id, FACTION_BOUNTY_GATE_LEVEL.name, faction_type.value,
        )
        return {"success": True, "paid": 0, "faction_type": faction_type.value, "gate_passed": False}

    if amount > 0:
        collector.credits += amount

    new_backstory = dict(backstory)
    del new_backstory[FACTION_BOUNTY_KEY]
    npc.backstory = new_backstory
    flag_modified(npc, "backstory")

    logger.info(
        "Faction bounty collected: %s paid %d to %s for NPC %s (%s)",
        faction_type.value, amount, collector.id, npc.id, entry.get("reason"),
    )
    return {
        "success": True,
        "paid": amount,
        "faction_type": faction_type.value,
        "gate_passed": True,
        "new_credits": collector.credits,
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

    def total_active_bounty_on(self, player: Player) -> int:
        """Total credits currently on this player's head (WO-PLAYERINFO id=142):
        the summed amounts of active PLAYER-placed bounties (escrowed in
        Player.settings["bounties"]) PLUS the system-bounty pot (0 for a
        non-criminal). Read-only; robust to missing/garbage entries."""
        placed = 0
        for b in self._get_bounties(player):
            try:
                placed += int(b.get("amount", 0) or 0)
            except (TypeError, ValueError):
                continue
        return max(0, placed) + self.get_system_bounty_pot(player)

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

    def _restore_target_rep_after_system_payout(self, target: Player) -> None:
        """Rehabilitate a criminal's reputation the moment their SYSTEM bounty
        pot actually pays out (WO-INTEGRITY-PAIR NH2 — bounty-collusion faucet).

        Before this fix, killing a target never touched the TARGET's own
        reputation — only the collector's. A criminal pinned at a deep negative
        score (e.g. two colluding players, one always the "wanted" accomplice)
        would sit at ``is_criminal() == True`` forever, so the accrual sweep
        kept re-filling their pot on the same schedule after every collection —
        a slow-but-*permanent* faucet requiring zero further "crime" after the
        initial rep tank. Approach (a) from the WO: restore the target's rep on
        collection so the SAME target can't keep generating bounties.

        Raises ``personal_reputation`` to exactly one point above the criminal
        threshold (``SYSTEM_BOUNTY_CRIMINAL_THRESHOLD + 1``, i.e. -499) — the
        MINIMAL restore that flips ``is_criminal()`` False and stops further
        accrual, "debt paid" rather than a full wipe to neutral. Monotonic: only
        ever RAISES reputation (never lowers it) and no-ops if the target is
        already clear, so this can never be abused to lower anyone's score or
        double-apply across the two call sites (collect_bounty /
        collect_bounty_share) — a target already restored simply reads > the
        threshold and the guard below skips.

        NO-CANON: bounties.md is silent on any reputation effect of being
        bounty-killed; the exact floor (threshold + 1, not a full reset to 0)
        is a conservative design choice — flagged for DECISIONS.md. Applies
        uniformly to legitimate bounty hunting too (a criminal genuinely brought
        to justice also has their case "closed"), which is intentional and not
        considered a legit-path regression.
        """
        current = target.personal_reputation or 0
        if current > SYSTEM_BOUNTY_CRIMINAL_THRESHOLD:
            return  # already clear — nothing to restore
        from src.services.personal_reputation_service import PersonalReputationService
        delta = (SYSTEM_BOUNTY_CRIMINAL_THRESHOLD + 1) - current
        PersonalReputationService(self.db).adjust_reputation(
            target.id, delta, "bounty_collected_rehabilitation"
        )

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
        tier's figure (5k / 75k / 250k — the WO-DBB-EC1 canon §1.3 payout scale).
        0 for a non-criminal."""
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

    def _load_two_players_for_update(
        self, id_a: uuid.UUID, id_b: uuid.UUID,
    ):
        """WO-ECON-BOUNTY-DUAL-LOCK-ORDER: lock two distinct Player rows
        for a single operation that touches both (cancel_bounty's
        placer+target, collect_bounty's collector+target) in a
        CONSISTENT order — ascending by id — regardless of which one is
        the semantic "first" party. Mirrors contract_service._load_two_
        players_for_update exactly (same reasoning, same shape — see that
        function's own docstring): without this, two concurrent
        operations that both need to lock the SAME pair of players (e.g.
        player X cancelling a bounty they placed on player Y, racing
        player Y's kill of player X collecting a bounty ON player X)
        could acquire the pair in opposite order and deadlock. BOTH
        dual-lock sites in this class funnel through this one method, so
        any two concurrent callers touching the same pair always agree on
        which row to lock first — including one cancel_bounty call racing
        one collect_bounty call on the SAME pair, not just two calls to
        the same method.

        Pure lock-ORDER fix — no credit/refund amount or business logic
        changes anywhere in this file.

        WO-BOUNTY-COLLECT-FLUSH: every lock query below also carries
        ``.populate_existing()`` — mirrors contract_service._load_player's
        ``for_update=True`` branch (its ``_load_two_players_for_update``
        twin routes ALL three lock cases, including the equal-id one,
        through that same for_update=True helper). Without it, a caller
        that already holds an UNLOCKED, identity-mapped copy of one of
        these players (route-level ``get_current_player`` in cancel_bounty's
        case) would have this with_for_update() re-read return the STALE
        cached instance instead of the fresh locked row — a lost-update on
        any RMW the caller performs after this call returns (cancel_bounty's
        ``placer.credits += refund``). This is safe everywhere it's called
        from in this file: cancel_bounty locks BEFORE any mutation (nothing
        pending to discard), and collect_bounty's caller (attack_player)
        now flushes its own pending in-memory mutations immediately before
        calling this helper (see collect_bounty), so populate_existing's
        re-read picks those up fresh rather than discarding them."""
        if id_a == id_b:
            player = self.db.query(Player).filter(Player.id == id_a).populate_existing().with_for_update().first()
            return player, player
        if id_a < id_b:
            player_a = self.db.query(Player).filter(Player.id == id_a).populate_existing().with_for_update().first()
            player_b = self.db.query(Player).filter(Player.id == id_b).populate_existing().with_for_update().first()
        else:
            player_b = self.db.query(Player).filter(Player.id == id_b).populate_existing().with_for_update().first()
            player_a = self.db.query(Player).filter(Player.id == id_a).populate_existing().with_for_update().first()
        return player_a, player_b

    def place_bounty(
        self,
        placer_id: uuid.UUID,
        target_id: uuid.UUID,
        amount: int,
        duration_days: Optional[int] = None,
        fee_pct: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Place a bounty on a target player. Placer pays amount + fee.

        ``fee_pct`` overrides the standard ``BOUNTY_PLACEMENT_FEE`` (10%) when
        given -- used by ship_registry_service's auto-placed stolen-report
        bounty, which per ship-registry.md "Reporting a ship stolen" waives
        the placement fee ("the owner has already lost the use of their
        ship; the registry doesn't double-tax"). Defaults to the standard
        fee for every other caller -- zero behavior change otherwise.

        ``duration_days`` is optional (default None = no expiry, the
        documented baseline). When given, must be within
        [BOUNTY_MIN_DURATION_DAYS, BOUNTY_MAX_DURATION_DAYS]; the bounty
        entry then carries an ``expires_at`` the sweep (``expire_due_
        bounties``) auto-cancels-and-refunds once passed."""
        if amount < BOUNTY_MIN_AMOUNT:
            return {
                "success": False,
                "message": f"Minimum bounty is {BOUNTY_MIN_AMOUNT} credits",
            }

        if duration_days is not None and not (
            BOUNTY_MIN_DURATION_DAYS <= duration_days <= BOUNTY_MAX_DURATION_DAYS
        ):
            return {
                "success": False,
                "message": (
                    f"duration_days must be between {BOUNTY_MIN_DURATION_DAYS} "
                    f"and {BOUNTY_MAX_DURATION_DAYS}"
                ),
            }

        # Lock placer AND target rows, in ASCENDING-ID order — deterministic,
        # matching cancel_bounty/collect_bounty's dual-lock convention (see
        # _load_two_players_for_update) so no two concurrent bounty
        # operations touching the same pair of players can acquire the pair
        # in opposite order and deadlock.
        # WO-MONEY-REREAD-SERVICES: placer was already loaded unlocked by the
        # route's get_current_player dependency on this same session;
        # populate_existing() forces its lock to re-read live credits rather
        # than returning the stale identity-mapped instance. target is
        # freshly loaded here, so no staleness risk — plain with_for_update()
        # suffices.
        if placer_id < target_id:
            placer = self.db.query(Player).filter(Player.id == placer_id).populate_existing().with_for_update().first()
            target = self.db.query(Player).filter(Player.id == target_id).with_for_update().first()
        else:
            target = self.db.query(Player).filter(Player.id == target_id).with_for_update().first()
            placer = self.db.query(Player).filter(Player.id == placer_id).populate_existing().with_for_update().first()

        if not placer or not target:
            return {"success": False, "message": "Player not found"}

        if placer_id == target_id:
            return {"success": False, "message": "Cannot place a bounty on yourself"}

        # ADR-0055 S-V3 / bounty-and-reputation.md "Bounty uniqueness": one
        # active bounty per (placer, target) pair. A single placer cannot
        # stack a second active bounty on the same target while their first
        # is still outstanding; DISTINCT placers each having their own
        # active bounty on the same target is intentional (ADR-0054 X-V2
        # stacking-pressure design) and is NOT blocked here. "Active" ==
        # "still present in the target's JSONB bounty list" — cancel_bounty/
        # collect_bounty/expire_due_bounties all REMOVE an entry the moment
        # it resolves (no separate resolved/cancelled/expired marker), so a
        # plain membership check is sufficient and matches every other
        # invalidation path in this file.
        existing_bounties = self._get_bounties(target)
        if any(str(b.get("placed_by")) == str(placer_id) for b in existing_bounties):
            return {
                "success": False,
                "message": "You already have an active bounty on this target",
            }

        fee = int(amount * (BOUNTY_PLACEMENT_FEE if fee_pct is None else fee_pct))
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
            "expires_at": (
                (datetime.now(UTC) + timedelta(days=duration_days)).isoformat()
                if duration_days is not None
                else None
            ),
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
            "expires_at": bounty_entry["expires_at"],
        }

    def cancel_bounty(
        self, placer_id: uuid.UUID, bounty_id: str, target_id: uuid.UUID,
        refund_pct: float = 1.0,
    ) -> Dict[str, Any]:
        """Cancel a still-uncollected PLAYER-placed bounty and refund the placer.

        ``refund_pct`` overrides the default 100%-of-principal refund --
        used by ship_registry_service's retract-stolen-report flow, whose
        75%/0% timing-dependent refund schedule (ship-registry.md "Reporting
        a ship stolen" retract paragraph) differs from this method's
        cancellation invariant #9.

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
        # WO-ECON-BOUNTY-DUAL-LOCK-ORDER: acquired in ascending-id order via
        # the shared helper (not placer-then-target unconditionally) so this
        # can never deadlock against collect_bounty locking the SAME pair in
        # the opposite role order.
        placer, target = self._load_two_players_for_update(placer_id, target_id)

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

        # Refund the escrowed principal only (fee is non-refundable, invariant #9),
        # scaled by refund_pct for callers with a partial-refund schedule.
        refund = int(int(entry.get("amount", 0)) * refund_pct)

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

    def expire_due_bounties(self, now: Optional[datetime] = None) -> Dict[str, int]:
        """Auto-cancel every PLAYER-placed bounty past its optional
        ``expires_at`` and refund the placer (bounty-and-reputation.md 📐
        "auto-refund-minus-fee on expiry") — the sweep counterpart to
        ``cancel_bounty``, triggered by the clock instead of the placer.

        Refund equals the escrowed ``amount`` only (the 10% placement fee
        was already taken and stays non-refundable, matching cancel's
        invariant #9 exactly — "refund-minus-fee" describes that the fee
        is never returned, not a further deduction off the principal).

        Scans all active players' JSONB bounty lists (mirrors
        ``get_available_bounties``'s existing full-scan pattern — no GIN
        index on ``Player.settings`` exists to query this server-side).
        Entries with no ``expires_at`` (the default — "do NOT auto-expire")
        are untouched. System (``type == "system"``) entries never carry
        ``expires_at`` and are skipped defensively. Caller commits."""
        now = now or datetime.now(UTC)
        result = {"expired": 0, "total_refunded": 0}

        targets = self.db.query(Player).filter(Player.is_active == True).all()  # noqa: E712
        for target in targets:
            bounties = self._get_bounties(target)
            if not bounties:
                continue

            due = []
            kept = []
            for entry in bounties:
                expires_at_raw = entry.get("expires_at")
                if entry.get("type") == "system" or not expires_at_raw:
                    kept.append(entry)
                    continue
                try:
                    expires_at = datetime.fromisoformat(str(expires_at_raw).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    kept.append(entry)
                    continue
                if expires_at <= now:
                    due.append(entry)
                else:
                    kept.append(entry)

            if not due:
                continue

            self._set_bounties(target, kept)

            for entry in due:
                try:
                    placer_id = uuid.UUID(str(entry.get("placed_by")))
                except (ValueError, TypeError):
                    logger.error(
                        "expire_due_bounties: bounty %s on %s has an unresolvable "
                        "placed_by (%r) — refund skipped",
                        entry.get("id"), target.id, entry.get("placed_by"),
                    )
                    continue
                placer = (
                    self.db.query(Player)
                    .filter(Player.id == placer_id)
                    .with_for_update()
                    .first()
                )
                if placer is None:
                    continue
                refund = int(entry.get("amount", 0))
                placer.credits += refund
                result["expired"] += 1
                result["total_refunded"] += refund
                logger.info(
                    "Bounty expired: %s on %s, refunded %d to placer %s",
                    entry.get("id"), target.id, refund, placer_id,
                )

        self.db.flush()
        return result

    def admin_force_cancel_bounty(
        self, target_id: uuid.UUID, bounty_id: str
    ) -> Dict[str, Any]:
        """Admin-only force-cancel of a stuck bounty (bounty-and-reputation.md:190
        — "Bounty placed on player who deletes account: bounty stays attached to
        deleted target's settings; mark unclaimable; admin tool refunds
        placers"). Unlike ``cancel_bounty``, this does NOT require the caller to
        be the original placer — any admin invoking this (via the RBAC-gated
        route) may force-cancel any player-placed bounty on ``target_id``.

        Refund mirrors ``cancel_bounty`` exactly (escrowed ``amount`` only, fee
        non-refundable). If the placer's Player row no longer resolves (also
        deleted, or a corrupted ``placed_by``), the entry is still removed
        (unstuck) but the refund is skipped and logged — the canon scenario is
        explicitly "target" deletion, but this guards the symmetric case too
        rather than leaving the entry unremovable."""
        target = self.db.query(Player).filter(Player.id == target_id).with_for_update().first()
        if not target:
            return {"success": False, "message": "Target player not found"}

        bounties = self._get_bounties(target)
        entry = next((b for b in bounties if str(b.get("id")) == str(bounty_id)), None)
        if entry is None:
            return {"success": False, "message": "Bounty not found or already resolved"}

        if entry.get("type") == "system":
            return {"success": False, "message": "System bounties cannot be force-cancelled"}

        remaining = [b for b in bounties if str(b.get("id")) != str(bounty_id)]
        self._set_bounties(target, remaining)

        refund = int(entry.get("amount", 0))
        placer_id_raw = entry.get("placed_by")
        placer = None
        try:
            placer_id = uuid.UUID(str(placer_id_raw))
            placer = self.db.query(Player).filter(Player.id == placer_id).with_for_update().first()
        except (ValueError, TypeError):
            placer_id = None

        refunded = False
        if placer is not None:
            placer.credits += refund
            refunded = True
        else:
            logger.error(
                "admin_force_cancel_bounty: bounty %s on %s had an unresolvable "
                "placer (%r) — entry removed, %d credits NOT refunded",
                bounty_id, target_id, placer_id_raw, refund,
            )

        self.db.flush()

        logger.info(
            "Admin force-cancelled bounty %s on %s (placer %s, refund %d, refunded=%s)",
            bounty_id, target_id, placer_id_raw, refund, refunded,
        )

        return {
            "success": True,
            "bounty_id": str(bounty_id),
            "target_id": str(target_id),
            "refund": refund,
            "refunded": refunded,
        }

    def collapse_excess_bounties(self, target_id: uuid.UUID) -> Dict[str, Any]:
        """Soft-cap collapse (bounty-and-reputation.md:192 — "Bounty list grows
        unbounded (many small placements): soft cap 50 entries; older entries
        collapsed by placer (sum amounts under one entry)"). No expiry/refund
        happens here — this only compacts the JSONB list so it can't grow
        unbounded; every credit stays escrowed exactly as before, just
        regrouped. System entries are never counted toward the cap (they don't
        live in the JSONB list) and are left untouched.

        Idempotent: a target already at or under the cap is a no-op."""
        target = self.db.query(Player).filter(Player.id == target_id).with_for_update().first()
        if not target:
            return {"success": False, "message": "Target player not found"}

        bounties = self._get_bounties(target)
        if len(bounties) <= BOUNTY_SOFT_CAP_ENTRIES:
            return {
                "success": True,
                "target_id": str(target_id),
                "collapsed": 0,
                "entry_count": len(bounties),
            }

        # Oldest-first by placed_at (entries without a parseable timestamp sort
        # first — treated as oldest, the conservative choice for what to collapse).
        def _sort_key(b: Dict[str, Any]) -> str:
            return str(b.get("placed_at") or "")

        ordered = sorted(bounties, key=_sort_key)
        overflow_count = len(ordered) - BOUNTY_SOFT_CAP_ENTRIES
        to_collapse = ordered[:overflow_count]
        kept = ordered[overflow_count:]

        by_placer: Dict[str, Dict[str, Any]] = {}
        for entry in to_collapse:
            placer_id = str(entry.get("placed_by"))
            bucket = by_placer.setdefault(placer_id, {
                "id": str(uuid.uuid4()),
                "placed_by": placer_id,
                "placed_by_name": entry.get("placed_by_name", "Anonymous"),
                "amount": 0,
                "placed_at": entry.get("placed_at"),
                "type": "player",
                "expires_at": None,  # a collapsed entry drops any individual expiry
            })
            bucket["amount"] += int(entry.get("amount", 0))
            # Keep the EARLIEST placed_at among the collapsed entries for this placer.
            if entry.get("placed_at") and (
                not bucket["placed_at"] or entry["placed_at"] < bucket["placed_at"]
            ):
                bucket["placed_at"] = entry["placed_at"]

        collapsed_entries = list(by_placer.values())
        self._set_bounties(target, kept + collapsed_entries)
        self.db.flush()

        logger.info(
            "Collapsed %d bounty entries into %d on target %s (soft cap %d)",
            len(to_collapse), len(collapsed_entries), target_id, BOUNTY_SOFT_CAP_ENTRIES,
        )

        return {
            "success": True,
            "target_id": str(target_id),
            "collapsed": len(to_collapse),
            "collapsed_into": len(collapsed_entries),
            "entry_count": len(kept) + len(collapsed_entries),
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
        # WO-BOUNTY-COLLECT-FLUSH: combat_service.attack_player mutates
        # attacker/defender IN-MEMORY (quantum-wallet loot transfer, drone
        # counts, ship-destruction swap) before calling this method, on a
        # session opened autoflush=False (core/database.py:19) — none of
        # that is persisted yet. _load_two_players_for_update below now
        # carries .populate_existing() (closes cancel_bounty's stale-placer
        # lost-update), which would otherwise DISCARD those unflushed
        # combat mutations on the locked re-read. Flushing here, immediately
        # before the lock call, persists them first so the populate_existing
        # re-read picks them up fresh instead of clobbering them. Same
        # transaction — attack_player still owns the eventual commit — so
        # this is not a premature commit, only an earlier flush.
        self.db.flush()

        # Lock both rows to prevent double-collection race condition.
        # WO-ECON-BOUNTY-DUAL-LOCK-ORDER: acquired in ascending-id order via
        # the shared helper (not collector-then-target unconditionally) so
        # this can never deadlock against cancel_bounty locking the SAME
        # pair in the opposite role order.
        collector, target = self._load_two_players_for_update(collector_id, target_id)

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
        # These are pay-once-then-cleared (paid entries are wiped below), so
        # no ledger dedup is needed for them — clearing the JSONB is the
        # dedup.
        #
        # Same-team collusion block (ADR-0055 S-F1, bounty-collection half):
        # a collector sharing a team with the entry's placer does NOT get
        # paid for that entry — cross-team hunting still works, same-team
        # laundering is blocked. The entry is left standing (escrow held,
        # untouched) rather than paid or removed, so the placer can still
        # retract it or a non-team-mate hunter can still collect it later.
        total_player = 0
        withheld_player_bounties = []
        for b in player_bounties:
            placed_by = b.get("placed_by")
            collector_team_id = getattr(collector, "team_id", None)
            if placed_by and collector_team_id is not None:
                placer_team = (
                    self.db.query(Player.team_id).filter(Player.id == placed_by).first()
                )
                if placer_team is not None and placer_team[0] is not None and placer_team[0] == collector.team_id:
                    withheld_player_bounties.append(b)
                    continue
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
            # Close the collusion faucet (WO-INTEGRITY-PAIR NH2): a paid-out
            # system bounty also rehabilitates the target's reputation, so the
            # same criminal cannot sit at a deeply-negative score and keep
            # regenerating a pot for a colluding "hunter" to farm forever.
            self._restore_target_rep_after_system_payout(target)

        total_raw = total_player + total_system

        if total_raw == 0:
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

        # Lifecycle balancing lever — global faucet throttle (in-process).
        from src.services.economy_balancing_levers import apply_bounty_payout_ratio

        total = apply_bounty_payout_ratio(total_raw)

        # Award credits
        collector.credits += total

        # Clear PAID player-placed bounties (clearing them IS their dedup);
        # same-team-withheld entries (see above) are left standing for a
        # future retract or a non-team-mate hunter. The system pot was
        # already zeroed above (its reset is ITS dedup).
        self._set_bounties(target, withheld_player_bounties)

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
        # .populate_existing() mirrors WO-BOUNTY-COLLECT-FLUSH above: no flush
        # needed here (nothing pending on hunter before this lock).
        hunter = (
            self.db.query(Player)
            .filter(Player.id == hunter_id)
            .populate_existing()
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
            # Close the collusion faucet (WO-INTEGRITY-PAIR NH2), mirrored from
            # the solo collect_bounty path — see _restore_target_rep_after_
            # system_payout for the rationale. Fires once per pot-zero event
            # (the designated member's turn), exactly like the reset itself.
            self._restore_target_rep_after_system_payout(target)

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

        total_raw = system_paid + player_paid
        from src.services.economy_balancing_levers import apply_bounty_payout_ratio

        total = apply_bounty_payout_ratio(total_raw) if total_raw > 0 else 0
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
        snap = None
        try:
            from src.models.player import Player
            from src.models.sector import Sector
            claimant = self.db.query(Player).filter(Player.id == claimant_id).first()
            if claimant and claimant.current_sector_id is not None:
                sec = (
                    self.db.query(Sector)
                    .filter(Sector.sector_id == claimant.current_sector_id)
                    .first()
                )
                snap = getattr(sec, "region_id", None) if sec else None
        except Exception:
            snap = None
        claim = BountyClaim(
            bounty_ref=bounty_ref,
            claimant_id=claimant_id,
            target_id=target_id,
            amount=amount,
            status=BountyClaimStatus.PAID,
            resolved_at=resolved_at,
            region_id_snapshot=snap,
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

    def _recent_pvp_kills_for_target(
        self, player_id: uuid.UUID, kill_limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Recent PvP wins by ``player_id`` from durable CombatLog rows (LEG-173).

        Portrait has no durable User/Player avatar column today (OAuth
        ``avatar_url`` is ephemeral) — callers emit ``portrait_url: null``.
        Kill entries reuse existing CombatLog columns only; empty list is OK.
        """
        from sqlalchemy import and_, or_

        from src.models.combat_log import CombatLog

        logs = (
            self.db.query(CombatLog)
            .filter(
                or_(
                    and_(
                        CombatLog.attacker_id == player_id,
                        CombatLog.outcome == "attacker_win",
                        CombatLog.defender_id.isnot(None),
                    ),
                    and_(
                        CombatLog.defender_id == player_id,
                        CombatLog.outcome == "defender_win",
                        CombatLog.attacker_id.isnot(None),
                    ),
                )
            )
            .order_by(CombatLog.timestamp.desc())
            .limit(kill_limit)
            .all()
        )
        if not logs:
            return []

        victim_ids = []
        for log in logs:
            if log.attacker_id == player_id:
                victim_ids.append(log.defender_id)
            else:
                victim_ids.append(log.attacker_id)
        victims = {
            p.id: p
            for p in self.db.query(Player).filter(Player.id.in_(victim_ids)).all()
        }

        out: List[Dict[str, Any]] = []
        for log in logs:
            if log.attacker_id == player_id:
                victim_id = log.defender_id
            else:
                victim_id = log.attacker_id
            victim = victims.get(victim_id) if victim_id else None
            ts = log.timestamp or log.ended_at or log.started_at
            if victim is not None:
                victim_name = getattr(victim, "nickname", None) or getattr(
                    victim, "username", None
                )
            else:
                victim_name = None
            out.append({
                "combat_id": str(log.id),
                "victim_id": str(victim_id) if victim_id else None,
                "victim_name": victim_name,
                "sector_id": log.sector_id,
                "timestamp": ts.isoformat() if ts is not None else None,
            })
        return out

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
                    # LEG-173 / LEG-156 contract: keys required; null/empty OK.
                    # No durable portrait store (LEG-DEC-33); CombatLog for kills.
                    "portrait_url": None,
                    "recent_kills": [],  # filled after top-N cut to avoid N+1 on full scan
                    "_player_id": player.id,
                })

        # Sort by total bounty descending
        bounty_targets.sort(key=lambda x: x["total_bounty"], reverse=True)

        trimmed = bounty_targets[:limit]
        for row in trimmed:
            pid = row.pop("_player_id")
            row["recent_kills"] = self._recent_pvp_kills_for_target(pid)

        return {
            "success": True,
            "bounties": trimmed,
            "total_targets": len(bounty_targets),
        }
