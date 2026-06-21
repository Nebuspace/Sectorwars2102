"""pioneer_service — pioneer migration contract ledger.

Helpers shared by the pioneer routes and the settlement flow:

- ``quote_fee`` snapshots the canon-clamped 30-80 cr per-pioneer fee,
  reusing the shipped trading_service pricing against the colonist-selling
  station in the hub's sector (defensive fallback to the canon formula).
- ``attribute_settlement`` is the passive ledger: whenever colonists
  settle on a frontier world (claim / disembark), it advances ``delivered``
  on the player's open contracts FIFO by created_at. Cargo is fungible, so
  attribution is by oldest-contract-with-loaded-pioneers first.
- ``reabsorb_on_ship_loss`` zeroes ``loaded`` when the carrying hull is
  destroyed (cryosleep pods die with the ship, canon).

Canon: FEATURES/planets/colonization.md (Pioneer Office, migration
contracts, 30-80 fee clamp). The Office is canonically at the Capital
Sector's Class-0 station; this layer deliberately surfaces it on the
population-hub planet (landed) while leaving the station buy flow intact.
"""

import logging

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.migration_contract import (
    MigrationContract,
    MigrationContractStatus,
)
from src.models.planet import Planet, player_planets
from src.models.player import Player
from src.models.station import Station, StationClass
from src.services.trading_service import TradingService, COMMODITY_PRICE_RANGES

logger = logging.getLogger(__name__)

# Canon clamp for the colonist commodity (mirrors trading_service).
_COLONIST_RANGE = COMMODITY_PRICE_RANGES.get("colonists", {"min": 30, "max": 80})
_SELL_SPREAD = 1.15  # matches trading_service.SELL_SPREAD


def quote_fee(db: Session, planet: Planet) -> int:
    """The locked per-pioneer fee for a contract brokered at ``planet``.

    Reuses trading_service.calculate_dynamic_price against the colonist-
    selling station in the planet's sector (canon: the Capital Sector's
    Class-0 station). Falls back to the canon midpoint formula when no such
    station exists. Always clamped to the 30-80 cr colonist range.
    """
    stations = (
        db.query(Station)
        .filter(Station.sector_id == planet.sector_id)
        .all()
    )
    seller = next(
        (
            s for s in stations
            if (s.commodities or {}).get("colonists", {}).get("sells")
        ),
        None,
    )
    if seller is not None:
        fee = TradingService(db).calculate_dynamic_price(seller, "colonists", "sell")
        if fee > 0:
            return fee

    # Defensive fallback: canon midpoint with no live supply signal
    # (supply_ratio assumed 0.5 -> neutral multiplier of 1.0), clamped.
    base = _COLONIST_RANGE["min"]
    midpoint = base * (1.5 - 0.5)
    fee = int(round(midpoint * _SELL_SPREAD))
    return max(_COLONIST_RANGE["min"], min(fee, _COLONIST_RANGE["max"]))


def quote_surplus_price(db: Session, station: Station) -> int:
    """The per-pioneer buyout price the Pioneer Office at ``station`` pays for a
    planet's accrued surplus pioneers (lifecycle.md §1.4 "Colonist sales").

    Mirrors ``quote_fee`` but prices off the Class-0 Pioneer Office station the
    player is docked at directly (the buyer), rather than resolving a seller by
    sector. Reuses the shipped trading_service colonist pricing when this station
    actually buys colonists; falls back to the canon midpoint otherwise. Always
    clamped to the 30-80 cr colonist range from COMMODITY_PRICE_RANGES.
    """
    commodities = station.commodities or {}
    colonist_market = commodities.get("colonists") or {}
    if colonist_market.get("buys"):
        # The Office BUYS the surplus — price the player's sell against this
        # station's colonist market (defensive: any non-positive result falls
        # through to the canon midpoint below).
        price = TradingService(db).calculate_dynamic_price(station, "colonists", "buy")
        if price and price > 0:
            return max(_COLONIST_RANGE["min"], min(int(price), _COLONIST_RANGE["max"]))

    # Defensive fallback: canon midpoint with no live colonist market signal
    # (mirrors quote_fee's fallback), clamped to the 30-80 band.
    base = _COLONIST_RANGE["min"]
    midpoint = base * (1.5 - 0.5)
    price = int(round(midpoint * _SELL_SPREAD))
    return max(_COLONIST_RANGE["min"], min(price, _COLONIST_RANGE["max"]))


def attribute_settlement(db: Session, player_id, settled_qty: int) -> int:
    """Advance ``delivered`` on the player's open contracts to account for
    ``settled_qty`` colonists that just settled on a frontier world.

    FIFO over IN_PROGRESS contracts with loaded > 0 (oldest first), moving
    ``min(remaining, loaded)`` from loaded -> delivered on each until the
    settled quantity is exhausted. Leftover settled colonists that were not
    carried against any contract (e.g. station-bought, or embarked off
    another owned planet) are simply not attributed.

    Returns the number of pioneers attributed to contracts (<= settled_qty).
    """
    if not settled_qty or settled_qty <= 0:
        return 0

    contracts = (
        db.query(MigrationContract)
        .filter(
            MigrationContract.player_id == player_id,
            MigrationContract.status == MigrationContractStatus.IN_PROGRESS,
            MigrationContract.loaded > 0,
        )
        .order_by(MigrationContract.created_at.asc())
        .with_for_update()
        .all()
    )

    remaining = settled_qty
    attributed = 0
    for contract in contracts:
        if remaining <= 0:
            break
        take = min(remaining, contract.loaded or 0)
        if take <= 0:
            continue
        contract.loaded -= take
        contract.delivered = (contract.delivered or 0) + take
        remaining -= take
        attributed += take
        if contract.delivered >= contract.cohort_total:
            contract.status = MigrationContractStatus.FULFILLED

    return attributed


def reabsorb_on_ship_loss(db: Session, player_id) -> int:
    """A carrying ship was destroyed — the cryosleep pods are lost with the
    hull (canon). Zero ``loaded`` on the player's open contracts; VOID any
    that have delivered nothing. Returns the number of contracts touched.

    Best-effort: callers should wrap in try/except so a ledger hiccup never
    blocks combat resolution.
    """
    contracts = (
        db.query(MigrationContract)
        .filter(
            MigrationContract.player_id == player_id,
            MigrationContract.status == MigrationContractStatus.IN_PROGRESS,
            MigrationContract.loaded > 0,
        )
        .with_for_update()
        .all()
    )
    touched = 0
    for contract in contracts:
        contract.loaded = 0
        if (contract.delivered or 0) <= 0:
            contract.status = MigrationContractStatus.VOID
        touched += 1
    return touched


# ----------------------------------------------------------------------------
# Planet-surplus pioneer sale (lifecycle.md §1.4 "Colonist sales")
# ----------------------------------------------------------------------------

# active_events key holding a planet's accrued surplus pioneers (written by
# planetary_service.apply_resource_production). Single source of truth for both
# the accrual writer and this sale reader.
SURPLUS_PIONEERS_KEY = "surplus_pioneers"


class SurplusSaleError(Exception):
    """A planet-surplus pioneer sale could not be completed. Carries an HTTP
    status so the route can surface a precise failure without leaking internals.
    Always raised BEFORE any mutation (no partial sale)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _read_surplus(planet: Planet) -> int:
    events = planet.active_events
    if not isinstance(events, dict):
        return 0
    try:
        return max(0, int(events.get(SURPLUS_PIONEERS_KEY, 0) or 0))
    except (TypeError, ValueError):
        return 0


def sell_planet_surplus(
    db: Session,
    player_id,
    planet_id,
    station: Station,
    quantity=None,
) -> dict:
    """Sell an owned planet's accrued surplus pioneers at a Class-0 Pioneer
    Office (lifecycle.md §1.4). Distinct from the hub-load / migration-contract
    ferry path — this is the planet-asset faucet: surplus accrued by the planet's
    production tick is bought out at 30-80 cr/pioneer and the credit is paid to
    the player.

    ``station`` is the Class-0 station the player is docked at (the caller has
    already validated docking + class). ``quantity`` None sells the entire
    accrued surplus; an integer sells exactly that many (clamped is NOT silent —
    over-asking raises).

    Transaction discipline (mirrors pioneer routes / trading): lock the PLANET
    row first, then the PLAYER row (leaf), decrement
    active_events['surplus_pioneers'] and credit the player in the SAME txn under
    those locks. Every failure raises SurplusSaleError BEFORE any mutation. The
    CALLER commits.

    Returns a summary dict: {planet_id, planet_name, sold, price_per_pioneer,
    credits_earned, surplus_remaining, player_credits}.
    """
    # Validate the requested quantity shape up-front (no mutation yet).
    if quantity is not None:
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            raise SurplusSaleError(400, "Invalid quantity.")
        if quantity <= 0:
            raise SurplusSaleError(400, "Quantity must be a positive number of pioneers.")

    # Lock order: PLANET row first (parent), then PLAYER row (leaf) — matches the
    # station-then-player ordering used across credit/stockpile moves. Ownership
    # is verified against the canonical player_planets ledger.
    planet = (
        db.query(Planet)
        .join(player_planets, Planet.id == player_planets.c.planet_id)
        .filter(
            Planet.id == planet_id,
            player_planets.c.player_id == player_id,
        )
        .with_for_update(of=Planet)
        .first()
    )
    if planet is None:
        raise SurplusSaleError(404, "Planet not found or not owned by you.")

    if planet.sector_id != station.sector_id:
        raise SurplusSaleError(
            400,
            "You can only export a planet's surplus pioneers from a Pioneer "
            "Office in the same sector as the planet.",
        )

    available = _read_surplus(planet)
    if available <= 0:
        raise SurplusSaleError(
            400,
            "This planet has no surplus pioneers to export yet. Surplus accrues "
            "over time as the colony grows.",
        )

    to_sell = available if quantity is None else quantity
    if to_sell > available:
        raise SurplusSaleError(
            400,
            f"Only {available} surplus pioneers are available to export on this "
            f"planet.",
        )

    price = quote_surplus_price(db, station)  # already clamped to the 30-80 band
    credits_earned = price * to_sell

    # Lock the player row (leaf) AFTER the planet row.
    locked_player = (
        db.query(Player).filter(Player.id == player_id).with_for_update().first()
    )
    if locked_player is None:
        raise SurplusSaleError(404, "Player not found.")

    # --- All validation passed: mutate under both locks, single txn. ----------
    events = dict(planet.active_events) if isinstance(planet.active_events, dict) else {}
    new_remaining = available - to_sell
    events[SURPLUS_PIONEERS_KEY] = new_remaining
    planet.active_events = events
    flag_modified(planet, "active_events")

    locked_player.credits = (locked_player.credits or 0) + credits_earned

    return {
        "planet_id": str(planet.id),
        "planet_name": planet.name,
        "sold": to_sell,
        "price_per_pioneer": price,
        "credits_earned": credits_earned,
        "surplus_remaining": new_remaining,
        "player_credits": locked_player.credits,
    }
