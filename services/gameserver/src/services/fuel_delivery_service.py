"""
Fuel delivery -- the paid-rescue counterpart to the free Escape Pod
(escape_pod_service.py) and the self-rescue Slipdrive (slipdrive_service.py,
now fuel-commodity-denominated per WO-GWQ-STRANDING-2).

Canon: Max's design direction (WO-GWQ-STRANDING-2, 2026-07-10) --
"conversely you could pay for someone to deliver you fuel." A stranded ship
with an empty fuel hold cannot charge/complete a Slipdrive escape on its
own; another player can fly fuel cargo to the stranded ship's sector and
sell it directly, letting the stranded player complete their OWN Slipdrive
escape (fuel goes onto the recipient's SHIP, not the requester's wallet --
the recipient still has to fly the Slipdrive themselves).

[NO-CANON] KERNEL, not a marketplace (dispatch's explicit instruction:
"Kernel honesty over completeness"). VERIFIED FIRST against the contracts
chain: FEATURES/economy/contracts.md's `cargo_delivery` contract type is
the natural long-term home for a "post a fuel request, any player browses
and accepts it" flow -- but per that doc's own Status section ("📐
Design-only. The entire trade-contract system is unimplemented") and
audit/BACKLOG.md (WO-ECON-CONTRACT-1-KERNEL is `[L]`, still QUEUED, not yet
built), that rail does not exist to plug into today. Building a duplicate
request/board/escrow system here would be exactly the "duplicating a
delivery system" the dispatch said not to do. This module ships ONLY the
underlying PRIMITIVE both a hand-arranged (players coordinate off-channel,
meet in person) delivery AND a future `cargo_delivery`-shaped Contract can
call: a same-sector, immediate, mutually-consenting fuel-for-credits
handoff. No request row, no discovery/board, no escrow, no matching. THE
DEPENDENCY: once WO-ECON-CONTRACT-1-KERNEL ships, a `fuel_delivery`
(or `cargo_delivery` with commodity_type="fuel") contract type should call
`deliver_fuel` at its `complete` transition instead of re-implementing the
transfer.
"""
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player

logger = logging.getLogger(__name__)

FUEL_COMMODITY_KEY = "fuel"


class FuelDeliveryError(Exception):
    """Raised for player-facing fuel-delivery failures; .args[0] is the
    human-readable detail string the route layer surfaces as a 4xx."""


def _now() -> datetime:
    return datetime.now(UTC)


def _cargo_contents(ship) -> Dict[str, Any]:
    cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
    return dict(cargo.get("contents") or {})


def deliver_fuel(
    db: Session,
    deliverer_player_id: uuid.UUID,
    recipient_player_id: uuid.UUID,
    fuel_amount: int,
    payment_credits: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Immediate same-sector fuel-for-credits handoff: `fuel_amount` moves from
    the deliverer's ship cargo to the recipient's ship cargo; `payment_
    credits` moves from the recipient's wallet to the deliverer's wallet.
    Both legs happen atomically in this one call -- no escrow, no partial
    fulfillment, no request lifecycle (see module docstring for why).

    Requires BOTH players' current ships to be in the SAME sector (a real
    in-person hand-off -- delivery IS the travel; getting there is ordinary
    ship movement, already covered by the existing game, not this
    function's job). Requires the deliverer to actually be carrying
    `fuel_amount` fuel and the recipient to actually afford `payment_
    credits`. Requires the recipient's cargo to have room for the
    incoming fuel (a real capacity check -- matches how cargo loading
    works everywhere else in this codebase; a delivery that doesn't fit
    fails outright rather than silently short-delivering).

    NOT gated on the recipient actually being stranded -- matches the
    Slipdrive / distress beacon / escape pod's own "not gated on actually
    being stranded" philosophy: this is a general fuel-resupply primitive
    that happens to also solve stranding, not a stranding-specific check.

    FLUSH-ONLY -- the route owns the commit.
    """
    now = now or _now()

    if fuel_amount <= 0:
        raise FuelDeliveryError("fuel_amount must be positive")
    if payment_credits < 0:
        raise FuelDeliveryError("payment_credits cannot be negative")
    if deliverer_player_id == recipient_player_id:
        raise FuelDeliveryError("Cannot deliver fuel to yourself")

    deliverer = (
        db.query(Player).filter(Player.id == deliverer_player_id)
        .populate_existing().with_for_update().first()
    )
    if not deliverer:
        raise FuelDeliveryError("Deliverer not found")
    recipient = (
        db.query(Player).filter(Player.id == recipient_player_id)
        .populate_existing().with_for_update().first()
    )
    if not recipient:
        raise FuelDeliveryError("Recipient not found")

    deliverer_ship = deliverer.current_ship
    if not deliverer_ship or deliverer_ship.is_destroyed:
        raise FuelDeliveryError("Deliverer has no active ship")
    recipient_ship = recipient.current_ship
    if not recipient_ship or recipient_ship.is_destroyed:
        raise FuelDeliveryError("Recipient has no active ship")

    if deliverer.current_sector_id != recipient.current_sector_id:
        raise FuelDeliveryError(
            "delivery_requires_same_sector: the deliverer and recipient "
            "must be in the same sector for a fuel hand-off"
        )

    deliverer_contents = _cargo_contents(deliverer_ship)
    fuel_held = int(deliverer_contents.get(FUEL_COMMODITY_KEY, 0) or 0)
    if fuel_held < fuel_amount:
        raise FuelDeliveryError(
            f"insufficient_fuel_cargo: deliverer has {fuel_held} fuel, "
            f"tried to deliver {fuel_amount}"
        )

    recipient_credits = recipient.credits or 0
    if recipient_credits < payment_credits:
        raise FuelDeliveryError(
            f"insufficient_credits: recipient has {recipient_credits} credits, "
            f"delivery costs {payment_credits}"
        )

    recipient_cargo = recipient_ship.cargo if isinstance(recipient_ship.cargo, dict) else {}
    recipient_contents = dict(recipient_cargo.get("contents") or {})
    recipient_capacity = int(recipient_cargo.get("capacity", 0) or 0)
    recipient_used = int(recipient_cargo.get("used", 0) or 0)
    if recipient_used + fuel_amount > recipient_capacity:
        raise FuelDeliveryError(
            f"insufficient_cargo_space: recipient's hold has {recipient_capacity - recipient_used} "
            f"free of {recipient_capacity}, delivery is {fuel_amount}"
        )

    # --- Leg 1: fuel, deliverer's ship -> recipient's ship ---
    deliverer_contents[FUEL_COMMODITY_KEY] = fuel_held - fuel_amount
    deliverer_cargo = deliverer_ship.cargo if isinstance(deliverer_ship.cargo, dict) else {}
    deliverer_cargo["contents"] = deliverer_contents
    deliverer_cargo["used"] = sum(
        int(q) for q in deliverer_contents.values() if isinstance(q, (int, float))
    )
    deliverer_ship.cargo = deliverer_cargo
    flag_modified(deliverer_ship, "cargo")

    recipient_contents[FUEL_COMMODITY_KEY] = int(recipient_contents.get(FUEL_COMMODITY_KEY, 0) or 0) + fuel_amount
    recipient_cargo["contents"] = recipient_contents
    recipient_cargo["used"] = recipient_used + fuel_amount
    recipient_ship.cargo = recipient_cargo
    flag_modified(recipient_ship, "cargo")

    # --- Leg 2: payment, recipient's wallet -> deliverer's wallet ---
    recipient.credits = recipient_credits - payment_credits
    deliverer.credits = (deliverer.credits or 0) + payment_credits

    db.flush()  # route owns the commit

    logger.info(
        "Player %s delivered %d fuel to player %s at sector %s for %d credits",
        deliverer.id, fuel_amount, recipient.id, recipient.current_sector_id, payment_credits,
    )

    return {
        "outcome": "fuel_delivered",
        "fuel_delivered": fuel_amount,
        "payment_credits": payment_credits,
        "deliverer_fuel_remaining": deliverer_contents[FUEL_COMMODITY_KEY],
        "recipient_fuel_total": recipient_contents[FUEL_COMMODITY_KEY],
        "deliverer_credits": deliverer.credits,
        "recipient_credits": recipient.credits,
        "sector_id": recipient.current_sector_id,
    }
