"""StorageLocker API routes -- WO-STORE-DEPOSIT-FLOW / WO-STORE-EXPIRY-
CLAIMABLE (STORAGE-HEIST S1).

- ``POST /storage/lockers``                 -- rent/reuse a locker for an
  accepted contract at its destination station.
- ``POST /storage/lockers/{locker_id}/deposit`` -- deposit cargo toward
  the locker's contract; auto-completes the contract on full quantity
  (see storage_service.deposit_cargo's own docstring for the "cargo
  bridge" that delegates to contract_service.complete()).
- ``POST /storage/lockers/{locker_id}/retrieve`` -- retrieve cargo from a
  CLAIMABLE locker (a contract that missed its deadline) back onto your
  ship; omit `quantity` to take as much as fits in one trip.

Route owns db.commit() / db.rollback() -- storage_service is flush-only
throughout, matching contracts.py's own exact convention (accept_
contract / complete_contract are this route's direct template).
"""
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.services import storage_service
from src.services.storage_service import StorageError, StorageNotFoundError

router = APIRouter(prefix="/storage", tags=["storage"])


class RentLockerRequest(BaseModel):
    contract_id: str


class DepositCargoRequest(BaseModel):
    quantity: int = Field(..., gt=0)


class RetrieveCargoRequest(BaseModel):
    # Optional: omit to take as much as fits in one trip (see
    # storage_service.retrieve_claimable_cargo's own docstring for why
    # partial multi-trip retrieve, not reject-if-over, is the design).
    quantity: Optional[int] = Field(None, gt=0)


def _parse_uuid(raw: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name}") from None


def _raise_for(exc: StorageError) -> None:
    if isinstance(exc, StorageNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _serialize_locker(locker: Any) -> Dict[str, Any]:
    return {
        "id": str(locker.id),
        "ownerPlayerId": str(locker.owner_player_id),
        "stationId": str(locker.station_id),
        "contractId": str(locker.contract_id) if locker.contract_id else None,
        "status": locker.status.value,
        "tier": locker.tier.value,
        "riskState": locker.risk_state.value,
        "rentRate": float(locker.rent_rate),
        "accruedFee": float(locker.accrued_fee),
        "createdAt": locker.created_at.isoformat() if locker.created_at else None,
    }


@router.post("/lockers", status_code=status.HTTP_201_CREATED)
async def rent_locker(
    body: RentLockerRequest,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """Rent (or reuse) a locker for a contract you've accepted -- the
    locker is created at the contract's own destination_station_id.
    Idempotent: a second call for the same contract returns your
    existing locker rather than minting a duplicate."""
    contract_uuid = _parse_uuid(body.contract_id, "contract_id")
    try:
        locker = storage_service.get_or_create_locker(db, current_player.id, contract_uuid)
    except StorageError as exc:
        db.rollback()
        _raise_for(exc)
    else:
        db.commit()
        return _serialize_locker(locker)


@router.post("/lockers/{locker_id}/deposit")
async def deposit_cargo(
    locker_id: str,
    body: DepositCargoRequest,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """Deposit cargo from your current ship into a locker you own.
    Auto-completes the underlying contract (payout + guard-transition)
    the moment the locker's accumulated deposits reach the contract's
    required quantity -- see storage_service.deposit_cargo for the
    full mechanics."""
    locker_uuid = _parse_uuid(locker_id, "locker_id")
    try:
        result = storage_service.deposit_cargo(db, locker_uuid, current_player.id, body.quantity)
    except StorageError as exc:
        db.rollback()
        _raise_for(exc)
    else:
        db.commit()
        return result


@router.post("/lockers/{locker_id}/retrieve")
async def retrieve_cargo(
    locker_id: str,
    body: RetrieveCargoRequest,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """Retrieve cargo from a CLAIMABLE locker (a contract whose deadline
    passed before reaching full quantity -- see storage_service.sweep_
    expired_lockers) back onto your current ship. Omit `quantity` to
    take as much as fits in one trip; a locker larger than your hold
    stays CLAIMABLE with the remainder for a later trip -- the retrieve-
    side mirror of the deposit flow's own multi-trip design."""
    locker_uuid = _parse_uuid(locker_id, "locker_id")
    try:
        result = storage_service.retrieve_claimable_cargo(db, locker_uuid, current_player.id, body.quantity)
    except StorageError as exc:
        db.rollback()
        _raise_for(exc)
    else:
        db.commit()
        return result
