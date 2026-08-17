"""Voluntary planet ownership transfer (LEG-159 / colonization.md Ownership controls).

LEG-DEC-15 ratified package (gameserver notes on wo#163):
  (1) Fee payer = current owner (transferor) at accept time.
  (2) Fee base = sunk_cost_for(citadel_level) — claim_fee + Σ CITADEL_LEVELS
      upgrade_cost for n=2..level (abandonment_service formula). Fee = 5% of base,
      charged once on accept.
  (3) Accept window = 24 real-time hours; owner may cancel while pending;
      expiry/cancel = no fee.
  (4) Pending locks: block offeror voluntary abandon; reject concurrent voluntary
      transfer offers (409); siege/assault may proceed — capture cancels pending
      with no fee.

Pending offer is stored under Planet.structures['pending_ownership_transfer']
(existing JSONB — no migration). Shape:
  {from_player_id, to_player_id, fee_credits, fee_base, offered_at, expires_at}.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.planet import Planet, player_planets
from src.models.player import Player
from src.services.abandonment_service import sunk_cost_for

logger = logging.getLogger(__name__)

TRANSFER_FEE_RATE = 0.05
ACCEPT_WINDOW = timedelta(hours=24)
_PENDING_KEY = "pending_ownership_transfer"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _structures_dict(planet: Planet) -> Dict[str, Any]:
    raw = planet.structures
    return dict(raw) if isinstance(raw, dict) else {}


def get_pending_transfer(planet: Planet) -> Optional[Dict[str, Any]]:
    pending = _structures_dict(planet).get(_PENDING_KEY)
    return dict(pending) if isinstance(pending, dict) else None


def clear_pending_transfer(planet: Planet) -> bool:
    """Clear any pending offer (capture / cancel / expiry). Returns True if cleared."""
    structs = _structures_dict(planet)
    if _PENDING_KEY not in structs:
        return False
    structs.pop(_PENDING_KEY, None)
    planet.structures = structs or None
    flag_modified(planet, "structures")
    return True


def _set_pending(planet: Planet, offer: Dict[str, Any]) -> None:
    structs = _structures_dict(planet)
    structs[_PENDING_KEY] = offer
    planet.structures = structs
    flag_modified(planet, "structures")


def _parse_expires(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _pending_alive(pending: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    expires = _parse_expires(pending.get("expires_at"))
    if expires is None:
        return False
    return expires > (now or _now())


def transfer_fee_credits(citadel_level: int) -> Dict[str, int]:
    base = sunk_cost_for(citadel_level)
    fee = int(round(TRANSFER_FEE_RATE * base))
    return {"fee_base": base, "fee_credits": fee}


def offer_transfer(
    db: Session,
    planet: Planet,
    owner: Player,
    recipient: Player,
) -> Dict[str, Any]:
    """Owner initiates a voluntary ownership transfer. Flush-only."""
    if planet.owner_id is None or planet.owner_id != owner.id:
        raise ValueError("not_owner")
    if recipient.id == owner.id:
        raise ValueError("self_transfer")
    if not recipient.is_active:
        raise ValueError("recipient_inactive")

    existing = get_pending_transfer(planet)
    if existing and _pending_alive(existing):
        raise ValueError("offer_pending")
    if existing:
        clear_pending_transfer(planet)

    fee = transfer_fee_credits(int(getattr(planet, "citadel_level", 0) or 0))
    now = _now()
    expires = now + ACCEPT_WINDOW
    offer = {
        "from_player_id": str(owner.id),
        "to_player_id": str(recipient.id),
        "fee_credits": fee["fee_credits"],
        "fee_base": fee["fee_base"],
        "offered_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }
    _set_pending(planet, offer)
    logger.info(
        "planet transfer offered: planet=%s from=%s to=%s fee=%s",
        planet.id, owner.id, recipient.id, fee["fee_credits"],
    )
    return {"success": True, "offer": offer, "planet_id": str(planet.id)}


def cancel_transfer(db: Session, planet: Planet, actor: Player) -> Dict[str, Any]:
    """Owner cancels a pending offer. No fee. Flush-only."""
    if planet.owner_id is None or planet.owner_id != actor.id:
        raise ValueError("not_owner")
    pending = get_pending_transfer(planet)
    if not pending:
        raise ValueError("no_pending")
    clear_pending_transfer(planet)
    return {"success": True, "planet_id": str(planet.id), "cancelled": True}


def accept_transfer(
    db: Session,
    planet: Planet,
    recipient: Player,
    owner: Player,
) -> Dict[str, Any]:
    """Recipient accepts; owner pays 5% fee; ownership flips. Flush-only.

    Caller MUST hold row locks: planet, then owner, then recipient (credits).
    """
    pending = get_pending_transfer(planet)
    if not pending:
        raise ValueError("no_pending")
    if not _pending_alive(pending):
        clear_pending_transfer(planet)
        raise ValueError("offer_expired")
    if str(pending.get("to_player_id")) != str(recipient.id):
        raise ValueError("not_recipient")
    if planet.owner_id is None or str(planet.owner_id) != str(pending.get("from_player_id")):
        clear_pending_transfer(planet)
        raise ValueError("owner_mismatch")
    if owner.id != planet.owner_id:
        raise ValueError("owner_mismatch")

    fee = int(pending.get("fee_credits") or 0)
    if fee < 0:
        fee = 0
    if int(owner.credits or 0) < fee:
        raise ValueError("insufficient_credits")

    owner.credits = int(owner.credits or 0) - fee

    # Flip association ledger + owner_id (mirrors reclaim_planet founding write).
    db.execute(player_planets.delete().where(player_planets.c.planet_id == planet.id))
    planet.owner_id = recipient.id
    db.execute(
        player_planets.insert().values(
            player_id=recipient.id,
            planet_id=planet.id,
            acquired_at=_now(),
        )
    )
    clear_pending_transfer(planet)

    # If previous owner was landed, lift them (planet no longer theirs).
    if owner.current_planet_id == planet.id:
        owner.is_landed = False
        owner.current_planet_id = None

    logger.info(
        "planet transfer accepted: planet=%s from=%s to=%s fee=%s",
        planet.id, owner.id, recipient.id, fee,
    )
    return {
        "success": True,
        "planet_id": str(planet.id),
        "from_player_id": str(owner.id),
        "to_player_id": str(recipient.id),
        "fee_credits": fee,
        "owner_credits_remaining": int(owner.credits or 0),
    }


def assert_abandon_allowed(planet: Planet) -> None:
    """Raise if a live pending transfer blocks voluntary abandon (LEG-DEC-15 §4)."""
    pending = get_pending_transfer(planet)
    if pending and _pending_alive(pending):
        raise ValueError("transfer_pending")
