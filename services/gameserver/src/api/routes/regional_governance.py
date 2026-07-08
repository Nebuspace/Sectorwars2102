"""
Regional Governance API Routes
Provides endpoints for regional owners to manage their territories, governance, and policies
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy import select, update, func, and_, or_
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import uuid

from src.core.database import get_async_session, get_db
from src.auth.dependencies import get_current_user, require_auth
from src.models.user import User
from src.models.region import (
    Region, RegionalMembership, RegionalPolicy, RegionalElection, 
    RegionalVote, RegionalTreaty, GovernanceType, PolicyStatus, ElectionStatus
)
from src.models.player import Player
from src.models.sector import Sector
from src.models.planet import Planet
from src.models.station import Station
from src.models.ship import Ship
from src.models.region_invite import RegionInvite
from src.services.regional_governance_service import RegionalGovernanceService
from src.services.policy_proposal_rules import validate_proposed_changes
from src.services import trading_service
from src.services import construction_service
from src.services.construction_service import ConstructionError
from src.services.region_invite_service import (
    RegionInviteService,
    DEFAULT_MAX_USES,
    MAX_MAX_USES,
)
from pydantic import BaseModel, Field


router = APIRouter(prefix="/regions")


# Map governance-service rejection codes to HTTP status. Anything not listed is
# a 400 (a validation/eligibility failure the caller can act on).
_VOTE_ERROR_STATUS = {
    "ERR_ELECTION_NOT_FOUND": 404,
    "ERR_POLICY_NOT_FOUND": 404,
    "ERR_REGION_NOT_FOUND": 404,
    "ERR_ELECTION_NOT_IN_REGION": 404,
    "ERR_POLICY_NOT_IN_REGION": 404,
    "ERR_NOT_A_MEMBER": 403,
    "ERR_NOT_ELIGIBLE": 403,
    "ERR_ALREADY_VOTED": 409,
    "ERR_ELECTION_NOT_ACTIVE": 409,
    "ERR_POLICY_NOT_VOTING": 409,
    "ERR_VOTING_WINDOW_CLOSED": 409,
    "ERR_UNKNOWN_CANDIDATE": 400,
    "ERR_NO_COLONY_IN_REGION": 403,
    "ERR_MEMBERSHIP_UPSERT_FAILED": 409,
    # Candidate self-registration (canon "Candidate registration").
    "ERR_CANDIDATES_LOCKED": 409,
    "ERR_NOT_A_CITIZEN": 403,
    "ERR_INSUFFICIENT_REPUTATION": 403,
    "ERR_ALREADY_CANDIDATE": 409,
}


# Map treaty-lifecycle service rejection codes to HTTP status (WO-TREATY). A
# missing region/treaty is 404; a same-region proposal or other validation
# failure is 400; a state-machine conflict (already exists, wrong state for the
# transition) is 409. Owner-authorization (403) is enforced at the route, not
# returned as a service code.
_TREATY_ERROR_STATUS = {
    "ERR_REGION_NOT_FOUND": 404,
    "ERR_SAME_REGION_TREATY": 400,
    "ERR_TREATY_ALREADY_EXISTS": 409,
    "ERR_TREATY_NOT_PROPOSED": 409,
    "ERR_TREATY_NOT_ACTIVE": 409,
}


# Map region-invite service rejection codes to HTTP status (WO-IL3). A
# non-owner trying to mint/list/revoke is 403 (the security-relevant denial);
# validation failures are 400; cap hits are 409 (state conflict, retry later).
_INVITE_ERROR_STATUS = {
    "ERR_NOT_REGION_OWNER": 403,
    "ERR_NOT_INVITE_OWNER": 403,
    "ERR_INVITE_NOT_FOUND": 404,
    "ERR_INVALID_MAX_USES": 400,
    "ERR_INVALID_EXPIRY": 400,
    "ERR_ACTIVE_INVITE_CAP": 409,
    "ERR_REDEMPTION_CAP": 409,
    "ERR_CODE_COLLISION": 500,
}


async def _get_region_by_id(db: AsyncSession, region_id: uuid.UUID) -> Region:
    """Fetch a region by id (404 if missing). Used by the member-facing vote
    routes, which are NOT owner-scoped (any eligible member can vote)."""
    region = await db.scalar(select(Region).where(Region.id == region_id))
    if region is None:
        raise HTTPException(status_code=404, detail="Region not found")
    return region


async def _get_current_player(db: AsyncSession, user: User) -> Player:
    """Resolve the Player record for the authenticated user (404 if absent),
    mirroring the create-policy route's lookup."""
    result = await db.execute(select(Player).where(Player.user_id == user.id))
    player = result.scalar_one_or_none()
    if player is None:
        raise HTTPException(status_code=404, detail="Player record not found")
    return player


class EconomicConfigUpdate(BaseModel):
    tax_rate: float = Field(ge=0.05, le=0.25)
    starting_credits: int = Field(ge=100, le=10000)
    trade_bonuses: Dict[str, float] = Field(default_factory=dict)
    economic_specialization: Optional[str] = None


class GovernanceConfigUpdate(BaseModel):
    governance_type: str
    voting_threshold: float = Field(ge=0.1, le=0.9)
    election_frequency_days: int = Field(ge=30, le=365)
    constitutional_text: Optional[str] = None


class PolicyCreate(BaseModel):
    policy_type: str
    title: str
    description: Optional[str] = None
    proposed_changes: Dict[str, Any] = Field(default_factory=dict)
    voting_duration_days: int = Field(default=7, ge=1, le=30)


class ElectionCreate(BaseModel):
    position: str
    voting_duration_days: int = Field(default=7, ge=1, le=30)
    candidates: Optional[List[str]] = None  # Optional pre-selected candidates


class CulturalUpdate(BaseModel):
    language_pack: Dict[str, str] = Field(default_factory=dict)
    aesthetic_theme: Dict[str, Any] = Field(default_factory=dict)
    traditions: Dict[str, Any] = Field(default_factory=dict)
    regional_motto: Optional[str] = None


class ElectionVoteCast(BaseModel):
    candidate_id: str


class CandidateRegister(BaseModel):
    """A citizen's self-nomination in a SCHEDULED election (canon "Candidate
    registration"). The nominee is the calling player; an optional short
    platform statement is attached to the candidates JSONB entry."""
    platform: Optional[str] = Field(default=None, max_length=500)


class PolicyVoteCast(BaseModel):
    # True = yes / for; False = no / against.
    support: bool


class InviteCreate(BaseModel):
    """Owner request to mint a region invite (WO-IL3).

    ``max_uses`` defaults to one-time (D2) and is bounded by MAX_MAX_USES so an
    owner cannot mint an infinitely-shareable link. ``expires_at`` is optional in
    the request — the service applies the mandatory default TTL (7 days, D3) when
    omitted; a supplied value must be in the future (enforced server-side)."""
    max_uses: int = Field(default=DEFAULT_MAX_USES, ge=1, le=MAX_MAX_USES)
    expires_at: Optional[datetime] = None


class TariffSet(BaseModel):
    """Owner request to set the region COMMERCE tariff (WO-G9 / ADR-0062 E-F2).

    ``rate`` is the requested per-trade tariff surcharge (0.0 = neutral). The
    server CLAMPS it to the E-F2 sliding cap (by in-region station count) inside
    ``trading_service.set_region_tariff`` and returns the persisted clamped value
    — a request above the cap is accepted but stored at the cap, not rejected."""
    rate: float = Field(..., description="Requested tariff rate (clamped to the E-F2 cap on write)")


class TradedockConstructionRequest(BaseModel):
    """Owner request to fund construction of a new TradeDock (WO-TD-RGF-1).

    ``station_id`` is the existing station the project is initiated against
    (see construction_service.create_region_funded_construction for the full
    precondition contract — the station must already carry a tradedock_tier
    and sit inside the caller's region). Cost, region-ownership, and
    ≥ 500-sector eligibility are re-derived and re-checked server-side; none
    of that is trusted from the request body."""
    station_id: uuid.UUID


class TreatyPropose(BaseModel):
    """Owner request to PROPOSE a treaty to another region (WO-TREATY).

    The proposing region is resolved server-side from the caller's owned region
    (never trusted from the body); ``counterparty_region_id`` is the offeree.
    ``treaty_type`` is a free-form label (the model column is String(50));
    ``terms`` is the agreement payload (effect-application is DEFERRED — terms
    are stored, not yet interpreted). ``expires_at`` is optional; when set, the
    existing lazy + sweep expiry flips an accepted treaty to 'expired' once
    past."""
    counterparty_region_id: uuid.UUID
    treaty_type: str = Field(..., max_length=50)
    terms: Dict[str, Any] = Field(default_factory=dict)
    expires_at: Optional[datetime] = None


def _serialize_invite(invite: RegionInvite) -> Dict[str, Any]:
    """Shape a RegionInvite for the owner-facing API (includes the code — this
    surface is owner-scoped, so returning the redeem key here is intended)."""
    return {
        "id": str(invite.id),
        "code": invite.code,
        "region_id": str(invite.region_id),
        "created_by": str(invite.created_by) if invite.created_by else None,
        "max_uses": invite.max_uses,
        "uses": invite.uses,
        "status": invite.status,
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
        "created_at": invite.created_at.isoformat() if invite.created_at else None,
        "revoked_at": invite.revoked_at.isoformat() if invite.revoked_at else None,
    }


async def get_user_region(db: AsyncSession, user_id: uuid.UUID) -> Optional[Region]:
    """Get the region owned by the current user"""
    result = await db.execute(
        select(Region).where(Region.owner_id == user_id)
    )
    return result.scalar_one_or_none()


async def verify_region_owner(db: AsyncSession, user: User) -> Region:
    """Verify that the user owns a region and return it"""
    region = await get_user_region(db, user.id)
    if not region:
        raise HTTPException(status_code=404, detail="No region found for this user")
    return region


@router.get("/my-region")
async def get_my_region(
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session)
):
    """Get information about the user's owned region"""
    region = await verify_region_owner(db, current_user)
    
    return {
        "id": str(region.id),
        "name": region.name,
        "display_name": region.display_name,
        "owner_id": str(region.owner_id),
        "subscription_tier": region.subscription_tier,
        "subscription_status": region.subscription_status,
        "status": region.status,
        "governance_type": region.governance_type,
        "voting_threshold": float(region.voting_threshold),
        "election_frequency_days": region.election_frequency_days,
        "constitutional_text": region.constitutional_text,
        "tax_rate": float(region.tax_rate),
        "trade_bonuses": region.trade_bonuses,
        "economic_specialization": region.economic_specialization,
        "starting_credits": region.starting_credits,
        "starting_ship": region.starting_ship,
        "language_pack": region.language_pack,
        "aesthetic_theme": region.aesthetic_theme,
        "traditions": region.traditions,
        "total_sectors": region.total_sectors,
        "active_players_30d": region.active_players_30d,
        "total_trade_volume": float(region.total_trade_volume),
        # WO-TD-RGF-1: the owner panel needs this to show treasury vs. the
        # 50,000,000cr region-funded TradeDock cost. Safe to return as-is —
        # verify_region_owner() above already 404s a non-owner before this
        # dict is ever built, so this route is owner-gated by construction.
        "treasury_balance": region.treasury_balance,
        "created_at": region.created_at.isoformat(),
        "updated_at": region.updated_at.isoformat()
    }


@router.get("/my-region/stats")
async def get_regional_stats(
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session)
):
    """Get comprehensive statistics for the user's region"""
    region = await verify_region_owner(db, current_user)
    
    # Get membership statistics
    membership_stats = await db.execute(
        select(
            RegionalMembership.membership_type,
            func.count(RegionalMembership.id).label('count'),
            func.avg(RegionalMembership.reputation_score).label('avg_reputation')
        )
        .where(RegionalMembership.region_id == region.id)
        .group_by(RegionalMembership.membership_type)
    )
    memberships = membership_stats.all()
    
    # Calculate totals
    total_population = sum(m.count for m in memberships)
    citizen_count = next((m.count for m in memberships if m.membership_type == 'citizen'), 0)
    resident_count = next((m.count for m in memberships if m.membership_type == 'resident'), 0)
    visitor_count = next((m.count for m in memberships if m.membership_type == 'visitor'), 0)
    average_reputation = sum(m.avg_reputation * m.count for m in memberships if m.avg_reputation) / max(total_population, 1)
    
    # Get infrastructure counts
    planets_count = await db.scalar(
        select(func.count(Planet.id)).where(Planet.region_id == region.id)
    )
    ports_count = await db.scalar(
        select(func.count(Station.id)).where(Station.region_id == region.id)
    )
    ships_count = await db.scalar(
        select(func.count(Ship.id))
        .join(Player, Ship.owner_id == Player.id)
        .join(RegionalMembership, Player.id == RegionalMembership.player_id)
        .where(RegionalMembership.region_id == region.id)
    )
    
    # Get governance statistics
    active_elections = await db.scalar(
        select(func.count(RegionalElection.id))
        .where(
            and_(
                RegionalElection.region_id == region.id,
                RegionalElection.status == ElectionStatus.ACTIVE
            )
        )
    )
    pending_policies = await db.scalar(
        select(func.count(RegionalPolicy.id))
        .where(
            and_(
                RegionalPolicy.region_id == region.id,
                RegionalPolicy.status == PolicyStatus.VOTING
            )
        )
    )
    
    # Get treaty count
    treaties_count = await db.scalar(
        select(func.count(RegionalTreaty.id))
        .where(
            and_(
                or_(
                    RegionalTreaty.region_a_id == region.id,
                    RegionalTreaty.region_b_id == region.id
                ),
                RegionalTreaty.status == 'active'
            )
        )
    )
    
    return {
        "total_population": total_population,
        "citizen_count": citizen_count,
        "resident_count": resident_count,
        "visitor_count": visitor_count,
        "average_reputation": round(average_reputation, 2),
        "total_revenue": float(region.total_trade_volume * region.tax_rate),
        "trade_volume_30d": float(region.total_trade_volume),
        "active_elections": active_elections or 0,
        "pending_policies": pending_policies or 0,
        "treaties_count": treaties_count or 0,
        "planets_count": planets_count or 0,
        "ports_count": ports_count or 0,
        "ships_count": ships_count or 0
    }


@router.put("/my-region/economy")
async def update_economic_config(
    config: EconomicConfigUpdate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session)
):
    """Update economic configuration for the user's region"""
    region = await verify_region_owner(db, current_user)
    
    # Validate trade bonuses
    for resource, bonus in config.trade_bonuses.items():
        if bonus < 1.0 or bonus > 3.0:
            raise HTTPException(
                status_code=400, 
                detail=f"Trade bonus for {resource} must be between 1.0 and 3.0"
            )
    
    # Update region
    await db.execute(
        update(Region)
        .where(Region.id == region.id)
        .values(
            tax_rate=config.tax_rate,
            starting_credits=config.starting_credits,
            trade_bonuses=config.trade_bonuses,
            economic_specialization=config.economic_specialization,
            updated_at=datetime.utcnow()
        )
    )
    
    await db.commit()
    
    return {"message": "Economic configuration updated successfully"}


@router.put("/my-region/governance")
async def update_governance_config(
    config: GovernanceConfigUpdate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session)
):
    """Update governance configuration for the user's region"""
    region = await verify_region_owner(db, current_user)
    
    # Validate governance type
    if config.governance_type not in ['autocracy', 'democracy', 'council']:
        raise HTTPException(status_code=400, detail="Invalid governance type")
    
    # Update region
    await db.execute(
        update(Region)
        .where(Region.id == region.id)
        .values(
            governance_type=config.governance_type,
            voting_threshold=config.voting_threshold,
            election_frequency_days=config.election_frequency_days,
            constitutional_text=config.constitutional_text,
            updated_at=datetime.utcnow()
        )
    )
    
    await db.commit()
    
    return {"message": "Governance configuration updated successfully"}


@router.get("/my-region/policies")
async def get_regional_policies(
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session)
):
    """Get all policies for the user's region"""
    region = await verify_region_owner(db, current_user)
    
    result = await db.execute(
        select(RegionalPolicy)
        .where(RegionalPolicy.region_id == region.id)
        .order_by(RegionalPolicy.proposed_at.desc())
    )
    policies = result.scalars().all()
    
    return [
        {
            "id": str(policy.id),
            "policy_type": policy.policy_type,
            "title": policy.title,
            "description": policy.description,
            "proposed_changes": policy.proposed_changes,
            "proposed_by": str(policy.proposed_by),
            "proposed_at": policy.proposed_at.isoformat(),
            "voting_closes_at": policy.voting_closes_at.isoformat(),
            "votes_for": policy.votes_for,
            "votes_against": policy.votes_against,
            "status": policy.status,
            "approval_percentage": policy.approval_percentage
        }
        for policy in policies
    ]


@router.post("/my-region/policies")
async def create_policy(
    policy_data: PolicyCreate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session)
):
    """Create a new policy proposal for the user's region"""
    region = await verify_region_owner(db, current_user)

    # Get current user's player record
    player_result = await db.execute(
        select(Player).where(Player.user_id == current_user.id)
    )
    player = player_result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player record not found")

    # Validate proposed_changes AT PROPOSAL TIME (canon "Validator catches at
    # proposal time (400)") — mirrors the member POST below, so an owner and a
    # citizen proposal are held to the identical known-keys/bounds contract.
    errors = validate_proposed_changes(policy_data.proposed_changes)
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"code": "ERR_INVALID_PROPOSED_CHANGES", "errors": errors},
        )

    # Create policy
    voting_closes_at = datetime.utcnow() + timedelta(days=policy_data.voting_duration_days)
    
    new_policy = RegionalPolicy(
        region_id=region.id,
        policy_type=policy_data.policy_type,
        title=policy_data.title,
        description=policy_data.description,
        proposed_changes=policy_data.proposed_changes,
        proposed_by=player.id,
        voting_closes_at=voting_closes_at,
        status=PolicyStatus.VOTING
    )
    
    db.add(new_policy)
    await db.commit()
    await db.refresh(new_policy)
    
    return {
        "message": "Policy proposal created successfully",
        "policy_id": str(new_policy.id)
    }


@router.get("/my-region/elections")
async def get_regional_elections(
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session)
):
    """Get all elections for the user's region"""
    region = await verify_region_owner(db, current_user)
    
    result = await db.execute(
        select(RegionalElection)
        .where(RegionalElection.region_id == region.id)
        .order_by(RegionalElection.voting_opens_at.desc())
    )
    elections = result.scalars().all()
    
    return [
        {
            "id": str(election.id),
            "position": election.position,
            "candidates": election.candidates,
            "voting_opens_at": election.voting_opens_at.isoformat(),
            "voting_closes_at": election.voting_closes_at.isoformat(),
            "results": election.results,
            "status": election.status
        }
        for election in elections
    ]


@router.post("/my-region/elections")
async def start_election(
    election_data: ElectionCreate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session)
):
    """Start a new election for the user's region"""
    region = await verify_region_owner(db, current_user)
    
    # Check if there's already an active election for this position
    existing_election = await db.scalar(
        select(RegionalElection)
        .where(
            and_(
                RegionalElection.region_id == region.id,
                RegionalElection.position == election_data.position,
                RegionalElection.status == ElectionStatus.ACTIVE
            )
        )
    )
    
    if existing_election:
        raise HTTPException(
            status_code=409, 
            detail=f"An active election for {election_data.position} already exists"
        )
    
    # Create election
    voting_opens_at = datetime.utcnow()
    voting_closes_at = voting_opens_at + timedelta(days=election_data.voting_duration_days)
    
    new_election = RegionalElection(
        region_id=region.id,
        position=election_data.position,
        candidates=election_data.candidates or [],
        voting_opens_at=voting_opens_at,
        voting_closes_at=voting_closes_at,
        status=ElectionStatus.ACTIVE
    )
    
    db.add(new_election)
    await db.commit()
    await db.refresh(new_election)
    
    return {
        "message": f"Election for {election_data.position} started successfully",
        "election_id": str(new_election.id)
    }


@router.get("/my-region/treaties")
async def get_regional_treaties(
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session)
):
    """Get all treaties for the user's region"""
    region = await verify_region_owner(db, current_user)
    
    result = await db.execute(
        select(RegionalTreaty, Region.name.label('partner_name'))
        .join(
            Region,
            or_(
                and_(RegionalTreaty.region_a_id == region.id, Region.id == RegionalTreaty.region_b_id),
                and_(RegionalTreaty.region_b_id == region.id, Region.id == RegionalTreaty.region_a_id)
            )
        )
        .where(
            or_(
                RegionalTreaty.region_a_id == region.id,
                RegionalTreaty.region_b_id == region.id
            )
        )
        .order_by(RegionalTreaty.signed_at.desc())
    )
    treaties = result.all()
    
    return [
        {
            "id": str(treaty.id),
            "region_a_name": region.name if treaty.region_a_id == region.id else partner_name,
            "region_b_name": partner_name if treaty.region_a_id == region.id else region.name,
            "treaty_type": treaty.treaty_type,
            "terms": treaty.terms,
            "signed_at": treaty.signed_at.isoformat(),
            "expires_at": treaty.expires_at.isoformat() if treaty.expires_at else None,
            "status": treaty.status
        }
        for treaty, partner_name in treaties
    ]


# =====================================================================
# Treaty lifecycle CRUD — owner-gated propose / accept / reject / terminate
# (WO-TREATY). Before this slice only GET /my-region/treaties + lazy expiry
# existed; there was no way to CREATE or END a treaty. Effect-application (what
# a treaty DOES) is DEFERRED — this is the lifecycle ONLY.
#
# AUTHORIZATION (mirrors the owner-scoped governance routes): every transition
# is gated on the caller OWNING the relevant region, re-checked server-side
# against Region.owner_id — never trusted from the body:
#   - propose:   caller owns a region (the proposer = region_a); the body names
#                only the counterparty.
#   - accept/reject: caller owns the COUNTERPARTY (region_b) — the offeree
#                decides (a proposer cannot accept their own offer).
#   - terminate: caller owns EITHER signatory (region_a OR region_b) — either
#                side may unilaterally exit an active treaty.
# THE ROUTE OWNS db.commit() — the service methods only flush; a return without
# this commit silently rolls back the lifecycle change.
# =====================================================================

async def _fetch_treaty_or_404(db: AsyncSession, treaty_id: uuid.UUID) -> RegionalTreaty:
    """Fetch a treaty row (404 if missing) for the lifecycle transitions."""
    treaty = await db.scalar(
        select(RegionalTreaty).where(RegionalTreaty.id == treaty_id)
    )
    if treaty is None:
        raise HTTPException(status_code=404, detail="Treaty not found")
    return treaty


async def _require_region_owner(
    db: AsyncSession, user: User, region_id: uuid.UUID
) -> Region:
    """Verify the user owns the SPECIFIC region_id (403 otherwise), re-checked
    server-side against Region.owner_id. A NULL owner_id (unowned hub region)
    never matches a real user id, so hubs are safely excluded. 404 if the region
    does not exist."""
    region = await db.scalar(select(Region).where(Region.id == region_id))
    if region is None:
        raise HTTPException(status_code=404, detail="Region not found")
    if region.owner_id is None or region.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not the region owner")
    return region


@router.post("/my-region/treaties", status_code=201)
async def propose_treaty(
    body: TreatyPropose,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Propose a treaty FROM the caller's owned region TO another region.

    Owner-scoped: the proposing region is the caller's own region
    (verify_region_owner) — the proposer is never trusted from the body, only the
    counterparty is. Born 'proposed'; it does nothing until the counterparty's
    owner accepts. 400 if proposing to one's own region; 404 if the counterparty
    region does not exist; 409 if a live (proposed/active) treaty already exists
    between the pair (either direction)."""
    region = await verify_region_owner(db, current_user)

    result = await RegionalGovernanceService.propose_treaty(
        db,
        proposer_region_id=region.id,
        counterparty_region_id=body.counterparty_region_id,
        treaty_type=body.treaty_type,
        terms=body.terms,
        expires_at=body.expires_at,
    )
    if not result.get("ok"):
        code = result.get("code", "ERR_TREATY_PROPOSE_FAILED")
        raise HTTPException(
            status_code=_TREATY_ERROR_STATUS.get(code, 400), detail=code
        )
    # THE ROUTE OWNS db.commit() — the service only flushed the new row.
    await db.commit()
    return {"message": "Treaty proposed successfully", "treaty": result["treaty"]}


@router.post("/treaties/{treaty_id}/accept")
async def accept_treaty(
    treaty_id: uuid.UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Accept a PROPOSED treaty (the COUNTERPARTY region's owner only).

    The caller must own region_b (the offeree) — a proposer cannot accept their
    own offer (403 otherwise). 'proposed' -> 'active'. 404 if the treaty does not
    exist; 409 if it is not in the proposed state."""
    treaty = await _fetch_treaty_or_404(db, treaty_id)
    # Only the counterparty (region_b) may accept.
    await _require_region_owner(db, current_user, treaty.region_b_id)

    result = await RegionalGovernanceService.accept_treaty(db, treaty)
    if not result.get("ok"):
        code = result.get("code", "ERR_TREATY_ACCEPT_FAILED")
        raise HTTPException(
            status_code=_TREATY_ERROR_STATUS.get(code, 400), detail=code
        )
    await db.commit()
    return {"message": "Treaty accepted successfully", "treaty": result["treaty"]}


@router.post("/treaties/{treaty_id}/reject")
async def reject_treaty(
    treaty_id: uuid.UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Reject a PROPOSED treaty (the COUNTERPARTY region's owner only).

    The caller must own region_b (the offeree) — 403 otherwise. 'proposed' ->
    'rejected'. 404 if the treaty does not exist; 409 if it is not in the
    proposed state."""
    treaty = await _fetch_treaty_or_404(db, treaty_id)
    await _require_region_owner(db, current_user, treaty.region_b_id)

    result = await RegionalGovernanceService.reject_treaty(db, treaty)
    if not result.get("ok"):
        code = result.get("code", "ERR_TREATY_REJECT_FAILED")
        raise HTTPException(
            status_code=_TREATY_ERROR_STATUS.get(code, 400), detail=code
        )
    await db.commit()
    return {"message": "Treaty rejected successfully", "treaty": result["treaty"]}


@router.post("/treaties/{treaty_id}/terminate")
async def terminate_treaty(
    treaty_id: uuid.UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Terminate an ACTIVE treaty (EITHER signatory region's owner).

    The caller must own region_a OR region_b — either side may unilaterally exit
    an active treaty (403 otherwise). 'active' -> 'terminated'. 404 if the treaty
    does not exist; 409 if it is not in the active state."""
    treaty = await _fetch_treaty_or_404(db, treaty_id)
    # Either signatory's owner may terminate. Resolve both candidate regions and
    # require ownership of at least one; re-checked server-side.
    region_a = await db.scalar(select(Region).where(Region.id == treaty.region_a_id))
    region_b = await db.scalar(select(Region).where(Region.id == treaty.region_b_id))
    owns_either = (
        (region_a is not None and region_a.owner_id is not None
         and region_a.owner_id == current_user.id)
        or (region_b is not None and region_b.owner_id is not None
            and region_b.owner_id == current_user.id)
    )
    if not owns_either:
        raise HTTPException(status_code=403, detail="Not a signatory region owner")

    result = await RegionalGovernanceService.terminate_treaty(db, treaty)
    if not result.get("ok"):
        code = result.get("code", "ERR_TREATY_TERMINATE_FAILED")
        raise HTTPException(
            status_code=_TREATY_ERROR_STATUS.get(code, 400), detail=code
        )
    await db.commit()
    return {"message": "Treaty terminated successfully", "treaty": result["treaty"]}


@router.put("/my-region/culture")
async def update_cultural_identity(
    culture_data: CulturalUpdate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session)
):
    """Update cultural identity for the user's region"""
    region = await verify_region_owner(db, current_user)
    
    # Update region
    await db.execute(
        update(Region)
        .where(Region.id == region.id)
        .values(
            language_pack=culture_data.language_pack,
            aesthetic_theme=culture_data.aesthetic_theme,
            traditions=culture_data.traditions,
            updated_at=datetime.utcnow()
        )
    )
    
    await db.commit()
    
    return {"message": "Cultural identity updated successfully"}


@router.get("/my-region/members")
async def get_regional_members(
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
    limit: int = 100,
    offset: int = 0
):
    """Get members of the user's region"""
    region = await verify_region_owner(db, current_user)

    # Delegate to the service (single source of truth for the
    # Player.username-is-a-property fallback query) rather than duplicating
    # the same query inline here.
    return await RegionalGovernanceService.get_regional_members(
        db, region.id, limit=limit, offset=offset
    )


# =====================================================================
# The democratic loop — member-facing vote casting + result reads
# (canon paths: POST /regions/{region_id}/elections/{election_id}/vote,
#  POST /regions/{region_id}/policies/{policy_id}/vote). These are gated by
# the existing auth dependency but are NOT owner-scoped — any eligible region
# member can vote (eligibility is enforced in the service).
# =====================================================================

@router.post("/{region_id}/elections/{election_id}/vote")
async def cast_election_vote(
    region_id: uuid.UUID,
    election_id: uuid.UUID,
    vote: ElectionVoteCast,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Cast a vote in an ACTIVE regional election (one vote per voter, final)."""
    region = await _get_region_by_id(db, region_id)
    player = await _get_current_player(db, current_user)

    election = await db.scalar(
        select(RegionalElection).where(RegionalElection.id == election_id)
    )
    if election is None or election.region_id != region.id:
        raise HTTPException(status_code=404, detail="Election not found in this region")

    result = await RegionalGovernanceService.cast_election_vote(
        db, region, election, player, vote.candidate_id
    )
    if not result.get("ok"):
        code = result.get("code", "ERR_VOTE_REJECTED")
        raise HTTPException(
            status_code=_VOTE_ERROR_STATUS.get(code, 400), detail=code
        )
    return result


@router.post("/{region_id}/elections/{election_id}/candidates")
async def register_election_candidate(
    region_id: uuid.UUID,
    election_id: uuid.UUID,
    body: CandidateRegister,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Self-nominate as a candidate in a SCHEDULED election (canon "Candidate
    registration"). A region citizen with reputation >= MIN_CANDIDACY_REP may
    register while the election is still PENDING/SCHEDULED; the candidate list
    locks the moment the governance sweep advances it to ACTIVE. NOT
    owner-scoped — any eligible citizen may stand."""
    region = await _get_region_by_id(db, region_id)
    player = await _get_current_player(db, current_user)

    election = await db.scalar(
        select(RegionalElection).where(RegionalElection.id == election_id)
    )
    if election is None or election.region_id != region.id:
        raise HTTPException(status_code=404, detail="Election not found in this region")

    result = await RegionalGovernanceService.register_candidate(
        db, region, election, player, body.platform
    )
    if not result.get("ok"):
        code = result.get("code", "ERR_CANDIDATE_REJECTED")
        raise HTTPException(
            status_code=_VOTE_ERROR_STATUS.get(code, 400), detail=code
        )
    # THE ROUTE OWNS db.commit() — the service only flushed. A return without
    # this commit silently rolls back the appended candidate.
    await db.commit()
    return result


@router.post("/{region_id}/policies/{policy_id}/vote")
async def cast_policy_vote(
    region_id: uuid.UUID,
    policy_id: uuid.UUID,
    vote: PolicyVoteCast,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Cast a yes/no vote on a policy that is in the VOTING state."""
    region = await _get_region_by_id(db, region_id)
    player = await _get_current_player(db, current_user)

    policy = await db.scalar(
        select(RegionalPolicy).where(RegionalPolicy.id == policy_id)
    )
    if policy is None or policy.region_id != region.id:
        raise HTTPException(status_code=404, detail="Policy not found in this region")

    result = await RegionalGovernanceService.cast_policy_vote(
        db, region, policy, player, vote.support
    )
    if not result.get("ok"):
        code = result.get("code", "ERR_VOTE_REJECTED")
        raise HTTPException(
            status_code=_VOTE_ERROR_STATUS.get(code, 400), detail=code
        )
    return result


@router.get("/{region_id}/membership/me")
async def get_my_membership(
    region_id: uuid.UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Read the calling player's own citizenship status in a region (WO-CF).

    Reflects PATH A: a player who owns a colony in the region is reported as a
    citizen on the voter roll (`can_vote: true`, `citizenship_source: "colony"`)
    even if their stored membership row has not yet been upgraded. A player with
    no colony and no qualifying membership is reported as not on the roll. This
    is the player-facing read that backs the governance panel's citizenship
    badge — it is NOT owner-scoped (any authenticated player may read their own
    status)."""
    region = await _get_region_by_id(db, region_id)
    player = await _get_current_player(db, current_user)
    return await RegionalGovernanceService.get_membership_status(
        db, region.id, player.id
    )


@router.post("/{region_id}/citizenship/colony-claim")
async def claim_colony_citizenship(
    region_id: uuid.UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Claim region citizenship on the strength of owning a colony here (WO-CF
    PATH A).

    Verifies the caller owns ≥1 colony whose sector is in this region, then
    upserts their RegionalMembership to citizen. Idempotent — confirming an
    already-citizen colony owner succeeds. Rejects with 403 ERR_NO_COLONY_IN_REGION
    if the player owns no colony in the region. (Voting also auto-enrolls a colony
    owner, so this endpoint is the explicit on-ramp; it is not required to vote.)"""
    region = await _get_region_by_id(db, region_id)
    player = await _get_current_player(db, current_user)
    result = await RegionalGovernanceService.grant_citizenship_for_colony(
        db, player.id, region.id
    )
    if not result.get("ok"):
        code = result.get("code", "ERR_CITIZENSHIP_DENIED")
        raise HTTPException(
            status_code=_VOTE_ERROR_STATUS.get(code, 400), detail=code
        )
    return result


@router.get("/{region_id}/elections/{election_id}/results")
async def get_election_results(
    region_id: uuid.UUID,
    election_id: uuid.UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Read an election's status + tally results (the results JSONB is
    populated once the election COMPLETES)."""
    election = await db.scalar(
        select(RegionalElection).where(RegionalElection.id == election_id)
    )
    if election is None or election.region_id != region_id:
        raise HTTPException(status_code=404, detail="Election not found in this region")
    return {
        "id": str(election.id),
        "position": election.position,
        "status": election.status,
        "candidates": election.candidates,
        "voting_opens_at": election.voting_opens_at.isoformat(),
        "voting_closes_at": election.voting_closes_at.isoformat(),
        "results": election.results,
    }


# =====================================================================
# Member-facing governance discovery + policy proposal (WO-REGOV-CITIZEN-API).
#
# Before this slice the /my-region/* reads above served the REGION OWNER
# only; a member (citizen/resident/visitor) had no route to discover any
# policy/election/treaty id at all — only per-id vote/candidate actions
# existed (:838 vote, :867 candidates, :903 policy-vote, :981 results). These
# four routes close that gap: membership-verified reads (mirrors the :932
# get_my_membership pattern — 403 for a non-member, 404 for a missing region)
# that CALL the already-existing (previously uncalled) service read methods,
# plus a member policy PROPOSAL route.
#
# NO-CANON: FEATURES …/regional-governance.md:159-168 targets a proposal gate
# of "regional reputation >= 100" — no regional-reputation field exists on
# RegionalMembership today (only the per-region reputation_score used for
# candidacy, which is a DIFFERENT canon number). Gating instead on citizen/
# resident membership with can_vote (region.py:260-263), same as the vote-
# casting routes. Flagged to DECISIONS for the real threshold.
# =====================================================================

def _serialize_policy(policy: RegionalPolicy) -> Dict[str, Any]:
    """Shape a RegionalPolicy for the member-facing API (mirrors the owner
    /my-region/policies serialization above)."""
    return {
        "id": str(policy.id),
        "policy_type": policy.policy_type,
        "title": policy.title,
        "description": policy.description,
        "proposed_changes": policy.proposed_changes,
        "proposed_by": str(policy.proposed_by),
        "proposed_at": policy.proposed_at.isoformat(),
        "voting_closes_at": policy.voting_closes_at.isoformat(),
        "votes_for": policy.votes_for,
        "votes_against": policy.votes_against,
        "status": policy.status,
        "approval_percentage": policy.approval_percentage,
    }


def _serialize_election_for_member(election: RegionalElection) -> Dict[str, Any]:
    """Shape a RegionalElection for the member-facing API (mirrors the owner
    /my-region/elections serialization above)."""
    return {
        "id": str(election.id),
        "position": election.position,
        "candidates": election.candidates,
        "voting_opens_at": election.voting_opens_at.isoformat(),
        "voting_closes_at": election.voting_closes_at.isoformat(),
        "results": election.results,
        "status": election.status,
    }


async def _require_member(
    db: AsyncSession, region: Region, player: Player
) -> Dict[str, Any]:
    """403 ERR_NOT_A_MEMBER if the caller is not a member of `region` (any
    tier, or an in-region colony owner, counts — mirrors the :932
    get_my_membership read). Returns the membership-status dict so callers
    needing can_vote don't have to re-fetch it."""
    status = await RegionalGovernanceService.get_membership_status(
        db, region.id, player.id
    )
    if not status.get("is_member"):
        raise HTTPException(status_code=403, detail="ERR_NOT_A_MEMBER")
    return status


@router.get("/{region_id}/policies")
async def list_region_policies_for_member(
    region_id: uuid.UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Member-scoped policy discovery — any region member (not just the
    owner) can list policy ids. 404 if the region does not exist; 403 if the
    caller is not a member. Calls the pre-existing (previously uncalled)
    RegionalGovernanceService.get_regional_policies."""
    region = await _get_region_by_id(db, region_id)
    player = await _get_current_player(db, current_user)
    await _require_member(db, region, player)

    policies = await RegionalGovernanceService.get_regional_policies(db, region.id)
    return [_serialize_policy(p) for p in policies]


@router.get("/{region_id}/elections")
async def list_region_elections_for_member(
    region_id: uuid.UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Member-scoped election discovery — any region member can list election
    ids. 404 if the region does not exist; 403 if the caller is not a member.
    Calls the pre-existing (previously uncalled)
    RegionalGovernanceService.get_regional_elections."""
    region = await _get_region_by_id(db, region_id)
    player = await _get_current_player(db, current_user)
    await _require_member(db, region, player)

    elections = await RegionalGovernanceService.get_regional_elections(db, region.id)
    return [_serialize_election_for_member(e) for e in elections]


@router.get("/{region_id}/treaties")
async def list_region_treaties_for_member(
    region_id: uuid.UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Member-scoped treaty discovery — any region member can list treaty
    ids. 404 if the region does not exist; 403 if the caller is not a member.
    Calls the pre-existing (previously uncalled)
    RegionalGovernanceService.get_regional_treaties.

    NO-CANON: the full `terms` payload is REDACTED for this member-facing
    view (citizens see type/partner/status/expiry, not the negotiated terms
    — the owner-scoped /my-region/treaties read is unaffected and still
    returns terms in full). Flagged to DECISIONS."""
    region = await _get_region_by_id(db, region_id)
    player = await _get_current_player(db, current_user)
    await _require_member(db, region, player)

    treaties = await RegionalGovernanceService.get_regional_treaties(db, region.id)
    return [{k: v for k, v in treaty.items() if k != "terms"} for treaty in treaties]


@router.post("/{region_id}/policies")
async def create_policy_proposal_for_member(
    region_id: uuid.UUID,
    policy_data: PolicyCreate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Member-scoped policy proposal — any eligible citizen/resident (not
    just the owner) may propose. 404 if the region does not exist; 403 if the
    caller is not a member or is not vote-eligible (ERR_NOT_ELIGIBLE — see the
    NO-CANON note on this section re: the reputation>=100 target). 400 if
    proposed_changes fails policy_proposal_rules.validate_proposed_changes
    (an unknown key or an out-of-band value) — no row is written on a 400.
    Calls the pre-existing (previously uncalled)
    RegionalGovernanceService.create_policy_proposal."""
    region = await _get_region_by_id(db, region_id)
    player = await _get_current_player(db, current_user)
    status = await _require_member(db, region, player)
    if not status.get("can_vote"):
        raise HTTPException(status_code=403, detail="ERR_NOT_ELIGIBLE")

    errors = validate_proposed_changes(policy_data.proposed_changes)
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"code": "ERR_INVALID_PROPOSED_CHANGES", "errors": errors},
        )

    new_policy = await RegionalGovernanceService.create_policy_proposal(
        db,
        region_id=region.id,
        proposer_id=player.id,
        policy_data=policy_data.model_dump(),
    )
    if new_policy is None:
        raise HTTPException(status_code=500, detail="ERR_POLICY_CREATE_FAILED")

    return {
        "message": "Policy proposal created successfully",
        "policy_id": str(new_policy.id),
    }


# =====================================================================
# Region invite onramp — owner-gated mint / list / revoke (WO-IL3).
# Brief: audit/design-briefs/invite-link-onramp.md §4.2.
#
# AUTH-FREE infrastructure: these endpoints manage invite CODES; they do NOT
# create accounts (that is WO-IL6, Max-gated). Every endpoint is owner-scoped:
# ownership of THIS region_id is re-checked SERVER-SIDE on every call via the
# NEW region_id-keyed RegionInviteService.owns_region (NOT the single-region
# verify_region_owner above). The client-supplied region_id is never trusted —
# a non-owner gets 403, never another owner's region.
# =====================================================================

@router.post("/{region_id}/invites", status_code=201)
async def create_region_invite(
    region_id: uuid.UUID,
    invite_data: InviteCreate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Mint a region invite for ``region_id`` (region OWNER only).

    Returns 201 with the high-entropy invite ``code``. The caller must own
    ``region_id`` (re-checked server-side); a non-owner gets 403. ``max_uses``
    defaults to 1 (one-time, D2); ``expires_at`` defaults to now + 7 days (D3)."""
    result = await RegionInviteService.mint_invite(
        db,
        owner_user_id=current_user.id,
        region_id=region_id,
        max_uses=invite_data.max_uses,
        expires_at=invite_data.expires_at,
    )
    if not result.get("ok"):
        code = result.get("code", "ERR_INVITE_MINT_FAILED")
        raise HTTPException(
            status_code=_INVITE_ERROR_STATUS.get(code, 400), detail=code
        )
    return {
        "message": "Invite created successfully",
        "invite": _serialize_invite(result["invite"]),
    }


@router.get("/{region_id}/invites")
async def list_region_invites(
    region_id: uuid.UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """List the invites the calling owner has minted for ``region_id``.

    Owner-scoped (403 if the caller does not own ``region_id``). Returns newest
    first, each with its current usage/status (the code is included — this is the
    owner's own management surface)."""
    result = await RegionInviteService.list_invites(
        db, owner_user_id=current_user.id, region_id=region_id
    )
    if not result.get("ok"):
        code = result.get("code", "ERR_INVITE_LIST_FAILED")
        raise HTTPException(
            status_code=_INVITE_ERROR_STATUS.get(code, 400), detail=code
        )
    return {"invites": [_serialize_invite(inv) for inv in result["invites"]]}


@router.post("/{region_id}/invites/{invite_id}/revoke")
async def revoke_region_invite(
    region_id: uuid.UUID,
    invite_id: uuid.UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Revoke an invite the calling owner minted (owner-only).

    Owner-scoped on BOTH the invite's minter and current ownership of
    ``region_id`` (re-checked server-side). Sets status -> 'revoked' and stamps
    ``revoked_at``; revoking an already-revoked invite is an idempotent success.
    The ``region_id`` in the path is validated against the invite's region so an
    owner cannot revoke via a region they own but the invite does not belong to."""
    # Guard: the invite must belong to the region named in the path. This is a
    # defence-in-depth scoping check on top of the service's owner checks — a
    # mismatched path/invite is a 404 (the invite is not "in this region").
    invite_row = await db.scalar(
        select(RegionInvite).where(RegionInvite.id == invite_id)
    )
    if invite_row is None or invite_row.region_id != region_id:
        raise HTTPException(status_code=404, detail="ERR_INVITE_NOT_FOUND")

    result = await RegionInviteService.revoke_invite(
        db, owner_user_id=current_user.id, invite_id=invite_id
    )
    if not result.get("ok"):
        code = result.get("code", "ERR_INVITE_REVOKE_FAILED")
        raise HTTPException(
            status_code=_INVITE_ERROR_STATUS.get(code, 400), detail=code
        )
    return {
        "message": "Invite revoked successfully",
        "invite": _serialize_invite(result["invite"]),
    }


# =====================================================================
# Region COMMERCE tariff — owner-gated WRITE lever (WO-G9 / ADR-0062 E-F2).
#
# The read/apply path (trading.py compute_region_tariff_multiplier) and the
# clamp+persist (trading_service.set_region_tariff, with the E-F2 sliding cap)
# were already shipped; only this WRITE endpoint was missing, leaving the region
# revenue lever stuck at 0. Owner-scoped: ownership of THIS region_id is
# re-checked SERVER-SIDE (id == region_id AND owner_id == caller) — the same
# region_id-keyed guard the invite endpoints use, expressed in the sync session
# this route runs on (set_region_tariff is a sync helper, so this route takes a
# sync Session to call it verbatim — the cap lives entirely inside that helper,
# never duplicated here). The tariff persists in the existing trade_bonuses
# JSONB; no migration.
# =====================================================================

@router.post("/{region_id}/tariff")
def set_region_tariff_endpoint(
    region_id: uuid.UUID,
    body: TariffSet,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Set the region COMMERCE tariff (region OWNER only).

    404 if ``region_id`` does not exist; 403 if the caller does not own it. On
    success the rate is clamped to the E-F2 sliding cap inside
    ``trading_service.set_region_tariff`` (which persists
    ``trade_bonuses['tariff_rate']``), committed, and the persisted CLAMPED rate
    is returned — a request above the cap is stored at the cap, not rejected."""
    region = db.query(Region).filter(Region.id == region_id).first()
    if region is None:
        raise HTTPException(status_code=404, detail="Region not found")
    # Server-side ownership re-check, mirroring RegionInviteService.owns_region:
    # the caller must own THIS specific region. A NULL owner_id (unowned hub
    # region) never matches a real user id, so hubs are safely excluded.
    if region.owner_id is None or region.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the region owner")

    clamped = trading_service.set_region_tariff(db, region, body.rate)
    db.commit()
    return {
        "message": "Region tariff updated successfully",
        "tariff_rate": clamped,
    }


# =====================================================================
# Region-funded TradeDock construction (WO-TD-RGF-1).
#
# construction_service.create_region_funded_construction carries the full
# precondition/state-machine contract (owner check, ≥ 500-sector gate,
# treasury deduct + escrow, ledger write, market-book seed) and previously
# had zero callers. This wires it up, running on the sync Session
# construction_service expects (mirrors set_region_tariff_endpoint above).
# =====================================================================

def _region_construction_status(error: ConstructionError) -> int:
    """Remap select construction_service statuses to more precise REST
    semantics for this route: the service's generic 400 for the < 500-sector
    precondition becomes 409 (a region-state conflict, consistent with the
    409 this route also returns for a double-POST); its generic 400 for
    insufficient region treasury becomes 402, mirroring the established
    insufficient-funds -> 402 convention elsewhere in this codebase
    (research_cockpit.py, black_market.py, planet_grid.py, first_login.py).
    Every other code (403 non-owner, 404 region, 409 double-post) is already
    precise and passes through unchanged."""
    if error.status_code == 400:
        detail_lower = error.detail.lower()
        if "sectors" in detail_lower:
            return 409
        if "treasury" in detail_lower:
            return 402
    return error.status_code


@router.post("/my-region/tradedock-construction")
def create_region_funded_tradedock(
    body: TradedockConstructionRequest,
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Fund construction of a new TradeDock at ``station_id`` (region OWNER
    only). Pulls 50,000,000 cr from the REGION treasury, not the caller's
    personal credits.

    404 if the caller has no Player record, or ``station_id`` doesn't
    resolve to a station linked to any region; otherwise every further
    validation (ownership, sector count, treasury, in-progress guard) is
    delegated to construction_service.create_region_funded_construction and
    remapped to REST-precise codes by ``_region_construction_status``: 403
    non-owner, 409 < 500 sectors or a build already in progress at this
    station, 402 insufficient region treasury."""
    player = db.query(Player).filter(Player.user_id == current_user.id).first()
    if player is None:
        raise HTTPException(status_code=404, detail="Player record not found")

    station = db.query(Station).filter(Station.id == body.station_id).first()
    if station is None or station.region_id is None:
        raise HTTPException(status_code=404, detail="Station not found in any region")

    try:
        result = construction_service.create_region_funded_construction(
            db, station, player, station.region_id
        )
        db.commit()
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=_region_construction_status(e), detail=e.detail)

    return {
        "message": "Region-funded TradeDock construction initiated",
        **result,
    }
