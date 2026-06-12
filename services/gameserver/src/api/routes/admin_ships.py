"""
Admin ship management API endpoints.

Provides administrative controls for individual ship operations,
emergency interventions, and fleet health monitoring.
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import and_, or_, func
from pydantic import BaseModel, Field
from enum import Enum

from src.core.database import get_db
from src.auth.dependencies import get_current_user, require_admin
from src.models.user import User
from src.models.ship import Ship, ShipType, ShipStatus, ShipSpecification
from src.models.player import Player
from src.models.sector import Sector
from src.services.audit_service import AuditService, AuditAction

router = APIRouter(prefix="/admin/ships", tags=["admin", "ships"])


# Request/Response Models

class EmergencyAction(str, Enum):
    REPAIR = "repair"
    REFUEL = "refuel"
    TELEPORT = "teleport"


class EmergencyActionRequest(BaseModel):
    """Request for emergency ship action."""
    action: EmergencyAction
    target_sector_id: Optional[UUID] = Field(None, description="Required for teleport action")


class EmergencyActionResponse(BaseModel):
    """Response for emergency ship action."""
    success: bool
    ship_id: UUID
    action: str
    new_status: str
    message: str


class CreateShipRequest(BaseModel):
    """Request to create a new ship."""
    type: ShipType
    owner_id: UUID
    sector_id: UUID
    name: Optional[str] = None


class ShipListResponse(BaseModel):
    """Response for ship listing."""
    ships: List[Dict[str, Any]]
    total: int
    page: int
    total_pages: int


class HealthReportResponse(BaseModel):
    """Fleet health report response."""
    total_ships: int
    by_status: Dict[str, int]
    by_condition: Dict[str, int]
    maintenance_needed: List[Dict[str, Any]]
    critical_issues: List[Dict[str, Any]]


class DeleteShipResponse(BaseModel):
    """Response for ship deletion."""
    success: bool


# Admin Ship Management Endpoints

@router.get("", response_model=ShipListResponse)
async def get_ships(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    owner_id: Optional[UUID] = Query(None, alias="ownerId"),
    sector_id: Optional[UUID] = Query(None, alias="sectorId"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get all ships with optional filters and pagination."""
    
    # Build query - outer join with ShipSpecification to get cargo capacity
    # Use outerjoin in case ShipSpecification table is not populated
    query = db.query(Ship, ShipSpecification).outerjoin(
        ShipSpecification, Ship.type == ShipSpecification.type
    )

    # Apply filters
    if status:
        query = query.filter(Ship.status == status)
    if type:
        query = query.filter(Ship.type == type)
    if owner_id:
        query = query.filter(Ship.owner_id == owner_id)
    if sector_id:
        query = query.filter(Ship.sector_id == sector_id)

    # Get total count for pagination
    total = query.count()

    # Apply pagination
    offset = (page - 1) * limit
    results = query.offset(offset).limit(limit).all()

    # Calculate total pages
    total_pages = (total + limit - 1) // limit

    # Format ship data
    ship_list = []
    for ship, spec in results:
        # Get JSONB data
        combat = ship.combat or {}
        cargo = ship.cargo or {}
        maintenance = ship.maintenance or {}

        # Calculate hull/shield percentages
        hull = combat.get("hull", 0)
        max_hull = combat.get("max_hull", 1)
        hull_percent = (hull / max_hull * 100) if max_hull > 0 else 100

        shields = combat.get("shields", 0)
        max_shields = combat.get("max_shields", 1)
        shields_percent = (shields / max_shields * 100) if max_shields > 0 else 100

        # Calculate condition from maintenance or hull
        condition_percent = maintenance.get("condition", hull_percent)
        if condition_percent >= 90:
            condition = "excellent"
        elif condition_percent >= 70:
            condition = "good"
        elif condition_percent >= 50:
            condition = "fair"
        elif condition_percent >= 25:
            condition = "poor"
        else:
            condition = "critical"

        # Calculate cargo usage
        cargo_used = sum(cargo.values()) if cargo else 0
        # Get cargo capacity from spec if available, otherwise use default
        cargo_capacity = spec.max_cargo if spec else 10  # Default to 10 if no spec
        cargo_percent = (cargo_used / cargo_capacity * 100) if cargo_capacity > 0 else 0

        ship_data = {
            "id": str(ship.id),
            "name": ship.name,
            "type": ship.type,
            "status": ship.status,
            "condition": condition,
            "owner": {
                "id": str(ship.owner_id) if ship.owner_id else None,
                "name": ship.owner.user.username if ship.owner else "Unassigned"
            },
            "sector": {
                "id": str(ship.sector_id) if ship.sector_id else None,
                "name": ship.sector.name if ship.sector else "Deep Space",
                "coordinates": f"({ship.sector.x_coord}, {ship.sector.y_coord}, {ship.sector.z_coord})" if ship.sector else "Unknown"
            },
            "health": {
                "hull": hull,
                "max_hull": max_hull,
                "hull_percent": round(hull_percent, 1),
                "shields": shields,
                "max_shields": max_shields,
                "shields_percent": round(shields_percent, 1),
                "condition_percent": round(condition_percent, 1)
            },
            "cargo": {
                "used": cargo_used,
                "capacity": cargo_capacity,
                "capacity_percent": round(cargo_percent, 1),
                "contents": cargo
            },
            "created_at": ship.created_at.isoformat() if ship.created_at else None,
            "last_updated": ship.last_updated.isoformat() if ship.last_updated else None
        }
        ship_list.append(ship_data)
    
    return ShipListResponse(
        ships=ship_list,
        total=total,
        page=page,
        total_pages=total_pages
    )


@router.post("/{ship_id}/emergency", response_model=EmergencyActionResponse)
async def emergency_ship_action(
    ship_id: UUID,
    request: EmergencyActionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Perform emergency action on a ship."""
    
    # Get ship
    ship = db.query(Ship).filter(Ship.id == ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="Ship not found")
    
    old_status = ship.status
    old_sector = ship.sector_id
    message = ""
    
    if request.action == EmergencyAction.REPAIR:
        # Fully repair ship - restore combat stats and maintenance
        combat = ship.combat or {}
        combat["hull"] = combat.get("max_hull", 100)
        combat["shields"] = combat.get("max_shields", 100)
        ship.combat = combat
        # `combat = ship.combat or {}` returns the SAME dict reference when the
        # column is already populated; reassigning it to itself does not mark
        # the attribute dirty. flag_modified guarantees the JSONB UPDATE fires.
        flag_modified(ship, "combat")

        maintenance = ship.maintenance or {}
        maintenance["condition"] = 100.0
        maintenance["last_maintenance"] = datetime.utcnow().isoformat()
        maintenance["repair_needed"] = False
        ship.maintenance = maintenance
        flag_modified(ship, "maintenance")

        ship.status = ShipStatus.DOCKED.value
        ship.is_active = True
        ship.is_destroyed = False
        message = f"Ship {ship.name} fully repaired"

    elif request.action == EmergencyAction.REFUEL:
        # Refuel ship - restore condition and set to docked
        maintenance = ship.maintenance or {}
        maintenance["condition"] = 100.0
        ship.maintenance = maintenance
        flag_modified(ship, "maintenance")

        ship.status = ShipStatus.DOCKED.value
        ship.is_active = True
        message = f"Ship {ship.name} refueled"
        
    elif request.action == EmergencyAction.TELEPORT:
        if not request.target_sector_id:
            raise HTTPException(status_code=400, detail="target_sector_id required for teleport")
        
        # Verify target sector exists
        target_sector = db.query(Sector).filter(Sector.id == request.target_sector_id).first()
        if not target_sector:
            raise HTTPException(status_code=404, detail="Target sector not found")
        
        # Teleport ship
        ship.sector_id = request.target_sector_id
        ship.status = ShipStatus.IN_SPACE.value
        message = f"Ship {ship.name} teleported to {target_sector.name}"
    
    # Log the emergency action
    audit_service = AuditService(db)
    audit_service.log_action(
        user_id=admin.id,
        action=AuditAction.UPDATE,
        resource_type="ship",
        resource_id=str(ship_id),
        details={
            "emergency_action": request.action.value,
            "old_status": old_status,
            "new_status": ship.status,
            "old_sector": str(old_sector) if old_sector else None,
            "new_sector": str(ship.sector_id) if ship.sector_id else None,
            "target_sector": str(request.target_sector_id) if request.target_sector_id else None
        }
    )
    
    db.commit()
    
    return EmergencyActionResponse(
        success=True,
        ship_id=ship_id,
        action=request.action.value,
        new_status=ship.status,
        message=message
    )


@router.get("/health-report", response_model=HealthReportResponse)
async def get_fleet_health_report(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Get comprehensive fleet health report."""
    
    # Total ships
    total_ships = db.query(func.count(Ship.id)).scalar()
    
    # Ships by status
    status_counts = db.query(
        Ship.status,
        func.count(Ship.id)
    ).group_by(Ship.status).all()
    
    by_status = {status: count for status, count in status_counts}
    
    # Ships by condition (calculated from hull/maintenance percentage)
    ships = db.query(Ship).all()

    by_condition = {"excellent": 0, "good": 0, "fair": 0, "poor": 0, "critical": 0}
    maintenance_needed = []
    critical_issues = []

    for ship in ships:
        # Get JSONB data
        combat = ship.combat or {}
        maintenance = ship.maintenance or {}

        # Calculate hull percentage
        hull = combat.get("hull", 0)
        max_hull = combat.get("max_hull", 1)
        hull_percent = (hull / max_hull * 100) if max_hull > 0 else 100

        # Get condition from maintenance or use hull percentage
        condition_percent = maintenance.get("condition", hull_percent)

        # Determine condition category
        if condition_percent >= 90:
            condition = "excellent"
        elif condition_percent >= 70:
            condition = "good"
        elif condition_percent >= 50:
            condition = "fair"
        elif condition_percent >= 25:
            condition = "poor"
        else:
            condition = "critical"

        by_condition[condition] += 1

        # Ships needing maintenance (< 70% condition)
        if condition_percent < 70:
            ship_info = {
                "id": str(ship.id),
                "name": ship.name,
                "type": ship.type,
                "owner": ship.owner.user.username if ship.owner else "Unassigned",
                "sector": ship.sector.name if ship.sector else "Deep Space",
                "condition_percent": round(condition_percent, 1),
                "hull_percent": round(hull_percent, 1),
                "status": ship.status
            }
            maintenance_needed.append(ship_info)

        # Critical issues (< 25% condition or destroyed)
        if condition_percent < 25 or ship.is_destroyed or ship.status == ShipStatus.DESTROYED.value:
            critical_info = {
                "id": str(ship.id),
                "name": ship.name,
                "type": ship.type,
                "owner": ship.owner.user.username if ship.owner else "Unassigned",
                "sector": ship.sector.name if ship.sector else "Deep Space",
                "issue": "Destroyed" if ship.is_destroyed else "Critical damage",
                "condition_percent": round(condition_percent, 1),
                "hull_percent": round(hull_percent, 1),
                "status": ship.status
            }
            critical_issues.append(critical_info)

    # Sort lists by severity (lowest condition first)
    maintenance_needed.sort(key=lambda x: x["condition_percent"])
    critical_issues.sort(key=lambda x: x["condition_percent"])
    
    return HealthReportResponse(
        total_ships=total_ships,
        by_status=by_status,
        by_condition=by_condition,
        maintenance_needed=maintenance_needed,
        critical_issues=critical_issues
    )


@router.post("/create", response_model=Dict[str, Any])
async def create_ship(
    request: CreateShipRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Create a new ship administratively."""
    
    # Verify owner exists
    owner = db.query(Player).filter(Player.id == request.owner_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Owner (player) not found")
    
    # Verify sector exists
    sector = db.query(Sector).filter(Sector.id == request.sector_id).first()
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")
    
    # Create ship name if not provided
    ship_name = request.name
    if not ship_name:
        ship_count = db.query(func.count(Ship.id)).filter(
            Ship.owner_id == request.owner_id,
            Ship.type == request.type.value
        ).scalar()
        ship_name = f"{owner.user.username}'s {request.type.value.replace('_', ' ').title()} #{ship_count + 1}"

    # Get ship specification from database
    spec = db.query(ShipSpecification).filter(
        ShipSpecification.type == request.type
    ).first()

    if not spec:
        raise HTTPException(status_code=400, detail=f"No specification found for ship type {request.type.value}")

    # Create new ship with proper JSONB initialization
    new_ship = Ship(
        name=ship_name,
        type=request.type,
        owner_id=request.owner_id,
        sector_id=request.sector_id,
        base_speed=spec.speed,
        current_speed=spec.speed,
        turn_cost=spec.turn_cost,
        warp_capable=spec.warp_compatible,
        is_active=True,
        status=ShipStatus.DOCKED,

        # Initialize maintenance JSONB
        maintenance={
            "condition": 100.0,
            "last_maintenance": datetime.utcnow().isoformat(),
            "next_maintenance": None,
            "repair_needed": False
        },

        # Initialize empty cargo JSONB
        cargo={},

        # Initialize combat JSONB from specifications
        combat={
            "shields": spec.max_shields,
            "max_shields": spec.max_shields,
            "shield_recharge_rate": spec.shield_recharge_rate,
            "hull": spec.hull_points,
            "max_hull": spec.hull_points,
            "evasion": spec.evasion,
            "attack_rating": spec.attack_rating,
            "defense_rating": spec.defense_rating
        },

        # Genesis and equipment
        genesis_devices=0,
        max_genesis_devices=spec.max_genesis_devices,
        mines=0,
        max_mines=spec.max_drones,
        has_automated_maintenance=False,
        has_cloaking=False,

        # Initialize upgrades as empty array
        upgrades=[],

        # No insurance initially
        insurance=None,

        # Special flags
        is_destroyed=False,
        is_flagship=False,
        purchase_value=spec.base_cost,
        current_value=spec.base_cost
    )

    db.add(new_ship)
    db.flush()  # Get ID
    
    # Log creation
    audit_service = AuditService(db)
    audit_service.log_action(
        user_id=admin.id,
        action=AuditAction.CREATE,
        resource_type="ship",
        resource_id=str(new_ship.id),
        details={
            "name": ship_name,
            "type": request.type.value,
            "owner_id": str(request.owner_id),
            "owner_name": owner.user.username,
            "sector_id": str(request.sector_id),
            "sector_name": sector.name
        }
    )
    
    db.commit()
    
    # Return created ship
    return {
        "ship": {
            "id": str(new_ship.id),
            "name": new_ship.name,
            "type": new_ship.type.value,
            "status": new_ship.status.value,
            "owner": {
                "id": str(owner.id),
                "name": owner.user.username
            },
            "sector": {
                "id": str(sector.id),
                "name": sector.name
            },
            "specs": {
                "speed": spec.speed,
                "max_cargo": spec.max_cargo,
                "max_shields": spec.max_shields,
                "hull_points": spec.hull_points,
                "attack_rating": spec.attack_rating,
                "defense_rating": spec.defense_rating,
                "base_cost": spec.base_cost
            },
            "combat": new_ship.combat,
            "maintenance": new_ship.maintenance,
            "created_at": new_ship.created_at.isoformat()
        }
    }


@router.delete("/{ship_id}", response_model=DeleteShipResponse)
async def delete_ship(
    ship_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Delete a ship administratively."""
    
    # Get ship
    ship = db.query(Ship).filter(Ship.id == ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="Ship not found")
    
    # Store ship info for audit log
    ship_info = {
        "name": ship.name,
        "type": ship.type,
        "owner": ship.owner.user.username if ship.owner else "Unassigned",
        "sector": ship.sector.name if ship.sector else "Deep Space"
    }
    
    # Check if ship is in critical operations (battles, etc.)
    if ship.status == ShipStatus.IN_COMBAT.value:
        raise HTTPException(
            status_code=400, 
            detail="Cannot delete ship that is currently in combat"
        )
    
    # Log deletion before removing
    audit_service = AuditService(db)
    audit_service.log_action(
        user_id=admin.id,
        action=AuditAction.DELETE,
        resource_type="ship",
        resource_id=str(ship_id),
        details=ship_info
    )
    
    # Delete ship
    db.delete(ship)
    db.commit()
    
    return DeleteShipResponse(success=True)


# DEPRECATED: Ship specifications are now fetched from ShipSpecification database table
# This function used incorrect field names (armor instead of hull) and is no longer used
# def get_ship_specifications(ship_type: ShipType) -> Dict[str, int]:
#     """Get ship specifications based on type."""
#     # See ShipSpecification model for actual specifications