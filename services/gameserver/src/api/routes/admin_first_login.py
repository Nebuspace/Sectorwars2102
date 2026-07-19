"""
Admin routes for First Login conversation management and debugging
"""

from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, desc
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.admin_scopes import PLAYERS_VIEW
from src.auth.dependencies import require_scope
from src.models.first_login import FirstLoginSession, DialogueExchange, DialogueOutcome
from src.models.player import Player
from src.models.user import User

router = APIRouter(
    prefix="/admin/first-login",
    tags=["admin", "first-login"],
    dependencies=[Depends(require_scope(PLAYERS_VIEW))]
)


# Response Models
class DialogueExchangeDetail(BaseModel):
    id: str
    sequence_number: int
    npc_prompt: str
    player_response: str
    timestamp: datetime
    topic: Optional[str]

    # Analysis metrics
    persuasiveness: Optional[float]
    confidence: Optional[float]
    consistency: Optional[float]
    believability: Optional[float]
    current_suspicion: Optional[float]
    detected_contradictions: Optional[List[str]]

    # AI metadata
    ai_provider: Optional[str]
    response_time_ms: Optional[int]
    estimated_cost_usd: Optional[float]
    tokens_used: Optional[int]

    class Config:
        from_attributes = True


class ConversationSummary(BaseModel):
    session_id: str
    player_username: str
    player_id: str
    started_at: datetime
    completed_at: Optional[datetime]
    ship_claimed: Optional[str]
    awarded_ship: Optional[str]
    outcome: Optional[str]
    final_persuasion_score: Optional[float]
    negotiation_skill: Optional[str]
    total_questions: int
    ai_providers_used: List[str]
    total_cost_usd: float

    class Config:
        from_attributes = True


class ConversationDetail(BaseModel):
    session: ConversationSummary
    exchanges: List[DialogueExchangeDetail]
    guard_personality: dict

    class Config:
        from_attributes = True


class ConversationStats(BaseModel):
    total_sessions: int
    completed_sessions: int
    success_rate: float
    average_questions: float
    total_cost_usd: float
    ai_provider_breakdown: dict
    outcome_breakdown: dict


# Endpoints
@router.get("/conversations", response_model=List[ConversationSummary])
async def list_conversations(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    outcome: Optional[str] = None,
    ai_provider: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    """
    List all first login conversations with filtering options
    """
    query = db.query(FirstLoginSession).options(
        joinedload(FirstLoginSession.player).joinedload(Player.user)
    )

    # Apply filters
    if outcome:
        query = query.filter(FirstLoginSession.outcome == outcome)

    if start_date:
        query = query.filter(FirstLoginSession.started_at >= start_date)

    if end_date:
        query = query.filter(FirstLoginSession.started_at <= end_date)

    # AI provider filter (requires joining dialogue exchanges)
    if ai_provider:
        query = query.join(DialogueExchange).filter(
            DialogueExchange.ai_provider == ai_provider
        )

    # Order by most recent first
    query = query.order_by(desc(FirstLoginSession.started_at))

    # Pagination
    sessions = query.offset(skip).limit(limit).all()

    # Build response
    result = []
    for session in sessions:
        # Get dialogue exchanges for this session
        exchanges = db.query(DialogueExchange).filter_by(
            session_id=session.id
        ).all()

        # Calculate aggregates
        ai_providers_used = list(set([ex.ai_provider for ex in exchanges if ex.ai_provider]))
        total_cost = sum([ex.estimated_cost_usd or 0.0 for ex in exchanges])

        result.append(ConversationSummary(
            session_id=str(session.id),
            player_username=session.player.user.username if session.player and session.player.user else "Unknown",
            player_id=str(session.player_id),
            started_at=session.started_at,
            completed_at=session.completed_at,
            ship_claimed=session.ship_claimed.name if session.ship_claimed else None,
            awarded_ship=session.awarded_ship.name if session.awarded_ship else None,
            outcome=session.outcome.name if session.outcome else None,
            final_persuasion_score=session.final_persuasion_score,
            negotiation_skill=session.negotiation_skill.name if session.negotiation_skill else None,
            total_questions=len([ex for ex in exchanges if ex.player_response]),
            ai_providers_used=ai_providers_used,
            total_cost_usd=total_cost
        ))

    return result


@router.get("/conversations/{session_id}", response_model=ConversationDetail)
async def get_conversation_detail(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Get complete conversation detail including all exchanges and analysis
    """
    session = db.query(FirstLoginSession).options(
        joinedload(FirstLoginSession.player).joinedload(Player.user)
    ).filter_by(id=session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get all exchanges
    exchanges = db.query(DialogueExchange).filter_by(
        session_id=session.id
    ).order_by(DialogueExchange.sequence_number).all()

    # Build summary
    ai_providers_used = list(set([ex.ai_provider for ex in exchanges if ex.ai_provider]))
    total_cost = sum([ex.estimated_cost_usd or 0.0 for ex in exchanges])

    summary = ConversationSummary(
        session_id=str(session.id),
        player_username=session.player.user.username if session.player and session.player.user else "Unknown",
        player_id=str(session.player_id),
        started_at=session.started_at,
        completed_at=session.completed_at,
        ship_claimed=session.ship_claimed.name if session.ship_claimed else None,
        awarded_ship=session.awarded_ship.name if session.awarded_ship else None,
        outcome=session.outcome.name if session.outcome else None,
        final_persuasion_score=session.final_persuasion_score,
        negotiation_skill=session.negotiation_skill.name if session.negotiation_skill else None,
        total_questions=len([ex for ex in exchanges if ex.player_response]),
        ai_providers_used=ai_providers_used,
        total_cost_usd=total_cost
    )

    # Build exchanges list
    exchange_details = [
        DialogueExchangeDetail(
            id=str(ex.id),
            sequence_number=ex.sequence_number,
            npc_prompt=ex.npc_prompt,
            player_response=ex.player_response,
            timestamp=ex.timestamp,
            topic=ex.topic,
            persuasiveness=ex.persuasiveness,
            confidence=ex.confidence,
            consistency=ex.consistency,
            believability=ex.believability,
            current_suspicion=ex.current_suspicion,
            detected_contradictions=ex.detected_contradictions,
            ai_provider=ex.ai_provider,
            response_time_ms=ex.response_time_ms,
            estimated_cost_usd=ex.estimated_cost_usd,
            tokens_used=ex.tokens_used
        )
        for ex in exchanges
    ]

    # Guard personality
    guard_personality = {
        "name": session.guard_name,
        "title": session.guard_title,
        "trait": session.guard_trait,
        "description": session.guard_description,
        "base_suspicion": session.guard_base_suspicion
    }

    return ConversationDetail(
        session=summary,
        exchanges=exchange_details,
        guard_personality=guard_personality
    )


@router.get("/stats", response_model=ConversationStats)
async def get_conversation_stats(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db)
):
    """
    Get aggregate statistics for first login conversations
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Total sessions
    total_sessions = db.query(func.count(FirstLoginSession.id)).filter(
        FirstLoginSession.started_at >= cutoff_date
    ).scalar()

    # Completed sessions
    completed_sessions = db.query(func.count(FirstLoginSession.id)).filter(
        FirstLoginSession.started_at >= cutoff_date,
        FirstLoginSession.completed_at.isnot(None)
    ).scalar()

    # Success rate (SUCCESS outcome)
    successful_sessions = db.query(func.count(FirstLoginSession.id)).filter(
        FirstLoginSession.started_at >= cutoff_date,
        FirstLoginSession.outcome == DialogueOutcome.SUCCESS
    ).scalar()

    success_rate = (successful_sessions / completed_sessions * 100) if completed_sessions > 0 else 0.0

    # Average questions per session
    avg_questions_result = db.query(
        func.avg(
            db.query(func.count(DialogueExchange.id)).filter(
                DialogueExchange.session_id == FirstLoginSession.id,
                DialogueExchange.player_response != ""
            ).scalar_subquery()
        )
    ).filter(
        FirstLoginSession.started_at >= cutoff_date
    ).scalar()

    average_questions = float(avg_questions_result) if avg_questions_result else 0.0

    # Total cost
    total_cost = db.query(func.sum(DialogueExchange.estimated_cost_usd)).join(
        FirstLoginSession
    ).filter(
        FirstLoginSession.started_at >= cutoff_date
    ).scalar() or 0.0

    # AI provider breakdown
    provider_counts = db.query(
        DialogueExchange.ai_provider,
        func.count(DialogueExchange.id)
    ).join(FirstLoginSession).filter(
        FirstLoginSession.started_at >= cutoff_date,
        DialogueExchange.ai_provider.isnot(None)
    ).group_by(DialogueExchange.ai_provider).all()

    ai_provider_breakdown = {provider: count for provider, count in provider_counts}

    # Outcome breakdown
    outcome_counts = db.query(
        FirstLoginSession.outcome,
        func.count(FirstLoginSession.id)
    ).filter(
        FirstLoginSession.started_at >= cutoff_date,
        FirstLoginSession.outcome.isnot(None)
    ).group_by(FirstLoginSession.outcome).all()

    outcome_breakdown = {outcome.name: count for outcome, count in outcome_counts if outcome}

    return ConversationStats(
        total_sessions=total_sessions or 0,
        completed_sessions=completed_sessions or 0,
        success_rate=round(success_rate, 2),
        average_questions=round(average_questions, 2),
        total_cost_usd=round(total_cost, 4),
        ai_provider_breakdown=ai_provider_breakdown,
        outcome_breakdown=outcome_breakdown
    )
