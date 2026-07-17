from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
from pydantic import BaseModel
import random
import math
import uuid

from src.core.database import get_db
from src.auth.admin_scopes import PLAYERS_VIEW
from src.auth.dependencies import require_scope
from src.models.user import User
from src.models.galaxy import Galaxy
from src.models.cluster import Cluster, ClusterType
from src.models.sector import Sector, SectorType, SectorSpecialType
from src.models.warp_tunnel import WarpTunnel
from src.models.station import Station, StationClass, StationType, StationStatus
from src.models.planet import Planet

# Enhanced request schemas
# DEPRECATED: EnhancedGalaxyConfig no longer used (zone-based generation removed)
# class EnhancedGalaxyConfig(BaseModel):
#     name: str
#     total_sectors: int
#     region_distribution: Dict[str, int]  # federation, border, frontier percentages
#     density: Dict[str, float]  # port_density, planet_density, one_way_warp_percentage
#     warp_tunnel_config: Dict[str, Any]  # min_per_region, max_per_region, stability_range
#     resource_distribution: Dict[str, Dict[str, int]]  # min/max by region type
#     hazard_levels: Dict[str, Dict[str, int]]  # min/max by region type

class SectorUpdateRequest(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    hazard_level: Optional[float] = None
    is_navigable: Optional[bool] = None
    is_explorable: Optional[bool] = None
    resources: Optional[Dict[str, Any]] = None

class StationCreateRequest(BaseModel):
    sector_id: int
    name: str
    port_class: int
    commodities: Dict[str, Dict[str, int]]
    services: Dict[str, bool]
    defense_drones: int
    has_turrets: bool
    tax_rate: float

class PlanetCreateRequest(BaseModel):
    sector_id: int
    name: str
    planet_type: str
    colonists: Dict[str, Dict[str, int]]
    production_rates: Dict[str, int]
    breeding_rate: int
    citadel_level: int
    shield_level: int
    fighters: int
    size: int = 5  # 1-10 planet size; caps the citadel level (max_citadel_level_for_size)

class WarpTunnelEnhancedRequest(BaseModel):
    source_sector_id: int
    target_sector_id: int
    tunnel_type: str  # natural or artificial
    is_one_way: bool
    stability: int
    turn_cost: int
    access_control: str  # public, team_only, toll
    toll_amount: Optional[int] = None

router = APIRouter()

# DEPRECATED: Zone-based galaxy generation endpoint removed
# Use POST /admin/galaxy/generate instead, which uses the updated region-based architecture
# Architecture: Galaxy → Region → Cluster → Sector (zones eliminated)

@router.put("/sector/{sector_id}", response_model=dict)
async def update_sector(
    sector_id: int,
    request: SectorUpdateRequest,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Update sector properties"""
    sector = db.query(Sector).filter(Sector.sector_id == sector_id).first()
    
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")
    
    # Update fields if provided
    if request.name is not None:
        sector.name = request.name
    
    if request.type is not None:
        # Map string to enum
        type_map = {
            "normal": SectorSpecialType.NORMAL,
            "nebula": SectorSpecialType.NEBULA,
            "asteroid_field": SectorSpecialType.ASTEROID_FIELD,
            "radiation_zone": SectorSpecialType.RADIATION_ZONE,
            "warp_storm": SectorSpecialType.WARP_STORM
        }
        sector.special_type = type_map.get(request.type, SectorSpecialType.NORMAL)
    
    if request.hazard_level is not None:
        sector.hazard_level = request.hazard_level
    
    if request.is_navigable is not None:
        sector.navhazard = not request.is_navigable
    
    if request.is_explorable is not None:
        sector.is_discovered = request.is_explorable
    
    if request.resources is not None:
        sector.resources = request.resources
    
    db.commit()
    
    return {
        "id": str(sector.id),
        "sector_id": sector.sector_id,
        "name": sector.name,
        "type": sector.special_type.value,
        "hazard_level": sector.hazard_level,
        "updated": True
    }


@router.post("/port/create", response_model=dict)
async def create_port(
    request: StationCreateRequest,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Create a new port in a sector"""
    # Check if sector exists and doesn't have a port
    sector = db.query(Sector).filter(Sector.sector_id == request.sector_id).first()
    
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")
    
    if sector.has_port:
        raise HTTPException(status_code=400, detail="Sector already has a port")

    # Validate station class
    try:
        station_class = StationClass(request.port_class)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid port class: {request.port_class}")

    # Create port (defense values map into the Station.defenses JSONB)
    station = Station(
        name=request.name,
        sector_id=request.sector_id,
        sector_uuid=sector.id,
        station_class=station_class,
        type=StationType.TRADING,
        status=StationStatus.OPERATIONAL,
        owner_id=None,  # Admin-created ports are NPC owned
        tax_rate=request.tax_rate,
        commodities=request.commodities,
        services=request.services,
        defenses={
            "defense_drones": request.defense_drones,
            "max_defense_drones": 50,
            "auto_turrets": request.has_turrets,
            "defense_grid": False,
            "shield_strength": 50,
            "patrol_ships": 0,
            "military_contract": False
        }
    )

    db.add(station)
    sector.has_port = True
    db.commit()

    return {
        "id": str(station.id),
        "name": station.name,
        "sector_id": station.sector_id,
        "station_class": station.station_class.value,
        "created": True
    }


@router.post("/planet/create", response_model=dict)
async def create_planet(
    request: PlanetCreateRequest,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Create a new planet in a sector"""
    # Check if sector exists and doesn't have a planet
    sector = db.query(Sector).filter(Sector.sector_id == request.sector_id).first()
    
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")
    
    if sector.has_planet:
        raise HTTPException(status_code=400, detail="Sector already has a planet")

    # CRT size-gate: a planet's size caps its citadel level (small worlds can't pack a
    # full citadel). Clamp the admin-requested level so an admin can't create an
    # over-cap planet — the same invariant citadel_service.start_upgrade enforces.
    from src.services.structures import max_citadel_level_for_size
    planet_size = max(1, min(10, request.size))
    clamped_citadel_level = max(0, min(request.citadel_level, max_citadel_level_for_size(planet_size)))

    # Create planet
    planet = Planet(
        name=request.name,
        sector_id=request.sector_id,
        planet_type=request.planet_type,
        owner_id=None,  # Uncolonized
        size=planet_size,
        citadel_level=clamped_citadel_level,
        shield_level=request.shield_level,
        colonists=request.colonists,
        production=request.production_rates,
        breeding_rate=request.breeding_rate,
        morale=100,
        treasury=0,
        fighters=request.fighters,
        max_fighters=10000,
        under_attack=False
    )
    
    db.add(planet)
    sector.has_planet = True
    db.commit()
    
    return {
        "id": str(planet.id),
        "name": planet.name,
        "sector_id": planet.sector_id,
        "planet_type": planet.planet_type,
        "created": True
    }


@router.post("/warp-tunnel/create-enhanced", response_model=dict)
async def create_enhanced_warp_tunnel(
    request: WarpTunnelEnhancedRequest,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Create a warp tunnel with enhanced options"""
    from src.models.warp_tunnel import WarpTunnelType, WarpTunnelStatus

    # Validate sectors exist (request carries human-readable Integer sector numbers;
    # WarpTunnel FKs reference the Sector UUID primary key, so resolve to .id below).
    source = db.query(Sector).filter(Sector.sector_id == request.source_sector_id).first()
    target = db.query(Sector).filter(Sector.sector_id == request.target_sector_id).first()

    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or target sector not found")

    # Check if warp already exists (real model fields: origin_/destination_sector_id, by UUID)
    existing = db.query(WarpTunnel).filter(
        ((WarpTunnel.origin_sector_id == source.id) &
         (WarpTunnel.destination_sector_id == target.id)) |
        ((WarpTunnel.origin_sector_id == target.id) &
         (WarpTunnel.destination_sector_id == source.id))
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Warp tunnel already exists between these sectors")

    # Map the request's tunnel_type ("natural"/"artificial") to the model enum.
    # sectors.md:42-48 -- WarpTunnel.type has exactly two canon values; admin
    # creation is restricted to them (WO-GWQ-TUNNELTYPE). The prior silent
    # fallback to STANDARD on an unknown string minted a non-canon type.
    try:
        tunnel_type = WarpTunnelType[request.tunnel_type.upper()]
    except KeyError:
        tunnel_type = None
    if tunnel_type not in (WarpTunnelType.NATURAL, WarpTunnelType.ARTIFICIAL):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tunnel_type: {request.tunnel_type!r} (must be 'natural' or 'artificial')"
        )

    # Access control / toll do not have dedicated WarpTunnel columns; persist them
    # in the access_requirements JSONB so the intent isn't lost.
    access_requirements = {
        "access_control": request.access_control,
        "toll_amount": request.toll_amount or 0,
    }

    # Create warp tunnel using the REAL model fields.
    warp = WarpTunnel(
        name=f"{source.name} <-> {target.name}",
        origin_sector_id=source.id,
        destination_sector_id=target.id,
        type=tunnel_type,
        status=WarpTunnelStatus.ACTIVE,
        is_bidirectional=not request.is_one_way,
        stability=request.stability / 100.0,
        turn_cost=request.turn_cost,
        is_public=(request.access_control == "public"),
        access_requirements=access_requirements,
        created_by_player_id=None,  # admin-created; no player owner
    )

    db.add(warp)
    source.has_warp_tunnel = True
    if not request.is_one_way:
        target.has_warp_tunnel = True

    db.commit()

    return {
        "id": str(warp.id),
        "source_sector_id": request.source_sector_id,
        "target_sector_id": request.target_sector_id,
        "is_one_way": request.is_one_way,
        "stability": request.stability,
        "created": True
    }


@router.get("/sectors/enhanced", response_model=dict)
async def get_enhanced_sectors(
    region_id: Optional[str] = None,
    cluster_id: Optional[str] = None,
    include_contents: bool = True,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get sectors with enhanced information"""
    query = db.query(Sector)
    
    if cluster_id:
        query = query.filter(Sector.cluster_id == cluster_id)
    elif region_id:
        query = query.join(Cluster).filter(Cluster.region_id == region_id)
    
    sectors = query.all()
    
    sector_list = []
    for sector in sectors:
        sector_data = {
            "id": str(sector.id),
            "sector_id": sector.sector_id,
            "name": sector.name,
            "type": sector.special_type.value if hasattr(sector, 'special_type') and sector.special_type is not None else sector.type.value,
            "cluster_id": str(sector.cluster_id),
            "x_coord": sector.x_coord,
            "y_coord": sector.y_coord,
            "z_coord": sector.z_coord,
            "hazard_level": sector.hazard_level,
            "is_discovered": sector.is_discovered,
            "is_navigable": True,  # Default to True, calculate from nav_hazards if needed
            "resources": sector.resources
        }
        
        if include_contents:
            # Check for port in this sector
            has_port = db.query(Station).filter(Station.sector_id == sector.sector_id).first() is not None
            # Check for planet in this sector
            has_planet = db.query(Planet).filter(Planet.sector_id == sector.sector_id).first() is not None
            # Check for warp tunnels from this sector
            has_warp_tunnel = db.query(WarpTunnel).filter(
                (WarpTunnel.origin_sector_id == sector.id) |
                (WarpTunnel.destination_sector_id == sector.id)
            ).first() is not None
            
            sector_data["has_port"] = has_port
            sector_data["has_planet"] = has_planet  
            sector_data["has_warp_tunnel"] = has_warp_tunnel
            
            # Add port info if exists
            if has_port:
                station = db.query(Station).filter(Station.sector_id == sector.sector_id).first()
                if station:
                    sector_data["port"] = {
                        "id": str(station.id),
                        "name": station.name,
                        "class": station.station_class.value,
                        "owner": "NPC" if not station.owner_id else str(station.owner_id)
                    }
            
            # Add planet info if exists
            if has_planet:
                planet = db.query(Planet).filter(Planet.sector_id == sector.sector_id).first()
                if planet:
                    sector_data["planet"] = {
                        "id": str(planet.id),
                        "name": planet.name,
                        "type": planet.type.value,
                        "owner": "Uncolonized" if not planet.owner_id else str(planet.owner_id)
                    }
            
            # Add warp tunnel info
            if has_warp_tunnel:
                warps = db.query(WarpTunnel).filter(
                    (WarpTunnel.origin_sector_id == sector.id) |
                    (WarpTunnel.destination_sector_id == sector.id)
                ).all()
                
                sector_data["warp_tunnels"] = [
                    {
                        "id": str(warp.id),
                        "to_sector": warp.destination_sector_id if warp.origin_sector_id == sector.id else warp.origin_sector_id,
                        "is_bidirectional": warp.is_bidirectional,
                        "stability": int(warp.stability * 100)
                    }
                    for warp in warps
                ]
        
        sector_list.append(sector_data)
    
    return {"sectors": sector_list}