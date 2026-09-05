"""
Enhanced AI API Routes - OWASP Security-First Design
Comprehensive cross-system AI intelligence endpoints extending ARIA foundation

Security Features:
- Input validation and sanitization (OWASP A03)
- Authentication and authorization checks (OWASP A01)
- Rate limiting and quota enforcement (OWASP A04)
- XSS prevention in all outputs (OWASP A03)
- SQL injection prevention via SQLAlchemy ORM (OWASP A03)
- Comprehensive audit logging (OWASP A09)
- Error handling without information disclosure (OWASP A09)
"""

import logging
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Body, status
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from pydantic import BaseModel, Field, validator

from src.core.database import get_async_session
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.station import Station
from src.services.enhanced_ai_service import (
    EnhancedAIService, AISystemType
)
from src.services.ai_security_service import AISecurityService, get_security_service
from src.services.aria_data_index_service import ARIADataIndexService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai", tags=["Enhanced AI"])
security = HTTPBearer()

# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class AISystemTypeRequest(BaseModel):
    """Request model for AI system type selection"""
    system_types: List[str] = Field(
        default=["trading"], 
        description="AI system types to include",
        example=["trading", "combat", "colony"]
    )
    max_recommendations: int = Field(
        default=5, 
        ge=1, 
        le=10, 
        description="Maximum number of recommendations"
    )
    
    @validator('system_types')
    def validate_system_types(cls, v):
        """Validate system types against allowed values"""
        valid_types = {t.value for t in AISystemType}
        for sys_type in v:
            if sys_type not in valid_types:
                raise ValueError(f"Invalid system type: {sys_type}")
        return v


class ConversationRequest(BaseModel):
    """Request model for AI conversation"""
    message: str = Field(
        ..., 
        min_length=1, 
        max_length=4000, 
        description="User message to AI"
    )
    conversation_id: Optional[str] = Field(
        None, 
        description="Existing conversation ID to continue"
    )
    conversation_type: str = Field(
        default="query", 
        description="Type of conversation"
    )
    
    @validator('message')
    def sanitize_message(cls, v):
        """Basic sanitization - full sanitization happens in service layer"""
        if not v or not v.strip():
            raise ValueError("Message cannot be empty")
        return v.strip()
    
    @validator('conversation_type')
    def validate_conversation_type(cls, v):
        """Validate conversation type"""
        valid_types = {"query", "command", "feedback", "learning", "strategic"}
        if v not in valid_types:
            raise ValueError(f"Invalid conversation type: {v}")
        return v


class TradeCascadeRequest(BaseModel):
    """Request model for ARIA multi-hop trade cascade planning."""
    start_sector_id: str = Field(
        ...,
        min_length=1,
        description="Sector UUID to start the cascade from (must be explored)",
    )
    target_profit: float = Field(
        ...,
        gt=0,
        description="Minimum total profit the cascade should aim for",
    )
    max_jumps: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum hops through explored sectors",
    )


class ExplorationSuggestionOut(BaseModel):
    kind: str
    sector_id: str
    sector_number: Optional[int] = None
    sector_name: Optional[str] = None
    visit_count: Optional[int] = None
    trade_opportunity_score: Optional[float] = None
    safety_rating: Optional[float] = None
    summary: str


class ExplorationSuggestionsOut(BaseModel):
    suggestions: List[ExplorationSuggestionOut]
    empty_message: Optional[str] = None


class CombatAdviceOut(BaseModel):
    has_history: bool
    opponent_ship_type: str
    summary: str
    weapon_suggestion: Optional[str] = None
    encounters: int = 0
    wins: int = 0
    losses: int = 0


class AssistantConfigRequest(BaseModel):
    """Request model for AI assistant configuration"""
    assistant_name: Optional[str] = Field(
        None, 
        min_length=1, 
        max_length=50, 
        description="Assistant name"
    )
    personality_type: Optional[str] = Field(
        None, 
        description="Assistant personality"
    )
    access_permissions: Optional[Dict[str, bool]] = Field(
        None, 
        description="System access permissions"
    )
    
    @validator('personality_type')
    def validate_personality(cls, v):
        """Validate personality type"""
        if v is None:
            return v
        valid_personalities = {"analytical", "friendly", "tactical", "cautious", "adaptive"}
        if v not in valid_personalities:
            raise ValueError(f"Invalid personality type: {v}")
        return v


class RecommendationResponse(BaseModel):
    """Response model for AI recommendations"""
    id: str
    category: str
    recommendation_type: str
    title: str
    summary: str
    priority: int
    risk_assessment: str
    confidence: float
    expected_outcome: Dict[str, Any]
    expires_at: str
    security_clearance_required: str
    
    class Config:
        schema_extra = {
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "category": "trading",
                "recommendation_type": "buy_opportunity",
                "title": "Trading Opportunity: Buy Organics",
                "summary": "Strong profit potential in Sector 15 organics market",
                "priority": 4,
                "risk_assessment": "low",
                "confidence": 0.85,
                "expected_outcome": {"type": "profit", "value": 15000, "currency": "credits"},
                "expires_at": "2025-06-08T10:30:00Z",
                "security_clearance_required": "standard"
            }
        }


class ConversationResponse(BaseModel):
    """Response model for AI conversation"""
    response: str
    conversation_id: str
    response_time: str
    intent: Optional[Dict[str, Any]] = None
    # WO-ARIA-COST-CAPS: additive. `degraded` marks a cost-cap fallback
    # (never a hard error, per dispatch); `scope` distinguishes the
    # instance-wide circuit-breaker/provider-chain-failure case from a
    # personal per-player cap-hit (ADR-0092 §4's "quantum storm" vs
    # "attunement fatigue" split -- narration copy is a later WO's job,
    # this carries only the machine-readable flag). Both None/False on a
    # normal response.
    degraded: bool = False
    scope: Optional[str] = None
    # WO-ARIA-CHAT-LLM: which engine answered this turn -- "llm" |
    # "template" | None. None on every response while ARIA_LLM_CHAT_ENABLED
    # is off (the pinned flag-off contract) or on any error-path response
    # built before EnhancedAIService ever ran.
    mode: Optional[str] = None
    # human's GO amendment on WO-ARIA-CHAT-LLM: a Resonance-ledger accounting
    # SEAM -- a documented hook point only. The ledger itself is a future
    # post-ADR-0092 WO; this field is deliberately always None today.
    ledger_entry: Optional[Any] = None

    class Config:
        schema_extra = {
            "example": {
                "response": "Based on current market analysis, I recommend focusing on organics trading in the outer rim sectors. The profit margins are excellent with low risk.",
                "conversation_id": "123e4567-e89b-12d3-a456-426614174000",
                "response_time": "2025-06-07T15:30:00Z",
                "intent": {"primary_intent": "trading", "confidence": 0.9}
            }
        }


class AssistantStatusResponse(BaseModel):
    """Response model for AI assistant status"""
    assistant_id: str
    assistant_name: str
    security_level: str
    api_usage: Dict[str, int]
    total_interactions: int
    last_active: str
    access_permissions: Dict[str, bool]
    
    class Config:
        schema_extra = {
            "example": {
                "assistant_id": "123e4567-e89b-12d3-a456-426614174000",
                "assistant_name": "ARIA",
                "security_level": "standard",
                "api_usage": {"quota": 1000, "used": 247, "remaining": 753},
                "total_interactions": 1542,
                "last_active": "2025-06-07T15:30:00Z",
                "access_permissions": {"trading": True, "combat": False, "colony": False, "station": True}
            }
        }


class ARIADataStreamOut(BaseModel):
    """WO-P6-aria-data-index-registry: one row of the ARIA data-index
    catalog, as exposed to players via the memory-journal transparency
    browser (DATA_MODELS/aria-data-index.md rule 3)."""
    key: str
    domain: str
    display_name: str
    retention_class: str
    transparency_visible: bool

    class Config:
        from_attributes = True
        schema_extra = {
            "example": {
                "key": "threat.combat",
                "domain": "threat",
                "display_name": "Combat",
                "retention_class": "budget_pruned",
                "transparency_visible": True,
            }
        }


class ARIAMemoryOut(BaseModel):
    """One decrypted ARIA memory -- the read-back half of the encrypted
    Tier-1 memory-journal (WO-DRIFT-aria-rt-mem-readpath-dead, ADR-0016).
    ``content`` is the plaintext dict ``_decrypt_memory`` recovered from
    ``ARIAPersonalMemory.memory_content``."""
    id: str
    memory_type: str
    importance_score: float
    confidence_level: float
    created_at: Optional[str] = None
    content: Dict[str, Any]

    class Config:
        schema_extra = {
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "memory_type": "market",
                "importance_score": 0.72,
                "confidence_level": 0.9,
                "created_at": "2026-07-09T12:00:00Z",
                "content": {"event": "trade_transaction", "commodity": "organics"},
            }
        }


class ARIAMarketIntelligenceOut(BaseModel):
    """Player-owned market intelligence at a visited port (aria-companion.md:21-31)."""
    commodity: str
    observation_count: int
    average_price: Optional[float] = Field(
        None, description="Mean observed price; null until ≥5 observations",
    )
    price_band: Optional[float] = Field(
        None, description="± band from stored price_volatility; null until ≥5 observations",
    )
    next_prediction: Optional[float] = Field(
        None, description="Stored next_prediction; null until ≥5 observations",
    )
    prediction_confidence: Optional[float] = Field(
        None, description="0–1 confidence; null until ≥5 observations",
    )


class ARIAMarketIntelligenceListOut(BaseModel):
    station_id: str
    items: List[ARIAMarketIntelligenceOut]


# =============================================================================
# SECURITY MIDDLEWARE
# =============================================================================

async def validate_ai_access(
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
) -> str:
    """Validate player has access to AI features"""
    try:
        # Additional AI-specific validation could go here
        return str(current_player.id)
    except Exception as e:
        logger.error(f"AI access validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI access denied"
        )


# =============================================================================
# AI RECOMMENDATION ENDPOINTS
# =============================================================================

@router.post(
    "/recommendations",
    response_model=List[RecommendationResponse],
    summary="Get comprehensive AI recommendations",
    description="Get AI recommendations across multiple game systems with security controls"
)
async def get_ai_recommendations(
    request: AISystemTypeRequest = Body(...),
    player_id: str = Depends(validate_ai_access),
    db: AsyncSession = Depends(get_async_session)
):
    """
    Get comprehensive AI recommendations across all game systems
    
    - **system_types**: List of AI systems to include (trading, combat, colony, port, strategic)
    - **max_recommendations**: Maximum number of recommendations to return (1-10)
    
    Returns personalized recommendations based on player's current situation and AI analysis.
    """
    try:
        ai_service = EnhancedAIService(db)
        
        # Convert string system types to enum
        system_types = [AISystemType(t) for t in request.system_types]
        
        # Get recommendations
        recommendations = await ai_service.get_comprehensive_recommendations(
            player_id=uuid.UUID(player_id),
            system_types=system_types,
            max_recommendations=request.max_recommendations
        )
        
        # Convert to response format
        response_recommendations = []
        for rec in recommendations:
            response_recommendations.append(RecommendationResponse(
                id=rec.id,
                category=rec.category.value,
                recommendation_type=rec.recommendation_type,
                title=rec.title,
                summary=rec.summary,
                priority=rec.priority.value,
                risk_assessment=rec.risk_assessment.value,
                confidence=rec.confidence,
                expected_outcome=rec.expected_outcome,
                expires_at=rec.expires_at.isoformat(),
                security_clearance_required=rec.security_clearance_required.value
            ))
        
        await db.commit()
        return response_recommendations
        
    except ValueError as e:
        logger.warning(f"Invalid request for recommendations: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except PermissionError as e:
        logger.warning(f"Permission denied for AI recommendations: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error getting recommendations: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Recommendation service temporarily unavailable"
        )
    except Exception as e:
        logger.error(f"Unexpected error getting recommendations: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AI service temporarily unavailable"
        )


@router.get(
    "/recommendations/trading",
    response_model=List[RecommendationResponse],
    summary="Get trading-specific recommendations",
    description="Get AI trading recommendations using proven ARIA intelligence"
)
async def get_trading_recommendations(
    limit: int = Query(default=5, ge=1, le=10, description="Number of recommendations"),
    player_id: str = Depends(validate_ai_access),
    db: AsyncSession = Depends(get_async_session)
):
    """
    Get trading-specific recommendations from ARIA's proven intelligence
    
    Leverages the existing ARIA trading AI foundation with enhanced security and validation.
    """
    try:
        ai_service = EnhancedAIService(db)
        
        recommendations = await ai_service.get_comprehensive_recommendations(
            player_id=uuid.UUID(player_id),
            system_types=[AISystemType.TRADING],
            max_recommendations=limit
        )
        
        # Convert to response format
        response_recommendations = []
        for rec in recommendations:
            response_recommendations.append(RecommendationResponse(
                id=rec.id,
                category=rec.category.value,
                recommendation_type=rec.recommendation_type,
                title=rec.title,
                summary=rec.summary,
                priority=rec.priority.value,
                risk_assessment=rec.risk_assessment.value,
                confidence=rec.confidence,
                expected_outcome=rec.expected_outcome,
                expires_at=rec.expires_at.isoformat(),
                security_clearance_required=rec.security_clearance_required.value
            ))
        
        await db.commit()
        return response_recommendations
        
    except Exception as e:
        logger.error(f"Error getting trading recommendations: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Trading AI temporarily unavailable"
        )


# =============================================================================
# AI CONVERSATION ENDPOINTS
# =============================================================================

@router.post(
    "/chat",
    response_model=ConversationResponse,
    summary="Chat with AI assistant",
    description="Natural language conversation with comprehensive AI intelligence"
)
async def chat_with_ai(
    request: ConversationRequest = Body(...),
    player_id: str = Depends(validate_ai_access),
    db: AsyncSession = Depends(get_async_session),
    security_service: AISecurityService = Depends(get_security_service),
):
    """
    Natural language conversation with AI assistant

    - **message**: Your message to the AI (1-4000 characters)
    - **conversation_id**: Optional conversation ID to continue existing chat
    - **conversation_type**: Type of conversation (query, command, feedback, learning, strategic)

    ARIA can help with trading, strategic planning, and game guidance across all systems.
    """
    # WO-ARIA-COST-CAPS: route through AISecurityService validation + limits
    # BEFORE any processing -- mirrors the one proven integration point
    # (api/routes/first_login.py's answer_dialogue). Content-safety and
    # rate-limit failures stay HARD ERRORS (same canon-cited codes first-
    # login already uses); a COST-cap hit is the one outcome dispatch says
    # must NEVER be a hard error -- it degrades to a fallback response with
    # a scope flag instead (ADR-0092 §4).
    #
    # WO-ARIA-TRUST-PERSIST: this route never loaded a Player row before --
    # fetched here solely to seed/write-through the trust ladder, mirroring
    # first_login.py's pattern with an AsyncSession twin.
    player_row = await db.get(Player, uuid.UUID(player_id))
    is_safe, violations = security_service.validate_input(
        request.message, player_id, request.conversation_id or "chat",
        seed_from=player_row,
    )
    if player_row is not None:
        for _col, _val in security_service.get_trust_columns(player_id).items():
            setattr(player_row, _col, _val)
        # EXPLICIT commit -- verified get_db()'s async twin behaves the
        # same as the sync version (never auto-commits); an uncommitted
        # mutation would be silently discarded on the early `raise` below,
        # exactly the path where a NEW block is most likely to have just
        # triggered (see first_login.py's identical fix, same WO).
        await db.commit()
    if not is_safe:
        logger.warning(f"Security violation by player {player_id}: {[v.violation_type.value for v in violations]}")
        raise HTTPException(status_code=400, detail="Input validation failed due to security policy")

    if not security_service.check_rate_limits(player_id):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait before making another request.")

    estimated_cost = security_service.estimate_ai_cost(request.message)
    cost_result = security_service.check_cost_limits_detailed(player_id, estimated_cost)
    if not cost_result.allowed:
        logger.info(
            "ARIA cost cap hit for player %s: %s (scope=%s)",
            player_id, cost_result.error_code, cost_result.scope,
        )
        return ConversationResponse(
            # Plain operational notice, NOT in-character narration -- the
            # "quantum storm" / "attunement fatigue" flavor text is a later
            # ARIA WO's job per dispatch, not this one's.
            response="ARIA's advanced response is temporarily unavailable. Please try again later.",
            conversation_id=request.conversation_id or "",
            response_time=datetime.utcnow().isoformat(),
            degraded=True,
            scope=cost_result.scope,
        )

    sanitized_input = security_service.sanitize_input(request.message)

    try:
        ai_service = EnhancedAIService(db)

        # Let the service build the ConversationContext: only it has the
        # authenticated assistant id, and ConversationContext validation rejects
        # an empty assistant_id (a pre-built context with assistant_id="" raised
        # a ValidationError — not a ValueError, so the old guard never caught it —
        # breaking every threaded follow-up query). Pass the client's
        # conversation_id so the service can continue the thread when valid.
        response_data = await ai_service.process_natural_language_query(
            player_id=uuid.UUID(player_id),
            user_input=sanitized_input,
            conversation_id=request.conversation_id,
        )

        await db.commit()

        # Simplified real-cost tracking (matches first_login.py's own
        # "actual_cost = estimated_cost -- Simplified for now" convention;
        # real per-call token accounting is a separate, later concern).
        security_service.track_cost(player_id, estimated_cost)

        return ConversationResponse(
            response=response_data["response"],
            conversation_id=response_data["conversation_id"],
            response_time=response_data["response_time"],
            intent=response_data.get("intent"),
            # WO-ARIA-CHAT-LLM: absent from response_data (== None here)
            # whenever ARIA_LLM_CHAT_ENABLED is off -- the flag-off pin.
            mode=response_data.get("mode"),
            ledger_entry=response_data.get("ledger_entry"),
        )

    except ValueError as e:
        logger.warning(f"Invalid chat request: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except PermissionError as e:
        logger.warning(f"Permission denied for AI chat: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error in AI chat: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AI chat service temporarily unavailable"
        )


# =============================================================================
# AI ASSISTANT MANAGEMENT ENDPOINTS
# =============================================================================

@router.get(
    "/assistant/status",
    response_model=AssistantStatusResponse,
    summary="Get AI assistant status",
    description="Get comprehensive status and performance metrics for your AI assistant"
)
async def get_assistant_status(
    player_id: str = Depends(validate_ai_access),
    db: AsyncSession = Depends(get_async_session)
):
    """
    Get comprehensive AI assistant status and performance metrics
    
    Returns information about your AI assistant including usage statistics,
    security level, permissions, and recent activity.
    """
    try:
        ai_service = EnhancedAIService(db)
        
        # Get assistant for this player
        assistant = await ai_service._validate_and_authenticate(uuid.UUID(player_id))
        
        # Get performance metrics
        metrics = await ai_service.get_ai_performance_metrics(assistant.id)
        
        await db.commit()
        
        return AssistantStatusResponse(
            assistant_id=str(assistant.id),
            assistant_name=assistant.assistant_name,
            security_level=assistant.security_level,
            api_usage=metrics.get("api_usage", {}),
            total_interactions=metrics.get("total_interactions", 0),
            last_active=metrics.get("last_active", assistant.last_active.isoformat()),
            access_permissions=assistant.access_permissions
        )
        
    except Exception as e:
        logger.error(f"Error getting assistant status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Assistant status temporarily unavailable"
        )


@router.put(
    "/assistant/config",
    response_model=AssistantStatusResponse,
    summary="Update AI assistant configuration",
    description="Update your AI assistant's configuration and permissions"
)
async def update_assistant_config(
    config: AssistantConfigRequest = Body(...),
    player_id: str = Depends(validate_ai_access),
    db: AsyncSession = Depends(get_async_session)
):
    """
    Update AI assistant configuration
    
    - **assistant_name**: Custom name for your AI assistant
    - **personality_type**: Assistant personality (analytical, friendly, tactical, cautious, adaptive)
    - **access_permissions**: System access permissions (trading, combat, colony, port)
    
    Changes take effect immediately for new interactions.
    """
    try:
        ai_service = EnhancedAIService(db)
        
        # Get assistant for this player
        assistant = await ai_service._validate_and_authenticate(uuid.UUID(player_id))
        
        # Update configuration
        if config.assistant_name:
            assistant.assistant_name = config.assistant_name
        
        if config.personality_type:
            assistant.personality_type = config.personality_type
        
        if config.access_permissions:
            # Validate permissions structure
            required_keys = {'trading', 'combat', 'colony', 'port'}
            if required_keys.issubset(config.access_permissions.keys()):
                assistant.access_permissions = config.access_permissions
            else:
                raise ValueError("Invalid permissions structure")
        
        # Get updated metrics
        metrics = await ai_service.get_ai_performance_metrics(assistant.id)
        
        await db.commit()
        
        return AssistantStatusResponse(
            assistant_id=str(assistant.id),
            assistant_name=assistant.assistant_name,
            security_level=assistant.security_level,
            api_usage=metrics.get("api_usage", {}),
            total_interactions=metrics.get("total_interactions", 0),
            last_active=metrics.get("last_active", assistant.last_active.isoformat()),
            access_permissions=assistant.access_permissions
        )
        
    except ValueError as e:
        logger.warning(f"Invalid assistant config update: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error updating assistant config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Assistant configuration update failed"
        )


# =============================================================================
# AI LEARNING AND ANALYTICS ENDPOINTS
# =============================================================================

@router.post(
    "/learning/record-action",
    summary="Record player action for AI learning",
    description="Record player actions to improve AI recommendations"
)
async def record_player_action(
    action_type: str = Body(..., description="Type of action"),
    action_data: Dict[str, Any] = Body(..., description="Action details"),
    outcome: Optional[Dict[str, Any]] = Body(None, description="Action outcome"),
    player_id: str = Depends(validate_ai_access),
    db: AsyncSession = Depends(get_async_session)
):
    """
    Record player actions for AI learning and pattern recognition
    
    - **action_type**: Type of action (trade, combat, colonization, etc.)
    - **action_data**: Detailed action information
    - **outcome**: Optional outcome data for learning validation
    
    Helps ARIA learn your preferences and improve recommendations over time.
    """
    try:
        ai_service = EnhancedAIService(db)
        
        await ai_service.record_player_action(
            player_id=uuid.UUID(player_id),
            action_type=action_type,
            action_data=action_data,
            outcome=outcome
        )
        
        await db.commit()
        
        return {"status": "success", "message": "Action recorded for AI learning"}
        
    except ValueError as e:
        logger.warning(f"Invalid action recording request: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error recording player action: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Action recording failed"
        )


@router.get(
    "/analytics/performance",
    summary="Get AI performance analytics",
    description="Get detailed performance metrics and analytics for your AI assistant"
)
async def get_ai_analytics(
    player_id: str = Depends(validate_ai_access),
    db: AsyncSession = Depends(get_async_session)
):
    """
    Get comprehensive AI performance analytics and metrics
    
    Returns detailed analytics about your AI assistant's performance,
    recommendation accuracy, and learning progress.
    """
    try:
        ai_service = EnhancedAIService(db)
        
        # Get assistant for this player
        assistant = await ai_service._validate_and_authenticate(uuid.UUID(player_id))
        
        # Get comprehensive metrics
        metrics = await ai_service.get_ai_performance_metrics(assistant.id)
        
        await db.commit()
        return metrics
        
    except Exception as e:
        logger.error(f"Error getting AI analytics: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AI analytics temporarily unavailable"
        )


# =============================================================================
# SYSTEM MAINTENANCE ENDPOINTS (ADMIN ONLY)
# =============================================================================

@router.post(
    "/system/cleanup",
    summary="Clean up expired AI data",
    description="Clean up expired AI data for GDPR compliance (Admin only)"
)
async def cleanup_ai_data(
    player_id: str = Depends(validate_ai_access),
    db: AsyncSession = Depends(get_async_session)
):
    """
    Clean up expired AI data for GDPR compliance
    
    This endpoint is typically used by system administrators for data lifecycle management.
    """
    try:
        ai_service = EnhancedAIService(db)

        # SECURITY / honesty: section banner + OpenAPI say "Admin only", but
        # auth is plain `validate_ai_access` (any AI-eligible player). The call
        # below is GLOBAL DELETE (ai_conversation_logs / ai_cross_system_knowledge /
        # ai_security_audit_log) — not scoped to the caller. Do NOT "fix" the
        # gate here without human OK — Pending DECISION
        # `enhanced-ai-cleanup-admin-gate` (HIGH / safety-list). Diagnose-only.
        deleted_count = await ai_service.cleanup_expired_data()
        
        await db.commit()
        
        return {
            "status": "success",
            "deleted_records": deleted_count,
            "message": "Expired AI data cleaned up successfully"
        }
        
    except Exception as e:
        logger.error(f"Error cleaning up AI data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Data cleanup failed"
        )


# =============================================================================
# TRADE CASCADE (WO-PULL-ARIA-CASCADE-ENTRYPOINT)
# =============================================================================

@router.post(
    "/trade-cascade",
    summary="Plan an explored-sector trade cascade",
    description=(
        "ARIA multi-hop trade cascade through ONLY sectors this player has "
        "explored (aria-companion.md Route optimization). Returns a cascade "
        "plan or an explored-space refusal payload — never invents unknown "
        "space routes."
    ),
)
async def plan_trade_cascade(
    request: TradeCascadeRequest = Body(...),
    player_id: str = Depends(validate_ai_access),
    db: AsyncSession = Depends(get_async_session),
):
    """Owner-only by construction: player_id comes from validate_ai_access
    (JWT), never from the request body. Frontier refusal is service-owned —
    insufficient exploration / no profitable route return structured error
    dicts (HTTP 200 with error key) rather than inventing routes."""
    try:
        from src.services.aria_personal_intelligence_service import (
            get_aria_intelligence_service,
        )

        aria_service = get_aria_intelligence_service()
        result = await aria_service.plan_trade_cascade(
            player_id,
            request.start_sector_id,
            request.target_profit,
            request.max_jumps,
            db,
        )
        await db.commit()
        if result is None:
            return {
                "error": "no_exploration_map",
                "message": (
                    "Explore more sectors to plan trade routes"
                ),
            }
        return result
    except Exception as e:
        logger.error(f"Error planning trade cascade: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ARIA trade cascade temporarily unavailable",
        )


@router.get(
    "/exploration-suggestions",
    response_model=ExplorationSuggestionsOut,
    summary="ARIA exploration-map suggestions",
    description=(
        "Returns repeat-visit, frontier expansion, and risky-sector suggestions "
        "derived from the authenticated player's ARIAExplorationMap "
        "(aria-companion.md § Exploration suggestions)."
    ),
)
async def get_exploration_suggestions(
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session),
):
    try:
        from src.services.aria_personal_intelligence_service import (
            get_aria_intelligence_service,
        )

        aria_service = get_aria_intelligence_service()
        result = await aria_service.get_exploration_suggestions(
            str(current_player.id), db,
        )
        await db.commit()
        return result
    except Exception as e:
        logger.error(f"Error fetching ARIA exploration suggestions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ARIA exploration suggestions temporarily unavailable",
        )


@router.get(
    "/combat-advice",
    response_model=CombatAdviceOut,
    summary="ARIA combat advice for a ship matchup",
    description=(
        "Aggregates the player's own combat memories against an opponent ship "
        "type and returns a matchup hint (aria-companion.md § Combat advice)."
    ),
)
async def get_combat_advice(
    opponent_ship_type: str = Query(
        ..., min_length=1, description="Opponent ship type enum key",
    ),
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session),
):
    try:
        from src.services.aria_personal_intelligence_service import (
            get_aria_intelligence_service,
        )

        player_ship_type: Optional[str] = None
        ship = getattr(current_player, "current_ship", None)
        if ship is not None and getattr(ship, "type", None) is not None:
            player_ship_type = ship.type.value

        aria_service = get_aria_intelligence_service()
        result = await aria_service.get_combat_advice(
            str(current_player.id),
            opponent_ship_type,
            player_ship_type,
            db,
        )
        await db.commit()
        return result
    except Exception as e:
        logger.error(f"Error fetching ARIA combat advice: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ARIA combat advice temporarily unavailable",
        )


# =============================================================================
# DATA INDEX (WO-P6-aria-data-index-registry)
# =============================================================================

@router.get(
    "/data-index",
    response_model=List[ARIADataStreamOut],
    summary="Get the ARIA data-index catalog",
    description="List every observation stream ARIA learns from, per DATA_MODELS/aria-data-index.md -- the memory-journal transparency surface."
)
async def get_aria_data_index(
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Read-only catalog listing, ordered by registry key. No player-scoped
    filtering here -- the registry itself is global; per-player memory
    presence is a separate, future memory-journal endpoint."""
    try:
        index_service = ARIADataIndexService(db)
        return await index_service.list_streams()
    except Exception as e:
        logger.error(f"Error listing ARIA data index: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ARIA data index temporarily unavailable"
        )


@router.get(
    "/market-intelligence/{station_id}",
    response_model=ARIAMarketIntelligenceListOut,
    summary="Read your ARIA market intelligence at a docked station",
    description=(
        "Returns the authenticated player's own ARIAMarketIntelligence rows for "
        "the requested station while docked there (aria-companion.md Market "
        "predictions). Prediction fields stay empty until ≥5 observations."
    ),
)
async def get_aria_market_intelligence(
    station_id: str,
    commodity: Optional[str] = Query(
        None, description="Optional filter to one commodity at this station",
    ),
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session),
):
    """Owner-only by construction (ADR-0016): player id comes from JWT, never
    from path/query spoof parameters."""
    station = await db.get(Station, station_id)
    if not station:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Station not found",
        )

    if not current_player.is_docked or not current_player.current_port_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be docked at a station to view ARIA market intelligence",
        )

    if str(current_player.current_port_id) != str(station_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be docked at this station to view its market intelligence",
        )

    from src.services.trading_service import TradingService

    can_trade, reason = TradingService.can_player_trade(current_player, station)
    if not can_trade:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=reason,
        )

    try:
        from src.services.aria_personal_intelligence_service import (
            get_aria_intelligence_service,
        )

        aria_service = get_aria_intelligence_service()
        items = await aria_service.list_market_intelligence_at_station(
            str(current_player.id),
            station_id,
            db,
            commodity=commodity,
        )
        return {"station_id": station_id, "items": items}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading ARIA market intelligence: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ARIA market intelligence temporarily unavailable",
        )


@router.get(
    "/memories/dump",
    summary="Export your ARIA personal memory store",
    description=(
        "Owner-scoped data export of the caller's ARIA personal memory store "
        "(aria-companion.md:173-175). Path is /memories/dump not /export so it "
        "cannot collide with security.py _ADMIN_REPORT_MARKERS if this router "
        "is ever mounted under /admin. Decrypts Tier-1 memories with the same "
        "path as GET /ai/memories (no player-id parameter). Does not call "
        "POST /ai/system/cleanup."
    ),
)
async def export_aria_personal_store(
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session),
):
    """JWT owner only — ``current_player.id`` is the sole filter."""
    try:
        from src.services.aria_personal_intelligence_service import (
            get_aria_intelligence_service,
        )

        aria_service = get_aria_intelligence_service()
        payload = await aria_service.export_personal_store(
            str(current_player.id), db,
        )
        await db.commit()
        return payload
    except Exception as e:
        logger.error(f"Error exporting ARIA personal store: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ARIA memory export temporarily unavailable",
        )


@router.post(
    "/memories/reset",
    summary="Reset your ARIA personal data",
    description=(
        "Owner-scoped delete of the caller's ARIA personal tables listed in "
        "aria-companion.md:169. Never a global cleanup — POST /ai/system/cleanup "
        "is untouched."
    ),
)
async def reset_aria_personal_store(
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session),
):
    """JWT owner only — deletes rows for ``current_player.id`` only."""
    try:
        from src.services.aria_personal_intelligence_service import (
            get_aria_intelligence_service,
        )

        aria_service = get_aria_intelligence_service()
        deleted = await aria_service.reset_personal_store(
            str(current_player.id), db,
        )
        await db.commit()
        return {
            "status": "success",
            "deleted": deleted,
        }
    except Exception as e:
        logger.error(f"Error resetting ARIA personal store: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ARIA memory reset temporarily unavailable",
        )


@router.get(
    "/memories",
    response_model=List[ARIAMemoryOut],
    summary="Recall your own decrypted ARIA memories",
    description=(
        "Tier-1 memory-journal transparency read path -- decrypts and returns "
        "YOUR OWN ARIAPersonalMemory rows (WO-DRIFT-aria-rt-mem-readpath-dead), "
        "the endpoint the data-index route's docstring flagged as future work."
    )
)
async def get_aria_memories(
    memory_type: Optional[str] = Query(
        None, description="Filter to one memory_type registry key (e.g. 'market', 'threat.combat')"
    ),
    limit: int = Query(50, ge=1, le=200),
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Owner-only by construction: there is no player-id path/query
    parameter to spoof -- ``current_player.id`` comes from the
    JWT-authenticated dependency and is the only id ever passed to
    ``recall_memories``'s query-level filter (ADR-0016 isolation). A
    request can therefore only ever recall the requester's own memories,
    never another player's."""
    try:
        from src.services.aria_personal_intelligence_service import get_aria_intelligence_service

        aria_service = get_aria_intelligence_service()
        memories = await aria_service.recall_memories(
            str(current_player.id), db, memory_type=memory_type, limit=limit,
        )
        await db.commit()
        return memories
    except Exception as e:
        logger.error(f"Error recalling ARIA memories: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ARIA memory recall temporarily unavailable"
        )


# =============================================================================
# ERROR HANDLERS (Note: These are handled by FastAPI's main app exception handlers)
# =============================================================================

# Exception handlers are implemented at the application level in main.py
# Individual route error handling is done within each endpoint's try/catch blocks