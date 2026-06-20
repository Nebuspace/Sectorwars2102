"""
Regional Governance Service
Handles business logic for regional governance operations
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, and_, or_
from sqlalchemy.exc import IntegrityError
from typing import List, Dict, Optional, Any, Tuple
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
import uuid
import logging

from src.models.region import (
    Region, RegionalMembership, RegionalPolicy, RegionalElection,
    RegionalVote, RegionalTreaty, RegionalPolicyVote, RegionalTreasuryEntry,
    GovernanceType, PolicyStatus, ElectionStatus
)
from src.models.player import Player
from src.models.user import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Governance loop — canon constants (sw2102-docs SYSTEMS/regional-governance.md
# + FEATURES/gameplay/regional-governance.md + ADR-0059 N-D5/N-F5/N-I4).
# ---------------------------------------------------------------------------

# ADR-0059 N-D5: region-owner-configurable quorum, Decimal(3,2), clamped
# [0.25, 0.60], default 0.33. The `Region.governance_quorum_pct` COLUMN now
# exists (migration c5a8e2f1b9d3) and is read directly; the canon default below
# is the fallback only for legacy rows whose column is NULL.
QUORUM_PCT_DEFAULT = Decimal("0.33")
QUORUM_PCT_MIN = Decimal("0.25")
QUORUM_PCT_MAX = Decimal("0.60")

# Constitutional changes require a fixed supermajority regardless of the
# region default (FEATURES …/regional-governance.md "Supermajority").
SUPERMAJORITY_THRESHOLD = Decimal("0.66")
# Positions whose winner must clear Region.voting_threshold (FEATURES result
# determination step 3) rather than win on plurality alone.
SUPERMAJORITY_POSITIONS = frozenset({"governor"})
# Policy types that require the constitutional supermajority to pass.
CONSTITUTIONAL_POLICY_TYPES = frozenset({"governance_change", "voting_threshold"})

# Transient tally phase. The model's ElectionStatus enum has no TALLYING value
# (PENDING/ACTIVE/COMPLETED/CANCELLED), and the `status` column is a plain
# String(50); we briefly stamp this string between ACTIVE and the terminal
# state so a concurrent reader/sweep sees the in-flight phase and never
# re-tallies a COMPLETED election. Mirrors the doc state machine
# ACTIVE -> TALLYING -> COMPLETED.
ELECTION_TALLYING = "tallying"

# LEGACY reserved key that USED to hold the per-policy voter ledger inside
# RegionalPolicy.proposed_changes (a stop-gap when no RegionalPolicyVote table
# existed). The real `regional_policy_votes` table (migration c5a8e2f1b9d3) now
# backs per-policy vote dedup + weighted tally. This key is retained ONLY so any
# legacy row that still carries it gets it stripped on read/enactment — it is
# never written again.
POLICY_VOTERS_KEY = "_voters"


# ---------------------------------------------------------------------------
# Pure, session-agnostic governance helpers
#
# These are called from BOTH the async service methods below AND the SYNC
# scheduler sweep in npc_scheduler_service (which cannot await the async path
# without poisoning the shared async engine pool — the same constraint that
# forces the faction/ARIA decay to be reimplemented synchronously). Keeping the
# rules here as pure functions over plain values guarantees the cast path and
# the sweep path apply IDENTICAL canon.
# ---------------------------------------------------------------------------

def quorum_pct_for_region(region: Region) -> Decimal:
    """ADR-0059 N-D5 participation threshold for a region, clamped to the
    canon [0.25, 0.60] band with the 0.33 default. Reads the real
    governance_quorum_pct column (migration c5a8e2f1b9d3); falls back to the
    canon default for a legacy row whose column is NULL."""
    raw = getattr(region, "governance_quorum_pct", None)
    if raw is None:
        return QUORUM_PCT_DEFAULT
    try:
        pct = Decimal(str(raw))
    except (TypeError, ValueError):
        return QUORUM_PCT_DEFAULT
    return max(QUORUM_PCT_MIN, min(QUORUM_PCT_MAX, pct))


def compute_quorum(total_eligible: int, quorum_pct: Decimal) -> int:
    """Canon quorum (ADR-0059 N-D5 / FEATURES quorum table):

        total_eligible if total_eligible <= 1   # single-voter region: moot
        else max(2, ceil(total_eligible * pct))

    The 2-voter hard floor (when 2+ eligible) prevents single-voter
    rubberstamps; pct is already clamped to [0.25, 0.60]."""
    if total_eligible <= 1:
        return total_eligible
    return max(2, ceil(total_eligible * float(quorum_pct)))


def threshold_for_policy(region: Region, policy_type: str) -> Decimal:
    """Approval threshold a policy must clear. Constitutional changes need the
    fixed 0.66 supermajority regardless of region default; everything else uses
    Region.voting_threshold (default 0.51, range 0.10-0.90)."""
    if policy_type in CONSTITUTIONAL_POLICY_TYPES:
        return SUPERMAJORITY_THRESHOLD
    return Decimal(str(region.voting_threshold))


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a numeric to [lo, hi]."""
    return max(lo, min(hi, value))


def enact_changes_onto_region(region: Region, proposed_changes: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a PASSED policy's proposed_changes onto the region row, CLAMPED to
    the region CHECK bounds so an enacted policy can never write an out-of-range
    value (the DB CHECK would otherwise reject the whole transaction). Mutates
    `region` in place and returns the dict of fields actually changed
    (old -> new) for the audit/event payload.

    Canon policy-type -> region-field map (FEATURES …/regional-governance.md
    "Policy types"). Only fields with an existing CHECK-bounded column are
    enacted here; design-only types (starting_credits beyond floor,
    economic_specialization modifiers, immigration_policy) are reported as
    skipped. Treasury-touching enactment is handled separately in
    finalize_policy (it must write a RegionalTreasuryEntry row in the same
    transaction as the balance mutation, which a pure helper cannot do) — see
    compute_treasury_adjustment / finalize_policy. The legacy POLICY_VOTERS_KEY
    ledger is stripped so it never reaches a column.
    """
    changes = dict(proposed_changes or {})
    changes.pop(POLICY_VOTERS_KEY, None)
    applied: Dict[str, Any] = {}

    # tax_rate -> Region.tax_rate, CHECK 0.05-0.25.
    if "tax_rate" in changes:
        try:
            new_tax = _clamp(float(changes["tax_rate"]), 0.05, 0.25)
            old = float(region.tax_rate)
            if new_tax != old:
                region.tax_rate = new_tax
                applied["tax_rate"] = {"old": old, "new": new_tax}
        except (TypeError, ValueError):
            logger.warning("Policy enact: non-numeric tax_rate ignored")

    # voting_threshold -> Region.voting_threshold, CHECK 0.1-0.9.
    if "voting_threshold" in changes:
        try:
            new_vt = _clamp(float(changes["voting_threshold"]), 0.1, 0.9)
            old = float(region.voting_threshold)
            if new_vt != old:
                region.voting_threshold = new_vt
                applied["voting_threshold"] = {"old": old, "new": new_vt}
        except (TypeError, ValueError):
            logger.warning("Policy enact: non-numeric voting_threshold ignored")

    # election_frequency_days -> Region.election_frequency_days, CHECK 30-365.
    if "election_frequency_days" in changes:
        try:
            new_freq = int(_clamp(int(changes["election_frequency_days"]), 30, 365))
            old = int(region.election_frequency_days)
            if new_freq != old:
                region.election_frequency_days = new_freq
                applied["election_frequency_days"] = {"old": old, "new": new_freq}
        except (TypeError, ValueError):
            logger.warning("Policy enact: non-numeric election_frequency_days ignored")

    # governance_change -> Region.governance_type (enum-validated).
    if "governance_type" in changes:
        gt = str(changes["governance_type"])
        valid = {g.value for g in GovernanceType}
        if gt in valid and gt != region.governance_type:
            old = region.governance_type
            region.governance_type = gt
            applied["governance_type"] = {"old": old, "new": gt}
        elif gt not in valid:
            logger.warning("Policy enact: invalid governance_type %r ignored", gt)

    # governance_quorum_pct -> Region column (only if the column exists),
    # clamped to the ADR-0059 band.
    if "governance_quorum_pct" in changes and hasattr(region, "governance_quorum_pct"):
        try:
            new_q = _clamp(float(changes["governance_quorum_pct"]),
                           float(QUORUM_PCT_MIN), float(QUORUM_PCT_MAX))
            old_raw = getattr(region, "governance_quorum_pct", None)
            old = float(old_raw) if old_raw is not None else None
            if old != new_q:
                region.governance_quorum_pct = new_q
                applied["governance_quorum_pct"] = {"old": old, "new": new_q}
        except (TypeError, ValueError):
            logger.warning("Policy enact: non-numeric governance_quorum_pct ignored")

    # trade_policy -> Region.trade_bonuses JSONB (per-resource, each 1.0-3.0
    # mirroring the economy-config route's validation). Merged, not replaced.
    if "trade_bonuses" in changes and isinstance(changes["trade_bonuses"], dict):
        merged = dict(region.trade_bonuses or {})
        touched = {}
        # ADR-0062: trade_bonuses ALSO holds non-multiplier keys (e.g. tariff_rate,
        # a ~0.0 per-trade modifier). Clamping those into the [1.0,3.0] multiplier
        # band would corrupt them — skip reserved keys; only per-resource multipliers enact.
        RESERVED_NON_MULTIPLIER = {"tariff_rate"}
        for resource, bonus in changes["trade_bonuses"].items():
            if resource in RESERVED_NON_MULTIPLIER:
                continue
            try:
                clamped = _clamp(float(bonus), 1.0, 3.0)
            except (TypeError, ValueError):
                continue
            if merged.get(resource) != clamped:
                merged[resource] = clamped
                touched[resource] = clamped
        if touched:
            region.trade_bonuses = merged
            applied["trade_bonuses"] = touched

    return applied


# Reserved proposed_changes key for a treasury-touching policy: a signed integer
# credit adjustment to Region.treasury_balance (positive = inflow, negative =
# outflow). No current canon policy type carries it, so existing policies enact
# byte-for-byte as before; when present it drives the ADR-0059 N-I4 treasury
# ledger write in finalize_policy. Balance floored at 0 (no negative treasury).
POLICY_TREASURY_KEY = "treasury_adjustment"


def compute_treasury_adjustment(region: Region, proposed_changes: Dict[str, Any]) -> Optional[int]:
    """If a PASSED policy carries the reserved POLICY_TREASURY_KEY, return the
    signed integer credit delta that should be applied to Region.treasury_balance
    (clamped so the resulting balance never goes negative). Returns None when the
    policy does not touch the treasury — in which case no RegionalTreasuryEntry
    is written. Pure helper: it computes the delta but does NOT mutate the row
    (the mutation + ledger write happen together in finalize_policy)."""
    changes = proposed_changes or {}
    if POLICY_TREASURY_KEY not in changes:
        return None
    try:
        requested = int(changes[POLICY_TREASURY_KEY])
    except (TypeError, ValueError):
        logger.warning("Policy enact: non-integer treasury_adjustment ignored")
        return None
    before = int(region.treasury_balance or 0)
    after = max(0, before + requested)
    delta = after - before
    if delta == 0:
        return None
    return delta


def determine_election_winner(
    region: Region,
    election: RegionalElection,
    tallies: Dict[str, float],
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Compute the winner of an election from per-candidate weight tallies.

    Canon (FEATURES result determination): plurality winner by default; for
    SUPERMAJORITY_POSITIONS (governor) the leader must also clear
    Region.voting_threshold of the total weight cast, else the election is
    VOIDED (no winner). Ties broken by earliest candidate registration order in
    the candidates JSONB (SYSTEMS invariant: "ties broken by earliest
    registration").

    Returns (winner_candidate_id_or_None, results_payload). The payload is
    written verbatim to RegionalElection.results.
    """
    total_weight = sum(tallies.values())
    # Registration order index for deterministic tie-break.
    order: Dict[str, int] = {}
    for i, cand in enumerate(election.candidates or []):
        cid = cand.get("player_id") if isinstance(cand, dict) else str(cand)
        if cid is not None and str(cid) not in order:
            order[str(cid)] = i

    winner: Optional[str] = None
    if tallies:
        # Sort by (weight desc, registration order asc) so the earliest-
        # registered candidate wins a tie.
        ranked = sorted(
            tallies.items(),
            key=lambda kv: (-kv[1], order.get(kv[0], len(order) + 1)),
        )
        leader_id, leader_weight = ranked[0]
        winner = leader_id
        # Supermajority gate for high-stakes positions.
        if election.position in SUPERMAJORITY_POSITIONS and total_weight > 0:
            share = leader_weight / total_weight
            if share < float(region.voting_threshold):
                winner = None  # voided — no candidate cleared the threshold

    payload = {
        "tallies": {cid: float(w) for cid, w in tallies.items()},
        "total_weight": float(total_weight),
        "winner": winner,
        "voided": winner is None,
        "position": election.position,
        "tallied_at": datetime.utcnow().isoformat(),
    }
    return winner, payload


class RegionalGovernanceService:
    """Service for managing regional governance operations"""
    
    @staticmethod
    async def get_region_by_owner(db: AsyncSession, owner_id: uuid.UUID) -> Optional[Region]:
        """Get region owned by user"""
        result = await db.execute(
            select(Region).where(Region.owner_id == owner_id)
        )
        return result.scalar_one_or_none()
    
    @staticmethod
    async def _expire_stale_treaties(db: AsyncSession, region_id: uuid.UUID) -> int:
        """Lazily expire treaties whose expiry has passed (advance-on-read).

        Treaties carry an explicit expires_at but there is no background sweep
        that flips their status; instead we settle them on read (the same
        lazy-settle pattern used for citadel/shield state). Any treaty still
        marked 'active' but past its expires_at is flipped to 'expired' so that
        all downstream reads (counts, listings, governance checks) see the
        truthful status. Returns the number of treaties expired.
        """
        now = datetime.utcnow()
        result = await db.execute(
            update(RegionalTreaty)
            .where(
                and_(
                    or_(
                        RegionalTreaty.region_a_id == region_id,
                        RegionalTreaty.region_b_id == region_id
                    ),
                    RegionalTreaty.status == 'active',
                    RegionalTreaty.expires_at.isnot(None),
                    RegionalTreaty.expires_at < now
                )
            )
            .values(status='expired')
        )
        expired = result.rowcount or 0
        if expired:
            await db.commit()
            logger.info(f"Lazily expired {expired} treaty(ies) for region {region_id}")
        return expired

    @staticmethod
    async def get_regional_stats(db: AsyncSession, region_id: uuid.UUID) -> Dict[str, Any]:
        """Get comprehensive statistics for a region"""
        # Settle any treaties past their expiry before counting active ones.
        await RegionalGovernanceService._expire_stale_treaties(db, region_id)

        # Get membership statistics
        membership_stats = await db.execute(
            select(
                RegionalMembership.membership_type,
                func.count(RegionalMembership.id).label('count'),
                func.avg(RegionalMembership.reputation_score).label('avg_reputation')
            )
            .where(RegionalMembership.region_id == region_id)
            .group_by(RegionalMembership.membership_type)
        )
        memberships = membership_stats.all()
        
        # Calculate totals
        total_population = sum(m.count for m in memberships)
        citizen_count = next((m.count for m in memberships if m.membership_type == 'citizen'), 0)
        resident_count = next((m.count for m in memberships if m.membership_type == 'resident'), 0)
        visitor_count = next((m.count for m in memberships if m.membership_type == 'visitor'), 0)
        
        # Calculate average reputation
        total_reputation = sum(m.avg_reputation * m.count for m in memberships if m.avg_reputation)
        average_reputation = total_reputation / max(total_population, 1) if total_population > 0 else 0
        
        # Get governance statistics
        active_elections = await db.scalar(
            select(func.count(RegionalElection.id))
            .where(
                and_(
                    RegionalElection.region_id == region_id,
                    RegionalElection.status == ElectionStatus.ACTIVE
                )
            )
        ) or 0
        
        pending_policies = await db.scalar(
            select(func.count(RegionalPolicy.id))
            .where(
                and_(
                    RegionalPolicy.region_id == region_id,
                    RegionalPolicy.status == PolicyStatus.VOTING
                )
            )
        ) or 0
        
        # Get treaty count
        treaties_count = await db.scalar(
            select(func.count(RegionalTreaty.id))
            .where(
                and_(
                    or_(
                        RegionalTreaty.region_a_id == region_id,
                        RegionalTreaty.region_b_id == region_id
                    ),
                    RegionalTreaty.status == 'active'
                )
            )
        ) or 0
        
        return {
            "total_population": total_population,
            "citizen_count": citizen_count,
            "resident_count": resident_count,
            "visitor_count": visitor_count,
            "average_reputation": round(average_reputation, 2),
            "active_elections": active_elections,
            "pending_policies": pending_policies,
            "treaties_count": treaties_count
        }
    
    @staticmethod
    async def update_economic_config(
        db: AsyncSession, 
        region_id: uuid.UUID, 
        config: Dict[str, Any]
    ) -> bool:
        """Update economic configuration for a region"""
        try:
            await db.execute(
                update(Region)
                .where(Region.id == region_id)
                .values(
                    tax_rate=config.get('tax_rate'),
                    starting_credits=config.get('starting_credits'),
                    trade_bonuses=config.get('trade_bonuses', {}),
                    economic_specialization=config.get('economic_specialization'),
                    updated_at=datetime.utcnow()
                )
            )
            await db.commit()
            logger.info(f"Updated economic config for region {region_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update economic config: {e}")
            await db.rollback()
            return False
    
    @staticmethod
    async def update_governance_config(
        db: AsyncSession, 
        region_id: uuid.UUID, 
        config: Dict[str, Any]
    ) -> bool:
        """Update governance configuration for a region"""
        try:
            await db.execute(
                update(Region)
                .where(Region.id == region_id)
                .values(
                    governance_type=config.get('governance_type'),
                    voting_threshold=config.get('voting_threshold'),
                    election_frequency_days=config.get('election_frequency_days'),
                    constitutional_text=config.get('constitutional_text'),
                    updated_at=datetime.utcnow()
                )
            )
            await db.commit()
            logger.info(f"Updated governance config for region {region_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update governance config: {e}")
            await db.rollback()
            return False
    
    @staticmethod
    async def create_policy_proposal(
        db: AsyncSession,
        region_id: uuid.UUID,
        proposer_id: uuid.UUID,
        policy_data: Dict[str, Any]
    ) -> Optional[RegionalPolicy]:
        """Create a new policy proposal"""
        try:
            voting_closes_at = datetime.utcnow() + timedelta(
                days=policy_data.get('voting_duration_days', 7)
            )
            
            new_policy = RegionalPolicy(
                region_id=region_id,
                policy_type=policy_data['policy_type'],
                title=policy_data['title'],
                description=policy_data.get('description'),
                proposed_changes=policy_data.get('proposed_changes', {}),
                proposed_by=proposer_id,
                voting_closes_at=voting_closes_at,
                status=PolicyStatus.VOTING
            )
            
            db.add(new_policy)
            await db.commit()
            await db.refresh(new_policy)
            
            logger.info(f"Created policy proposal {new_policy.id} for region {region_id}")
            return new_policy
        except Exception as e:
            logger.error(f"Failed to create policy proposal: {e}")
            await db.rollback()
            return None
    
    @staticmethod
    async def start_election(
        db: AsyncSession,
        region_id: uuid.UUID,
        position: str,
        voting_duration_days: int = 7,
        candidates: Optional[List[str]] = None
    ) -> Optional[RegionalElection]:
        """Start a new election"""
        try:
            # Check for existing active election for this position
            existing = await db.scalar(
                select(RegionalElection)
                .where(
                    and_(
                        RegionalElection.region_id == region_id,
                        RegionalElection.position == position,
                        RegionalElection.status == ElectionStatus.ACTIVE
                    )
                )
            )
            
            if existing:
                logger.warning(f"Active election already exists for {position} in region {region_id}")
                return None
            
            voting_opens_at = datetime.utcnow()
            voting_closes_at = voting_opens_at + timedelta(days=voting_duration_days)
            
            new_election = RegionalElection(
                region_id=region_id,
                position=position,
                candidates=candidates or [],
                voting_opens_at=voting_opens_at,
                voting_closes_at=voting_closes_at,
                status=ElectionStatus.ACTIVE
            )
            
            db.add(new_election)
            await db.commit()
            await db.refresh(new_election)
            
            logger.info(f"Started election {new_election.id} for {position} in region {region_id}")
            return new_election
        except Exception as e:
            logger.error(f"Failed to start election: {e}")
            await db.rollback()
            return None
    
    @staticmethod
    async def get_regional_policies(
        db: AsyncSession,
        region_id: uuid.UUID,
        limit: int = 50
    ) -> List[RegionalPolicy]:
        """Get policies for a region"""
        result = await db.execute(
            select(RegionalPolicy)
            .where(RegionalPolicy.region_id == region_id)
            .order_by(RegionalPolicy.proposed_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
    
    @staticmethod
    async def get_regional_elections(
        db: AsyncSession,
        region_id: uuid.UUID,
        limit: int = 20
    ) -> List[RegionalElection]:
        """Get elections for a region"""
        result = await db.execute(
            select(RegionalElection)
            .where(RegionalElection.region_id == region_id)
            .order_by(RegionalElection.voting_opens_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
    
    @staticmethod
    async def get_regional_treaties(
        db: AsyncSession,
        region_id: uuid.UUID
    ) -> List[Dict[str, Any]]:
        """Get treaties involving a region"""
        # Settle any treaties past their expiry so listings show accurate status.
        await RegionalGovernanceService._expire_stale_treaties(db, region_id)

        result = await db.execute(
            select(RegionalTreaty, Region.name.label('partner_name'))
            .join(
                Region,
                or_(
                    and_(RegionalTreaty.region_a_id == region_id, Region.id == RegionalTreaty.region_b_id),
                    and_(RegionalTreaty.region_b_id == region_id, Region.id == RegionalTreaty.region_a_id)
                )
            )
            .where(
                or_(
                    RegionalTreaty.region_a_id == region_id,
                    RegionalTreaty.region_b_id == region_id
                )
            )
            .order_by(RegionalTreaty.signed_at.desc())
        )
        treaties = result.all()
        
        return [
            {
                "id": str(treaty.id),
                "partner_region": partner_name,
                "treaty_type": treaty.treaty_type,
                "terms": treaty.terms,
                "signed_at": treaty.signed_at.isoformat(),
                "expires_at": treaty.expires_at.isoformat() if treaty.expires_at else None,
                "status": treaty.status
            }
            for treaty, partner_name in treaties
        ]
    
    @staticmethod
    async def get_regional_members(
        db: AsyncSession,
        region_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get members of a region"""
        result = await db.execute(
            select(RegionalMembership, Player.username)
            .join(Player, RegionalMembership.player_id == Player.id)
            .where(RegionalMembership.region_id == region_id)
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
    
    @staticmethod
    async def update_cultural_identity(
        db: AsyncSession,
        region_id: uuid.UUID,
        culture_data: Dict[str, Any]
    ) -> bool:
        """Update cultural identity for a region"""
        try:
            await db.execute(
                update(Region)
                .where(Region.id == region_id)
                .values(
                    language_pack=culture_data.get('language_pack', {}),
                    aesthetic_theme=culture_data.get('aesthetic_theme', {}),
                    traditions=culture_data.get('traditions', {}),
                    updated_at=datetime.utcnow()
                )
            )
            await db.commit()
            logger.info(f"Updated cultural identity for region {region_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update cultural identity: {e}")
            await db.rollback()
            return False

    # =====================================================================
    # THE DEMOCRATIC LOOP — vote casting, tally + winner, policy enactment
    # (sw2102-docs SYSTEMS/regional-governance.md + FEATURES/gameplay/
    # regional-governance.md + ADR-0059).
    # =====================================================================

    @staticmethod
    async def _get_voting_membership(
        db: AsyncSession,
        region_id: uuid.UUID,
        player_id: uuid.UUID,
    ) -> Optional[RegionalMembership]:
        """The voter's membership row in this region, or None if they are not a
        member. Eligibility (can_vote) is checked by the caller so the precise
        rejection reason can be surfaced."""
        result = await db.execute(
            select(RegionalMembership).where(
                and_(
                    RegionalMembership.region_id == region_id,
                    RegionalMembership.player_id == player_id,
                )
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _count_eligible_voters(db: AsyncSession, region_id: uuid.UUID) -> int:
        """Count memberships eligible to vote (can_vote == true): citizen or
        resident with voting_power > 0. Drives the quorum denominator."""
        result = await db.scalar(
            select(func.count(RegionalMembership.id)).where(
                and_(
                    RegionalMembership.region_id == region_id,
                    RegionalMembership.membership_type.in_(["citizen", "resident"]),
                    RegionalMembership.voting_power > 0,
                )
            )
        )
        return int(result or 0)

    @staticmethod
    async def cast_election_vote(
        db: AsyncSession,
        region: Region,
        election: RegionalElection,
        voter: Player,
        candidate_id: str,
    ) -> Dict[str, Any]:
        """Cast (or reject) a vote in an ACTIVE election.

        Canon validations (FEATURES …/regional-governance.md "Voting"):
        - voter is a member of the region,
        - membership.can_vote is true (citizen/resident with voting_power > 0),
        - election is ACTIVE and now is within [voting_opens_at, voting_closes_at],
        - candidate is one of the election's registered candidates,
        - one vote per (election, voter) — ADR-0059 N-F5 first-vote-sticks; a
          second attempt rejects with ERR_ALREADY_VOTED (the UNIQUE constraint
          is the backstop against a concurrent double-cast).

        Vote weight is SNAPSHOT from membership.voting_power at cast time and is
        immutable thereafter (ADR-0059 N-F5). Returns {ok, code, ...}.
        """
        now = datetime.utcnow()

        if election.region_id != region.id:
            return {"ok": False, "code": "ERR_ELECTION_NOT_IN_REGION"}
        if election.status != ElectionStatus.ACTIVE:
            return {"ok": False, "code": "ERR_ELECTION_NOT_ACTIVE"}
        if not (election.voting_opens_at <= now <= election.voting_closes_at):
            return {"ok": False, "code": "ERR_VOTING_WINDOW_CLOSED"}

        membership = await RegionalGovernanceService._get_voting_membership(
            db, region.id, voter.id
        )
        if membership is None:
            return {"ok": False, "code": "ERR_NOT_A_MEMBER"}
        if not membership.can_vote:
            return {"ok": False, "code": "ERR_NOT_ELIGIBLE"}

        # Candidate must be one of the registered candidates.
        candidate_ids = {
            str(c.get("player_id")) if isinstance(c, dict) else str(c)
            for c in (election.candidates or [])
        }
        if candidate_ids and str(candidate_id) not in candidate_ids:
            return {"ok": False, "code": "ERR_UNKNOWN_CANDIDATE"}

        # candidate_id must be a real player UUID (the FK target).
        try:
            candidate_uuid = uuid.UUID(str(candidate_id))
        except (TypeError, ValueError):
            return {"ok": False, "code": "ERR_UNKNOWN_CANDIDATE"}

        # Pre-check for an existing vote (fast path); the UNIQUE constraint is
        # the authoritative backstop against a concurrent double-cast.
        existing = await db.scalar(
            select(RegionalVote.id).where(
                and_(
                    RegionalVote.election_id == election.id,
                    RegionalVote.voter_id == voter.id,
                )
            )
        )
        if existing is not None:
            return {"ok": False, "code": "ERR_ALREADY_VOTED"}

        vote = RegionalVote(
            election_id=election.id,
            voter_id=voter.id,
            candidate_id=candidate_uuid,
            weight=membership.voting_power,  # snapshot, immutable
            cast_at=now,
        )
        db.add(vote)
        try:
            await db.commit()
        except IntegrityError:
            # Concurrent double-cast lost the race to the UNIQUE constraint.
            await db.rollback()
            return {"ok": False, "code": "ERR_ALREADY_VOTED"}
        return {
            "ok": True,
            "code": "VOTE_RECORDED",
            "vote_id": str(vote.id),
            "weight": float(membership.voting_power),
        }

    @staticmethod
    async def cast_policy_vote(
        db: AsyncSession,
        region: Region,
        policy: RegionalPolicy,
        voter: Player,
        support: bool,
    ) -> Dict[str, Any]:
        """Cast (or reject) a yes/no vote on a policy in the VOTING state.

        Each vote adds the voter's voting_power to votes_for or votes_against
        (FEATURES weighted tally) AND inserts a RegionalPolicyVote row — the real
        per-policy ledger (migration c5a8e2f1b9d3). One vote per (policy, voter):
        the UNIQUE(policy_id, voter_id) constraint is the first-vote-sticks
        backstop against a concurrent double-cast (mirrors cast_election_vote).

        Vote weight is snapshot at cast time (ADR-0059 N-F5). Returns
        {ok, code, ...}.
        """
        now = datetime.utcnow()
        if policy.region_id != region.id:
            return {"ok": False, "code": "ERR_POLICY_NOT_IN_REGION"}
        if policy.status != PolicyStatus.VOTING:
            return {"ok": False, "code": "ERR_POLICY_NOT_VOTING"}
        if now > policy.voting_closes_at:
            return {"ok": False, "code": "ERR_VOTING_WINDOW_CLOSED"}

        membership = await RegionalGovernanceService._get_voting_membership(
            db, region.id, voter.id
        )
        if membership is None:
            return {"ok": False, "code": "ERR_NOT_A_MEMBER"}
        if not membership.can_vote:
            return {"ok": False, "code": "ERR_NOT_ELIGIBLE"}

        # Re-read the policy under a row lock so the read-modify-write of the
        # tallies is atomic against a concurrent vote on the same policy.
        locked = await db.execute(
            select(RegionalPolicy)
            .where(RegionalPolicy.id == policy.id)
            .with_for_update()
        )
        policy = locked.scalar_one()

        # Recheck under the lock: a concurrent finalize may have closed the
        # window between the route's unlocked read and this lock.
        if policy.status != PolicyStatus.VOTING:
            await db.rollback()
            return {"ok": False, "code": "ERR_POLICY_NOT_VOTING"}
        if now > policy.voting_closes_at:
            await db.rollback()
            return {"ok": False, "code": "ERR_VOTING_WINDOW_CLOSED"}

        # Pre-check for an existing policy vote (fast path); the UNIQUE
        # constraint is the authoritative backstop against a concurrent
        # double-cast.
        existing = await db.scalar(
            select(RegionalPolicyVote.id).where(
                and_(
                    RegionalPolicyVote.policy_id == policy.id,
                    RegionalPolicyVote.voter_id == voter.id,
                )
            )
        )
        if existing is not None:
            await db.rollback()  # release the row lock; nothing changed
            return {"ok": False, "code": "ERR_ALREADY_VOTED"}

        weight = int(round(float(membership.voting_power)))  # tallies are Integer columns
        # voting_power is DECIMAL(5,4) in [0,5]; a member with can_vote is > 0,
        # so guarantee at least 1 weight is counted for an eligible voter.
        weight = max(1, weight)
        if support:
            policy.votes_for = int(policy.votes_for or 0) + weight
        else:
            policy.votes_against = int(policy.votes_against or 0) + weight

        # Record the individual vote in the real ledger (snapshot weight is the
        # raw voting_power per ADR-0059 N-F5; the aggregate columns above carry
        # the integer-rounded tally).
        db.add(RegionalPolicyVote(
            policy_id=policy.id,
            voter_id=voter.id,
            support=support,
            weight=membership.voting_power,
            created_at=now,
        ))
        try:
            await db.commit()
        except IntegrityError:
            # Concurrent double-cast lost the race to the UNIQUE constraint.
            await db.rollback()
            return {"ok": False, "code": "ERR_ALREADY_VOTED"}
        return {
            "ok": True,
            "code": "VOTE_RECORDED",
            "support": support,
            "weight": weight,
            "votes_for": policy.votes_for,
            "votes_against": policy.votes_against,
        }

    @staticmethod
    async def tally_election(
        db: AsyncSession,
        election_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """Close + tally a single election (idempotent).

        Transitions ACTIVE -> (TALLYING) -> COMPLETED, aggregates vote weight
        per candidate, determines the winner per canon, writes the results
        JSONB, AND persists the winner to RegionalElection.winner_id + the
        single-seat Region.{position}_id column (governor_id / ambassador_id)
        per SYSTEMS/regional-governance.md step 3. A COMPLETED election is NEVER
        re-tallied. Locks the election row so a concurrent sweep + manual close
        cannot double-tally.
        """
        locked = await db.execute(
            select(RegionalElection)
            .where(RegionalElection.id == election_id)
            .with_for_update()
        )
        election = locked.scalar_one_or_none()
        if election is None:
            return {"ok": False, "code": "ERR_ELECTION_NOT_FOUND"}
        # Idempotency: a terminal election is never re-tallied.
        if election.status in (ElectionStatus.COMPLETED, ElectionStatus.CANCELLED):
            return {"ok": False, "code": "ERR_ALREADY_TALLIED",
                    "status": election.status}

        region = await db.scalar(
            select(Region).where(Region.id == election.region_id)
        )
        if region is None:
            await db.rollback()
            return {"ok": False, "code": "ERR_REGION_NOT_FOUND"}

        # Mark the in-flight tally phase.
        election.status = ELECTION_TALLYING

        # Aggregate sum(weight) per candidate_id.
        rows = await db.execute(
            select(
                RegionalVote.candidate_id,
                func.coalesce(func.sum(RegionalVote.weight), 0),
            )
            .where(RegionalVote.election_id == election.id)
            .group_by(RegionalVote.candidate_id)
        )
        tallies = {str(cid): float(total) for cid, total in rows.all()}

        winner, payload = determine_election_winner(region, election, tallies)
        # No votes cast -> inconclusive (SYSTEMS failure mode); still COMPLETED
        # with a voided result so the cycle can schedule a fresh election.
        if not tallies:
            payload["inconclusive"] = True

        election.results = payload

        # Persist the winner (SYSTEMS step 3). winner is the winning candidate's
        # player_id, or None when voided/inconclusive (no winner cleared the
        # supermajority gate / no votes cast). A voided election leaves the
        # incumbent Region.{position}_id untouched (a failed election does not
        # vacate the seat).
        winner_uuid: Optional[uuid.UUID] = None
        if winner is not None:
            try:
                winner_uuid = uuid.UUID(str(winner))
            except (TypeError, ValueError):
                winner_uuid = None
        election.winner_id = winner_uuid
        if winner_uuid is not None:
            # Region.{position}_id for single-seat positions. council_member is
            # multi-seat and has no single-occupant column — it persists to the
            # election row only.
            position_column = f"{election.position}_id"
            if hasattr(region, position_column):
                setattr(region, position_column, winner_uuid)
                region.updated_at = datetime.utcnow()

        election.status = ElectionStatus.COMPLETED
        await db.commit()
        return {
            "ok": True,
            "code": "ELECTION_COMPLETED",
            "election_id": str(election.id),
            "winner": winner,
            "results": payload,
        }

    @staticmethod
    async def finalize_policy(
        db: AsyncSession,
        policy_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """Close + resolve a single policy (idempotent).

        VOTING -> {PASSED -> IMPLEMENTED | REJECTED}. Verifies quorum, then the
        approval threshold; on pass, applies proposed_changes onto the region
        CLAMPED to the CHECK bounds and marks IMPLEMENTED (no double-enact). A
        non-VOTING policy is never re-finalized. Locks the policy + region rows.
        """
        locked = await db.execute(
            select(RegionalPolicy)
            .where(RegionalPolicy.id == policy_id)
            .with_for_update()
        )
        policy = locked.scalar_one_or_none()
        if policy is None:
            return {"ok": False, "code": "ERR_POLICY_NOT_FOUND"}
        # Idempotency: only a VOTING policy is ever finalized.
        if policy.status != PolicyStatus.VOTING:
            return {"ok": False, "code": "ERR_ALREADY_FINALIZED",
                    "status": policy.status}

        region = await db.execute(
            select(Region).where(Region.id == policy.region_id).with_for_update()
        )
        region = region.scalar_one_or_none()
        if region is None:
            await db.rollback()
            return {"ok": False, "code": "ERR_REGION_NOT_FOUND"}

        eligible = await RegionalGovernanceService._count_eligible_voters(
            db, region.id
        )
        quorum = compute_quorum(eligible, quorum_pct_for_region(region))

        # Quorum denominator: number of distinct voters who actually voted,
        # counted from the real regional_policy_votes ledger (migration
        # c5a8e2f1b9d3). Falls back to the legacy proposed_changes['_voters']
        # list (then raw tally presence) for legacy/manual rows predating the
        # table — strictly a backward-compat read; nothing writes _voters now.
        votes_cast = int(await db.scalar(
            select(func.count(RegionalPolicyVote.id)).where(
                RegionalPolicyVote.policy_id == policy.id
            )
        ) or 0)
        changes = dict(policy.proposed_changes or {})
        if votes_cast == 0:
            legacy_voters = changes.get(POLICY_VOTERS_KEY)
            votes_cast = (
                len(legacy_voters) if isinstance(legacy_voters, list)
                else (1 if (policy.votes_for or 0) + (policy.votes_against or 0) > 0 else 0)
            )

        threshold = threshold_for_policy(region, policy.policy_type)
        total_weight = int(policy.votes_for or 0) + int(policy.votes_against or 0)
        approval = (float(policy.votes_for or 0) / total_weight) if total_weight > 0 else 0.0

        result: Dict[str, Any] = {
            "ok": True,
            "policy_id": str(policy.id),
            "quorum_required": quorum,
            "votes_cast": votes_cast,
            "approval": round(approval, 4),
            "threshold": float(threshold),
        }

        if votes_cast < quorum:
            policy.status = PolicyStatus.REJECTED
            result.update(code="POLICY_REJECTED", reason="no_quorum")
            await db.commit()
            return result

        if approval >= float(threshold):
            # PASSED -> apply effects -> IMPLEMENTED, all in one transaction.
            policy.status = PolicyStatus.PASSED
            applied = enact_changes_onto_region(region, policy.proposed_changes)
            region.updated_at = datetime.utcnow()

            # Treasury-touching enactment (ADR-0059 N-I4): if the policy carries
            # a treasury adjustment, mutate Region.treasury_balance and write a
            # RegionalTreasuryEntry row in THIS SAME transaction so the running
            # balance stays reconcilable (SUM(delta) == treasury_balance). No
            # current canon policy type carries it, so existing policies are
            # unaffected.
            treasury_delta = compute_treasury_adjustment(region, policy.proposed_changes)
            if treasury_delta is not None:
                before = int(region.treasury_balance or 0)
                after = before + treasury_delta
                region.treasury_balance = after
                db.add(RegionalTreasuryEntry(
                    region_id=region.id,
                    before_balance=before,
                    after_balance=after,
                    delta=treasury_delta,
                    cause_type=RegionalTreasuryEntry.CAUSE_POLICY_ENACTMENT,
                    cause_id=policy.id,
                    reason=f"Policy enacted: {policy.title}",
                ))
                applied["treasury_balance"] = {"old": before, "new": after, "delta": treasury_delta}

            # Strip any legacy voter ledger from the stored policy now that it's
            # resolved (it was only needed during the voting window; nothing
            # writes it anymore, but legacy rows may still carry it).
            cleaned = dict(policy.proposed_changes or {})
            if POLICY_VOTERS_KEY in cleaned:
                cleaned.pop(POLICY_VOTERS_KEY, None)
                policy.proposed_changes = cleaned
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(policy, "proposed_changes")
            policy.status = PolicyStatus.IMPLEMENTED
            result.update(code="POLICY_ENACTED", applied=applied)
            await db.commit()
            return result

        policy.status = PolicyStatus.REJECTED
        result.update(code="POLICY_REJECTED", reason="below_threshold")
        await db.commit()
        return result