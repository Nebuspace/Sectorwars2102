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

from src.models.migration_contract import (
    MigrationContract,
    MigrationContractStatus,
)
from src.models.planet import Planet
from src.models.station import Station
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
