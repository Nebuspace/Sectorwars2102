"""
Admin Combat Overview API routes
"""

from typing import Optional, List
from uuid import UUID
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import require_admin
from src.models.user import User
from src.models.combat_log import CombatLog
from src.models.player import Player
from src.services.combat_analytics_service import CombatAnalyticsService


router = APIRouter(prefix="/admin/combat", tags=["admin-combat"])


# Request/Response models
class CombatInterventionRequest(BaseModel):
    intervention_type: str = Field(..., description="Type of intervention: stop_combat, adjust_damage, restore_shields, declare_winner")
    parameters: dict = Field(..., description="Intervention-specific parameters")


class CombatParticipant(BaseModel):
    id: str
    type: str
    name: str
    level: Optional[int] = None
    team_id: Optional[str] = None
    owner_id: Optional[str] = None


class CombatFeedItem(BaseModel):
    id: str
    combat_type: str
    status: str
    started_at: str
    ended_at: Optional[str]
    duration_seconds: float
    current_round: int
    sector: dict
    attacker: CombatParticipant
    defender: CombatParticipant
    combat_stats: dict
    victor_id: Optional[str]
    is_active: bool
    needs_intervention: bool


class CombatBalanceResponse(BaseModel):
    timeframe: str
    total_combats: int
    group_by: str
    analytics: dict
    balance_metrics: dict
    outliers: List[dict]
    recommendations: List[str]


class CombatDisputeResponse(BaseModel):
    id: str
    combat_id: Optional[str]
    type: str
    severity: str
    timestamp: str
    description: str
    participants: dict
    status: str
    recommended_action: str


class InterventionResponse(BaseModel):
    intervention_id: str
    combat_id: str
    type: str
    status: str
    timestamp: str
    result: dict
    message: str


class CombatStatsResponse(BaseModel):
    total_combats_today: int
    total_ships_destroyed: int
    total_credits_looted: int
    average_combat_duration: float
    most_active_combatant: str
    deadliest_ship_type: str


class CombatResolutionRequest(BaseModel):
    outcome: Optional[str] = Field(None, description="Override combat outcome")
    notes: Optional[str] = Field(None, description="Admin resolution notes")
    credits_adjustment: Optional[int] = Field(None, description="Adjusted credits looted")


@router.get("/live", response_model=List[CombatFeedItem])
async def get_live_combat_feed(
    limit: int = Query(50, ge=1, le=100, description="Number of combat entries to return"),
    combat_type: Optional[str] = Query(None, description="Filter by combat type"),
    sector_id: Optional[UUID] = Query(None, description="Filter by sector"),
    active_only: bool = Query(True, description="Show only active combats"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get live/recent combat activities.
    
    This endpoint provides:
    - Real-time combat monitoring
    - Combat status and progress
    - Participant information
    - Intervention indicators
    
    Combat types: player_vs_player, player_vs_ship, player_vs_planet, fleet_battle
    
    **Required permissions**: Admin access
    """
    try:
        analytics_service = CombatAnalyticsService(db)
        combat_feed = analytics_service.get_live_combat_feed(
            limit=limit,
            combat_type=combat_type,
            sector_id=sector_id,
            active_only=active_only
        )
        
        return [CombatFeedItem(**combat) for combat in combat_feed]
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve combat feed: {str(e)}"
        )


@router.post("/{combat_id}/intervene", response_model=InterventionResponse)
async def intervene_in_combat(
    combat_id: UUID,
    request: CombatInterventionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Perform admin intervention in ongoing combat.
    
    Available intervention types:
    
    1. **stop_combat**: Immediately end the combat
       - Parameters: reason (string)
    
    2. **adjust_damage**: Modify damage multipliers
       - Parameters: target (attacker/defender), damage_multiplier (float)
    
    3. **restore_shields**: Restore shields to participants
       - Parameters: target (attacker/defender/both), shield_percent (int)
    
    4. **declare_winner**: Manually declare a winner
       - Parameters: winner (attacker/defender)
    
    All interventions are logged in the audit trail.
    
    **Required permissions**: Admin access
    """
    try:
        analytics_service = CombatAnalyticsService(db)
        
        # Add admin ID to parameters for audit logging
        parameters = request.parameters.copy()
        parameters['admin_id'] = admin.id
        
        result = analytics_service.intervene_in_combat(
            combat_id=combat_id,
            intervention_type=request.intervention_type,
            parameters=parameters
        )
        
        return InterventionResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Combat intervention failed: {str(e)}"
        )


@router.get("/balance", response_model=CombatBalanceResponse)
async def get_combat_balance_analytics(
    timeframe: str = Query("7d", description="Timeframe: 1d, 7d, 30d"),
    group_by: str = Query("ship_type", description="Group by: ship_type, player_level, combat_type, overall"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get combat balance analytics and win rates.
    
    This endpoint analyzes:
    - Win rates by ship type, player level, or combat type
    - Combat duration and round statistics
    - Balance metrics and outliers
    - Recommendations for game balance adjustments
    
    The balance score (0-100) indicates how well-balanced combat is,
    with 100 being perfectly balanced.
    
    **Required permissions**: Admin access
    """
    try:
        analytics_service = CombatAnalyticsService(db)
        balance_data = analytics_service.get_combat_balance_analytics(
            timeframe=timeframe,
            group_by=group_by
        )
        
        return CombatBalanceResponse(**balance_data)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve balance analytics: {str(e)}"
        )


@router.get("/disputes", response_model=List[CombatDisputeResponse])
async def get_combat_disputes(
    status: Optional[str] = Query(None, description="Filter by status: pending_review, investigating, resolved"),
    limit: int = Query(50, ge=1, le=100, description="Number of disputes to return"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get combat-related disputes and suspicious activities.
    
    This endpoint identifies:
    - Extreme damage disparities
    - Suspicious combat patterns
    - Potential exploit usage
    - Combat farming/harassment
    
    Disputes are sorted by severity (critical, high, medium, low).
    
    **Required permissions**: Admin access
    """
    try:
        analytics_service = CombatAnalyticsService(db)
        disputes = analytics_service.get_combat_disputes(
            status=status,
            limit=limit
        )
        
        return [CombatDisputeResponse(**dispute) for dispute in disputes]
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve combat disputes: {str(e)}"
        )


# ---------------------------------------------------------------------------
# Endpoints ported from the retired legacy /admin/combat router so the
# still-working capabilities are not lost.
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=CombatStatsResponse)
async def get_combat_stats(
    time_filter: str = Query("24h", description="Time filter: 24h, 7d, 30d"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get aggregate combat statistics for the selected time window.

    **Required permissions**: Admin access
    """
    now = datetime.utcnow()
    time_filters = {
        "24h": now - timedelta(hours=24),
        "7d": now - timedelta(days=7),
        "30d": now - timedelta(days=30)
    }

    time_threshold = time_filters.get(time_filter, time_filters["24h"])

    # Get base query with time filter
    base_query = db.query(CombatLog).filter(CombatLog.timestamp >= time_threshold)

    # Total combats
    total_combats = base_query.count()

    # Ships destroyed (a decisive outcome means a ship was destroyed)
    ships_destroyed = base_query.filter(
        CombatLog.outcome.in_(["attacker_win", "defender_win"])
    ).count()

    # Total credits looted
    total_credits = base_query.with_entities(func.sum(CombatLog.credits_looted)).scalar() or 0

    # Average combat duration
    avg_duration = base_query.with_entities(func.avg(CombatLog.combat_duration)).scalar() or 0

    # Most active combatant (by participation count)
    most_active_result = db.query(
        Player.username,
        func.count().label('combat_count')
    ).join(
        CombatLog,
        (CombatLog.attacker_id == Player.id) | (CombatLog.defender_id == Player.id)
    ).filter(
        CombatLog.timestamp >= time_threshold
    ).group_by(Player.id, Player.username).order_by(desc('combat_count')).first()

    most_active_combatant = most_active_result.username if most_active_result else "None"

    # Deadliest ship type (by wins)
    deadliest_result = db.query(
        CombatLog.attacker_ship_type,
        func.count().label('wins')
    ).filter(
        and_(
            CombatLog.timestamp >= time_threshold,
            CombatLog.outcome == "attacker_win"
        )
    ).group_by(CombatLog.attacker_ship_type).order_by(desc('wins')).first()

    deadliest_ship_type = deadliest_result.attacker_ship_type if deadliest_result else "None"

    return CombatStatsResponse(
        total_combats_today=total_combats,
        total_ships_destroyed=ships_destroyed,
        total_credits_looted=int(total_credits),
        average_combat_duration=float(avg_duration),
        most_active_combatant=most_active_combatant,
        deadliest_ship_type=deadliest_ship_type
    )


@router.post("/{combat_id}/resolve")
async def resolve_combat_dispute(
    combat_id: UUID,
    resolution: CombatResolutionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Resolve a disputed combat result.

    **Required permissions**: Admin access
    """
    combat_log = db.query(CombatLog).filter(CombatLog.id == combat_id).first()
    if not combat_log:
        raise HTTPException(status_code=404, detail="Combat log not found")

    # Update combat log with admin resolution
    if resolution.outcome is not None:
        combat_log.outcome = resolution.outcome

    if resolution.notes is not None:
        combat_log.admin_notes = resolution.notes

    if resolution.credits_adjustment is not None:
        combat_log.credits_looted = resolution.credits_adjustment

    combat_log.admin_resolved = True
    combat_log.admin_resolved_at = datetime.utcnow()

    db.commit()

    return {"message": "Combat dispute resolved", "combat_id": str(combat_id)}


@router.get("/dashboard-summary")
async def get_combat_dashboard_summary(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get a comprehensive summary for the combat dashboard.
    
    Combines key metrics from all combat endpoints for a quick overview.
    
    **Required permissions**: Admin access
    """
    try:
        analytics_service = CombatAnalyticsService(db)
        
        # Get all data
        live_combats = analytics_service.get_live_combat_feed(limit=10, active_only=True)
        balance_data = analytics_service.get_combat_balance_analytics(timeframe="24h", group_by="overall")
        disputes = analytics_service.get_combat_disputes(limit=10)
        
        # Count active combats by type
        active_by_type = {}
        for combat in live_combats:
            if combat['is_active']:
                ctype = combat['combat_type']
                active_by_type[ctype] = active_by_type.get(ctype, 0) + 1
        
        # Count disputes by severity
        dispute_counts = {
            "critical": len([d for d in disputes if d.get('severity') == 'critical']),
            "high": len([d for d in disputes if d.get('severity') == 'high']),
            "medium": len([d for d in disputes if d.get('severity') == 'medium']),
            "low": len([d for d in disputes if d.get('severity') == 'low'])
        }
        
        return {
            "timestamp": live_combats[0]['started_at'] if live_combats else None,
            "active_combats": {
                "total": len([c for c in live_combats if c['is_active']]),
                "by_type": active_by_type,
                "needing_intervention": len([c for c in live_combats if c['needs_intervention']])
            },
            "balance_summary": {
                "score": balance_data['balance_metrics']['balance_score'],
                "total_combats_24h": balance_data['total_combats'],
                "outliers_count": len(balance_data['outliers']),
                "top_recommendation": balance_data['recommendations'][0] if balance_data['recommendations'] else None
            },
            "dispute_summary": {
                "total_disputes": len(disputes),
                "by_severity": dispute_counts,
                "critical_disputes": [d for d in disputes if d.get('severity') == 'critical'][:3]
            },
            "recent_combats": live_combats[:5]
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate dashboard summary: {str(e)}"
        )