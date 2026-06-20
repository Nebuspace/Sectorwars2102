"""
Regional Governance API Routes
Provides endpoints for regional owners to manage their territories, governance, and policies
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, and_, or_
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import uuid

from src.core.database import get_async_session
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
from src.services.regional_governance_service import RegionalGovernanceService
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


class PolicyVoteCast(BaseModel):
    # True = yes / for; False = no / against.
    support: bool


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
    
    result = await db.execute(
        select(RegionalMembership, Player.username)
        .join(Player, RegionalMembership.player_id == Player.id)
        .where(RegionalMembership.region_id == region.id)
        .order_by(RegionalMembership.joined_at.desc())
        .limit(limit)
        .offset(offset)
    )
    members = result.all()
    
    return [
        {
            "player_id": str(membership.player_id),
            "username": username,
            "membership_type": membership.membership_type,
            "reputation_score": membership.reputation_score,
            "local_rank": membership.local_rank,
            "voting_power": float(membership.voting_power),
            "joined_at": membership.joined_at.isoformat(),
            "last_visit": membership.last_visit.isoformat(),
            "total_visits": membership.total_visits
        }
        for membership, username in members
    ]


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