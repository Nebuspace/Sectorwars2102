"""
Tier-2 admin arbitration for trade-contract disputes (contracts.md:404-416,
WO-CONTRACT-6-DISPUTE-T2-ADMIN). Tier-1 (contract_service.file_dispute) is
synchronous and automated; anything it can't resolve escalates here
(status == DISPUTED, escalated_to_admin == True) for a human ruling.

`contract_service.resolve_dispute` is authz-FREE by design (it only logs
admin_id) -- this route owns ALL authz via ``require_scope`` (``PLAYERS_VIEW``
for read/list; ``DISPUTES_RESOLVE`` for the Tier-2 ruling).  Scope deps
are resolved BEFORE ``db``/the service call on every mutating endpoint, so an
unauthenticated or scopeless caller is rejected before ``resolve_dispute`` (and
therefore any credit mutation) is ever reached -- see
tests/unit/test_admin_contract_disputes.py for the route-level proof.

[Honest gap] canon's own Tier-2 section also names reputation/cooldowns and
a "2 false disputes in 30d -> manual-review flag" auto-escalation
(contracts.md dispute section). Neither exists in this codebase yet
(contract_service.py's own dispute-section header comment: Settlement column
only, no reputation system) -- this route does not invent one. The queue and
ruling form below are the whole Tier-2 surface for now; a reputation/
cooldown cross-link is deferred to when that system actually lands.
"""
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.admin_scopes import DISPUTES_RESOLVE, PLAYERS_VIEW
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.contract import Contract, ContractDisputeResolution, ContractStatus
from src.models.user import User
from src.services.admin_action_attempt import admin_action_attempt
from src.services.contract_service import (
    ContractConflictError,
    ContractError,
    ContractNotFoundError,
    resolve_dispute,
)

router = APIRouter(prefix="/admin/contracts", tags=["admin-contract-disputes"])


def _parse_uuid(raw: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name}") from None


def _raise_for(exc: ContractError) -> None:
    if isinstance(exc, ContractNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ContractConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _serialize_dispute(c: Contract) -> Dict[str, Any]:
    """Evidence-panel fields for the Tier-2 console -- an admin sees every
    field (no is_party gating unlike the player-facing serializer in
    contracts.py)."""
    return {
        "id": str(c.id),
        "payment": float(c.payment) if c.payment is not None else None,
        "penalty": float(c.penalty) if c.penalty is not None else None,
        "dispute_notes": c.dispute_notes,
        "dispute_filed_at": c.dispute_filed_at.isoformat() if c.dispute_filed_at else None,
        "deadline": c.deadline.isoformat() if c.deadline else None,
        "commodity_type": c.commodity_type,
        "quantity": c.quantity,
        "acceptor_player_id": str(c.acceptor_player_id) if c.acceptor_player_id else None,
        "issuer_type": c.issuer_type.value if c.issuer_type else None,
        "issuer_id": str(c.issuer_id) if c.issuer_id else None,
        "escalated_to_admin": bool(c.escalated_to_admin),
        "contract_type": c.contract_type.value if c.contract_type else None,
        "status": c.status.value if c.status else None,
    }


class ResolveDisputeRequest(BaseModel):
    outcome: str = Field(
        ..., description="One of: full_payout, partial_payout, refund, split, penalty"
    )
    notes: Optional[str] = Field(default=None, max_length=2000)


@router.get("/disputes")
async def list_disputed_contracts(
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """The Tier-2 queue: contracts Tier-1 escalated (contracts.md:404)."""
    contracts = (
        db.query(Contract)
        .filter(
            Contract.status == ContractStatus.DISPUTED,
            Contract.escalated_to_admin.is_(True),
        )
        .order_by(Contract.dispute_filed_at.asc())
        .all()
    )
    return [_serialize_dispute(c) for c in contracts]


@router.get("/{contract_id}")
async def get_disputed_contract(
    contract_id: str,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Single-contract evidence detail for the arbitration panel."""
    contract_uuid = _parse_uuid(contract_id, "contract_id")
    contract = db.query(Contract).filter(Contract.id == contract_uuid).first()
    if contract is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")
    return _serialize_dispute(contract)


@router.post("/{contract_id}/resolve-dispute")
async def resolve_contract_dispute(
    contract_id: str,
    body: ResolveDisputeRequest,
    admin: User = Depends(require_scope(DISPUTES_RESOLVE)),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """The Tier-2 ruling. ``admin`` resolves first -- an unauthenticated or
    scopeless caller never reaches ``resolve_dispute`` below."""
    contract_uuid = _parse_uuid(contract_id, "contract_id")
    try:
        outcome = ContractDisputeResolution(body.outcome)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown outcome '{body.outcome}' -- expected one of "
            f"{[o.value for o in ContractDisputeResolution]}",
        ) from None

    with admin_action_attempt(
        db,
        actor=admin,
        scope_used=DISPUTES_RESOLVE,
        action="contract_dispute_resolve",
        target_type="contract",
        target_id=str(contract_uuid),
        payload={"outcome": outcome.value},
    ) as attempt:
        try:
            result = resolve_dispute(
                db, contract_uuid, admin.id, outcome=outcome, notes=body.notes
            )
        except ContractError as exc:
            _raise_for(exc)
        attempt.succeed(payload={"outcome": outcome.value})
        db.commit()
        return result
