from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.cargo_wreck import CargoWreck
from src.models.planet import Planet
from src.models.player import Player
from src.models.sector import Sector
from src.models.station import Station
from src.services import salvage_service
from src.services.celestial_service import generate_system

router = APIRouter(
    prefix="/sectors",
    tags=["sectors"],
    responses={404: {"description": "Not found"}},
)

# WO-CMB-SALVAGE-LOOP-1: cap the wreck-listing response so a heavily-fought
# sector can't return an unbounded page — newest wrecks are the ones a
# player actually cares about salvaging.
WRECK_LISTING_LIMIT = 100


class SalvageRequest(BaseModel):
    wreck_id: str
    # None = take as much as fits (pre-WO-CMB-SALVAGE-LOOP-1 default
    # behavior); a positive int requests a specific amount, further capped
    # by free cargo hold and available turns (salvage_service.salvage_wreck).
    quantity: Optional[int] = None


class SalvageResponse(BaseModel):
    salvaged: Dict[str, int]
    suspect_flagged: bool
    wreck_cleared: bool
    turns_spent: int


class WreckResponse(BaseModel):
    id: str
    original_owner_id: str | None = None
    original_owner_name: str | None = None
    destroyed_ship_type: str
    cause: str
    created_at: str
    age_seconds: float
    cargo: Dict[str, int]
    # ADR-0007 preview: would salvaging THIS wreck, right now, flag the
    # calling player Suspect? (No damage_type key here on purpose — that
    # column does not exist on CargoWreck; NO-CANON, parked with Max.)
    would_flag_suspect: bool

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
            # ADR-0073: show the discoverer's custom name (else auto/legacy) so
            # the PLANETARY panel + helm controls match the windshield label.
            name=planet.display_name,
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

    # ADR-0073: viewing a sector's system discovers its planets (first wins);
    # mark BEFORE generating so the merged bodies carry the fresh discoverer.
    from src.services.discovery_service import mark_feature_discovered, mark_planet_discovered
    for p in planets:
        mark_planet_discovered(db, p, player.id)

    result = generate_system(db, sector, planets, stations)

    # Discover the per-sector features present (kept separate from planets).
    for feat in ("belt", "debris", "nebula"):
        if result.get(feat):
            mark_feature_discovered(db, sector.id, feat, player.id)

    # can_rename: only the discoverer may rename a planet (claimed or not).
    pid = str(player.id)
    for b in (result.get("bodies") or []):
        if b.get("real"):
            b["can_rename"] = (b.get("discovered_by") == pid)

    db.commit()  # persist celestial skeleton + discovery marks
    return result


@router.get("/{sector_id}/wrecks", response_model=List[WreckResponse])
async def get_sector_wrecks(
    sector_id: int,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """List salvageable CargoWrecks in a sector (WO-CMB-SALVAGE-LOOP-1).

    ``sector_id`` is the cockpit-native NUMERIC sector number, resolved to
    the sector's UUID server-side — CargoWreck.sector_id is itself a UUID FK
    (sectors.id), not the numeric id, same "resolve then filter by UUID"
    shape as /planets and /stations above. An existing, wreck-free sector
    returns ``[]`` with 200; an unknown sector 404s. Newest
    ``WRECK_LISTING_LIMIT`` wrecks only.

    No ``damage_type`` field — CargoWreck carries no such column (Max-parked
    NO-CANON, see this WO's report). ``would_flag_suspect`` is a live preview
    of what salvaging THIS wreck right now would do to the CALLING player —
    it can flip from true to false while the page is open as the grace
    window elapses; the client should treat it as advisory, not a lock-in.
    """
    player_region_id = player.current_region_id
    sector_query = db.query(Sector).filter(Sector.sector_id == sector_id)
    if player_region_id:
        sector_query = sector_query.filter(Sector.region_id == player_region_id)
    else:
        sector_query = sector_query.filter(Sector.region_id == None)  # noqa: E711
    sector = sector_query.first()
    if not sector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sector {sector_id} not found in your region"
        )

    wrecks = (
        db.query(CargoWreck)
        .filter(CargoWreck.sector_id == sector.id)
        .options(joinedload(CargoWreck.original_owner).joinedload(Player.user))
        .order_by(CargoWreck.created_at.desc())
        .limit(WRECK_LISTING_LIMIT)
        .all()
    )

    now = datetime.now(timezone.utc)
    responses = []
    for wreck in wrecks:
        created = wreck.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_seconds = (now - created).total_seconds() if created is not None else 0.0

        in_grace, exempt = salvage_service.grace_status(wreck, player, now=now)

        responses.append(WreckResponse(
            id=str(wreck.id),
            original_owner_id=str(wreck.original_owner_id) if wreck.original_owner_id else None,
            original_owner_name=wreck.original_owner.username if wreck.original_owner else None,
            destroyed_ship_type=wreck.destroyed_ship_type.value,
            cause=wreck.cause.value,
            created_at=wreck.created_at.isoformat() if wreck.created_at else None,
            age_seconds=age_seconds,
            cargo=dict(wreck.cargo or {}),
            would_flag_suspect=bool(in_grace and not exempt),
        ))

    return responses


@router.post("/salvage", response_model=SalvageResponse)
async def salvage_wreck(
    request: SalvageRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Salvage a CargoWreck sitting in the player's CURRENT sector.

    Transfers as much of the wreck's cargo as fits the player's free hold,
    ``request.quantity`` if provided, and available turns (whichever cap is
    tightest — salvage_service.salvage_wreck); 1 turn per 100 units taken,
    rounded up. The wreck row is deleted once emptied. ADR-0007: salvaging
    another team's wreck inside its 1-hour grace window is allowed but flags
    the salvager Suspect (original owner / a current team-mate / the
    killing-blow pilot are exempt) — the time cost applies regardless.
    """
    return salvage_service.salvage_wreck(db, player, request.wreck_id, request.quantity)
