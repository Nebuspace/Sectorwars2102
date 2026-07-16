from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

# WO-UI2-INTRASYSTEM-MODEL (REVISE): the unified /contents endpoint below
# must be genuinely READ-ONLY (orchestrator ruling) -- it assembles its union
# from the underlying READ services directly, NEVER the 4 fragments' own
# ROUTE functions (those pace progress mechanics: discovery marks, gate-
# harmonization ADVANCE). Only FormationResponse/SectorStructuresResponse
# (plain Pydantic models, no side effects) are imported from the sibling
# route modules; get_current_sector/get_sector_structures are NOT imported
# here anymore. Module-level so tests can monkeypatch
# ``sectors_routes.generate_system`` / ``sectors_routes.warp_gate_service``
# the same way this file's other routes are monkeypatched elsewhere in the
# suite (e.g. the frozen-datetime fixture). Neither player.py nor
# warp_gates.py imports anything from this module, so no circular-import risk.
from src.api.routes.player import FormationResponse
from src.api.routes.warp_gates import SectorStructuresResponse
from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.cargo_wreck import CargoWreck
from src.models.planet import Planet
from src.models.player import Player
from src.models.sector import Sector
from src.models.station import Station
from src.services import salvage_service, warp_gate_service
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


class SectorHazards(BaseModel):
    """Live environmental hazard reading for a sector (SectorResponse's
    hazard_level/radiation_level -- api/routes/player.py -- grouped under
    one key so the unified payload names it explicitly rather than leaving
    two loose top-level numbers)."""
    hazard_level: int
    radiation_level: float


class SectorContentsResponse(BaseModel):
    """WO-UI2-INTRASYSTEM-MODEL -- the single-call union of the 5 sources
    the player-client currently stitches together per sector: the static
    celestial system (GET /sectors/{id}/system), live ship presence +
    hazards + special-formations (GET /player/current-sector), salvageable
    wrecks (GET /sectors/{id}/wrecks), and warp-gate structures
    (GET /warp-gates/sector/{id}). The static-system fields below are named
    to match SystemSnapshot (player-client SolarSystemViewscreen.tsx)
    exactly, so a later FE pass can adopt this payload directly.

    The star/nebula/belt/debris/habitable_zone/bodies/stations fields are
    passed through from GET /sectors/{id}/system as loosely-typed
    Dict[str, Any] -- exactly as loose as that route's own
    ``response_model=Dict[str, Any]`` -- rather than reimplementing a
    stricter shape that isn't this endpoint's to own (celestial_service
    controls it; rigidifying it here would risk silent drift the next time
    that shape changes).
    """
    sector_id: int
    sector_type: str | None = None
    star: Dict[str, Any] | None = None
    extra_stars: List[Dict[str, Any]] | None = None
    nebula: Dict[str, Any] | None = None
    belt: Dict[str, Any] | None = None
    debris: Dict[str, Any] | None = None
    habitable_zone: Dict[str, Any] | None = None
    bodies: List[Dict[str, Any]] = []
    stations: List[Dict[str, Any]] = []
    # Live sector state (GET /player/current-sector passthrough).
    live_ships: List[Any] = []
    hazards: SectorHazards
    formations: List[FormationResponse] = []
    # Salvage (GET /sectors/{id}/wrecks passthrough).
    wrecks: List[WreckResponse] = []
    # Gate structures (GET /warp-gates/sector/{id} passthrough).
    warp_gates: SectorStructuresResponse

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
            # Same hub detection land/claim/pioneer use — a missed DB flag
            # (e.g. New Earth re-imported without is_population_hub) must not
            # strand the capital welcome world as an ownership-gated colony.
            is_population_hub=bool(
                planet.is_population_hub or (planet.population or 0) >= 1_000_000
            ),
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


def _enrich_players_present(db: Session, present: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure read: enriches NPC presence entries with LIVE activity/mission/
    archetype (NPCCharacter query only, no write) -- lifted out of
    get_sector_contents's body verbatim (same logic get_current_sector,
    api/routes/player.py, applies inline) purely to keep the route's own
    cyclomatic complexity readable; not a behavior change."""
    npc_ids = [e.get("player_id") for e in present
               if isinstance(e, dict) and e.get("is_npc") and e.get("player_id")]
    if not npc_ids:
        return present
    from src.models.npc_character import NPCCharacter
    npcs = db.query(NPCCharacter).filter(NPCCharacter.id.in_(npc_ids)).all()
    by_id = {str(n.id): n for n in npcs}
    enriched = []
    for e in present:
        if isinstance(e, dict) and e.get("is_npc"):
            n = by_id.get(str(e.get("player_id")))
            if n is not None:
                e = dict(e)
                act = n.current_activity
                e["activity"] = (act.name if hasattr(act, "name") else str(act)) if act else None
                e["mission"] = (n.daily_schedule or {}).get("mission") or "commerce"
                e["archetype"] = n.archetype.name if n.archetype else None
                if n.intrasystem_pose is not None:
                    from src.services import intrasystem_movement_service as isp
                    e["pose"] = isp.pose_public(n.intrasystem_pose)
        enriched.append(e)
    return enriched


@router.get("/{sector_id}/contents", response_model=SectorContentsResponse)
async def get_sector_contents(
    sector_id: int,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """WO-UI2-INTRASYSTEM-MODEL (REVISE) -- the single unified, GENUINELY
    READ-ONLY GET consolidating the 4 sector-contents sources the
    player-client stitches together (SolarSystemViewscreen's SystemSnapshot
    fetch, GameDashboard's players_present/special_formations/hazard props,
    sectorWrecks, GatewrightPanel's warp-gate-structures fetch) into one
    call, additively -- none of the 4 backing ROUTES are modified or called.

    Orchestrator ruling: a display fetch must never pace a progress
    mechanic. This endpoint assembles its union from the underlying READ
    queries/services directly, deliberately bypassing every write those 4
    routes normally perform as a side effect of being viewed:
      - planet/feature discovery marks + the persisted-skeleton
        first-visit INSERT (get_sector_system's celestial_service calls) --
        replaced by generate_system(..., read_only=True), which sources the
        skeleton via get_celestial_read_only (pure, deterministic
        in-memory fallback -- see that function's docstring) instead of
        get_or_create_celestial, and simply never calls
        mark_planet_discovered/mark_feature_discovered at all.
      - per-player formation discovery-flip (get_current_sector's
        flip_formation_discovery + commit) -- replaced by reading
        find_formations_for_sector + is_formation_known_to_player +
        is_formation_investigated directly, exactly mirroring how
        MoveOption.special_formations already discloses formations on
        adjacent sectors WITHOUT discovering them (player.py's own
        AvailableMovesResponse docstring: "viewing the move list does NOT
        discover anything... an undiscovered formation here is withheld to
        a generic, identity-less anomaly").
      - gate-harmonization ADVANCE + beacon-expiry write
        (get_sector_structures' list_sector_structures) -- replaced by
        warp_gate_service.list_sector_structures(..., read_only=True),
        which skips advance_gates_touching_sector entirely and applies a
        pure, non-writing expiry PREVIEW instead of flipping beacon status.
      - get_sector_wrecks is called AS-IS: confirmed already 100%
        read-only (Sector + CargoWreck queries, salvage_service.grace_status
        is itself a pure preview) -- no write to strip.

    Net effect: a HARMONIZING gate whose timer already elapsed, or a
    DEPLOYED beacon past its window, keeps showing its current persisted
    state via THIS endpoint until a normal write-capable visit to the real
    routes settles it -- the accepted tradeoff of "genuinely read-only".

    Restricted to the player's OWN current sector (403 otherwise, RULED
    KEEP): this is NOT a generic "peek at any sector" fetch -- live ship
    presence/composition is disclosed only for the sector the player is
    standing in, mirroring GET /player/current-sector's own fog-of-war
    boundary. See monk's threat-rollup-static-only-security-precedent /
    fog-of-war-bounds-endpoint-consolidation memory.
    """
    if sector_id != player.current_sector_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sector contents are only available for your current sector.",
        )

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
            detail=f"Sector {sector_id} not found in your region",
        )

    # --- Static celestial system: read-only skeleton, zero discovery marks. ---
    planets = db.query(Planet).filter(Planet.sector_uuid == sector.id).all()
    stations = db.query(Station).filter(Station.sector_uuid == sector.id).all()
    system = generate_system(db, sector, planets, stations, read_only=True)
    # can_rename mirrors get_sector_system's own post-processing (a pure
    # comparison, no write) so bodies carry the same field the FE expects.
    pid = str(player.id)
    for b in (system.get("bodies") or []):
        if b.get("real"):
            b["can_rename"] = (b.get("discovered_by") == pid)

    # --- Live ships: sector.players_present + NPC enrichment (already
    #     100% read-only in the source route -- NPCCharacter query only). ---
    present = _enrich_players_present(db, sector.players_present or [])

    # --- Formations: read without discovering (MoveOption precedent). ---
    from src.services.special_formation_service import (
        find_formations_for_sector,
        is_formation_investigated,
        is_formation_known_to_player,
    )
    formation_responses = []
    for f in find_formations_for_sector(db, sector):
        discovered = is_formation_known_to_player(db, player.id, f.id)
        formation_responses.append(FormationResponse(
            id=str(f.id),
            is_discovered=discovered,
            is_anchor=(f.anchor_sector_id == sector.id),
            name=f.name if discovered else None,
            type=(f.type.value if hasattr(f.type, 'value') else str(f.type)) if discovered else None,
            is_investigated=is_formation_investigated(f) if discovered else False,
        ))

    # --- Wrecks: get_sector_wrecks is already read-only -- called as-is. ---
    wrecks = await get_sector_wrecks(sector_id=sector_id, player=player, db=db)

    # --- Warp-gate structures: read-only (no ADVANCE, no expiry write). ---
    gates = warp_gate_service.list_sector_structures(db, sector_id, read_only=True)

    return SectorContentsResponse(
        sector_id=system.get("sector_id", sector_id),
        sector_type=system.get("sector_type"),
        star=system.get("star"),
        extra_stars=system.get("extra_stars"),
        nebula=system.get("nebula"),
        belt=system.get("belt"),
        debris=system.get("debris"),
        habitable_zone=system.get("habitable_zone"),
        bodies=system.get("bodies", []),
        stations=system.get("stations", []),
        live_ships=present,
        hazards=SectorHazards(
            hazard_level=sector.hazard_level,
            radiation_level=sector.radiation_level,
        ),
        formations=formation_responses,
        wrecks=wrecks,
        warp_gates=SectorStructuresResponse(**gates),
    )
