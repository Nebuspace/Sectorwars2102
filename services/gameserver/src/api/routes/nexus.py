"""Central Nexus management API routes"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from typing import Dict, List, Any, Optional
from pydantic import BaseModel

from src.auth.admin_scopes import GALAXY_MANAGE
from src.auth.dependencies import get_current_user, get_current_player, require_scope
from src.core.database import get_async_session
from src.models.user import User
from src.models.player import Player
from src.models.sector import Sector
from src.models.station import Station
from src.models.planet import Planet
from src.models.region import Region
from src.models.cluster import Cluster
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus
from src.services.nexus_generation_service import nexus_generation_service
from src.services.regional_auth_service import regional_auth, RegionalPermission

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nexus", tags=["Central Nexus"])


class NexusGenerationRequest(BaseModel):
    """Request to generate or regenerate Central Nexus"""
    force_regenerate: bool = False
    preserve_player_data: bool = True
    # districts_to_regenerate field removed - districts concept eliminated


class NexusStatsResponse(BaseModel):
    """Central Nexus statistics response"""
    total_sectors: int
    total_ports: int
    total_planets: int
    total_warp_gates: int
    clusters: List[Dict[str, Any]]  # Changed from districts
    # Nullable: no player-location or traffic telemetry exists yet, so these
    # are None rather than fabricated zeros.
    active_players: Optional[int] = None
    daily_traffic: Optional[int] = None


class ClusterInfoResponse(BaseModel):
    """Cluster information response (replaces DistrictInfoResponse)"""
    cluster_id: str
    name: str
    cluster_type: str
    sector_count: int
    ports_count: int
    planets_count: int
    avg_security_level: float
    avg_development_level: float
    is_discovered: bool
    economic_value: int


@router.post("/generate")
async def generate_central_nexus(
    request: NexusGenerationRequest,
    background_tasks: BackgroundTasks,
    current_admin: User = Depends(require_scope(GALAXY_MANAGE)),
    session: AsyncSession = Depends(get_async_session)
):
    """Generate the Central Nexus galaxy (Admin only). Requires admin authentication."""
    try:
        
        # Check if nexus already exists
        existing_nexus = await session.execute(
            select(Region).where(Region.name == "central-nexus")
        )
        nexus_region = existing_nexus.scalar_one_or_none()
        
        if nexus_region and not request.force_regenerate:
            raise HTTPException(
                status_code=409,
                detail="Central Nexus already exists. Use force_regenerate=true to regenerate."
            )
        
        # Start generation in background
        background_tasks.add_task(
            generate_nexus_task,
            request.force_regenerate,
            request.preserve_player_data
        )
        
        return {
            "message": "Central Nexus generation started",
            "status": "in_progress",
            "estimated_completion": "15-20 minutes"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start nexus generation: {e}")
        raise HTTPException(status_code=500, detail="Failed to start generation")


@router.get("/status")
async def get_nexus_status(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session)
) -> Dict[str, Any]:
    """Get Central Nexus status and basic information"""
    try:
        # Check if nexus exists
        result = await session.execute(
            select(Region).where(Region.name == "central-nexus")
        )
        nexus_region = result.scalar_one_or_none()
        
        if not nexus_region:
            return {
                "exists": False,
                "status": "not_generated",
                "message": "Central Nexus has not been generated yet"
            }
        
        # Get basic statistics
        sectors_count = await session.execute(
            select(func.count(Sector.id)).where(Sector.region_id == nexus_region.id)
        )
        total_sectors = sectors_count.scalar() or 0
        
        ports_count = await session.execute(
            select(func.count(Station.id)).where(Station.region_id == nexus_region.id)
        )
        total_ports = ports_count.scalar() or 0
        
        planets_count = await session.execute(
            select(func.count(Planet.id)).where(Planet.region_id == nexus_region.id)
        )
        total_planets = planets_count.scalar() or 0
        
        return {
            "exists": True,
            "status": nexus_region.status,
            "nexus_id": str(nexus_region.id),
            "created_at": nexus_region.created_at.isoformat() if nexus_region.created_at else None,
            "total_sectors": total_sectors,
            "total_ports": total_ports,
            "total_planets": total_planets,
            "governance_type": nexus_region.governance_type,
            "economic_specialization": nexus_region.economic_specialization
        }
    
    except Exception as e:
        logger.error(f"Failed to get nexus status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get status")


@router.get("/stats", response_model=NexusStatsResponse)
async def get_nexus_statistics(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session)
):
    """Get comprehensive Central Nexus statistics"""
    try:
        # Get nexus region
        result = await session.execute(
            select(Region).where(Region.name == "central-nexus")
        )
        nexus_region = result.scalar_one_or_none()
        
        if not nexus_region:
            raise HTTPException(status_code=404, detail="Central Nexus not found")
        
        # Get total counts
        sectors_result = await session.execute(
            select(func.count(Sector.id)).where(Sector.region_id == nexus_region.id)
        )
        total_sectors = sectors_result.scalar() or 0
        
        ports_result = await session.execute(
            select(func.count(Station.id)).where(Station.region_id == nexus_region.id)
        )
        total_ports = ports_result.scalar() or 0
        
        planets_result = await session.execute(
            select(func.count(Planet.id)).where(Planet.region_id == nexus_region.id)
        )
        total_planets = planets_result.scalar() or 0
        
        # Get cluster breakdown (replaces district breakdown)
        clusters_result = await session.execute(
            select(
                Cluster.id,
                Cluster.name,
                Cluster.type,
                Cluster.sector_count,
                func.avg(Sector.security_level).label('avg_security'),
                func.avg(Sector.development_level).label('avg_development')
            ).join(
                Sector, Cluster.id == Sector.cluster_id
            ).where(
                Cluster.region_id == nexus_region.id
            ).group_by(Cluster.id, Cluster.name, Cluster.type, Cluster.sector_count)
        )

        clusters = []
        for row in clusters_result:
            clusters.append({
                "cluster_id": str(row.id),
                "name": row.name,
                "type": row.type.value if hasattr(row.type, 'value') else str(row.type),
                "sectors": row.sector_count,
                "avg_security": round(row.avg_security, 1) if row.avg_security else 0,
                "avg_development": round(row.avg_development, 1) if row.avg_development else 0
            })

        # Count warp gates: active warp tunnels touching the nexus region
        # from EITHER end — tunnels terminating at the nexus are gates too,
        # and counting only origins missed them entirely.
        nexus_sector_ids = select(Sector.id).where(
            Sector.region_id == nexus_region.id
        ).scalar_subquery()
        warp_gates_result = await session.execute(
            select(func.count(WarpTunnel.id)).where(
                or_(
                    WarpTunnel.origin_sector_id.in_(nexus_sector_ids),
                    WarpTunnel.destination_sector_id.in_(nexus_sector_ids)
                ),
                WarpTunnel.status == WarpTunnelStatus.ACTIVE
            )
        )
        total_warp_gates = warp_gates_result.scalar() or 0

        # active_players / daily_traffic: no player-location or traffic
        # telemetry exists yet — return None instead of fabricated zeros.
        return NexusStatsResponse(
            total_sectors=total_sectors,
            total_ports=total_ports,
            total_planets=total_planets,
            total_warp_gates=total_warp_gates,
            clusters=clusters,  # Changed from districts
            active_players=None,
            daily_traffic=None
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get nexus statistics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get statistics")


@router.get("/clusters")
async def get_clusters_info(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session)
) -> List[ClusterInfoResponse]:
    """Get information about all clusters in Central Nexus (replaces /districts)"""
    try:
        # Get nexus region
        result = await session.execute(
            select(Region).where(Region.name == "central-nexus")
        )
        nexus_region = result.scalar_one_or_none()

        if not nexus_region:
            raise HTTPException(status_code=404, detail="Central Nexus not found")

        # Get cluster information
        clusters_result = await session.execute(
            select(Cluster).where(Cluster.region_id == nexus_region.id)
        )
        clusters_list = clusters_result.scalars().all()

        response = []
        for cluster in clusters_list:
            # Get sector statistics for this cluster
            sector_stats = await session.execute(
                select(
                    func.count(Sector.id).label('sector_count'),
                    func.avg(Sector.security_level).label('avg_security'),
                    func.avg(Sector.development_level).label('avg_development')
                ).where(Sector.cluster_id == cluster.id)
            )
            stats = sector_stats.one()

            # Get ports count for this cluster
            ports_result = await session.execute(
                select(func.count(Station.id)).join(
                    Sector, Station.sector_id == Sector.sector_id
                ).where(Sector.cluster_id == cluster.id)
            )
            ports_count = ports_result.scalar() or 0

            # Get planets count for this cluster
            planets_result = await session.execute(
                select(func.count(Planet.id)).join(
                    Sector, Planet.sector_id == Sector.sector_id
                ).where(Sector.cluster_id == cluster.id)
            )
            planets_count = planets_result.scalar() or 0

            response.append(ClusterInfoResponse(
                cluster_id=str(cluster.id),
                name=cluster.name,
                cluster_type=cluster.type.value if hasattr(cluster.type, 'value') else str(cluster.type),
                sector_count=stats.sector_count or 0,
                ports_count=ports_count,
                planets_count=planets_count,
                avg_security_level=round(stats.avg_security, 1) if stats.avg_security else 0,
                avg_development_level=round(stats.avg_development, 1) if stats.avg_development else 0,
                is_discovered=cluster.is_discovered,
                economic_value=cluster.economic_value or 0
            ))

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get clusters info: {e}")
        raise HTTPException(status_code=500, detail="Failed to get clusters information")


@router.get("/clusters/{cluster_id}")
async def get_cluster_details(
    cluster_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session)
) -> Dict[str, Any]:
    """Get detailed information about a specific cluster (replaces /districts/{district_type})"""
    try:
        # Get cluster
        cluster_result = await session.execute(
            select(Cluster).where(Cluster.id == cluster_id)
        )
        cluster = cluster_result.scalar_one_or_none()

        if not cluster:
            raise HTTPException(status_code=404, detail="Cluster not found")

        # Get sectors in this cluster
        sectors_result = await session.execute(
            select(Sector).where(Sector.cluster_id == cluster.id).limit(50)  # Limit for performance
        )
        sectors = sectors_result.scalars().all()

        if not sectors:
            return {
                "cluster_id": str(cluster.id),
                "name": cluster.name,
                "cluster_type": cluster.type.value if hasattr(cluster.type, 'value') else str(cluster.type),
                "total_sectors": 0,
                "sample_sectors": [],
                "sample_ports": [],
                "sample_planets": []
            }

        # Get sample ports and planets
        sector_numbers = [s.sector_number for s in sectors[:20]]  # Sample first 20

        ports_result = await session.execute(
            select(Station).where(Station.sector_id.in_(sector_numbers)).limit(10)
        )
        sample_ports = ports_result.scalars().all()

        planets_result = await session.execute(
            select(Planet).where(Planet.sector_id.in_(sector_numbers)).limit(10)
        )
        sample_planets = planets_result.scalars().all()

        return {
            "cluster_id": str(cluster.id),
            "name": cluster.name,
            "cluster_type": cluster.type.value if hasattr(cluster.type, 'value') else str(cluster.type),
            "total_sectors": len(sectors),
            "economic_value": cluster.economic_value or 0,
            "is_discovered": cluster.is_discovered,
            "warp_stability": cluster.warp_stability or 0,
            "sample_sectors": [
                {
                    "sector_number": s.sector_number,
                    "sector_id": s.sector_id,
                    "security_level": s.security_level,
                    "development_level": s.development_level,
                    "traffic_level": s.traffic_level,
                    "special_features": s.special_features or {}
                }
                for s in sectors[:10]
            ],
            "sample_ports": [
                {
                    "sector_id": p.sector_id,
                    "name": p.name,
                    "station_class": p.station_class.value if hasattr(p.station_class, 'value') else str(p.station_class),
                    "type": p.type.value if hasattr(p.type, 'value') else str(p.type),
                    "docking_fee": p.docking_fee
                }
                for p in sample_ports
            ],
            "sample_planets": [
                {
                    "sector_id": p.sector_id,
                    "name": p.name,
                    "type": p.type.value if hasattr(p.type, 'value') else str(p.type),
                    "population": p.population,
                    "habitability_score": p.habitability_score
                }
                for p in sample_planets
            ]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get cluster details: {e}")
        raise HTTPException(status_code=500, detail="Failed to get cluster details")


# ENDPOINT REMOVED: POST /districts/{district_type}/regenerate
# District concept eliminated in favor of cluster-based organization
# Central Nexus now uses 20 clusters to organize 5000 sectors
# For cluster-specific regeneration, regenerate the entire Central Nexus region


# Background task functions
async def generate_nexus_task(
    force_regenerate: bool,
    preserve_player_data: bool
):
    """Background task to generate Central Nexus with cluster-based organization"""
    try:
        async with get_async_session() as session:
            # Full Central Nexus generation with 20 clusters organizing 5000 sectors
            result = await nexus_generation_service.generate_central_nexus(session)
            logger.info("Central Nexus generation completed successfully")

    except Exception as e:
        logger.error(f"Central Nexus generation failed: {e}")


# FUNCTION REMOVED: regenerate_district_task
# District regeneration no longer applicable with cluster-based architecture
# Central Nexus uses unified cluster organization (20 clusters × 250 sectors each)