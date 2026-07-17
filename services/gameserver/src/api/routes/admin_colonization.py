"""
Admin Colonization API Routes
Handles colony production, genesis devices, and planetary management for admin UI
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, desc
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field
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

router = APIRouter()
logger = logging.getLogger(__name__)

# Pydantic Models for Responses

class ProductionData(BaseModel):
    timestamp: str
    energy: int
    minerals: int
    food: int
    water: int

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

@router.get("/colonization/production")
async def get_colony_production(
    timeRange: str = Query("day", pattern="^(hour|day|week|month)$"),
    resource: str = Query("all", pattern="^(all|energy|minerals|food|water)$"),
    current_admin: User = Depends(require_scope(REGIONS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get colony production data for monitoring"""
    try:
        # Calculate time filter
        now = datetime.now(timezone.utc)
        if timeRange == "hour":
            start_time = now - timedelta(hours=1)
            interval_minutes = 5
        elif timeRange == "day":
            start_time = now - timedelta(days=1)
            interval_minutes = 60
        elif timeRange == "week":
            start_time = now - timedelta(weeks=1)
            interval_minutes = 360
        else:  # month
            start_time = now - timedelta(days=30)
            interval_minutes = 1440

        # Get colonized planets with production data
        planets = db.query(Planet).filter(
            Planet.owner_id.isnot(None),
            Planet.colonized_at.isnot(None)
        ).all()

        # Generate production history data
        history = []
        current_time = start_time
        while current_time <= now:
            # Calculate production based on planet buildings and production settings
            # Base production on population and planet characteristics
            energy_prod = 0
            minerals_prod = 0
            food_prod = 0
            water_prod = 0
            
            for p in planets:
                # Base production on population (if any)
                pop_multiplier = min(p.population / 1000000, 10) if p.population else 1
                
                # Energy production
                energy_base = 100 * pop_multiplier
                if hasattr(p, 'production') and p.production:
                    energy_base += p.production.get('fuel', 0) * 50
                if hasattr(p, 'fuel_ore'):
                    energy_base += (p.fuel_ore or 0) * 10
                energy_prod += energy_base
                
                # Minerals production
                minerals_base = 80 * pop_multiplier
                if hasattr(p, 'mine_level') and p.mine_level:
                    minerals_base += p.mine_level * 50
                if hasattr(p, 'equipment'):
                    minerals_base += (p.equipment or 0) * 5
                minerals_prod += minerals_base
                
                # Food production
                food_base = 120 * pop_multiplier
                if hasattr(p, 'farm_level') and p.farm_level:
                    food_base += p.farm_level * 75
                if hasattr(p, 'organics'):
                    food_base += (p.organics or 0) * 8
                food_prod += food_base
                
                # Water production (based on water coverage and population)
                water_base = 60 * pop_multiplier
                if hasattr(p, 'water_coverage') and p.water_coverage:
                    water_base += p.water_coverage * 10
                water_prod += water_base
            
            # Derived deterministically from real planet fields above;
            # no random jitter — the series only changes when the data does.
            # int() because the pop_multiplier math yields floats and the
            # ProductionData fields are ints (pydantic v2 rejects floats).
            history.append(ProductionData(
                timestamp=current_time.isoformat(),
                energy=max(0, int(energy_prod)),
                minerals=max(0, int(minerals_prod)),
                food=max(0, int(food_prod)),
                water=max(0, int(water_prod))
            ))
            current_time += timedelta(minutes=interval_minutes)

        # Calculate trends
        trends = []
        resources = ['energy', 'minerals', 'food', 'water']
        for res in resources:
            if resource == 'all' or resource == res:
                values = [getattr(h, res) for h in history]
                current_val = values[-1] if values else 0
                avg_val = sum(values) / len(values) if values else 0
                peak_val = max(values) if values else 0
                
                # Determine trend
                if len(values) > 1:
                    recent_avg = sum(values[-5:]) / len(values[-5:])
                    older_avg = sum(values[:-5]) / (len(values) - 5) if len(values) > 5 else avg_val
                    if recent_avg > older_avg * 1.1:
                        trend = 'increasing'
                    elif recent_avg < older_avg * 0.9:
                        trend = 'decreasing'
                    else:
                        trend = 'stable'
                else:
                    trend = 'stable'
                
                efficiency = (current_val / peak_val * 100) if peak_val > 0 else 0
                
                trends.append(ProductionTrend(
                    resource=res,
                    current=current_val,
                    average=int(avg_val),
                    peak=peak_val,
                    trend=trend,
                    efficiency=efficiency
                ))

        # No real alert source exists: there is no production alert table,
        # threshold engine, or telemetry that detects shortages/surpluses/
        # maintenance issues. Return an empty list rather than randomly
        # fabricated alerts.
        alerts: List[ProductionAlert] = []

        # Calculate stats
        total_production = {
            'energy': sum(getattr(h, 'energy', 0) for h in history[-24:]),
            'minerals': sum(getattr(h, 'minerals', 0) for h in history[-24:]),
            'food': sum(getattr(h, 'food', 0) for h in history[-24:]),
            'water': sum(getattr(h, 'water', 0) for h in history[-24:])
        }

        # Get top producers
        top_producers = []
        for planet in sorted(planets, key=lambda p: p.population if p.population else 0, reverse=True)[:5]:
            for res in resources:
                if resource == 'all' or resource == res:
                    # Calculate production for this planet
                    pop_multiplier = min(planet.population / 1000000, 10) if planet.population else 1
                    
                    energy_val = 100 * pop_multiplier
                    if hasattr(planet, 'production') and planet.production:
                        energy_val += planet.production.get('fuel', 0) * 50
                    if hasattr(planet, 'fuel_ore'):
                        energy_val += (planet.fuel_ore or 0) * 10
                    
                    minerals_val = 80 * pop_multiplier
                    if hasattr(planet, 'mine_level') and planet.mine_level:
                        minerals_val += planet.mine_level * 50
                    if hasattr(planet, 'equipment'):
                        minerals_val += (planet.equipment or 0) * 5
                    
                    food_val = 120 * pop_multiplier
                    if hasattr(planet, 'farm_level') and planet.farm_level:
                        food_val += planet.farm_level * 75
                    if hasattr(planet, 'organics'):
                        food_val += (planet.organics or 0) * 8
                    
                    water_val = 60 * pop_multiplier
                    if hasattr(planet, 'water_coverage') and planet.water_coverage:
                        water_val += planet.water_coverage * 10
                    
                    production_map = {
                        'energy': int(energy_val),
                        'minerals': int(minerals_val),
                        'food': int(food_val),
                        'water': int(water_val)
                    }
                    
                    production = production_map.get(res, 0)
                    if production > 0:
                        top_producers.append({
                            'colonyId': str(planet.id),
                            'colonyName': planet.name,
                            'resource': res,
                            'amount': production
                        })

        # Identify bottlenecks
        bottlenecks = []
        for planet in planets[:5]:
            # Check minimum infrastructure levels
            min_level = min(planet.factory_level, planet.farm_level, planet.mine_level, planet.research_level)
            if min_level < 3:
                bottlenecks.append({
                    'colonyId': str(planet.id),
                    'colonyName': planet.name,
                    'issue': 'Insufficient infrastructure',
                    'impact': (3 - min_level) * 10
                })

        stats = ProductionStats(
            totalProduction=total_production,
            topProducers=top_producers[:5],
            bottlenecks=bottlenecks
        )

        return {
            "history": [h.dict() for h in history],
            "trends": [t.dict() for t in trends],
            "alerts": [a.dict() for a in alerts],
            "stats": stats.dict()
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
    from src.services.planetary_service import PlanetaryService

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