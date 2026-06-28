"""
Player warp-gate construction routes (ADR-0029 + FEATURES/galaxy/warp-gates.md).

Three-phase ritual: deploy-beacon (source, 48h window) -> travel ->
anchor-focus (destination, 1h harmonization that consumes the Warp Jumper).
Every read endpoint runs the lazy advance (warp_gate_service.advance_gate) so
harmonization completion and beacon expiry settle on access — there is no
background worker. All project endpoints are ownership-gated: a project that
isn't yours 404s (no existence leak, mirrors construction.py).

Traversal does NOT live here — an ACTIVE gate is a WarpTunnel row
(type=ARTIFICIAL, one-way, 0 turns) that the normal movement endpoints pick
up via MovementService.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.services import warp_gate_service
from src.services.warp_gate_service import WarpGateError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/warp-gates", tags=["warp-gates"])


class DeployBeaconRequest(BaseModel):
    destination_sector_id: int


class AnchorFocusRequest(BaseModel):
    beacon_id: str


class DeployBeaconResponse(BaseModel):
    beacon_id: str
    invulnerable_until: Optional[str] = None
    costs_charged: Dict[str, int]


class AnchorFocusResponse(BaseModel):
    gate_id: str
    harmonization_completes_at: Optional[str] = None
    status: str


class CancelResponse(BaseModel):
    cancelled: str
    refunded: Dict[str, int]
    message: str


class SetPermissionsRequest(BaseModel):
    # One of PUBLIC / TEAM_ONLY / PRIVATE / WHITELIST / ALLIANCE
    # (warp-gates.md "Access control").
    mode: str
    # Player UUIDs for WHITELIST mode; team UUIDs for ALLIANCE allies. Both are
    # accepted on every call and persisted, but only consulted by their mode.
    whitelist: Optional[List[str]] = None
    allies: Optional[List[str]] = None


class SetPermissionsResponse(BaseModel):
    gate_id: str
    mode: str
    whitelist: List[str]
    allies: List[str]
    is_public: bool


class TransferRequest(BaseModel):
    new_owner_id: str
    sale_price: Optional[int] = None


class TransferResponse(BaseModel):
    gate_id: str
    previous_owner_id: str
    new_owner_id: str
    sale_price: int
    buyer_credits: int
    seller_credits: int
    access_carried_over: str


class ProjectEntry(BaseModel):
    beacon_id: str
    gate_id: Optional[str] = None
    phase: str
    source_sector_id: int
    source_name: Optional[str] = None
    destination_sector_id: int
    destination_name: Optional[str] = None
    invulnerable_until: Optional[str] = None
    harmonization_completes_at: Optional[str] = None
    created_at: Optional[str] = None


class MyProjectsResponse(BaseModel):
    projects: List[ProjectEntry]


class SectorStructuresResponse(BaseModel):
    beacons: List[Dict[str, Any]]
    gates: List[Dict[str, Any]]


@router.post("/deploy-beacon", response_model=DeployBeaconResponse)
async def deploy_beacon(
    request: DeployBeaconRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Phase 1 — deploy a WarpGateBeacon in the current sector. Validation
    failures cost nothing; on pass charges 50 turns + 10,000 cr + 1,000 ore +
    500 equipment + 1 Quantum Crystal."""
    try:
        result = warp_gate_service.deploy_beacon(db, player, request.destination_sector_id)
        db.commit()
    except WarpGateError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    beacon = result["beacon"]
    return DeployBeaconResponse(
        beacon_id=str(beacon.id),
        invulnerable_until=beacon.invulnerable_until.isoformat() if beacon.invulnerable_until else None,
        costs_charged=result["costs_charged"],
    )


@router.post("/anchor-focus", response_model=AnchorFocusResponse)
async def anchor_focus(
    request: AnchorFocusRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Phase 3 Step A — anchor the Warp Jumper at the destination focus.
    Charges 100 turns + 10,000 cr + 1,000 ore + 500 equipment + 30 lumen
    crystals; the ship enters HARMONIZING for one canonical hour."""
    try:
        result = warp_gate_service.anchor_focus(db, player, request.beacon_id)
        db.commit()
    except WarpGateError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    gate = result["gate"]
    return AnchorFocusResponse(
        gate_id=str(gate.id),
        harmonization_completes_at=result["harmonization_completes_at"].isoformat(),
        status="HARMONIZING",
    )


@router.get("/mine", response_model=MyProjectsResponse)
async def get_my_projects(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """All of the player's gate projects, lazily advanced — reading this
    endpoint settles harmonization completion and beacon expiry."""
    try:
        projects = warp_gate_service.list_player_projects(db, player)
        db.commit()
    except WarpGateError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return MyProjectsResponse(projects=[ProjectEntry(**p) for p in projects])


@router.get("/sector/{sector_id}", response_model=SectorStructuresResponse)
async def get_sector_structures(
    sector_id: int,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Beacons and active outbound gates in a sector — gate structures are
    physical objects visible to everyone passing through (canon)."""
    try:
        result = warp_gate_service.list_sector_structures(db, sector_id)
        db.commit()
    except WarpGateError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return SectorStructuresResponse(**result)


@router.post("/{gate_id}/permissions", response_model=SetPermissionsResponse)
async def set_permissions(
    gate_id: str,
    request: SetPermissionsRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """WO-DBB-WG1 — set an active gate's access mode, whitelist and allied
    teams atomically (owner-only; a gate that isn't yours 404s). The mode is
    enforced at traversal by warp_gate_service.check_traversal_access."""
    try:
        result = warp_gate_service.set_gate_permissions(
            db, player, gate_id,
            request.mode, request.whitelist, request.allies,
        )
        db.commit()
    except WarpGateError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return SetPermissionsResponse(**result)


@router.post("/{gate_id}/transfer", response_model=TransferResponse)
async def transfer_gate(
    gate_id: str,
    request: TransferRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """WO-DBB-WG2 — transfer an active gate to another player, carrying the
    toll / access / revenue config and settling an optional salePrice under
    row locks. The buyer's gate cap is enforced; on any failure no credits move
    and ownership is unchanged (single transaction)."""
    try:
        result = warp_gate_service.transfer_gate(
            db, player, gate_id, request.new_owner_id, request.sale_price,
        )
        db.commit()
    except WarpGateError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return TransferResponse(**result)


@router.post("/{gate_or_beacon_id}/cancel", response_model=CancelResponse)
async def cancel_project(
    gate_or_beacon_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Cancel a gate project per ADR-0029: a DEPLOYED beacon cancels with
    Phase 1 materials sunk; a HARMONIZING gate cancels with a full Phase 3
    refund and the Warp Jumper intact. The Quantum Crystal never refunds."""
    try:
        result = warp_gate_service.cancel(db, player, gate_or_beacon_id)
        db.commit()
    except WarpGateError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return CancelResponse(**result)
