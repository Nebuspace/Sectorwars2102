from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.sector import Sector
from src.models.planet import Planet
from src.models.station import Station
from src.services.celestial_service import generate_system

router = APIRouter(
    prefix="/sectors",
    tags=["sectors"],
    responses={404: {"description": "Not found"}},
)

class PlanetResponse(BaseModel):
    id: str
    name: str
    type: str
    status: str
    sector_id: int
    owner_id: str | None = None
    resources: Dict[str, Any]
    population: int
    max_population: int
    habitability_score: float
    # Capital hubs are public worlds under regional administration — the
    # claim endpoint refuses them, so the client must not advertise a claim
    is_population_hub: bool = False

class StationResponse(BaseModel):
    id: str
    name: str
    station_class: int | None = None  # Station class 0-11 (trading classification)
    type: str
    status: str
    sector_id: int
    owner_id: str | None = None
    services: Dict[str, Any]
    faction_affiliation: str | None = None
    is_spacedock: bool = False
    tradedock_tier: str | None = None

class SectorPlanetsResponse(BaseModel):
    planets: List[PlanetResponse]

class SectorStationsResponse(BaseModel):
    stations: List[StationResponse]

@router.get("/{sector_id}/planets", response_model=SectorPlanetsResponse)
async def get_sector_planets(
    sector_id: int,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get all planets in a specific sector"""
    # Get player's current region (or None for regionless sectors)
    player_region_id = player.current_region_id

    # Verify sector exists in player's current region
    sector_query = db.query(Sector).filter(Sector.sector_id == sector_id)
    if player_region_id:
        sector_query = sector_query.filter(Sector.region_id == player_region_id)
    else:
        # For players without region, get sectors with no region
        sector_query = sector_query.filter(Sector.region_id == None)

    sector = sector_query.first()
    if not sector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sector {sector_id} not found in your region"
        )

    # Get all planets in this specific sector (by UUID)
    planets = db.query(Planet).filter(Planet.sector_uuid == sector.id).all()
    
    planet_responses = []
    for planet in planets:
        planet_responses.append(PlanetResponse(
            id=str(planet.id),
            name=planet.name,
            type=planet.type.value if hasattr(planet.type, 'value') else str(planet.type),
            status=planet.status.value if hasattr(planet.status, 'value') else str(planet.status),
            sector_id=planet.sector_id,
            owner_id=str(planet.owner_id) if planet.owner_id else None,
            resources=planet.resources or {},
            population=planet.population,
            max_population=planet.max_population,
            habitability_score=planet.habitability_score,
            is_population_hub=bool(planet.is_population_hub)
        ))
    
    return SectorPlanetsResponse(planets=planet_responses)

@router.get("/{sector_id}/stations", response_model=SectorStationsResponse)
async def get_sector_stations(
    sector_id: int,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get all stations in a specific sector"""
    # Get player's current region (or None for regionless sectors)
    player_region_id = player.current_region_id

    # Verify sector exists in player's current region
    sector_query = db.query(Sector).filter(Sector.sector_id == sector_id)
    if player_region_id:
        sector_query = sector_query.filter(Sector.region_id == player_region_id)
    else:
        # For players without region, get sectors with no region
        sector_query = sector_query.filter(Sector.region_id == None)

    sector = sector_query.first()
    if not sector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sector {sector_id} not found in your region"
        )

    # Get all stations in this specific sector (by UUID)
    stations = db.query(Station).filter(Station.sector_uuid == sector.id).all()

    station_responses = []
    for station in stations:
        station_responses.append(StationResponse(
            id=str(station.id),
            name=station.name,
            station_class=station.station_class.value if hasattr(station.station_class, 'value') else station.station_class,
            is_spacedock=bool(station.is_spacedock),
            tradedock_tier=station.tradedock_tier,
            type=station.type.value if hasattr(station.type, 'value') else str(station.type),
            status=station.status.value if hasattr(station.status, 'value') else str(station.status),
            sector_id=station.sector_id,
            owner_id=str(station.owner_id) if station.owner_id else None,
            services=station.services or {},
            faction_affiliation=station.faction_affiliation
        ))

    return SectorStationsResponse(stations=station_responses)

@router.get("/{sector_id}/system", response_model=Dict[str, Any])
async def get_sector_system(
    sector_id: int,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get the deterministic celestial system composition for a sector.

    Procedurally generated star(s)/nebula/belt/filler bodies (seeded from the
    sector id — identical sector always returns an identical response) with
    the sector's real Planet and Station rows merged onto stable orbits.
    """
    # Get player's current region (or None for regionless sectors)
    player_region_id = player.current_region_id

    # Verify sector exists in player's current region
    sector_query = db.query(Sector).filter(Sector.sector_id == sector_id)
    if player_region_id:
        sector_query = sector_query.filter(Sector.region_id == player_region_id)
    else:
        # For players without region, get sectors with no region
        sector_query = sector_query.filter(Sector.region_id == None)

    sector = sector_query.first()
    if not sector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sector {sector_id} not found in your region"
        )

    # Two simple queries: real planets + real stations in this sector (by UUID)
    planets = db.query(Planet).filter(Planet.sector_uuid == sector.id).all()
    stations = db.query(Station).filter(Station.sector_uuid == sector.id).all()

    return generate_system(sector, planets, stations)