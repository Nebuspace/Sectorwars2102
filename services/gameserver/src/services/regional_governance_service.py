"""
Regional Governance Service
Handles business logic for regional governance operations
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, and_, or_
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import uuid
import logging

from src.models.region import (
    Region, RegionalMembership, RegionalPolicy, RegionalElection, 
    RegionalVote, RegionalTreaty, GovernanceType, PolicyStatus, ElectionStatus
)
from src.models.player import Player
from src.models.user import User

logger = logging.getLogger(__name__)


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