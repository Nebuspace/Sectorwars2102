from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.admin_scopes import PLAYERS_VIEW
from src.auth.dependencies import require_scope
from src.models.game_event import GameEvent, EventTemplate, EventEffect, EventParticipation, EventType, EventStatus
from src.models.user import User

router = APIRouter(prefix="/admin/events", tags=["events"])


class EventEffectResponse(BaseModel):
    type: str
    target: str
    modifier: float
    duration_hours: Optional[int]
    description: Optional[str]


class GameEventResponse(BaseModel):
    id: str
    title: str
    description: str
    event_type: str
    status: str
    start_time: datetime
    end_time: Optional[datetime]
    affected_regions: List[str]  # Regions affected (central-nexus, terran-space, player-owned regions)
    effects: List[EventEffectResponse]
    participation_count: int
    rewards_distributed: int
    created_by: str
    created_at: datetime


class EventStatsResponse(BaseModel):
    total_events: int
    active_events: int
    scheduled_events: int
    total_participants: int
    rewards_distributed: int


class EventTemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    event_type: str
    default_effects: List[EventEffectResponse]
    duration_hours: int


class CreateEventRequest(BaseModel):
    title: str
    description: str
    event_type: str
    start_time: datetime
    end_time: Optional[datetime]
    affected_regions: List[str]  # Regions affected (central-nexus, terran-space, player-owned regions)
    effects: List[Dict[str, Any]]


@router.get("/", response_model=Dict[str, Any])
async def get_events(
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
    status_filter: Optional[str] = Query(None),
    type_filter: Optional[str] = Query(None),
    search_term: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin = Depends(require_scope(PLAYERS_VIEW))
):
    """Get paginated events with filters"""
    
    query = db.query(GameEvent)
    
    # Apply filters
    if status_filter and status_filter != "all":
        try:
            query = query.filter(GameEvent.status == EventStatus(status_filter))
        except ValueError:
            pass  # Invalid status filter, ignore

    if type_filter and type_filter != "all":
        try:
            query = query.filter(GameEvent.event_type == EventType(type_filter))
        except ValueError:
            pass  # Invalid type filter, ignore
    
    if search_term:
        query = query.filter(
            or_(
                GameEvent.title.ilike(f"%{search_term}%"),
                GameEvent.description.ilike(f"%{search_term}%")
            )
        )
    
    # Get total count
    total_events = query.count()
    total_pages = (total_events + limit - 1) // limit
    
    # Get paginated results
    events = query.order_by(desc(GameEvent.created_at)).offset((page - 1) * limit).limit(limit).all()
    
    # Transform to response format
    event_responses = []
    for event in events:
        # Get effects
        effects = db.query(EventEffect).filter(EventEffect.event_id == event.id).all()
        effect_responses = [
            EventEffectResponse(
                type=effect.effect_type,
                target=effect.target,
                modifier=effect.modifier,
                duration_hours=effect.duration_hours,
                description=effect.description
            )
            for effect in effects
        ]
        
        # Get participation count
        participation_count = db.query(EventParticipation).filter(
            EventParticipation.event_id == event.id
        ).count()
        
        # Get creator name
        creator = db.query(User).filter(User.id == event.created_by).first()
        creator_name = creator.username if creator else "System"
        
        event_responses.append(GameEventResponse(
            id=str(event.id),
            title=event.title,
            description=event.description,
            event_type=event.event_type.value if isinstance(event.event_type, EventType) else event.event_type,
            status=event.status.value if isinstance(event.status, EventStatus) else event.status,
            start_time=event.start_time,
            end_time=event.end_time,
            affected_regions=event.affected_regions or [],
            effects=effect_responses,
            participation_count=participation_count,
            rewards_distributed=event.rewards_distributed or 0,
            created_by=creator_name,
            created_at=event.created_at
        ))
    
    return {
        "events": event_responses,
        "total_events": total_events,
        "total_pages": total_pages,
        "current_page": page
    }


@router.get("/stats", response_model=EventStatsResponse)
async def get_event_stats(
    db: Session = Depends(get_db),
    current_admin = Depends(require_scope(PLAYERS_VIEW))
):
    """Get event statistics"""
    
    # Get event counts by status
    total_events = db.query(GameEvent).count()
    active_events = db.query(GameEvent).filter(GameEvent.status == EventStatus.ACTIVE).count()
    scheduled_events = db.query(GameEvent).filter(GameEvent.status == EventStatus.SCHEDULED).count()
    
    # Get participation stats
    total_participants = db.query(EventParticipation).count()
    
    # Get total rewards distributed
    rewards_result = db.query(func.sum(GameEvent.rewards_distributed)).scalar()
    total_rewards = int(rewards_result) if rewards_result else 0
    
    return EventStatsResponse(
        total_events=total_events,
        active_events=active_events,
        scheduled_events=scheduled_events,
        total_participants=total_participants,
        rewards_distributed=total_rewards
    )


@router.get("/templates", response_model=List[EventTemplateResponse])
async def get_event_templates(
    db: Session = Depends(get_db),
    current_admin = Depends(require_scope(PLAYERS_VIEW))
):
    """Get available event templates"""
    
    templates = db.query(EventTemplate).filter(EventTemplate.is_active == True).all()
    
    template_responses = []
    for template in templates:
        # Parse default effects from JSON
        default_effects = []
        if template.default_effects:
            for effect_data in template.default_effects:
                default_effects.append(EventEffectResponse(
                    type=effect_data.get("type", ""),
                    target=effect_data.get("target", ""),
                    modifier=effect_data.get("modifier", 1.0),
                    duration_hours=effect_data.get("duration_hours"),
                    description=effect_data.get("description")
                ))
        
        template_responses.append(EventTemplateResponse(
            id=str(template.id),
            name=template.name,
            description=template.description,
            event_type=template.event_type.value if isinstance(template.event_type, EventType) else template.event_type,
            default_effects=default_effects,
            duration_hours=template.default_duration_hours or 24
        ))
    
    return template_responses


@router.post("/", response_model=GameEventResponse)
async def create_event(
    event_data: CreateEventRequest,
    db: Session = Depends(get_db),
    current_admin = Depends(require_scope(PLAYERS_VIEW))
):
    """Create a new game event"""
    
    # Create the event
    new_event = GameEvent(
        title=event_data.title,
        description=event_data.description,
        event_type=EventType(event_data.event_type),
        status=EventStatus.SCHEDULED,
        start_time=event_data.start_time,
        end_time=event_data.end_time,
        affected_regions=event_data.affected_regions,
        created_by=current_admin.id,
        created_at=datetime.utcnow()
    )
    
    db.add(new_event)
    db.flush()  # Get the ID
    
    # Create effects
    for effect_data in event_data.effects:
        effect = EventEffect(
            event_id=new_event.id,
            effect_type=effect_data["type"],
            target=effect_data["target"],
            modifier=effect_data["modifier"],
            duration_hours=effect_data.get("duration_hours"),
            description=effect_data.get("description")
        )
        db.add(effect)
    
    db.commit()
    
    # Return the created event
    effects = db.query(EventEffect).filter(EventEffect.event_id == new_event.id).all()
    effect_responses = [
        EventEffectResponse(
            type=effect.effect_type,
            target=effect.target,
            modifier=effect.modifier,
            duration_hours=effect.duration_hours,
            description=effect.description
        )
        for effect in effects
    ]
    
    return GameEventResponse(
        id=str(new_event.id),
        title=new_event.title,
        description=new_event.description,
        event_type=new_event.event_type.value,
        status=new_event.status.value,
        start_time=new_event.start_time,
        end_time=new_event.end_time,
        affected_regions=new_event.affected_regions or [],
        effects=effect_responses,
        participation_count=0,
        rewards_distributed=0,
        created_by=current_admin.username,
        created_at=new_event.created_at
    )


@router.put("/{event_id}", response_model=GameEventResponse)
async def update_event(
    event_id: str,
    event_data: CreateEventRequest,
    db: Session = Depends(get_db),
    current_admin = Depends(require_scope(PLAYERS_VIEW))
):
    """Update an existing event"""
    
    event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Update event fields
    event.title = event_data.title
    event.description = event_data.description
    event.event_type = EventType(event_data.event_type)
    event.start_time = event_data.start_time
    event.end_time = event_data.end_time
    event.affected_regions = event_data.affected_regions
    
    # Remove old effects and create new ones
    db.query(EventEffect).filter(EventEffect.event_id == event.id).delete()
    
    for effect_data in event_data.effects:
        effect = EventEffect(
            event_id=event.id,
            effect_type=effect_data["type"],
            target=effect_data["target"],
            modifier=effect_data["modifier"],
            duration_hours=effect_data.get("duration_hours"),
            description=effect_data.get("description")
        )
        db.add(effect)
    
    db.commit()
    
    # Return updated event (similar to create response)
    effects = db.query(EventEffect).filter(EventEffect.event_id == event.id).all()
    effect_responses = [
        EventEffectResponse(
            type=effect.effect_type,
            target=effect.target,
            modifier=effect.modifier,
            duration_hours=effect.duration_hours,
            description=effect.description
        )
        for effect in effects
    ]
    
    participation_count = db.query(EventParticipation).filter(
        EventParticipation.event_id == event.id
    ).count()
    
    creator = db.query(User).filter(User.id == event.created_by).first()
    creator_name = creator.username if creator else "System"
    
    return GameEventResponse(
        id=str(event.id),
        title=event.title,
        description=event.description,
        event_type=event.event_type.value,
        status=event.status.value,
        start_time=event.start_time,
        end_time=event.end_time,
        affected_regions=event.affected_regions or [],
        effects=effect_responses,
        participation_count=participation_count,
        rewards_distributed=event.rewards_distributed or 0,
        created_by=creator_name,
        created_at=event.created_at
    )


@router.post("/{event_id}/activate")
async def activate_event(
    event_id: str,
    db: Session = Depends(get_db),
    current_admin = Depends(require_scope(PLAYERS_VIEW))
):
    """Activate a scheduled event"""
    
    event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    if event.status != EventStatus.SCHEDULED:
        raise HTTPException(status_code=400, detail="Only scheduled events can be activated")
    
    event.status = EventStatus.ACTIVE
    event.actual_start_time = datetime.utcnow()
    
    # Activate all effects
    effects = db.query(EventEffect).filter(EventEffect.event_id == event.id).all()
    for effect in effects:
        effect.is_active = True
        effect.applied_at = datetime.utcnow()
    
    db.commit()
    
    return {"message": "Event activated successfully", "event_id": event_id}


@router.post("/{event_id}/deactivate")
async def deactivate_event(
    event_id: str,
    db: Session = Depends(get_db),
    current_admin = Depends(require_scope(PLAYERS_VIEW))
):
    """Deactivate an active event"""
    
    event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    if event.status not in (EventStatus.ACTIVE, EventStatus.SCHEDULED):
        raise HTTPException(status_code=400, detail="Only active or scheduled events can be deactivated")

    event.status = EventStatus.CANCELLED if event.status == EventStatus.SCHEDULED else EventStatus.COMPLETED
    event.actual_end_time = datetime.utcnow()
    
    # Deactivate all effects
    effects = db.query(EventEffect).filter(EventEffect.event_id == event.id).all()
    for effect in effects:
        effect.is_active = False
    
    db.commit()
    
    return {"message": "Event deactivated successfully", "event_id": event_id}


@router.delete("/{event_id}")
async def delete_event(
    event_id: str,
    db: Session = Depends(get_db),
    current_admin = Depends(require_scope(PLAYERS_VIEW))
):
    """Delete an event (only if not active)"""
    
    event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    if event.status == EventStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Cannot delete active events")
    
    # Delete associated effects and participations
    db.query(EventEffect).filter(EventEffect.event_id == event.id).delete()
    db.query(EventParticipation).filter(EventParticipation.event_id == event.id).delete()
    
    # Delete the event
    db.delete(event)
    db.commit()
    
    return {"message": "Event deleted successfully", "event_id": event_id}