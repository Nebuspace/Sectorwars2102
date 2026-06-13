"""Pioneer Office API — broker and ferry pioneer migration contracts at a
capital population hub (FEATURES/planets/colonization.md).

CANON DEVIATION (deliberate): canon places the Pioneer Office at the
Capital Sector's Class-0 station, reached by docking; the colonist
commodity buy at that station is the shipped path and is left intact.
These endpoints instead surface the Office on the population-hub PLANET
(landed) and add a tracked migration-contract layer on top. Every
endpoint gates on the player being landed on a population hub, so ordinary
planets and the station buy flow are unaffected.

Contract lifecycle: broker (lock fee) -> load batches into cargo
(``loaded``) -> settle on frontier worlds (``delivered`` advances via the
claim/disembark ledger in pioneer_service) -> FULFILLED when
delivered == cohort_total.
"""

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.planet import Planet
from src.models.ship import Ship
from src.models.migration_contract import (
    MigrationContract,
    MigrationContractStatus,
)
from src.services import pioneer_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pioneer", tags=["pioneer"])

# Canon: the full migration contract tops out at a 10,000-pioneer cohort.
MAX_COHORT = 10_000

_OPEN_STATUSES = (
    MigrationContractStatus.BROKERED,
    MigrationContractStatus.IN_PROGRESS,
)


# ----------------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------------

class BrokerRequest(BaseModel):
    cohort_total: int = Field(..., gt=0, le=MAX_COHORT)


class LoadRequest(BaseModel):
    quantity: int = Field(..., gt=0)


class ContractOut(BaseModel):
    id: str
    source_planet_id: str
    source_planet_name: Optional[str] = None
    source_sector_id: int
    cohort_total: int
    loaded: int
    delivered: int
    remaining_to_load: int
    fee_per_pioneer_locked: int
    status: str


class OfficeOut(BaseModel):
    planet_id: str
    planet_name: str
    fee_per_pioneer: int
    cargo_colonists: int
    cargo_free: int
    contracts: List[ContractOut]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _require_landed_on_hub(player: Player, db: Session) -> Planet:
    """Assert the player is landed on a capital population hub; return it.

    Belt-and-braces population check mirrors the claim/landing guards
    (planets.py): a missed is_population_hub flag can't strand the hub.
    """
    if not player.is_landed or not player.current_planet_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be landed on a population hub to use the Pioneer Office.",
        )
    planet = db.query(Planet).filter(Planet.id == player.current_planet_id).first()
    if planet is None:
        raise HTTPException(status_code=404, detail="Planet not found")
    is_hub = bool(planet.is_population_hub) or (planet.population or 0) >= 1_000_000
    if not is_hub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The Pioneer Office operates only at a Capital Sector population hub.",
        )
    return planet


def _contract_out(c: MigrationContract, planet_name: Optional[str] = None) -> ContractOut:
    return ContractOut(
        id=str(c.id),
        source_planet_id=str(c.source_planet_id),
        source_planet_name=planet_name,
        source_sector_id=c.source_sector_id,
        cohort_total=c.cohort_total,
        loaded=c.loaded or 0,
        delivered=c.delivered or 0,
        remaining_to_load=c.remaining_to_load,
        fee_per_pioneer_locked=c.fee_per_pioneer_locked,
        status=c.status.value,
    )


def _active_ship(player: Player, db: Session) -> Ship:
    ship = db.query(Ship).filter(
        Ship.id == player.current_ship_id,
        Ship.owner_id == player.id,
    ).first()
    if not ship:
        raise HTTPException(status_code=404, detail="No active ship found")
    return ship


# ----------------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------------

@router.get("/office", response_model=OfficeOut)
async def pioneer_office(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """One-round-trip render payload for the Pioneer Office venue: the live
    fee quote, current cargo colonists + free space, and the player's open
    contracts."""
    planet = _require_landed_on_hub(player, db)
    fee = pioneer_service.quote_fee(db, planet)

    ship = _active_ship(player, db)
    cargo = ship.cargo or {"used": 0, "capacity": 0, "contents": {}}
    cargo_colonists = int((cargo.get("contents") or {}).get("colonists", 0) or 0)
    cargo_free = int((cargo.get("capacity", 0) or 0) - (cargo.get("used", 0) or 0))

    contracts = (
        db.query(MigrationContract)
        .filter(
            MigrationContract.player_id == player.id,
            MigrationContract.status.in_(_OPEN_STATUSES),
        )
        .order_by(MigrationContract.created_at.asc())
        .all()
    )
    # Resolve source planet names in one pass.
    names = {
        str(p.id): p.name
        for p in db.query(Planet.id, Planet.name)
        .filter(Planet.id.in_([c.source_planet_id for c in contracts]))
        .all()
    } if contracts else {}

    return OfficeOut(
        planet_id=str(planet.id),
        planet_name=planet.name,
        fee_per_pioneer=fee,
        cargo_colonists=cargo_colonists,
        cargo_free=max(0, cargo_free),
        contracts=[_contract_out(c, names.get(str(c.source_planet_id))) for c in contracts],
    )


@router.post("/contracts", response_model=ContractOut)
async def broker_contract(
    req: BrokerRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Broker a cohort at the hub: lock the per-pioneer fee, create a
    BROKERED contract. No credits are charged at broker time — the fee is
    paid per batch as pioneers are loaded (canon: ferry them in batches)."""
    planet = _require_landed_on_hub(player, db)
    fee = pioneer_service.quote_fee(db, planet)

    contract = MigrationContract(
        player_id=player.id,
        source_planet_id=planet.id,
        source_sector_id=planet.sector_id,
        cohort_total=req.cohort_total,
        loaded=0,
        delivered=0,
        fee_per_pioneer_locked=fee,
        status=MigrationContractStatus.BROKERED,
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)
    return _contract_out(contract, planet.name)


@router.post("/contracts/{contract_id}/load", response_model=ContractOut)
async def load_batch(
    contract_id: str,
    req: LoadRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Load a batch of pioneers from the hub into ship cargo against a
    contract. Charges the locked fee x batch; clamps to remaining cohort,
    cargo free space, and affordable credits (400 with the binding reason —
    never silently truncated)."""
    planet = _require_landed_on_hub(player, db)

    try:
        cid = UUID(contract_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Contract not found")

    # Lock player first, then the contract row (leaf), serializing concurrent
    # loads on the same contract.
    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    contract = (
        db.query(MigrationContract)
        .filter(
            MigrationContract.id == cid,
            MigrationContract.player_id == locked_player.id,
        )
        .with_for_update()
        .first()
    )
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.status not in _OPEN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Contract is {contract.status.value.lower()} and cannot take on more pioneers.",
        )
    if contract.source_planet_id != planet.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"This contract was brokered at Sector {contract.source_sector_id}. "
                "Return to that Pioneer Office to load its pioneers."
            ),
        )

    ship = _active_ship(locked_player, db)
    cargo = ship.cargo or {"used": 0, "capacity": 0, "contents": {}}
    cargo_free = int((cargo.get("capacity", 0) or 0) - (cargo.get("used", 0) or 0))
    remaining = contract.remaining_to_load
    fee = contract.fee_per_pioneer_locked
    affordable = (locked_player.credits // fee) if fee > 0 else req.quantity

    max_loadable = max(0, min(remaining, cargo_free, affordable))

    if req.quantity > remaining:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only {remaining} pioneers remain on this contract. (max loadable now: {max_loadable})",
        )
    if req.quantity > cargo_free:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient cargo space. {cargo_free} free, need {req.quantity}. (max loadable now: {max_loadable})",
        )
    total = fee * req.quantity
    if locked_player.credits < total:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient credits. Need {total}, have {locked_player.credits}. (max loadable now: {max_loadable})",
        )

    # Execute: charge fee, load colonists into cargo, advance the contract.
    locked_player.credits -= total
    contents = cargo.get("contents") or {}
    contents["colonists"] = int(contents.get("colonists", 0) or 0) + req.quantity
    cargo["contents"] = contents
    cargo["used"] = int(cargo.get("used", 0) or 0) + req.quantity
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    contract.loaded = (contract.loaded or 0) + req.quantity
    if contract.status == MigrationContractStatus.BROKERED:
        contract.status = MigrationContractStatus.IN_PROGRESS

    db.commit()
    db.refresh(contract)
    return _contract_out(contract, planet.name)


@router.get("/contracts", response_model=List[ContractOut])
async def list_contracts(
    include_closed: bool = False,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """List the player's migration contracts (open by default; all when
    include_closed=true). Reachable from anywhere, not just the hub."""
    q = db.query(MigrationContract).filter(MigrationContract.player_id == player.id)
    if not include_closed:
        q = q.filter(MigrationContract.status.in_(_OPEN_STATUSES))
    contracts = q.order_by(MigrationContract.created_at.asc()).all()
    names = {
        str(p.id): p.name
        for p in db.query(Planet.id, Planet.name)
        .filter(Planet.id.in_([c.source_planet_id for c in contracts]))
        .all()
    } if contracts else {}
    return [_contract_out(c, names.get(str(c.source_planet_id))) for c in contracts]


@router.post("/contracts/{contract_id}/cancel", response_model=ContractOut)
async def cancel_contract(
    contract_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Void a contract. Only allowed while no pioneers are loaded — you
    cannot void cryosleep pods physically in your hold; disembark or settle
    them first. The brokerage fee is not refunded (it was a fee, not
    escrow)."""
    try:
        cid = UUID(contract_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Contract not found")

    contract = (
        db.query(MigrationContract)
        .filter(
            MigrationContract.id == cid,
            MigrationContract.player_id == player.id,
        )
        .with_for_update()
        .first()
    )
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.status not in _OPEN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Contract is already {contract.status.value.lower()}.",
        )
    if (contract.loaded or 0) > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{contract.loaded} pioneers are aboard against this contract. "
                "Settle or disembark them before voiding it."
            ),
        )
    contract.status = MigrationContractStatus.VOID
    db.commit()
    db.refresh(contract)
    return _contract_out(contract)
