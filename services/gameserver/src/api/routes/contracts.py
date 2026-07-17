"""
Trade Contract API routes. WO-ECON-CONTRACT-1-KERNEL lane 4 shipped
board/mine/{id} reads and accept/complete/abandon writes. WO-ECON-
CONTRACT-2-PLAYER-ESCROW adds player-issued posting (`POST /contracts`,
cargo_delivery only) and issuer-only `POST /contracts/{id}/cancel`.
WO-1a-CORE adds `POST /contracts/{id}/insure` (contracts.md:219/:224).
A claim-filing route (the state-transition diagram's "cargo destroyed in
transit -> cancelled (insurance pays if held)" edge, :84) was built and
then excised in the same round -- cipher's gate found the self-reported
"my ship is gone" check a farmable money-mint with no real destruction-
event verification behind it; that half is deferred to a dedicated,
design-gated WO-1b-CLAIM-SAFETY, not mounted here.
WO-CONTRACT-2-DISPUTE-T1 adds `POST /contracts/{id}/dispute` (contracts.md
:223, :291-305) -- acceptor-only filing + synchronous Tier-1 automated
arbitration. [NO-CANON] the response shape here is NOT contracts.md:296-
305's literal async-202 stub (status/dispute_filed_at/escrow_frozen/
estimated_resolution/arbitration_tier) -- Tier-1 in this build resolves
SYNCHRONOUSLY inside the same call (see contract_service.file_dispute's
own docstring), so by the time this route returns, resolution has often
ALREADY happened; returning canon's "pending" shape would misrepresent
that. Returns 200 with `contract_service.file_dispute`'s actual result
(tier1_resolution / escalated_to_admin / payout) instead. The Tier-2
admin ruling route (`POST /contracts/{id}/resolve-dispute`) is impl-
admin-ui's lane -- `contract_service.resolve_dispute` is exposed as a
function only, NOT mounted here. Bulk-partial `deliver` remains a later
build step (contracts.md:421-431 step 7) and is intentionally NOT
mounted here either.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.contract import Contract, ContractInsuranceCoverageTier, ContractStatus, ContractType
from src.models.player import Player
from src.services import contract_service
from src.services.contract_service import ContractConflictError, ContractError, ContractNotFoundError

router = APIRouter(prefix="/contracts", tags=["contracts"])


def _serialize_contract(c: Contract, caller_player_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    is_party = caller_player_id is not None and caller_player_id in (c.issuer_id, c.acceptor_player_id)
    return {
        "id": str(c.id),
        "issuer_type": c.issuer_type.value,
        "issuer_id": str(c.issuer_id),
        "acceptor_player_id": str(c.acceptor_player_id) if c.acceptor_player_id else None,
        "contract_type": c.contract_type.value,
        "status": c.status.value,
        "origin_station_id": str(c.origin_station_id) if c.origin_station_id else None,
        "destination_station_id": str(c.destination_station_id),
        "commodity_type": c.commodity_type,
        "quantity": c.quantity,
        "payment": float(c.payment) if c.payment is not None else None,
        "penalty": float(c.penalty) if c.penalty is not None else None,
        "acceptance_fee_pct": float(c.acceptance_fee_pct) if c.acceptance_fee_pct is not None else None,
        "escrow_amount": float(c.escrow_amount) if c.escrow_amount is not None else None,
        "escrow_state": c.escrow_state.value if c.escrow_state else None,
        "faction_id": str(c.faction_id) if c.faction_id else None,
        "deadline": c.deadline.isoformat() if c.deadline else None,
        "posted_at": c.posted_at.isoformat() if c.posted_at else None,
        "accepted_at": c.accepted_at.isoformat() if c.accepted_at else None,
        "completed_at": c.completed_at.isoformat() if c.completed_at else None,
        # WO-CONTRACT-1-INSURANCE
        "insurance_coverage_tier": c.insurance_coverage_tier.value if c.insurance_coverage_tier else None,
        "insurance_premium_paid": float(c.insurance_premium_paid) if c.insurance_premium_paid is not None else None,
        "insurance_claim_filed": bool(c.insurance_claim_filed),
        # WO-CONTRACT-2-DISPUTE-T1
        "dispute_filed_at": c.dispute_filed_at.isoformat() if c.dispute_filed_at else None,
        "dispute_resolution": c.dispute_resolution.value if c.dispute_resolution else None,
        "dispute_resolved_at": c.dispute_resolved_at.isoformat() if c.dispute_resolved_at else None,
        # Party-only: dispute_notes is free-text reason/evidence a non-party
        # must not read (a contract UUID is discoverable via the public
        # /board endpoint, so omission must not depend on obscurity).
        "dispute_notes": c.dispute_notes if is_party else None,
        "escalated_to_admin": bool(c.escalated_to_admin),
    }


def _parse_uuid(raw: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name}") from None


class PostContractRequest(BaseModel):
    """cargo_delivery OR bulk_procurement (WO-CONTRACT-4-BULK extends the
    original cargo_delivery-only shape, WO-ECON-CONTRACT-2-PLAYER-ESCROW)
    -- contracts.md:219-232's request shape, trimmed to what this kernel
    exercises."""

    destination_station_id: str
    commodity_type: str
    quantity: int = Field(..., gt=0)
    # WO-CONTRACT-4-BULK: restricted to just these two -- the other 5
    # ContractType members (express_delivery/hazardous_transport/
    # refugee_transport/acquisition_bounty/escort) carry NPC-generator-
    # only pricing (type multipliers, reputation-penalty deltas -- see
    # contract_generator.py's own _classify_and_price_contract) that
    # post_player_contract never computes; a player posting one of those
    # would silently skip fields that type's own downstream logic
    # expects. Defaults to cargo_delivery -- byte-identical request shape
    # for every existing caller that omits this field.
    contract_type: ContractType = Field(default=ContractType.CARGO_DELIVERY)

    @field_validator("contract_type")
    @classmethod
    def _validate_contract_type(cls, value: ContractType) -> ContractType:
        if value not in (ContractType.CARGO_DELIVERY, ContractType.BULK_PROCUREMENT):
            raise ValueError(
                f"contract_type must be 'cargo_delivery' or 'bulk_procurement', got '{value.value}'"
            )
        return value
    # WO-ECON-CONTRACT-MONEY-HARDEN (Mack LOW #3): Player.credits is a
    # whole-credit integer column and penalty defaults to 1.0x payment
    # (post_player_contract) -- a fractional payment can never be honored
    # exactly regardless of how carefully the service-side rounding is
    # done, so it's rejected here rather than silently coerced. multiple_of
    # validates against the Decimal's numeric VALUE (1000.00 passes,
    # 1000.50 doesn't), not its string precision.
    payment: Decimal = Field(..., gt=0, multiple_of=1)
    deadline: datetime
    origin_station_id: Optional[str] = None
    # WO-CONTRACT-1b-CLAIM-SAFETY (cipher MEDIUM): a FRACTIONAL reserve lets
    # the sweep's `refund = escrow_amount - pool_draw` and `acceptor_debit =
    # penalty - pool_draw` round INDEPENDENTLY -- since escrow_amount =
    # payment + reserve and refund - acceptor_debit == reserve exactly in
    # real arithmetic, a fractional reserve can make one round down and the
    # other round up, minting ~1cr per cycle. Whole-credit reserve (matching
    # `payment`'s own multiple_of=1 above) makes frac(refund) ==
    # frac(acceptor_debit) always, so round(refund) - round(acceptor_debit)
    # == reserve holds exactly -- the rounding lever is gone by construction.
    insurance_pool_reserve: Decimal = Field(default=Decimal("0"), ge=0, multiple_of=1)


class InsureContractRequest(BaseModel):
    """contracts.md's Risk & insurance table (:359-365) -- one of the
    three coverage tiers."""

    tier: str


class DisputeContractRequest(BaseModel):
    """contracts.md:295 request shape -- `evidence_snapshot` is optional
    free-form (a URL/reference, matching canon's own worked example).
    WO-CONTRACT-2-DISPUTE-T1-REVISE (mack LOW): both fields fold
    unbounded into `dispute_notes` (Text, no DB-side limit) --
    max_length caps this attacker-initiated free text at the request
    boundary (2000/500 chars, matching this codebase's existing free-
    text caps -- e.g. MarketTransaction.admin_notes/PriceAlert.message
    both cap at 500)."""

    reason: str = Field(..., min_length=1, max_length=2000)
    evidence_snapshot: Optional[str] = Field(default=None, max_length=500)


def _raise_for(exc: ContractError) -> None:
    if isinstance(exc, ContractNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ContractConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/board")
async def get_contract_board(
    station_id: str = Query(..., description="List contracts visible at this station"),
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> List[Dict[str, Any]]:
    """contracts.md:203, :92-98. A board is the union of: NPC contracts
    ISSUED by this station (issuer_id -- see contract.py's issuer_id
    docstring for why that's destination_station_id, not origin, for
    cargo_delivery) and player-posted contracts listing this station in
    posting_stations (the latter is wired now so it activates
    automatically once CONTRACT-2 ships player posting -- this WO
    generates no player-issued rows yet)."""
    station_uuid = _parse_uuid(station_id, "station_id")
    contracts = (
        db.query(Contract)
        .filter(
            Contract.status == ContractStatus.POSTED,
            or_(
                Contract.issuer_id == station_uuid,
                Contract.posting_stations.any(station_uuid),
            ),
        )
        .order_by(Contract.posted_at.desc())
        .all()
    )
    return [_serialize_contract(c, current_player.id) for c in contracts]


@router.get("/mine")
async def get_my_contracts(
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, List[Dict[str, Any]]]:
    """contracts.md:204. The caller's posted (issuer_type=player,
    issuer_id=self -- none exist yet, this WO never sets issuer_type=
    player) plus accepted contracts."""
    accepted = (
        db.query(Contract)
        .filter(Contract.acceptor_player_id == current_player.id)
        .order_by(Contract.posted_at.desc())
        .all()
    )
    posted = (
        db.query(Contract)
        .filter(Contract.issuer_id == current_player.id)
        .order_by(Contract.posted_at.desc())
        .all()
    )
    return {
        "posted": [_serialize_contract(c, current_player.id) for c in posted],
        "accepted": [_serialize_contract(c, current_player.id) for c in accepted],
    }


@router.get("/{contract_id}")
async def get_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """contracts.md:205."""
    contract_uuid = _parse_uuid(contract_id, "contract_id")
    contract = db.query(Contract).filter(Contract.id == contract_uuid).first()
    if contract is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")
    return _serialize_contract(contract, current_player.id)


@router.post("/{contract_id}/accept")
async def accept_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """contracts.md:207, :247-262."""
    contract_uuid = _parse_uuid(contract_id, "contract_id")
    try:
        result = contract_service.accept(db, contract_uuid, current_player.id)
    except ContractError as exc:
        db.rollback()
        _raise_for(exc)
    except OperationalError:
        # WO-CONTRACT-LOCK-ORDER (task #54): a deadlock (40P01) or lock-
        # timeout (55P03) surfaces here as sqlalchemy.exc.OperationalError
        # -- previously uncaught, a raw 500 (money-safe regardless: get_db's
        # own `finally: db.close()` discards the uncommitted transaction,
        # nothing partially lands). Every contract-mutating route shares
        # this identical shape and gets the identical clean-retryable
        # response -- the client's retry is exactly correct here, unlike a
        # real 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This contract is busy with another operation -- try again in a moment.",
        ) from None
    else:
        db.commit()
        return result


@router.post("/{contract_id}/complete")
async def complete_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """contracts.md:210 -- server verifies cargo at destination."""
    contract_uuid = _parse_uuid(contract_id, "contract_id")
    try:
        result = contract_service.complete(db, contract_uuid, current_player.id)
    except ContractError as exc:
        db.rollback()
        _raise_for(exc)
    except OperationalError:
        # WO-CONTRACT-LOCK-ORDER (task #54): a deadlock (40P01) or lock-
        # timeout (55P03) surfaces here as sqlalchemy.exc.OperationalError
        # -- previously uncaught, a raw 500 (money-safe regardless: get_db's
        # own `finally: db.close()` discards the uncommitted transaction,
        # nothing partially lands). Every contract-mutating route shares
        # this identical shape and gets the identical clean-retryable
        # response -- the client's retry is exactly correct here, unlike a
        # real 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This contract is busy with another operation -- try again in a moment.",
        ) from None
    else:
        db.commit()
        return result


@router.post("/{contract_id}/abandon")
async def abandon_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """Kernel scope: the mutual-cancel / kill-fee flavor at
    contracts.md:211 targets player-issued contracts (CONTRACT-2); an
    NPC-issued contract's acceptor walking away simply pays the flat
    penalty -- there's no counterparty issuer to owe a kill-fee to."""
    contract_uuid = _parse_uuid(contract_id, "contract_id")
    try:
        result = contract_service.abandon(db, contract_uuid, current_player.id)
    except ContractError as exc:
        db.rollback()
        _raise_for(exc)
    except OperationalError:
        # WO-CONTRACT-LOCK-ORDER (task #54): a deadlock (40P01) or lock-
        # timeout (55P03) surfaces here as sqlalchemy.exc.OperationalError
        # -- previously uncaught, a raw 500 (money-safe regardless: get_db's
        # own `finally: db.close()` discards the uncommitted transaction,
        # nothing partially lands). Every contract-mutating route shares
        # this identical shape and gets the identical clean-retryable
        # response -- the client's retry is exactly correct here, unlike a
        # real 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This contract is busy with another operation -- try again in a moment.",
        ) from None
    else:
        db.commit()
        return result


@router.post("/{contract_id}/insure")
async def insure_contract(
    contract_id: str,
    body: InsureContractRequest,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """contracts.md:219/:224 -- buy coverage on an accepted contract. See
    contract_service.insure's own docstring for the verify-first finding on
    why this is a separate endpoint (not folded into `/accept`)."""
    contract_uuid = _parse_uuid(contract_id, "contract_id")
    try:
        tier = ContractInsuranceCoverageTier(body.tier)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown insurance tier '{body.tier}' -- expected one of "
            f"{[t.value for t in ContractInsuranceCoverageTier]}",
        ) from None
    try:
        result = contract_service.insure(db, contract_uuid, current_player.id, tier)
    except ContractError as exc:
        db.rollback()
        _raise_for(exc)
    except OperationalError:
        # WO-CONTRACT-LOCK-ORDER (task #54): a deadlock (40P01) or lock-
        # timeout (55P03) surfaces here as sqlalchemy.exc.OperationalError
        # -- previously uncaught, a raw 500 (money-safe regardless: get_db's
        # own `finally: db.close()` discards the uncommitted transaction,
        # nothing partially lands). Every contract-mutating route shares
        # this identical shape and gets the identical clean-retryable
        # response -- the client's retry is exactly correct here, unlike a
        # real 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This contract is busy with another operation -- try again in a moment.",
        ) from None
    else:
        db.commit()
        return result


@router.post("/{contract_id}/dispute")
async def dispute_contract(
    contract_id: str,
    body: DisputeContractRequest,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """contracts.md:223/:291-305 -- acceptor-only filing on a failed
    (expired) contract, within 48 hours of the failure timestamp. See
    contract_service.file_dispute's own docstring for the synchronous
    Tier-1 arbitration this triggers, and this module's own docstring
    for why the response shape here isn't canon's literal async-202
    stub."""
    contract_uuid = _parse_uuid(contract_id, "contract_id")
    try:
        result = contract_service.file_dispute(
            db, contract_uuid, current_player.id, body.reason,
            evidence_snapshot=body.evidence_snapshot,
        )
    except ContractError as exc:
        db.rollback()
        _raise_for(exc)
    except OperationalError:
        # WO-CONTRACT-LOCK-ORDER (task #54): a deadlock (40P01) or lock-
        # timeout (55P03) surfaces here as sqlalchemy.exc.OperationalError
        # -- previously uncaught, a raw 500 (money-safe regardless: get_db's
        # own `finally: db.close()` discards the uncommitted transaction,
        # nothing partially lands). Every contract-mutating route shares
        # this identical shape and gets the identical clean-retryable
        # response -- the client's retry is exactly correct here, unlike a
        # real 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This contract is busy with another operation -- try again in a moment.",
        ) from None
    else:
        db.commit()
        return result


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_contract(
    body: PostContractRequest,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """contracts.md:206, :219-245. Debits escrow_amount from the caller at
    post time -- see contract_service.post_player_contract's docstring for
    the full validation order and the [NO-CANON] insurance_pool_reserve
    default."""
    destination_uuid = _parse_uuid(body.destination_station_id, "destination_station_id")
    origin_uuid = (
        _parse_uuid(body.origin_station_id, "origin_station_id")
        if body.origin_station_id
        else None
    )
    try:
        result = contract_service.post_player_contract(
            db, current_player.id, destination_uuid, body.commodity_type, body.quantity,
            body.payment, body.deadline, origin_station_id=origin_uuid,
            insurance_pool_reserve=body.insurance_pool_reserve,
            contract_type=body.contract_type,
        )
    except ContractError as exc:
        db.rollback()
        _raise_for(exc)
    except OperationalError:
        # WO-CONTRACT-LOCK-ORDER (task #54): a deadlock (40P01) or lock-
        # timeout (55P03) surfaces here as sqlalchemy.exc.OperationalError
        # -- previously uncaught, a raw 500 (money-safe regardless: get_db's
        # own `finally: db.close()` discards the uncommitted transaction,
        # nothing partially lands). Every contract-mutating route shares
        # this identical shape and gets the identical clean-retryable
        # response -- the client's retry is exactly correct here, unlike a
        # real 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This contract is busy with another operation -- try again in a moment.",
        ) from None
    else:
        db.commit()
        return result


@router.post("/{contract_id}/cancel")
async def cancel_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """contracts.md:211. Issuer-only -- see contract_service.cancel_
    player_contract's docstring for the two reachable matrix rows and the
    [NO-CANON] issuer-unilateral simplification of the post-accept
    "mutual cancel" row."""
    contract_uuid = _parse_uuid(contract_id, "contract_id")
    try:
        result = contract_service.cancel_player_contract(db, contract_uuid, current_player.id)
    except ContractError as exc:
        db.rollback()
        _raise_for(exc)
    except OperationalError:
        # WO-CONTRACT-LOCK-ORDER (task #54): a deadlock (40P01) or lock-
        # timeout (55P03) surfaces here as sqlalchemy.exc.OperationalError
        # -- previously uncaught, a raw 500 (money-safe regardless: get_db's
        # own `finally: db.close()` discards the uncommitted transaction,
        # nothing partially lands). Every contract-mutating route shares
        # this identical shape and gets the identical clean-retryable
        # response -- the client's retry is exactly correct here, unlike a
        # real 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This contract is busy with another operation -- try again in a moment.",
        ) from None
    else:
        db.commit()
        return result
