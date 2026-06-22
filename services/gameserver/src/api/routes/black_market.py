"""
Black-Market API Routes — contraband trading loop (WO-BLACKMARKET kernel).

Player-facing endpoints for the single-port-class contraband loop
(audit/design-briefs/black-market.md §1; design intent
sw2102-docs/FEATURES/economy/black-market.md). A station qualifies as a
black-market venue iff ``Station.type == StationType.BLACK_MARKET`` AND the
player holds Fringe-Alliance (``FactionType.OUTLAWS``) reputation ≥ the
RECOGNIZED tier; otherwise the catalog 404s/hides (gate unmet is
indistinguishable from "no such venue" by design — a player who can't see the
market shouldn't learn one exists). Three actions:

  GET  /trading/black-market/{station_id} → contraband catalog + computed prices
  POST /trading/black-market/buy          → haggle roll, charge credits, hold cargo
  POST /trading/black-market/sell         → payout, detection roll, consequences

All three delegate to the server-authoritative, atomic ``ContrabandService``
(frozen signature WO-BLACKMARKET (D)); this layer validates the request shape,
supplies the authenticated player + active ship, maps the service's stable
``reason`` codes onto HTTP statuses, and — CRITICALLY — owns the transaction
commit/rollback (the just-shipped mining wave shipped a CRITICAL by forgetting
this: ``get_db()`` does NOT auto-commit, so without an explicit ``db.commit()``
on the success path the whole trade silently rolls back when the session
closes). The route patterns (Pydantic request model,
``Depends(get_current_player)``, service-result → HTTPException, route-owns-
commit) mirror ``api/routes/mining.py`` and ``api/routes/trading.py``.

SLAVES is a permanently-disabled stub (WO-BLACKMARKET (B)): the service's
``ENABLED_COMMODITIES`` accessor never includes it and the buy/sell paths reject
it unconditionally, so a request naming SLAVES fails the same way an unknown
commodity does. This route adds no path to enable it.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.ship import Ship
from src.services.contraband_service import ContrabandService

logger = logging.getLogger(__name__)

# The router carries the full ``/trading`` prefix so the contraband endpoints
# sit alongside the legal trade desk at ``/trading/black-market/...`` (mirrors
# mining.py carrying its own /mining prefix; the include in api.py adds no extra
# prefix). The tag is distinct so the black-market endpoints group separately in
# the OpenAPI schema.
router = APIRouter(prefix="/trading", tags=["black-market"])


class BlackMarketTradeRequest(BaseModel):
    station_id: str
    commodity: str
    quantity: int = Field(..., gt=0, le=100000, description="Must be between 1 and 100,000")


# Stable ContrabandService failure ``reason`` codes that describe a missing /
# invalid / forbidden TARGET (no such station, the venue is not a black market,
# or the player fails the Fringe-Alliance access gate) rather than a precondition
# the player could satisfy. These map to 404 — the access-gate failure is
# deliberately a 404, not a 403: a player below the gate must not be able to
# distinguish "you're not cleared" from "no black market here" (else the 403
# itself advertises the venue). Mirrors mining.py / trading.py: a malformed or
# absent (or hidden) referent is a 404.
_NOT_FOUND_REASONS = frozenset(
    {
        "station_not_found",
        "not_black_market",
        "access_denied",
        "gate_unmet",
        "venue_hidden",
    }
)

# Failure reasons where the player cannot afford the trade — these map to 402
# Payment Required (insufficient credits to buy, or a fine they can't cover).
# Every other failure reason (not docked, wrong sector, no cargo space, unknown
# or disabled commodity such as SLAVES, zero contraband to sell) is a validation
# / gate failure and maps to 400.
_PAYMENT_REQUIRED_REASONS = frozenset(
    {
        "insufficient_credits",
    }
)


def _status_for_reason(reason) -> int:
    """Map a ContrabandService failure ``reason`` code to an HTTP status.

    404 for a missing / hidden / access-gated referent (see
    ``_NOT_FOUND_REASONS``); 402 for an affordability failure (see
    ``_PAYMENT_REQUIRED_REASONS``); 400 for every other gate / validation
    failure. A ``None``/empty reason on a failed result degrades to 400 (a
    generic bad-request gate failure)."""
    if reason in _NOT_FOUND_REASONS:
        return status.HTTP_404_NOT_FOUND
    if reason in _PAYMENT_REQUIRED_REASONS:
        return status.HTTP_402_PAYMENT_REQUIRED
    return status.HTTP_400_BAD_REQUEST


def _get_station_or_404(db: Session, station_id: str):
    """Fetch a station by id, turning a malformed UUID into a 404 instead of a
    DataError 500 (mirrors trading.py:_get_station_or_404). The contraband
    catalog deliberately 404s rather than revealing the venue, so an absent or
    malformed station is the same 404 a hidden venue produces."""
    import uuid as _uuid

    from src.models.station import Station

    try:
        station_uuid = _uuid.UUID(str(station_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Black market not found")
    station = db.query(Station).filter(Station.id == station_uuid).first()
    if not station:
        raise HTTPException(status_code=404, detail="Black market not found")
    return station


def _active_ship_or_404(db: Session, player: Player) -> Ship:
    """Resolve the player's current ship (the cargo hold contraband is held in).

    A missing active ship is a 404 — matching the legal trade desk
    (trading.py: "No active ship found")."""
    ship = (
        db.query(Ship)
        .filter(Ship.id == player.current_ship_id, Ship.owner_id == player.id)
        .first()
    )
    if not ship:
        raise HTTPException(status_code=404, detail="No active ship found")
    return ship


@router.get("/black-market/{station_id}")
async def get_black_market_catalog(
    station_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Contraband catalog + per-commodity computed prices for a black-market
    venue.

    Gated: returns 404 unless the station is a ``BLACK_MARKET`` venue AND the
    player holds Fringe-Alliance (``OUTLAWS``) reputation ≥ RECOGNIZED — a player
    below the gate gets the same 404 as a non-existent venue, so the 404 never
    leaks the venue's existence (WO-BLACKMARKET (D)/(E) access gate). On success
    the service's catalog dict is returned verbatim (the enabled
    ``IllegalCommodity`` rows at their haggle-rolled prices; SLAVES is never
    among them — it is a permanently-disabled stub, (B)).

    READ-ONLY: the catalog computes a fresh haggle quote but commits no state, so
    there is no commit on this path (a price quote that isn't acted on must not
    persist). Mirrors trading.py's GET /market/{station_id}."""
    station = _get_station_or_404(db, station_id)
    result = ContrabandService(db).get_catalog(player, station)

    # The service signals a gate failure either by returning a falsy result
    # (None / empty — the venue is hidden) or an explicit {success: False,
    # reason}. Both collapse to a 404 so the gate never advertises itself.
    if not result:
        raise HTTPException(status_code=404, detail="Black market not found")
    if isinstance(result, dict) and result.get("success") is False:
        reason = result.get("reason")
        raise HTTPException(
            status_code=_status_for_reason(reason),
            detail=reason or "Black market not found",
        )
    return result


@router.post("/black-market/buy")
async def buy_contraband(
    trade_request: BlackMarketTradeRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Buy contraband from a black-market venue.

    Delegates to the server-authoritative, atomic ``ContrabandService.buy`` —
    that method re-checks the docked + same-sector preflight and the access
    gate, takes the station-then-player row locks (lock order L3:
    station FOR UPDATE before player FOR UPDATE), rolls the BLACK_MARKET haggle
    modifier, charges credits, and adds the contraband to ``Ship.cargo`` under
    the ``illegal:<commodity>`` key (counting against capacity), all in one
    transaction. A failed buy (gate / affordability / no cargo space / unknown
    or disabled commodity) returns a stable ``reason`` code mapped onto an HTTP
    status; on success the service's result dict is returned verbatim.

    CRITICAL (mining-wave lesson L1 — THE ROUTE OWNS THE COMMIT): the service
    only FLUSHES; this handler MUST ``db.commit()`` on success and
    ``db.rollback()`` on failure / exception, because ``get_db()`` does not
    auto-commit. Forgetting the commit silently rolls back the entire purchase."""
    ship = _active_ship_or_404(db, player)
    station = _get_station_or_404(db, trade_request.station_id)

    try:
        result = ContrabandService(db).buy(
            player, ship, station, trade_request.commodity, trade_request.quantity
        )
    except HTTPException:
        # A referent/validation HTTPException raised by the service or helper —
        # nothing committed; discard any partial flush and re-raise as-is.
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error("Black-market buy failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Black-market trade failed")

    if not result or (isinstance(result, dict) and result.get("success") is False):
        # Gate / affordability / validation failure — the service flushed no
        # durable state, but roll back defensively before mapping to a status so
        # no partial side effect persists (mirrors mining.py's failed-result
        # rollback).
        db.rollback()
        reason = result.get("reason") if isinstance(result, dict) else None
        raise HTTPException(
            status_code=_status_for_reason(reason),
            detail=reason or "Black-market purchase failed",
        )

    # Success: the route owns the commit (L1). Without this the credit charge +
    # cargo grant silently roll back when the session closes.
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


@router.post("/black-market/sell")
async def sell_contraband(
    trade_request: BlackMarketTradeRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Sell contraband to a black-market venue, running the detection roll.

    Delegates to the server-authoritative, atomic ``ContrabandService.sell`` —
    that method re-checks the docked + same-sector preflight and the access gate,
    takes the station-then-player row locks (lock order L3), verifies the player
    holds the contraband in ``Ship.cargo`` under ``illegal:<commodity>``, runs
    the 4-term detection roll (base + cargo + sector-security + reputation,
    clamped [0.0, 0.95]), and on a CLEAN roll pays out at the haggle-rolled price
    while on a FAILED roll confiscates the illegal cargo, levies the
    severity-scaled fine, applies the Federation reputation delta (via the SYNC
    ``apply_faction_rep_delta`` inside this transaction — never the async
    ``update_reputation`` which would commit mid-transaction), and flips
    ``Player.is_suspect`` (LIGHT/MODERATE) or ``Player.is_wanted`` (SEVERE) —
    all in one transaction. The result (paid out vs detected, fine, rep deltas,
    heat-state change) is returned verbatim on success; a precondition failure
    returns a stable ``reason`` mapped onto an HTTP status.

    CRITICAL (mining-wave lesson L1 — THE ROUTE OWNS THE COMMIT): the service
    only FLUSHES; this handler MUST ``db.commit()`` on success and
    ``db.rollback()`` on failure / exception. A DETECTED sell is still a SUCCESS
    outcome (the confiscation / fine / rep / heat-flip MUST persist) — the
    service returns ``success: True`` for both the clean payout and the seizure,
    so this commits both. Only a genuine precondition failure (gate, no such
    cargo, disabled commodity) returns ``success: False`` and rolls back."""
    ship = _active_ship_or_404(db, player)
    station = _get_station_or_404(db, trade_request.station_id)

    try:
        result = ContrabandService(db).sell(
            player, ship, station, trade_request.commodity, trade_request.quantity
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error("Black-market sell failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Black-market trade failed")

    if not result or (isinstance(result, dict) and result.get("success") is False):
        db.rollback()
        reason = result.get("reason") if isinstance(result, dict) else None
        raise HTTPException(
            status_code=_status_for_reason(reason),
            detail=reason or "Black-market sale failed",
        )

    # Success — clean payout OR a detected seizure (both are success: True; the
    # seizure's confiscation/fine/rep/heat-flip MUST persist). The route owns the
    # commit (L1).
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result
