"""Regional authentication and authorization service for multi-regional platform"""

from typing import Dict, List, Optional, Set
from enum import Enum
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from src.core.database import get_async_session
from src.models.region import Region, RegionalMembership, MembershipType
from src.models.player import Player

import logging

logger = logging.getLogger(__name__)


class RegionalPermission(str, Enum):
    """Comprehensive regional permission system"""
    
    # Galaxy-level permissions (platform team)
    GALAXY_ADMIN_FULL = "galaxy:admin:*"
    GALAXY_MANAGE_REGIONS = "galaxy:regions:manage"
    GALAXY_MANAGE_NEXUS = "galaxy:nexus:manage"
    GALAXY_VIEW_ALL = "galaxy:view:*"
    GALAXY_MANAGE_USERS = "galaxy:users:manage"
    
    # Regional governor permissions
    REGION_OWNER_FULL = "region:{region_id}:owner:*"
    REGION_MANAGE_ECONOMY = "region:{region_id}:economy:manage"
    REGION_MANAGE_GOVERNANCE = "region:{region_id}:governance:manage"
    REGION_MANAGE_MEMBERS = "region:{region_id}:members:manage"
    REGION_VIEW_ANALYTICS = "region:{region_id}:analytics:view"
    REGION_MANAGE_CULTURE = "region:{region_id}:culture:manage"
    REGION_MANAGE_TREATIES = "region:{region_id}:treaties:manage"
    REGION_CREATE_ELECTIONS = "region:{region_id}:elections:create"
    REGION_MODERATE_CONTENT = "region:{region_id}:moderate"
    
    # Regional administrator permissions (appointed by governor)
    REGION_ADMIN_ECONOMIC = "region:{region_id}:admin:economic"
    REGION_ADMIN_SOCIAL = "region:{region_id}:admin:social"
    REGION_ADMIN_MILITARY = "region:{region_id}:admin:military"
    
    # Regional citizen permissions
    REGION_VOTE = "region:{region_id}:vote"
    REGION_PROPOSE_POLICY = "region:{region_id}:policy:propose"
    REGION_TRADE = "region:{region_id}:trade"
    REGION_COMMUNICATE = "region:{region_id}:communicate"
    REGION_CREATE_CONTENT = "region:{region_id}:content:create"
    REGION_PARTICIPATE_EVENTS = "region:{region_id}:events:participate"
    
    # Cross-regional permissions
    TRAVEL_BETWEEN_REGIONS = "travel:regions"
    GALACTIC_CITIZEN_BENEFITS = "galactic_citizen:benefits"
    DIPLOMATIC_IMMUNITY = "diplomatic:immunity"
    CROSS_REGIONAL_TRADE = "trade:cross_regional"
    NEXUS_ACCESS = "nexus:access"
    EMBASSY_ACCESS = "embassy:access"


class RegionalRole(str, Enum):
    """Regional role hierarchy"""
    GALAXY_ADMINISTRATOR = "galaxy_administrator"
    REGION_OWNER = "region_owner"
    REGION_ADMINISTRATOR = "region_administrator"
    REGION_MODERATOR = "region_moderator"
    REGION_CITIZEN = "region_citizen"
    REGION_RESIDENT = "region_resident"
    REGION_VISITOR = "region_visitor"
    GALACTIC_CITIZEN = "galactic_citizen"
    DIPLOMAT = "diplomat"
    BANNED = "banned"


class RegionalAuthService:
    """Comprehensive regional authentication and authorization service"""
    
    def __init__(self):
        self.permission_cache: Dict[str, Dict[str, Set[RegionalPermission]]] = {}
        self.cache_expiry: Dict[str, datetime] = {}
        self.cache_duration = timedelta(minutes=15)
    
    async def check_regional_permission(
        self, 
        user_id: str, 
        region_id: str, 
        permission: RegionalPermission,
        session: Optional[AsyncSession] = None
    ) -> bool:
        """Check if user has specific permission in region"""
        
        if session is None:
            async with get_async_session() as session:
                return await self._check_permission_impl(user_id, region_id, permission, session)
        else:
            return await self._check_permission_impl(user_id, region_id, permission, session)
    
    async def _check_permission_impl(
        self,
        user_id: str,
        region_id: str,
        permission: RegionalPermission,
        session: AsyncSession
    ) -> bool:
        """Internal permission check implementation"""
        
        # Check cache first
        cache_key = f"{user_id}:{region_id}"
        if cache_key in self.permission_cache:
            if cache_key in self.cache_expiry and datetime.utcnow() < self.cache_expiry[cache_key]:
                user_permissions = self.permission_cache[cache_key].get(region_id, set())
                if permission in user_permissions or RegionalPermission.GALAXY_ADMIN_FULL in user_permissions:
                    return True
        
        # Get user's player record
        result = await session.execute(
            select(Player).options(
                selectinload(Player.user),
                selectinload(Player.regional_memberships).selectinload(RegionalMembership.region)
            ).where(Player.user_id == user_id)
        )
        player = result.scalar_one_or_none()
        
        if not player:
            return False
        
        # Check galaxy-level permissions first
        if await self._has_galaxy_permission(player, permission, session):
            await self._cache_permission(user_id, region_id, permission)
            return True
        
        # Check galactic citizen permissions
        if player.is_galactic_citizen and await self._has_galactic_citizen_permission(permission):
            await self._cache_permission(user_id, region_id, permission)
            return True
        
        # Check regional membership permissions
        regional_membership = await self._get_regional_membership(player.id, region_id, session)
        if regional_membership:
            regional_role = await self._get_regional_role(regional_membership, session)
            role_permissions = await self._get_role_permissions(regional_role, region_id)
            
            if permission in role_permissions:
                await self._cache_permission(user_id, region_id, permission)
                return True
        
        return False
    
    async def _has_galaxy_permission(
        self, 
        player: Player, 
        permission: RegionalPermission, 
        session: AsyncSession
    ) -> bool:
        """Check if player has galaxy-level permissions"""
        
        # Check if user is platform administrator
        if player.user and hasattr(player.user, 'is_platform_admin') and player.user.is_platform_admin:
            return True
        
        # Check specific galaxy permissions
        galaxy_permissions = {
            RegionalPermission.GALAXY_ADMIN_FULL,
            RegionalPermission.GALAXY_MANAGE_REGIONS,
            RegionalPermission.GALAXY_MANAGE_NEXUS,
            RegionalPermission.GALAXY_VIEW_ALL,
            RegionalPermission.GALAXY_MANAGE_USERS
        }
        
        return permission in galaxy_permissions and await self._user_has_galaxy_role(player, session)
    
    async def _has_galactic_citizen_permission(self, permission: RegionalPermission) -> bool:
        """Check if permission is available to galactic citizens"""
        galactic_citizen_permissions = {
            RegionalPermission.TRAVEL_BETWEEN_REGIONS,
            RegionalPermission.GALACTIC_CITIZEN_BENEFITS,
            RegionalPermission.CROSS_REGIONAL_TRADE,
            RegionalPermission.NEXUS_ACCESS,
            RegionalPermission.EMBASSY_ACCESS
        }
        
        return permission in galactic_citizen_permissions
    
    async def _get_regional_membership(
        self, 
        player_id: str, 
        region_id: str, 
        session: AsyncSession
    ) -> Optional[RegionalMembership]:
        """Get player's membership in specific region"""
        
        result = await session.execute(
            select(RegionalMembership).options(
                selectinload(RegionalMembership.region)
            ).where(
                and_(
                    RegionalMembership.player_id == player_id,
                    RegionalMembership.region_id == region_id
                )
            )
        )
        return result.scalar_one_or_none()
    
    async def _get_regional_role(
        self, 
        membership: RegionalMembership, 
        session: AsyncSession
    ) -> RegionalRole:
        """Determine player's role in region"""
        
        # Check if player owns the region
        if membership.region.owner_id == membership.player_id:
            return RegionalRole.REGION_OWNER
        
        # Check for administrative roles
        if membership.local_rank == "administrator":
            return RegionalRole.REGION_ADMINISTRATOR
        elif membership.local_rank == "moderator":
            return RegionalRole.REGION_MODERATOR
        
        # Check membership type
        if membership.membership_type == MembershipType.CITIZEN:
            return RegionalRole.REGION_CITIZEN
        elif membership.membership_type == MembershipType.RESIDENT:
            return RegionalRole.REGION_RESIDENT
        else:
            return RegionalRole.REGION_VISITOR
    
    async def _get_role_permissions(
        self, 
        role: RegionalRole, 
        region_id: str
    ) -> Set[RegionalPermission]:
        """Get permissions for specific role in region"""
        
        # Format region-specific permissions
        def format_permission(perm: str) -> RegionalPermission:
            return RegionalPermission(perm.format(region_id=region_id))
        
        role_permissions = {
            RegionalRole.REGION_OWNER: {
                format_permission(RegionalPermission.REGION_OWNER_FULL.value),
                format_permission(RegionalPermission.REGION_MANAGE_ECONOMY.value),
                format_permission(RegionalPermission.REGION_MANAGE_GOVERNANCE.value),
                format_permission(RegionalPermission.REGION_MANAGE_MEMBERS.value),
                format_permission(RegionalPermission.REGION_VIEW_ANALYTICS.value),
                format_permission(RegionalPermission.REGION_MANAGE_CULTURE.value),
                format_permission(RegionalPermission.REGION_MANAGE_TREATIES.value),
                format_permission(RegionalPermission.REGION_CREATE_ELECTIONS.value),
                format_permission(RegionalPermission.REGION_MODERATE_CONTENT.value),
                format_permission(RegionalPermission.REGION_VOTE.value),
                format_permission(RegionalPermission.REGION_TRADE.value),
                format_permission(RegionalPermission.REGION_COMMUNICATE.value),
            },
            
            RegionalRole.REGION_ADMINISTRATOR: {
                format_permission(RegionalPermission.REGION_MANAGE_ECONOMY.value),
                format_permission(RegionalPermission.REGION_MANAGE_MEMBERS.value),
                format_permission(RegionalPermission.REGION_VIEW_ANALYTICS.value),
                format_permission(RegionalPermission.REGION_MODERATE_CONTENT.value),
                format_permission(RegionalPermission.REGION_VOTE.value),
                format_permission(RegionalPermission.REGION_TRADE.value),
                format_permission(RegionalPermission.REGION_COMMUNICATE.value),
            },
            
            RegionalRole.REGION_MODERATOR: {
                format_permission(RegionalPermission.REGION_MODERATE_CONTENT.value),
                format_permission(RegionalPermission.REGION_VOTE.value),
                format_permission(RegionalPermission.REGION_TRADE.value),
                format_permission(RegionalPermission.REGION_COMMUNICATE.value),
            },
            
            RegionalRole.REGION_CITIZEN: {
                format_permission(RegionalPermission.REGION_VOTE.value),
                format_permission(RegionalPermission.REGION_PROPOSE_POLICY.value),
                format_permission(RegionalPermission.REGION_TRADE.value),
                format_permission(RegionalPermission.REGION_COMMUNICATE.value),
                format_permission(RegionalPermission.REGION_CREATE_CONTENT.value),
                format_permission(RegionalPermission.REGION_PARTICIPATE_EVENTS.value),
            },
            
            RegionalRole.REGION_RESIDENT: {
                format_permission(RegionalPermission.REGION_TRADE.value),
                format_permission(RegionalPermission.REGION_COMMUNICATE.value),
                format_permission(RegionalPermission.REGION_PARTICIPATE_EVENTS.value),
            },
            
            RegionalRole.REGION_VISITOR: {
                format_permission(RegionalPermission.REGION_TRADE.value),
                format_permission(RegionalPermission.REGION_COMMUNICATE.value),
            }
        }
        
        return role_permissions.get(role, set())
    
    async def _user_has_galaxy_role(self, player: Player, session: AsyncSession) -> bool:
        """Check if user has galaxy-level administrative role"""
        # This would check against a galaxy_administrators table or user flags
        # For now, assume only users with is_platform_admin flag
        return player.user and hasattr(player.user, 'is_platform_admin') and player.user.is_platform_admin
    
    async def _cache_permission(self, user_id: str, region_id: str, permission: RegionalPermission):
        """Cache permission result"""
        cache_key = f"{user_id}:{region_id}"
        
        if cache_key not in self.permission_cache:
            self.permission_cache[cache_key] = {}
        
        if region_id not in self.permission_cache[cache_key]:
            self.permission_cache[cache_key][region_id] = set()
        
        self.permission_cache[cache_key][region_id].add(permission)
        self.cache_expiry[cache_key] = datetime.utcnow() + self.cache_duration
    
    async def get_accessible_regions(self, user_id: str) -> List[Dict[str, any]]:
        """Get list of regions user can access with their roles"""
        
        async with get_async_session() as session:
            # Get user's player record
            result = await session.execute(
                select(Player).options(
                    selectinload(Player.regional_memberships).selectinload(RegionalMembership.region)
                ).where(Player.user_id == user_id)
            )
            player = result.scalar_one_or_none()
            
            if not player:
                return []
            
            accessible_regions = []
            
            # Add regions from memberships
            for membership in player.regional_memberships:
                role = await self._get_regional_role(membership, session)
                accessible_regions.append({
                    "region_id": str(membership.region_id),
                    "region_name": membership.region.name,
                    "display_name": membership.region.display_name,
                    "membership_type": membership.membership_type,
                    "role": role.value,
                    "reputation": membership.reputation_score,
                    "can_vote": membership.can_vote
                })
            
            # Add all regions if galactic citizen
            if player.is_galactic_citizen:
                result = await session.execute(
                    select(Region).where(Region.status == "active")
                )
                all_regions = result.scalars().all()
                
                existing_region_ids = {r["region_id"] for r in accessible_regions}
                
                for region in all_regions:
                    if str(region.id) not in existing_region_ids:
                        accessible_regions.append({
                            "region_id": str(region.id),
                            "region_name": region.name,
                            "display_name": region.display_name,
                            "membership_type": "galactic_citizen",
                            "role": RegionalRole.GALACTIC_CITIZEN.value,
                            "reputation": 0,
                            "can_vote": False
                        })
            
            return accessible_regions
    
    async def promote_to_galactic_citizen(self, user_id: str) -> bool:
        """Promote player to galactic citizen status"""
        
        async with get_async_session() as session:
            result = await session.execute(
                select(Player).where(Player.user_id == user_id)
            )
            player = result.scalar_one_or_none()
            
            if not player:
                return False
            
            player.is_galactic_citizen = True
            await session.commit()
            
            # Clear cache for this user
            keys_to_clear = [k for k in self.permission_cache.keys() if k.startswith(f"{user_id}:")]
            for key in keys_to_clear:
                del self.permission_cache[key]
                if key in self.cache_expiry:
                    del self.cache_expiry[key]
            
            logger.info(f"Promoted user {user_id} to galactic citizen")
            return True
    
    async def revoke_galactic_citizenship(self, user_id: str) -> bool:
        """Revoke galactic citizen status"""
        
        async with get_async_session() as session:
            result = await session.execute(
                select(Player).where(Player.user_id == user_id)
            )
            player = result.scalar_one_or_none()
            
            if not player:
                return False
            
            player.is_galactic_citizen = False
            await session.commit()
            
            # Clear cache for this user
            keys_to_clear = [k for k in self.permission_cache.keys() if k.startswith(f"{user_id}:")]
            for key in keys_to_clear:
                del self.permission_cache[key]
                if key in self.cache_expiry:
                    del self.cache_expiry[key]
            
            logger.info(f"Revoked galactic citizenship for user {user_id}")
            return True
    
    async def assign_regional_role(
        self, 
        user_id: str, 
        region_id: str, 
        role: str
    ) -> bool:
        """Assign specific role to user in region"""
        
        async with get_async_session() as session:
            # Get regional membership
            result = await session.execute(
                select(RegionalMembership).where(
                    and_(
                        RegionalMembership.player_id == user_id,
                        RegionalMembership.region_id == region_id
                    )
                )
            )
            membership = result.scalar_one_or_none()
            
            if not membership:
                return False
            
            membership.local_rank = role
            await session.commit()
            
            # Clear cache for this user
            cache_key = f"{user_id}:{region_id}"
            if cache_key in self.permission_cache:
                del self.permission_cache[cache_key]
            if cache_key in self.cache_expiry:
                del self.cache_expiry[cache_key]
            
            logger.info(f"Assigned role {role} to user {user_id} in region {region_id}")
            return True


# Singleton instance for use across the application
regional_auth = RegionalAuthService()