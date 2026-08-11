"""Region-owner activity levers: docking-fee subsidy + arbitrage stipend.

Canon: FEATURES/economy/lifecycle.md § Cross-region economics —
treasury-funded opt-in levers enacted via regional policy
``proposed_changes``, draining ``Region.treasury_balance`` through
``RegionalTreasuryEntry(CAUSE_EXPENDITURE)``. Auto-suspend when the
treasury cannot cover a pending payout; resume when it recovers (no
owner action — checked at payout time).

Lever enablement lives in ``Region.trade_bonuses`` under reserved
non-multiplier keys (same pattern as ADR-0062 ``tariff_rate``) so no
migration is required.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from src.models.player import Player
from src.models.region import Region, RegionalTreasuryEntry

logger = logging.getLogger(__name__)
UTC = timezone.utc

# trade_bonuses reserved keys (must stay out of [1.0, 3.0] multiplier clamp)
LEVER_DOCKING_FEE_SUBSIDY = "docking_fee_subsidy"
LEVER_ARBITRAGE_STIPEND = "arbitrage_stipend"

# Canon magnitudes (lifecycle.md:370-371)
DOCKING_SUBSIDY_FRACTION = 0.5
DOCKING_SUBSIDY_CAP = 250
ARBITRAGE_STIPEND_AMOUNT = 500
ARBITRAGE_STIPEND_DAILY_CAP = 1_000

# Player.settings bookkeeping for round-trip + daily stipend
_SETTINGS_FOREIGN_VISIT = "arbitrage_foreign_visit_region_id"
_SETTINGS_STIPEND_DAY = "arbitrage_stipend_day"
_SETTINGS_STIPEND_PAID = "arbitrage_stipend_paid_today"


def lever_enabled(region: Region, key: str) -> bool:
    bonuses = region.trade_bonuses or {}
    return bool(bonuses.get(key))


def apply_lever_flags(
    region: Region, proposed_changes: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge docking/arbitrage lever booleans into trade_bonuses. Mutates region."""
    applied: Dict[str, Any] = {}
    bonuses = dict(region.trade_bonuses or {})
    for key in (LEVER_DOCKING_FEE_SUBSIDY, LEVER_ARBITRAGE_STIPEND):
        if key not in proposed_changes:
            continue
        new_val = bool(proposed_changes[key])
        old_val = bool(bonuses.get(key))
        if new_val != old_val:
            bonuses[key] = new_val
            applied[key] = {"old": old_val, "new": new_val}
    if applied:
        region.trade_bonuses = bonuses
    return applied


def docking_fee_rebate(fee: int) -> int:
    """50% rebate, capped at 250 cr (canon)."""
    if fee <= 0:
        return 0
    return min(DOCKING_SUBSIDY_CAP, int(fee * DOCKING_SUBSIDY_FRACTION))


def _treasury_spend(
    db: Session,
    region: Region,
    amount: int,
    *,
    reason: str,
    cause_id: Optional[UUID] = None,
) -> bool:
    """Debit region treasury if funds cover ``amount``. Returns False if suspended."""
    if amount <= 0:
        return True
    before = int(region.treasury_balance or 0)
    if before < amount:
        logger.info(
            "Region %s lever auto-suspended: need %s have %s (%s)",
            region.id, amount, before, reason,
        )
        return False
    after = before - amount
    region.treasury_balance = after
    db.add(RegionalTreasuryEntry(
        region_id=region.id,
        before_balance=before,
        after_balance=after,
        delta=-amount,
        cause_type=RegionalTreasuryEntry.CAUSE_EXPENDITURE,
        cause_id=cause_id,
        reason=reason,
    ))
    return True


def apply_docking_fee_subsidy(
    db: Session,
    region: Optional[Region],
    player: Player,
    fee: int,
) -> Tuple[int, int]:
    """Rebate resident docking fee from region treasury when lever is on.

    Returns ``(net_fee_charged_to_player, rebate_paid)``. If the lever is off,
    player is not a resident of ``region``, or treasury cannot cover, returns
    ``(fee, 0)`` unchanged.
    """
    if region is None or fee <= 0:
        return fee, 0
    if not lever_enabled(region, LEVER_DOCKING_FEE_SUBSIDY):
        return fee, 0
    home = getattr(player, "home_region_id", None)
    if home is None or str(home) != str(region.id):
        return fee, 0
    rebate = docking_fee_rebate(fee)
    if rebate <= 0:
        return fee, 0
    if not _treasury_spend(
        db, region, rebate,
        reason=f"Docking-fee subsidy for player {player.id}",
        cause_id=getattr(player, "id", None),
    ):
        return fee, 0
    return fee - rebate, rebate


def _canonical_day_key(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%d")


def _settings_mut(player: Player) -> Dict[str, Any]:
    return dict(player.settings or {})


def note_foreign_region_visit(player: Player, foreign_region_id) -> None:
    """Mark that the player visited a non-home region (round-trip leg 1)."""
    home = getattr(player, "home_region_id", None)
    if home is None or foreign_region_id is None:
        return
    if str(foreign_region_id) == str(home):
        return
    settings = _settings_mut(player)
    settings[_SETTINGS_FOREIGN_VISIT] = str(foreign_region_id)
    player.settings = settings


def try_pay_arbitrage_stipend(
    db: Session,
    home_region: Optional[Region],
    player: Player,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Pay 500 cr on qualifying home return after a foreign visit (cap 1k/day).

    Qualifying round-trip: player has ``home_region_id``, has recorded a
    foreign visit via ``note_foreign_region_visit``, and is now landing back
    in home. Clears the foreign-visit flag whether or not payment succeeds
    (one attempt per return).
    """
    now = now or datetime.now(UTC)
    result = {"paid": 0, "skipped": "n/a"}
    if home_region is None:
        result["skipped"] = "no_home_region"
        return result
    if not lever_enabled(home_region, LEVER_ARBITRAGE_STIPEND):
        result["skipped"] = "lever_off"
        return result
    home = getattr(player, "home_region_id", None)
    if home is None or str(home) != str(home_region.id):
        result["skipped"] = "not_home"
        return result

    settings = _settings_mut(player)
    foreign = settings.pop(_SETTINGS_FOREIGN_VISIT, None)
    if not foreign:
        player.settings = settings
        result["skipped"] = "no_foreign_leg"
        return result

    day = _canonical_day_key(now)
    if settings.get(_SETTINGS_STIPEND_DAY) != day:
        settings[_SETTINGS_STIPEND_DAY] = day
        settings[_SETTINGS_STIPEND_PAID] = 0
    paid_today = int(settings.get(_SETTINGS_STIPEND_PAID) or 0)
    remaining = ARBITRAGE_STIPEND_DAILY_CAP - paid_today
    amount = min(ARBITRAGE_STIPEND_AMOUNT, max(0, remaining))
    if amount <= 0:
        player.settings = settings
        result["skipped"] = "daily_cap"
        return result

    if not _treasury_spend(
        db, home_region, amount,
        reason=f"Arbitrage stipend for player {player.id} (via {foreign})",
        cause_id=getattr(player, "id", None),
    ):
        player.settings = settings
        result["skipped"] = "treasury_empty"
        return result

    player.credits = int(player.credits or 0) + amount
    settings[_SETTINGS_STIPEND_PAID] = paid_today + amount
    player.settings = settings
    result = {"paid": amount, "skipped": None, "via_region": foreign}
    logger.info(
        "Arbitrage stipend %s cr to player %s from region %s",
        amount, player.id, home_region.id,
    )
    return result
