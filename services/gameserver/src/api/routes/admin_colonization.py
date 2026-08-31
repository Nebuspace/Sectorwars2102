"""
Admin Colonization API Routes
Handles colony production, genesis devices, and planetary management for admin UI
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
import logging

from src.core.database import get_db
from src.auth.admin_scopes import GALAXY_MANAGE, REGIONS_VIEW
from src.auth.dependencies import require_scope
from src.models.user import User
from src.models.player import Player
from src.models.planet import Planet, PlanetType, PlanetStatus
from src.models.genesis_device import GenesisDevice, PlanetFormation
from src.models.ship import Ship
from src.models.sector import Sector
from src.models.team import Team
from src.services.admin_action_attempt import admin_action_attempt
from src.services.planetary_service import storage_cap_for, STARVATION_WARNING_KEY

router = APIRouter()
logger = logging.getLogger(__name__)

# Pydantic Models for Responses

class ProductionData(BaseModel):
    timestamp: str
    fuel_ore: int
    organics: int
    equipment: int

class ProductionTrend(BaseModel):
    resource: str
    current: int
    average: int
    peak: int
    trend: str  # 'increasing', 'decreasing', 'stable'
    efficiency: float

class ProductionAlert(BaseModel):
    id: str
    type: str  # 'shortage', 'surplus', 'efficiency', 'maintenance'
    severity: str  # 'low', 'medium', 'high'
    resource: str
    colony: str
    message: str
    timestamp: str

class ProductionStats(BaseModel):
    totalProduction: Dict[str, int]
    topProducers: List[Dict[str, Any]]
    bottlenecks: List[Dict[str, Any]]

class GenesisDeviceInfo(BaseModel):
    id: str
    name: str
    status: str  # 'active', 'dormant', 'deployed', 'destroyed'
    ownerId: str
    ownerName: str
    teamId: Optional[str]
    teamName: Optional[str]
    location: Dict[str, Any]
    powerLevel: int
    integrity: int
    chargeTime: int
    deploymentHistory: List[Dict[str, Any]]
    createdAt: str
    lastActivity: str

class GenesisStats(BaseModel):
    totalDevices: int
    activeDevices: int
    deployedThisWeek: int
    successRate: float
    averagePowerLevel: float
    topUsers: List[Dict[str, Any]]

class GenesisAlert(BaseModel):
    id: str
    deviceId: str
    deviceName: str
    type: str  # 'security', 'malfunction', 'unauthorized', 'critical'
    message: str
    timestamp: str
    severity: str  # 'low', 'medium', 'high', 'critical'

class PlanetInfo(BaseModel):
    id: str
    name: str
    sectorId: str
    sectorName: str
    type: str
    size: str
    atmosphere: str
    temperature: float
    gravity: float
    resources: Dict[str, int]
    habitability: int
    population: int
    maxPopulation: int
    colonies: int
    infrastructure: Dict[str, int]
    ownership: Dict[str, Any]
    discovered: bool
    colonizable: bool
    hasGenesisDevice: bool

class PlanetStats(BaseModel):
    totalPlanets: int
    discoveredPlanets: int
    colonizedPlanets: int
    contestedPlanets: int
    totalPopulation: int
    averageHabitability: float
    resourceDistribution: Dict[str, int]

class TerraformingProject(BaseModel):
    id: str
    planetId: str
    planetName: str
    type: str  # terraforming level name from the real 5-level system (e.g. 'Basic Atmospheric')
    progress: float
    duration: int
    cost: Dict[str, int]
    impact: Dict[str, Any]

class PlanetTickResult(BaseModel):
    """Result of force-advancing one planet's commodity production."""
    planetId: str
    planetName: str
    changed: bool
    before: Dict[str, int]
    after: Dict[str, int]
    delta: Dict[str, int]
    lastProductionAt: Optional[str]

# Production Monitoring Endpoint

_COMMODITY_KEYS = ("fuel_ore", "organics", "equipment")


def _planet_events(planet: Planet) -> Dict[str, Any]:
    events = planet.active_events
    return events if isinstance(events, dict) else {}


def _stockpile_totals(planets: List[Planet]) -> Dict[str, int]:
    return {
        "fuel_ore": sum(p.fuel_ore or 0 for p in planets),
        "organics": sum(p.organics or 0 for p in planets),
        "equipment": sum(p.equipment or 0 for p in planets),
    }


def _alerts_from_planets(planets: List[Planet]) -> List[ProductionAlert]:
    alerts: List[ProductionAlert] = []
    for planet in planets:
        events = _planet_events(planet)
        overflow = events.get("overflow_warning")
        if isinstance(overflow, dict):
            resources = overflow.get("resources") or {}
            cap = overflow.get("cap")
            at = overflow.get("at") or datetime.now(timezone.utc).isoformat()
            for resource, wasted in resources.items():
                alerts.append(ProductionAlert(
                    id=f"{planet.id}-overflow-{resource}",
                    type="overflow",
                    severity="high",
                    resource=str(resource),
                    colony=planet.name,
                    message=(
                        f"Storage overflow at {planet.name}: {int(wasted):,} {resource} wasted"
                        + (f" (cap {int(cap):,})" if cap is not None else "")
                    ),
                    timestamp=at,
                ))
        starvation = events.get(STARVATION_WARNING_KEY)
        if isinstance(starvation, dict):
            deficit = starvation.get("food_deficit", 0)
            lost = starvation.get("colonists_lost", 0)
            at = starvation.get("at") or datetime.now(timezone.utc).isoformat()
            alerts.append(ProductionAlert(
                id=f"{planet.id}-starvation",
                type="starvation",
                severity="high",
                resource="organics",
                colony=planet.name,
                message=(
                    f"Food deficit at {planet.name}: {int(deficit):,} organics short, "
                    f"{int(lost):,} colonists lost"
                ),
                timestamp=at,
            ))
    alerts.sort(key=lambda a: a.timestamp, reverse=True)
    return alerts


@router.get("/colonization/production")
async def get_colony_production(
    timeRange: str = Query("day", pattern="^(hour|day|week|month)$"),
    resource: str = Query("all", pattern="^(all|fuel_ore|organics|equipment)$"),
    current_admin: User = Depends(require_scope(REGIONS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get colony production data for monitoring.

    Returns aggregate commodity stockpiles and per-planet tick warnings
    (overflow_warning / starvation_warning) stamped by the production tick.
  timeRange is accepted for UI compatibility but history is a current snapshot
    — no synthetic time series is fabricated.
    """
    try:
        now = datetime.now(timezone.utc)

        planets = db.query(Planet).filter(
            Planet.owner_id.isnot(None),
            Planet.colonized_at.isnot(None)
        ).all()

        totals = _stockpile_totals(planets)

        history = [ProductionData(
            timestamp=now.isoformat(),
            fuel_ore=totals["fuel_ore"],
            organics=totals["organics"],
            equipment=totals["equipment"],
        )]

        trends: List[ProductionTrend] = []
        for res in _COMMODITY_KEYS:
            if resource != "all" and resource != res:
                continue
            current_val = totals[res]
            capped_total = 0
            for planet in planets:
                cap = storage_cap_for(planet.citadel_level or 0)
                if cap > 0:
                    stock = getattr(planet, res, 0) or 0
                    capped_total += min(stock, cap)
            efficiency = (capped_total / current_val * 100) if current_val > 0 else 100.0
            trends.append(ProductionTrend(
                resource=res,
                current=current_val,
                average=current_val,
                peak=current_val,
                trend="stable",
                efficiency=round(efficiency, 1),
            ))

        alerts = _alerts_from_planets(planets)

        total_production = {
            "fuel_ore": totals["fuel_ore"],
            "organics": totals["organics"],
            "equipment": totals["equipment"],
        }

        top_producers: List[Dict[str, Any]] = []
        for res in _COMMODITY_KEYS:
            if resource != "all" and resource != res:
                continue
            ranked = sorted(
                planets,
                key=lambda p: getattr(p, res, 0) or 0,
                reverse=True,
            )
            for planet in ranked[:5]:
                amount = getattr(planet, res, 0) or 0
                if amount > 0:
                    top_producers.append({
                        "colonyId": str(planet.id),
                        "colonyName": planet.name,
                        "resource": res,
                        "amount": amount,
                    })

        bottlenecks: List[Dict[str, Any]] = []
        for planet in planets:
            events = _planet_events(planet)
            if events.get("overflow_warning"):
                bottlenecks.append({
                    "colonyId": str(planet.id),
                    "colonyName": planet.name,
                    "issue": "Storage overflow — production wasted",
                    "impact": 100,
                })
            elif events.get(STARVATION_WARNING_KEY):
                lost = (events.get(STARVATION_WARNING_KEY) or {}).get("colonists_lost", 0)
                bottlenecks.append({
                    "colonyId": str(planet.id),
                    "colonyName": planet.name,
                    "issue": "Food deficit — colonist starvation",
                    "impact": min(100, int(lost) if lost else 50),
                })

        stats = ProductionStats(
            totalProduction=total_production,
            topProducers=top_producers[:5],
            bottlenecks=bottlenecks[:5],
        )

        return {
            "history": [h.dict() for h in history],
            "trends": [t.dict() for t in trends],
            "alerts": [a.dict() for a in alerts],
            "stats": stats.dict(),
            "timeRange": timeRange,
        }

    except Exception as e:
        logger.error(f"Error in get_colony_production: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch production data: {str(e)}")

# Genesis Device Tracking Endpoint

@router.get("/colonization/genesis-devices")
async def get_genesis_devices(
    current_admin: User = Depends(require_scope(REGIONS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get genesis device tracking data"""
    try:
        # Get all genesis devices
        devices = db.query(GenesisDevice).all()
        
        # Build device info
        device_list = []
        for device in devices:
            # Get owner info
            owner = db.query(Player).join(User).filter(Player.id == device.owner_id).first()
            owner_name = owner.user.username if owner else "Unknown"
            
            # Get team info if player has team
            team_name = None
            team_id = None
            if owner and owner.team_id:
                team = db.query(Team).filter(Team.id == owner.team_id).first()
                if team:
                    team_name = team.name
                    team_id = str(team.id)
            
            # Determine location
            location = {
                'type': 'ship' if device.ship_id else 'space',
                'id': str(device.ship_id) if device.ship_id else str(device.sector_id),
                'name': 'Unknown',
                'sectorId': str(device.sector_id),
                'sectorName': 'Unknown'
            }
            
            if device.ship_id:
                ship = db.query(Ship).filter(Ship.id == device.ship_id).first()
                if ship:
                    location['name'] = ship.name
            
            if device.sector_id:
                sector = db.query(Sector).filter(Sector.id == device.sector_id).first()
                if sector:
                    location['sectorName'] = sector.name
            
            # Calculate status and metrics based on actual model
            status_map = {
                'INACTIVE': 'dormant',
                'DEPLOYING': 'active',
                'ACTIVE': 'active',
                'COMPLETED': 'deployed',
                'FAILED': 'destroyed',
                'UNSTABLE': 'active',
                'ABORTED': 'destroyed'
            }
            status = status_map.get(device.status.value, 'dormant')
            
            power_level = device.terraforming_power  # Use actual power from model
            integrity = int(device.stability * 100)  # Convert stability to percentage
            charge_time = 0 if device.status.value in ['ACTIVE', 'DEPLOYING'] else 86400  # 24 hours if not active
            
            # Get deployment history from formations
            deployment_history = []
            formations = db.query(PlanetFormation).filter(
                PlanetFormation.genesis_device_id == device.id
            ).order_by(PlanetFormation.created_at.desc()).limit(5).all()
            
            for formation in formations:
                result_planet = None
                if formation.resulting_planet_id:
                    result_planet = db.query(Planet).filter(Planet.id == formation.resulting_planet_id).first()
                
                deployment_history.append({
                    'timestamp': formation.started_at.isoformat() if formation.started_at else datetime.now(timezone.utc).isoformat(),
                    'targetPlanetId': str(formation.resulting_planet_id) if formation.resulting_planet_id else 'unknown',
                    'targetPlanetName': result_planet.name if result_planet else 'Unknown Planet',
                    'result': 'success' if formation.is_completed else 'failure' if formation.is_failed else 'partial',
                    'transformationType': device.type.value
                })
            
            device_list.append(GenesisDeviceInfo(
                id=str(device.id),
                name=device.name,
                status=status,
                ownerId=str(device.owner_id),
                ownerName=owner_name,
                teamId=team_id,
                teamName=team_name,
                location=location,
                powerLevel=max(0, power_level),
                integrity=max(0, integrity),
                chargeTime=charge_time,
                deploymentHistory=deployment_history,
                createdAt=device.created_at.isoformat() if device.created_at else datetime.now(timezone.utc).isoformat(),
                lastActivity=device.last_updated.isoformat() if device.last_updated else device.created_at.isoformat()
            ))
        
        # Calculate stats
        total_devices = len(devices)
        active_devices = sum(1 for d in devices if d.status.value in ['ACTIVE', 'DEPLOYING'])
        
        # Deployments this week
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        recent_formations = db.query(PlanetFormation).filter(
            PlanetFormation.started_at > week_ago
        ).count()
        
        # Success rate from all formations
        all_formations = db.query(PlanetFormation).all()
        total_deployments = len(all_formations)
        successful_deployments = sum(1 for f in all_formations if f.is_completed)
        success_rate = (successful_deployments / total_deployments * 100) if total_deployments > 0 else 0
        
        # Average power level
        avg_power = sum(d.terraforming_power for d in devices) / len(devices) if devices else 0
        
        # Top users
        player_devices = {}
        for device in devices:
            if device.owner_id:
                if device.owner_id not in player_devices:
                    player_devices[device.owner_id] = {
                        'count': 0,
                        'successful': 0,
                        'player': None
                    }
                player_devices[device.owner_id]['count'] += 1
        
        # Count successful deployments per player
        for formation in all_formations:
            device = db.query(GenesisDevice).filter(GenesisDevice.id == formation.genesis_device_id).first()
            if device and device.owner_id in player_devices and formation.is_completed:
                player_devices[device.owner_id]['successful'] += 1
        
        top_users = []
        for player_id, data in sorted(player_devices.items(), key=lambda x: x[1]['count'], reverse=True)[:5]:
            player = db.query(Player).join(User).filter(Player.id == player_id).first()
            if player:
                top_users.append({
                    'playerId': str(player_id),
                    'playerName': player.user.username,
                    'deviceCount': data['count'],
                    'successfulDeployments': data['successful']
                })
        
        stats = GenesisStats(
            totalDevices=total_devices,
            activeDevices=active_devices,
            deployedThisWeek=recent_formations,
            successRate=success_rate,
            averagePowerLevel=max(0, avg_power),
            topUsers=top_users
        )
        
        # Alerts: real trigger (low stability or non-active status), with
        # type/severity derived deterministically from the device's actual
        # state and timestamp from its real last_updated — no random.
        alerts = []
        for device in devices[:8]:
            if device.stability < 0.5 or device.status.value not in ['ACTIVE', 'DEPLOYING']:
                if device.stability < 0.5:
                    alert_type = 'malfunction'
                    if device.stability < 0.2:
                        severity = 'critical'
                    elif device.stability < 0.35:
                        severity = 'high'
                    else:
                        severity = 'medium'
                    message = f"Genesis device stability at {int(device.stability * 100)}%"
                else:
                    status_value = device.status.value
                    if status_value in ('FAILED', 'ABORTED'):
                        alert_type = 'critical'
                        severity = 'high'
                    elif status_value == 'UNSTABLE':
                        alert_type = 'malfunction'
                        severity = 'medium'
                    else:  # INACTIVE / COMPLETED — dormant, informational
                        alert_type = 'security'
                        severity = 'low'
                    message = f"Genesis device status: {status_value}"

                alert_time = device.last_updated or device.created_at
                alert = GenesisAlert(
                    id=f"alert-{device.id}",
                    deviceId=str(device.id),
                    deviceName=device.name,
                    type=alert_type,
                    message=message,
                    timestamp=alert_time.isoformat() if alert_time else datetime.now(timezone.utc).isoformat(),
                    severity=severity
                )
                alerts.append(alert)
        
        return {
            "devices": [d.dict() for d in device_list],
            "stats": stats.dict(),
            "alerts": [a.dict() for a in alerts]
        }

    except Exception as e:
        logger.error(f"Error in get_genesis_devices: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch genesis device data: {str(e)}")

# Planetary Management Endpoint

@router.get("/colonization/planets")
async def get_admin_colonization_planets(
    current_admin: User = Depends(require_scope(REGIONS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get planetary management data for admin"""
    try:
        # Get all planets
        planets = db.query(Planet).all()
        
        # Build planet info
        planet_list = []
        for planet in planets:
            # Get sector info - using sector_uuid if available, else use sector_id
            sector_name = "Unknown"
            if planet.sector_uuid:
                sector = db.query(Sector).filter(Sector.id == planet.sector_uuid).first()
                if sector:
                    sector_name = sector.name
            else:
                # Use sector_id as a fallback
                sector_name = f"Sector {planet.sector_id}"
            
            # Get owner info
            owner_name = None
            team_name = None
            team_id = None
            contested = False
            
            if planet.owner_id:
                owner = db.query(Player).join(User).filter(Player.id == planet.owner_id).first()
                if owner:
                    owner_name = owner.user.username
                    if owner.team_id:
                        team = db.query(Team).filter(Team.id == owner.team_id).first()
                        if team:
                            team_name = team.name
                            team_id = str(team.id)
            
            # Planet properties read 1:1 from real Planet columns —
            # no synthesized or random values. UI-friendly casing: the UI
            # colors known title-cased names ('Gas Giant', 'Terran', ...);
            # unknown names render uncolored, which is acceptable.
            planet_type = planet.type.value.replace('_', ' ').title()

            # planet.size is an Integer on a 1-10 scale; bucket it onto the
            # existing label set so the response shape is unchanged.
            if planet.size <= 3:
                size = 'Small'
            elif planet.size <= 6:
                size = 'Medium'
            elif planet.size <= 8:
                size = 'Large'
            else:
                size = 'Massive'

            atmosphere = planet.atmosphere or 'Unknown'

            # Resources from the real commodity columns, using the same
            # column mapping as the production history above
            # (fuel_ore -> energy, equipment -> minerals) plus water_coverage
            # (surface water %) and special_resources (count of unique
            # resources). planet.organics has no slot in this legacy key set.
            resources = {
                'energy': planet.fuel_ore or 0,
                'minerals': planet.equipment or 0,
                'water': int(planet.water_coverage or 0),
                'rareMaterials': len(planet.special_resources or [])
            }

            # Infrastructure - map from individual fields
            infra_data = {
                'spaceports': 1 if planet.colonized_at else 0,  # Assume 1 spaceport if colonized
                'defenses': planet.defense_level,
                'factories': planet.factory_level,
                'research': planet.research_level
            }

            # Discovered/colonizable derived from real ownership and status —
            # there is no 'discovered' column, so a planet counts as
            # discovered once it is owned or its status reflects activity.
            discovered = planet.owner_id is not None or planet.status in (
                PlanetStatus.COLONIZED,
                PlanetStatus.DEVELOPED,
                PlanetStatus.TERRAFORMING
            )
            colonizable = planet.type != PlanetType.GAS_GIANT and planet.owner_id is None
            has_genesis = planet.genesis_created

            planet_info = PlanetInfo(
                id=str(planet.id),
                name=planet.name,
                sectorId=str(planet.sector_uuid) if planet.sector_uuid else str(planet.sector_id),
                sectorName=sector_name,
                type=planet_type,
                size=size,
                atmosphere=atmosphere,
                temperature=planet.temperature,
                gravity=planet.gravity,
                resources=resources,
                habitability=planet.habitability_score,
                population=planet.population or 0,
                maxPopulation=planet.max_population,
                colonies=1 if planet.colonized_at else 0,  # Number of colonies on the planet
                infrastructure=infra_data,
                ownership={
                    'playerId': str(planet.owner_id) if planet.owner_id else None,
                    'playerName': owner_name,
                    'teamId': team_id,
                    'teamName': team_name,
                    'contested': contested
                },
                discovered=discovered,
                colonizable=colonizable,
                hasGenesisDevice=has_genesis
            )
            planet_list.append(planet_info)
        
        # Calculate stats
        discovered_planets = [p for p in planet_list if p.discovered]
        colonized_planets = [p for p in planet_list if p.population > 0]
        contested_planets = [p for p in planet_list if p.ownership['contested']]
        
        total_population = sum(p.population for p in colonized_planets)
        avg_habitability = sum(p.habitability for p in discovered_planets) / len(discovered_planets) if discovered_planets else 0
        
        resource_dist = {
            'energy': sum(p.resources['energy'] for p in discovered_planets),
            'minerals': sum(p.resources['minerals'] for p in discovered_planets),
            'water': sum(p.resources['water'] for p in discovered_planets),
            'rareMaterials': sum(p.resources['rareMaterials'] for p in discovered_planets)
        }
        
        stats = PlanetStats(
            totalPlanets=len(planets),
            discoveredPlanets=len(discovered_planets),
            colonizedPlanets=len(colonized_planets),
            contestedPlanets=len(contested_planets),
            totalPopulation=total_population,
            averageHabitability=avg_habitability,
            resourceDistribution=resource_dist
        )
        
        # Real terraforming projects: planets with terraforming_active set
        # by the 5-level terraforming system (TerraformingService). Level
        # metadata (name/costs/duration/boost) lives in the active_events
        # JSONB {type: "terraforming"} entry that service writes; progress
        # and target come straight from the Planet terraforming columns.
        terraforming_projects = []
        for planet in planets:
            if not planet.terraforming_active:
                continue
            meta = next(
                (e for e in (planet.active_events or [])
                 if isinstance(e, dict) and e.get("type") == "terraforming"),
                {}
            )
            project = TerraformingProject(
                id=f"terraform-{planet.id}",
                planetId=str(planet.id),
                planetName=planet.name,
                type=meta.get("level_name", f"Level {meta.get('level', '?')}"),
                progress=float(planet.terraforming_progress or 0.0),
                duration=int(meta.get("duration_hours", 0)),
                cost={
                    'credits': int(meta.get("credit_cost", 0)),
                    'organics': int(meta.get("organics_cost", 0)),
                    'equipment': int(meta.get("equipment_cost", 0))
                },
                impact={
                    'habitability': int(meta.get("habitability_boost", 0)),
                    'targetHabitability': planet.terraforming_target
                }
            )
            terraforming_projects.append(project)
        
        return {
            "planets": [p.dict() for p in planet_list],
            "stats": stats.dict(),
            "terraformingProjects": [t.dict() for t in terraforming_projects]
        }

    except Exception as e:
        logger.error(f"Error in get_admin_planets: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch planetary data: {str(e)}")


# Manual Production Tick Trigger
#
# Canon: SYSTEMS/planetary-production-tick.md "Inputs" lists
# `POST /api/v1/admin/planets/{id}/tick` as the manual admin trigger for the
# production tick. This router mounts at /admin, so the path below resolves to
# exactly that under the /api/v1 prefix. Mutates production/siege/terraform —
# GALAXY_MANAGE (REGIONS_VIEW is read-only).

@router.post("/planets/{planet_id}/tick", response_model=PlanetTickResult)
async def tick_planet_production(
    planet_id: str,
    current_admin: User = Depends(require_scope(GALAXY_MANAGE)),
    db: Session = Depends(get_db)
):
    """Force-advance one planet's commodity production and return the DB delta.

    Drives PlanetaryService.realize_production (the lazy advance-on-read accrual
    extracted as a player-read-independent entry point) on a single planet under
    a row lock, mirroring the scheduler's planetary-advance sweep. Idempotent:
    the accrual consumes only the elapsed time that produced whole units from the
    durable last_production anchor (with sub-unit progress banked in
    active_events['production_carry']), so calling it repeatedly accrues exactly
    elapsed × rate once and is a no-op once caught up — it never double-counts.
    """
    from uuid import UUID

    try:
        pid = UUID(planet_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid planet id")

    # Lock the row for the duration of the accrual so a concurrent scheduled
    # sweep or player read serializes behind us and sees our advanced anchor
    # (no double-credit). Same with_for_update discipline as the sweep.
    planet = (
        db.query(Planet)
        .filter(Planet.id == pid)
        .with_for_update()
        .first()
    )
    if planet is None:
        raise HTTPException(status_code=404, detail="Planet not found")

    def _research_points(p) -> int:
        ev = p.active_events
        return int(ev.get("research_points", 0) or 0) if isinstance(ev, dict) else 0

    before = {
        "fuel": planet.fuel_ore or 0,
        "organics": planet.organics or 0,
        "equipment": planet.equipment or 0,
        "research": _research_points(planet),
    }

    try:
        # CRT WO-K1a cutover: the admin /tick drives the full planetary tick via settle()
        # (production + siege + terraform + research faucet, each idempotent on its own anchor).
        from src.services.structures import settle
        with admin_action_attempt(
            db,
            actor=current_admin,
            scope_used=GALAXY_MANAGE,
            action="planet_tick",
            target_type="planet",
            target_id=str(planet_id),
            payload={},
        ) as attempt:
            changed = settle(planet, db=db).changed
            # Commit only when something changed; no-op releases the lock with no
            # audit row (matches prior log-then-rollback-on-noop behavior).
            if changed:
                attempt.succeed(payload={"changed": True})
                db.refresh(planet)
            else:
                db.rollback()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error ticking production for planet {planet_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to tick planet production: {str(e)}")

    after = {
        "fuel": planet.fuel_ore or 0,
        "organics": planet.organics or 0,
        "equipment": planet.equipment or 0,
        "research": _research_points(planet),
    }
    delta = {k: after[k] - before[k] for k in before}

    return PlanetTickResult(
        planetId=str(planet.id),
        planetName=planet.name,
        changed=changed,
        before=before,
        after=after,
        delta=delta,
        lastProductionAt=planet.last_production.isoformat() if planet.last_production else None,
    )