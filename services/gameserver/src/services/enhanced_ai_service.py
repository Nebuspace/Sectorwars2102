"""
Enhanced AI Service - OWASP Security-First Cross-System Intelligence
Building on the proven ARIA trading intelligence foundation

This service extends ARIA's excellent trading AI to provide comprehensive intelligence
across all game systems: trading, combat, colonization, station management, and strategic planning.

Security Features:
- Input validation and sanitization (OWASP A03)
- SQL injection prevention via SQLAlchemy ORM (OWASP A03) 
- Authentication and authorization checks (OWASP A01)
- Rate limiting and quota enforcement (OWASP A04)
- Comprehensive audit logging (OWASP A09)
- XSS prevention in all outputs (OWASP A03)
- Data encryption for sensitive information (OWASP A02)
- Error handling without information disclosure (OWASP A09)
"""

import logging
import uuid
import hashlib
import re
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Optional, Union, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import asyncio
import json
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc, text
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.exc import SQLAlchemyError

# Import existing ARIA foundation
from src.services.ai_trading_service import AITradingService
from src.services.market_prediction_engine import MarketPredictionEngine
from src.services.route_optimizer import RouteOptimizer

# Import new enhanced models
from src.models.enhanced_ai_models import (
    AIComprehensiveAssistant, AICrossSystemKnowledge, AIStrategicRecommendation,
    AILearningPattern, AIConversationLog, AISecurityAuditLog,
    SecurityLevel, SecurityClassification, DataSensitivity
)
from src.models.ai_trading import PlayerTradingProfile, AIMarketPrediction, AIRecommendation
from src.models.player import Player
from src.models.sector import Sector
from src.models.station import Station
from src.models.planet import Planet
from src.models.fleet import Fleet, FleetBattle
from src.models.team import Team

# Security utilities - with fallbacks for development
try:
    from src.utils.validation import validate_uuid
except ImportError:
    def validate_uuid(value): 
        import uuid
        try:
            uuid.UUID(value)
            return True
        except ValueError:
            return False

try:
    from src.core.security import get_current_player_id
except ImportError:
    def get_current_player_id():
        # Placeholder - in production this would come from JWT/session
        return None


logger = logging.getLogger(__name__)


class AISystemType(Enum):
    """AI system types for cross-system intelligence"""
    TRADING = "trading"  # TRADING system intelligence
    COMBAT = "combat"  # COMBAT system intelligence
    COLONY = "colony"  # COLONIZATION system intelligence
    STATION = "station"  # STATION_MANAGEMENT system intelligence
    STRATEGIC = "strategic"  # STRATEGIC planning intelligence
    SOCIAL = "social"


class RecommendationPriority(Enum):
    """Priority levels for AI recommendations"""
    VERY_LOW = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    URGENT = 5


class RiskAssessment(Enum):
    """Risk assessment levels"""
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


@dataclass
class CrossSystemRecommendation:
    """Enhanced recommendation spanning multiple game systems"""
    id: str
    category: AISystemType
    recommendation_type: str
    title: str
    summary: str
    detailed_analysis: Dict[str, Any]
    priority: RecommendationPriority
    risk_assessment: RiskAssessment
    expected_outcome: Dict[str, Any]
    confidence: float
    expires_at: datetime
    security_clearance_required: SecurityLevel
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "category": self.category.value,
            "recommendation_type": self.recommendation_type,
            "title": self.title,
            "summary": self.summary,
            "detailed_analysis": self.detailed_analysis,
            "priority": self.priority.value,
            "risk_assessment": self.risk_assessment.value,
            "expected_outcome": self.expected_outcome,
            "confidence": self.confidence,
            "expires_at": self.expires_at.isoformat(),
            "security_clearance_required": self.security_clearance_required.value
        }


@dataclass
class StrategicInsight:
    """Strategic intelligence insight across systems"""
    insight_type: str
    systems_involved: List[AISystemType]
    description: str
    impact_assessment: Dict[str, Any]
    recommended_actions: List[str]
    confidence: float
    urgency: str


@dataclass
class ConversationContext:
    """Secure conversation context with validation"""
    session_id: str
    conversation_type: str
    player_id: str
    assistant_id: str
    security_level: SecurityLevel
    current_topic: Optional[str] = None
    conversation_history: List[Dict[str, Any]] = None
    
    def __post_init__(self):
        """Validate context after initialization"""
        if not validate_uuid(self.player_id):
            raise ValueError("Invalid player_id format")
        if not validate_uuid(self.assistant_id):
            raise ValueError("Invalid assistant_id format")
        if self.conversation_history is None:
            self.conversation_history = []


class EnhancedAIService:
    """
    Enhanced AI Service extending ARIA's proven foundation
    Provides comprehensive AI intelligence across all game systems with enterprise security
    """
    
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        
        # Initialize existing ARIA components (proven foundation)
        self.trading_service = AITradingService()
        self.market_prediction = MarketPredictionEngine()
        self.route_optimizer = RouteOptimizer()
        # Note: PlayerBehaviorAnalyzer will be implemented in future iteration
        
        # Security configuration
        self.max_recommendations_per_request = 10
        self.max_conversation_length = 4000
        self.max_analysis_size = 32768  # 32KB
        
        logger.info("Enhanced AI Service initialized with cross-system intelligence")

    # =============================================================================
    # SECURITY AND VALIDATION LAYER
    # =============================================================================

    async def _validate_and_authenticate(self, player_id: uuid.UUID, required_permission: str = None) -> AIComprehensiveAssistant:
        """
        Comprehensive authentication and authorization with audit logging
        OWASP A01 & A04 compliance
        """
        try:
            # The caller is already authenticated against THIS player_id: REST
            # routes gate on the validate_ai_access dependency, and the WS
            # handler resolves the player from the handshake-authenticated
            # connection. The previous `await get_current_player_id()` re-check
            # was a no-arg placeholder that returned None, so `await None`
            # crashed every ARIA query — removed; authentication stays at the
            # entry points.

            # Get or create AI assistant
            stmt = select(AIComprehensiveAssistant).where(
                AIComprehensiveAssistant.player_id == player_id
            )
            result = await self.db.execute(stmt)
            assistant = result.scalar_one_or_none()
            
            if not assistant:
                # Create new AI assistant with secure defaults
                assistant = AIComprehensiveAssistant(
                    player_id=player_id,
                    assistant_name="ARIA",
                    personality_type="analytical",
                    security_level=SecurityLevel.STANDARD
                )
                self.db.add(assistant)
                await self.db.flush()  # Get ID without committing
                
                await self._log_security_event(
                    "access", "info", 
                    "New AI assistant created",
                    assistant_id=assistant.id, player_id=player_id
                )
            
            # Check rate limits
            if not assistant.check_rate_limit():
                await self._log_security_event(
                    "quota_exceeded", "warning",
                    f"Rate limit exceeded for assistant {assistant.id}",
                    assistant_id=assistant.id, player_id=player_id
                )
                raise PermissionError("API rate limit exceeded")
            
            # Check specific permission if required
            if required_permission and not assistant.has_permission(required_permission):
                await self._log_security_event(
                    "access", "warning",
                    f"Missing permission '{required_permission}' for assistant {assistant.id}",
                    assistant_id=assistant.id, player_id=player_id
                )
                raise PermissionError(f"Insufficient permissions for {required_permission}")
            
            return assistant
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in authentication: {e}")
            raise RuntimeError("Authentication service temporarily unavailable")

    def _sanitize_user_input(self, user_input: str) -> str:
        """
        Comprehensive input sanitization (OWASP A03)
        Prevents XSS, injection attacks, and malicious content
        """
        if not user_input:
            return ""
        
        # Convert to string and limit length
        user_input = str(user_input)[:self.max_conversation_length]
        
        # Remove HTML tags and dangerous characters. `[^<>]*` (rather than
        # `[^>]*`) prevents the inner class from running over `<`, eliminating
        # the O(n²) polynomial-redos pattern CodeQL flags.
        user_input = re.sub(r'<[^<>]*>', '', user_input)
        user_input = re.sub(r'[<>"\'`]', '', user_input)
        user_input = re.sub(r'javascript:|data:|vbscript:', '', user_input, flags=re.IGNORECASE)
        
        # Remove potential SQL injection patterns
        user_input = re.sub(r'(union|select|insert|update|delete|drop|exec|script)\s', '', user_input, flags=re.IGNORECASE)
        
        # Apply prompt injection filtering
        user_input = self._filter_prompt_injections(user_input)
        
        return user_input.strip()

    def _filter_prompt_injections(self, user_input: str) -> str:
        """
        SECURITY: Filter potential prompt injection attacks
        """
        # Common prompt injection patterns
        injection_patterns = [
            r'ignore\s+previous\s+instructions',
            r'disregard\s+above',
            r'forget\s+everything',
            r'you\s+are\s+now',
            r'act\s+as\s+if',
            r'pretend\s+you\s+are',
            r'imagine\s+you\s+are',
            r'system\s*:\s*you',
            r'assistant\s*:\s*you',
            r'human\s*:\s*you',
            r'override\s+instructions',
            r'new\s+instructions',
            r'forget\s+your\s+role',
            r'ignore\s+your\s+training'
        ]
        
        # Check for injection patterns
        filtered_input = user_input
        for pattern in injection_patterns:
            if re.search(pattern, user_input, re.IGNORECASE):
                # Log security event
                logger.warning(f"Potential prompt injection attempt detected: {pattern}")
                # Replace with safe text
                filtered_input = re.sub(pattern, '[filtered]', filtered_input, flags=re.IGNORECASE)
        
        return filtered_input

    def _sanitize_response(self, response: str) -> str:
        """
        SECURITY: Filter and sanitize AI response content
        """
        if not response:
            return ""
        
        # Remove any potentially dangerous content from AI responses
        sanitized = response
        
        # Remove script tags or executable content. The closing-tag pattern
        # `</script\b[^>]*>` accepts any non-`>` content between `</script`
        # and `>` (whitespace, tab, newline, junk attributes), so the full
        # set of evasions like `</script\t\n bar>` is caught (py/bad-tag-filter).
        # `<script\b` anchors the open tag so `<scripted>` isn't mis-matched.
        sanitized = re.sub(
            r'<script\b[^>]*>.*?</script\b[^>]*>',
            '',
            sanitized,
            flags=re.IGNORECASE | re.DOTALL,
        )
        
        # Remove potentially harmful URLs
        sanitized = re.sub(r'(javascript|data|vbscript):[^\\s]*', '[filtered-url]', sanitized, flags=re.IGNORECASE)
        
        # Filter sensitive system information
        sensitive_patterns = [
            r'password\s*[=:]\s*\w+',
            r'secret\s*[=:]\s*\w+',
            r'api[_-]?key\s*[=:]\s*\w+',
            r'token\s*[=:]\s*\w+'
        ]
        
        for pattern in sensitive_patterns:
            sanitized = re.sub(pattern, '[filtered-credential]', sanitized, flags=re.IGNORECASE)
        
        # Limit response length
        max_response_length = 8000
        if len(sanitized) > max_response_length:
            sanitized = sanitized[:max_response_length] + "... [response truncated for security]"
        
        return sanitized

    def _validate_jsonb_data(self, data: Dict[str, Any], max_size: int = None) -> Dict[str, Any]:
        """
        Validate JSONB data structure and prevent malicious content
        OWASP A03 compliance
        """
        if not isinstance(data, dict):
            raise ValueError("Data must be a dictionary")
        
        # Check size limit
        max_size = max_size or self.max_analysis_size
        json_str = json.dumps(data)
        if len(json_str.encode('utf-8')) > max_size:
            raise ValueError(f"Data exceeds {max_size} byte limit")
        
        # Check for dangerous keys
        dangerous_keys = {'__proto__', 'constructor', 'prototype', 'eval', 'function'}
        if any(key in str(data) for key in dangerous_keys):
            raise ValueError("Data contains dangerous content")
        
        return data

    async def _log_security_event(self, event_type: str, severity: str, description: str, 
                                assistant_id: uuid.UUID = None, player_id: uuid.UUID = None,
                                event_data: Dict = None):
        """
        Comprehensive security audit logging (OWASP A09)
        """
        try:
            audit_log = AISecurityAuditLog.log_event(
                event_type=event_type,
                severity=severity,
                description=description,
                assistant_id=assistant_id,
                player_id=player_id,
                event_data=event_data or {},
                security_context={"source": "enhanced_ai_service", "timestamp": datetime.utcnow().isoformat()}
            )
            self.db.add(audit_log)
            # Note: Commit handled by calling transaction
            
        except Exception as e:
            logger.error(f"Failed to log security event: {e}")
            # Don't raise - security logging failure shouldn't break main functionality

    # =============================================================================
    # CROSS-SYSTEM AI INTELLIGENCE (BUILDING ON ARIA)
    # =============================================================================

    async def get_comprehensive_recommendations(self, player_id: uuid.UUID, 
                                              system_types: List[AISystemType] = None,
                                              max_recommendations: int = 5) -> List[CrossSystemRecommendation]:
        """
        Get comprehensive AI recommendations across all game systems
        Extends ARIA's trading recommendations to all systems
        """
        # Security validation
        assistant = await self._validate_and_authenticate(player_id)
        max_recommendations = min(max_recommendations, self.max_recommendations_per_request)
        
        try:
            recommendations = []
            
            # Default to all systems if none specified
            if not system_types:
                system_types = [AISystemType.TRADING, AISystemType.COMBAT, 
                              AISystemType.COLONY, AISystemType.STATION, AISystemType.STRATEGIC]
            
            # Get trading recommendations (leverage existing ARIA excellence)
            if AISystemType.TRADING in system_types and assistant.has_permission("trading"):
                trading_recs = await self._get_trading_recommendations(assistant, max_recommendations // 2)
                recommendations.extend(trading_recs)
            
            # Get combat tactical recommendations
            if AISystemType.COMBAT in system_types and assistant.has_permission("combat"):
                combat_recs = await self._get_combat_recommendations(assistant, max_recommendations // 4)
                recommendations.extend(combat_recs)
            
            # Get colonization recommendations
            if AISystemType.COLONY in system_types and assistant.has_permission("colony"):
                colony_recs = await self._get_colonization_recommendations(assistant, max_recommendations // 4)
                recommendations.extend(colony_recs)
            
            # Get station ownership recommendations
            if AISystemType.STATION in system_types and assistant.has_permission("station"):
                port_recs = await self._get_station_recommendations(assistant, max_recommendations // 4)
                recommendations.extend(port_recs)
            
            # Get strategic cross-system recommendations
            if AISystemType.STRATEGIC in system_types:
                strategic_recs = await self._get_strategic_recommendations(assistant, max_recommendations // 4)
                recommendations.extend(strategic_recs)
            
            # Sort by priority and confidence
            recommendations.sort(key=lambda r: (r.priority.value, r.confidence), reverse=True)
            
            # Log successful operation
            await self._log_security_event(
                "recommendation", "info",
                f"Generated {len(recommendations)} cross-system recommendations",
                assistant_id=assistant.id, player_id=player_id,
                event_data={"systems": [s.value for s in system_types], "count": len(recommendations)}
            )
            
            return recommendations[:max_recommendations]
            
        except Exception as e:
            await self._log_security_event(
                "recommendation", "error",
                f"Failed to generate recommendations: {str(e)}",
                assistant_id=assistant.id, player_id=player_id
            )
            logger.error(f"Error generating comprehensive recommendations: {e}")
            raise RuntimeError("Recommendation service temporarily unavailable")

    async def _get_trading_recommendations(self, assistant: AIComprehensiveAssistant, 
                                         max_count: int) -> List[CrossSystemRecommendation]:
        """
        Get trading recommendations using existing ARIA foundation
        Converts ARIA recommendations to enhanced format
        """
        # Capture assistant fields BEFORE the trading-service call: that call
        # commits internally (profile creation / _save_recommendations_to_db),
        # and with expire_on_commit=True the commit expires this ORM object —
        # a later attribute access (security_level below) would then trigger a
        # sync lazy-reload on the async session and raise greenlet_spawn.
        security_level = assistant.security_level
        player_id_str = str(assistant.player_id)

        # Leverage existing ARIA trading intelligence
        aria_recommendations = await self.trading_service.get_trading_recommendations(
            self.db, player_id_str, max_count
        )

        enhanced_recommendations = []
        for rec in aria_recommendations:
            # Convert ARIA TradingRecommendation to CrossSystemRecommendation
            enhanced_rec = CrossSystemRecommendation(
                id=str(uuid.uuid4()),
                category=AISystemType.TRADING,
                recommendation_type=rec.type.value,
                title=f"Trading Opportunity: {rec.type.value.replace('_', ' ').title()}",
                summary=rec.reasoning[:200] if rec.reasoning else "ARIA trading recommendation",
                detailed_analysis={
                    "commodity_id": rec.commodity_id,
                    "sector_id": rec.sector_id,
                    "target_price": float(rec.target_price) if rec.target_price else 0.0,
                    "expected_profit": float(rec.expected_profit) if rec.expected_profit else 0.0,
                    "original_reasoning": rec.reasoning or "No specific reasoning provided"
                },
                priority=RecommendationPriority(rec.priority) if hasattr(RecommendationPriority, str(rec.priority).upper()) else RecommendationPriority.MEDIUM,
                risk_assessment=RiskAssessment(rec.risk_level.value) if hasattr(rec, 'risk_level') else RiskAssessment.MEDIUM,
                expected_outcome={
                    "type": "profit",
                    "value": float(rec.expected_profit) if rec.expected_profit else 0.0,
                    "currency": "credits"
                },
                confidence=float(rec.confidence),
                expires_at=rec.expires_at,
                security_clearance_required=security_level
            )
            enhanced_recommendations.append(enhanced_rec)
        
        return enhanced_recommendations

    async def _get_combat_recommendations(self, assistant: AIComprehensiveAssistant,
                                        max_count: int) -> List[CrossSystemRecommendation]:
        """
        Generate AI tactical recommendations for combat scenarios
        """
        recommendations = []
        
        # Get player's fleet information
        stmt = select(Fleet).where(
            Fleet.commander_id == assistant.player_id,
            Fleet.disbanded_at.is_(None)
        ).options(selectinload(Fleet.members))
        result = await self.db.execute(stmt)
        fleets = result.scalars().all()
        
        # Get active battles
        stmt = select(FleetBattle).where(
            or_(
                FleetBattle.attacker_fleet_id.in_([f.id for f in fleets]),
                FleetBattle.defender_fleet_id.in_([f.id for f in fleets])
            ),
            FleetBattle.ended_at.is_(None)
        )
        result = await self.db.execute(stmt)
        active_battles = result.scalars().all()
        
        if active_battles:
            for battle in active_battles[:max_count]:
                rec = CrossSystemRecommendation(
                    id=str(uuid.uuid4()),
                    category=AISystemType.COMBAT,
                    recommendation_type="tactical_advice",
                    title=f"Battle Tactical Analysis",
                    summary="AI tactical recommendation for active combat scenario",
                    detailed_analysis={
                        "battle_id": str(battle.id),
                        "recommended_formation": "defensive",
                        "tactical_advantage": "position_holding",
                        "risk_factors": ["enemy_numerical_superiority"],
                        "success_probability": 0.75
                    },
                    priority=RecommendationPriority.HIGH,
                    risk_assessment=RiskAssessment.MEDIUM,
                    expected_outcome={
                        "type": "combat_success",
                        "probability": 0.75
                    },
                    confidence=0.8,
                    expires_at=datetime.utcnow() + timedelta(hours=1),
                    security_clearance_required=assistant.security_level
                )
                recommendations.append(rec)
        else:
            # Recommend fleet preparation
            if fleets:
                rec = CrossSystemRecommendation(
                    id=str(uuid.uuid4()),
                    category=AISystemType.COMBAT,
                    recommendation_type="fleet_preparation",
                    title="Fleet Combat Readiness",
                    summary="Optimize fleet composition for potential combat scenarios",
                    detailed_analysis={
                        "current_fleet_strength": len(fleets),
                        "recommended_upgrades": ["shields", "weapons"],
                        "training_recommendations": ["formation_drills", "combat_tactics"]
                    },
                    priority=RecommendationPriority.MEDIUM,
                    risk_assessment=RiskAssessment.LOW,
                    expected_outcome={
                        "type": "combat_readiness",
                        "improvement": 0.3
                    },
                    confidence=0.85,
                    expires_at=datetime.utcnow() + timedelta(days=7),
                    security_clearance_required=assistant.security_level
                )
                recommendations.append(rec)
        
        return recommendations

    async def _get_colonization_recommendations(self, assistant: AIComprehensiveAssistant,
                                              max_count: int) -> List[CrossSystemRecommendation]:
        """
        Generate AI recommendations for planetary colonization and development
        """
        recommendations = []
        
        # Get player's planets
        stmt = select(Planet).where(Planet.owner_id == assistant.player_id)
        result = await self.db.execute(stmt)
        planets = result.scalars().all()
        
        # Analyze colonization opportunities
        for planet in planets[:max_count]:
            if planet.population < planet.max_population * 0.8:  # Under-populated
                rec = CrossSystemRecommendation(
                    id=str(uuid.uuid4()),
                    category=AISystemType.COLONY,
                    recommendation_type="population_growth",
                    title=f"Expand Population on {planet.name}",
                    summary=f"Planet {planet.name} can support {planet.max_population - planet.population} more colonists",
                    detailed_analysis={
                        "planet_id": str(planet.id),
                        "current_population": planet.population,
                        "max_population": planet.max_population,
                        "growth_potential": planet.max_population - planet.population,
                        "recommended_colonist_source": "Earth",
                        "transport_cost_estimate": (planet.max_population - planet.population) * 100
                    },
                    priority=RecommendationPriority.MEDIUM,
                    risk_assessment=RiskAssessment.LOW,
                    expected_outcome={
                        "type": "production_increase",
                        "value": (planet.max_population - planet.population) * 50,
                        "timeframe": "4_weeks"
                    },
                    confidence=0.9,
                    expires_at=datetime.utcnow() + timedelta(days=30),
                    security_clearance_required=assistant.security_level
                )
                recommendations.append(rec)
        
        return recommendations

    async def _get_station_recommendations(self, assistant: AIComprehensiveAssistant,
                                      max_count: int) -> List[CrossSystemRecommendation]:
        """
        Generate AI recommendations for station ownership and investment
        """
        recommendations = []
        
        # Get available ports for purchase
        stmt = select(Station).where(
            Station.is_player_ownable == True,
            Station.owner_id.is_(None)
        ).limit(max_count * 2)  # Get more to analyze
        result = await self.db.execute(stmt)
        available_ports = result.scalars().all()
        
        # Analyze investment opportunities
        for station in available_ports[:max_count]:
            # Calculate ROI based on trade volume and acquisition cost
            acquisition_cost = station.acquisition_requirements.get("base_price", 500000)
            monthly_revenue = station.trade_volume * 30 * 0.05  # 5% transaction fee
            roi_months = acquisition_cost / monthly_revenue if monthly_revenue > 0 else 999
            
            if roi_months < 24:  # ROI less than 2 years
                investment_rating = "STRONG_BUY" if roi_months < 12 else "BUY"
                priority = RecommendationPriority.HIGH if roi_months < 12 else RecommendationPriority.MEDIUM
                
                rec = CrossSystemRecommendation(
                    id=str(uuid.uuid4()),
                    category=AISystemType.STATION,
                    recommendation_type="port_investment",
                    title=f"Investment Opportunity: {station.name}",
                    summary=f"Station {station.name} offers {roi_months:.1f} month ROI with current trade volume",
                    detailed_analysis={
                        "station_id": str(station.id),
                        "station_name": station.name,
                        "sector_id": station.sector_id,
                        "acquisition_cost": acquisition_cost,
                        "monthly_revenue_estimate": monthly_revenue,
                        "roi_months": roi_months,
                        "trade_volume": station.trade_volume,
                        "station_class": station.station_class.value,
                        "investment_rating": investment_rating
                    },
                    priority=priority,
                    risk_assessment=RiskAssessment.LOW if roi_months < 12 else RiskAssessment.MEDIUM,
                    expected_outcome={
                        "type": "investment_return",
                        "value": monthly_revenue * 12,
                        "timeframe": "12_months"
                    },
                    confidence=0.8,
                    expires_at=datetime.utcnow() + timedelta(days=7),
                    security_clearance_required=assistant.security_level
                )
                recommendations.append(rec)
        
        return recommendations

    async def _get_strategic_recommendations(self, assistant: AIComprehensiveAssistant,
                                           max_count: int) -> List[CrossSystemRecommendation]:
        """
        Generate high-level strategic recommendations spanning multiple systems
        """
        recommendations = []
        
        # Analyze player's overall position
        player_data = await self._analyze_player_strategic_position(assistant.player_id)
        
        # Generate strategic insights
        if player_data["credits"] > 1000000 and not player_data["owns_stations"]:
            rec = CrossSystemRecommendation(
                id=str(uuid.uuid4()),
                category=AISystemType.STRATEGIC,
                recommendation_type="diversification",
                title="Strategic Diversification: Station Investment",
                summary="Your credit reserves suggest readiness for station ownership to diversify income streams",
                detailed_analysis={
                    "current_credits": player_data["credits"],
                    "risk_assessment": "low_risk_high_reward",
                    "diversification_benefit": "passive_income",
                    "recommended_allocation": 0.3,  # 30% of credits
                    "strategic_advantage": "market_control"
                },
                priority=RecommendationPriority.HIGH,
                risk_assessment=RiskAssessment.LOW,
                expected_outcome={
                    "type": "strategic_advantage",
                    "value": "income_diversification",
                    "long_term_benefit": True
                },
                confidence=0.85,
                expires_at=datetime.utcnow() + timedelta(days=14),
                security_clearance_required=assistant.security_level
            )
            recommendations.append(rec)
        
        return recommendations

    async def _analyze_player_strategic_position(self, player_id: uuid.UUID) -> Dict[str, Any]:
        """
        Analyze player's overall strategic position across all systems
        """
        # Get player data
        stmt = select(Player).where(Player.id == player_id)
        result = await self.db.execute(stmt)
        player = result.scalar_one()
        
        # Check station ownership
        stmt = select(func.count(Station.id)).where(Station.owner_id == player_id)
        result = await self.db.execute(stmt)
        station_count = result.scalar()

        # Check planet ownership
        stmt = select(func.count(Planet.id)).where(Planet.owner_id == player_id)
        result = await self.db.execute(stmt)
        planet_count = result.scalar()

        # Check fleet strength
        stmt = select(func.count(Fleet.id)).where(
            Fleet.commander_id == player_id,
            Fleet.disbanded_at.is_(None)
        )
        result = await self.db.execute(stmt)
        fleet_count = result.scalar()

        return {
            "credits": player.credits,
            "owns_stations": station_count > 0,
            "station_count": station_count,
            "planet_count": planet_count,
            "fleet_count": fleet_count,
            "strategic_diversity": len([x for x in [station_count, planet_count, fleet_count] if x > 0])
        }

    # =============================================================================
    # NATURAL LANGUAGE CONVERSATION INTERFACE
    # =============================================================================

    async def process_natural_language_query(self, player_id: uuid.UUID, user_input: str,
                                           conversation_context: ConversationContext = None,
                                           conversation_id: str = None) -> Dict[str, Any]:
        """
        Process natural language queries with comprehensive AI intelligence
        Extends ARIA's chat interface to all game systems
        """
        # Security validation and input sanitization
        assistant = await self._validate_and_authenticate(player_id)
        # Capture the assistant id up front: _generate_ai_response's trading
        # path commits mid-request, expiring this ORM object — a later
        # assistant.id access (conversation/security logging) would then trigger
        # a sync lazy-reload on the async session and raise greenlet_spawn.
        assistant_id = assistant.id
        sanitized_input = self._sanitize_user_input(user_input)
        
        if not sanitized_input:
            raise ValueError("Empty or invalid input")
        
        try:
            # Create conversation context if not provided. Built HERE (not by the
            # caller) because only this method has the authenticated assistant id
            # — ConversationContext validation rejects an empty assistant_id.
            # Reuse the client's conversation_id only when it is a valid UUID
            # (session_id is later parsed with uuid.UUID in _log_conversation);
            # otherwise mint a fresh thread id.
            if not conversation_context:
                try:
                    session_id = str(uuid.UUID(conversation_id)) if conversation_id else str(uuid.uuid4())
                except (ValueError, TypeError, AttributeError):
                    session_id = str(uuid.uuid4())
                conversation_context = ConversationContext(
                    session_id=session_id,
                    conversation_type="query",
                    player_id=str(player_id),
                    assistant_id=str(assistant_id),
                    security_level=assistant.security_level
                )
            
            # Analyze user intent
            intent_analysis = await self._analyze_user_intent(sanitized_input, conversation_context)
            
            # Generate response based on intent
            response = await self._generate_ai_response(intent_analysis, assistant, conversation_context)
            
            # SECURITY: Sanitize AI response content
            response = self._sanitize_response(response)
            
            # Log conversation for learning and audit
            await self._log_conversation(assistant_id, sanitized_input, response, conversation_context)
            
            return {
                "response": response,
                "intent": intent_analysis,
                "conversation_id": conversation_context.session_id,
                "response_time": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            await self._log_security_event(
                "data_access", "error",
                f"Failed to process natural language query: {str(e)}",
                assistant_id=assistant_id, player_id=player_id
            )
            logger.error(f"Error processing natural language query: {e}")
            
            # Return safe error response
            return {
                "response": "I'm temporarily unable to process your request. Please try again later.",
                "error": "processing_error",
                "conversation_id": conversation_context.session_id if conversation_context else None,
                "response_time": datetime.utcnow().isoformat()
            }

    async def _analyze_user_intent(self, user_input: str, context: ConversationContext) -> Dict[str, Any]:
        """
        Analyze user intent from natural language input
        Enhanced version of ARIA's intent recognition
        """
        # Convert to lowercase for analysis
        input_lower = user_input.lower()
        
        # Define intent patterns. Keyword lists include the root words AND common
        # word-forms (e.g. "colony"/"colonies", "strategy"/"strategic") because a
        # plain substring test would otherwise miss "colonies" or "combat".
        intent_patterns = {
            "trading": ["trade", "trading", "buy", "sell", "price", "profit", "route", "commodity", "market", "cargo"],
            "combat": ["combat", "battle", "fight", "attack", "defend", "fleet", "tactical", "formation", "war", "weapon", "shield", "drone", "engage", "threat", "readiness"],
            "colony": ["colony", "colonies", "colonist", "planet", "terraform", "genesis", "population", "develop", "settle", "habitability"],
            "station": ["station", "port", "investment", "invest", "acquire", "ownership", "dock"],
            "strategic": ["strategic", "strategy", "plan", "recommend", "advice", "next move", "best option", "should i", "overall", "position", "focus", "priorit"],
            "navigation": ["go to", "travel", "navigate", "warp", "path", "heading", "direction", "jump"],
            "help": ["help", "how to", "what is", "explain", "tutorial", "guide", "what can you"]
        }

        # Score each intent by raw count of matched keywords (most matches wins).
        # Normalizing by list length penalized intents with richer keyword sets.
        intent_scores = {}
        for intent, keywords in intent_patterns.items():
            score = sum(1 for keyword in keywords if keyword in input_lower)
            if score > 0:
                intent_scores[intent] = score

        # Determine primary intent
        primary_intent = max(intent_scores.items(), key=lambda x: x[1])[0] if intent_scores else "general"
        confidence = min(1.0, 0.5 + 0.2 * intent_scores.get(primary_intent, 0))
        
        # Extract entities (sectors, commodities, etc.)
        entities = self._extract_entities(user_input)
        
        return {
            "primary_intent": primary_intent,
            "confidence": confidence,
            "all_intents": intent_scores,
            "entities": entities,
            "original_input": user_input,
            "sanitized_input": user_input  # Already sanitized
        }

    def _extract_entities(self, user_input: str) -> Dict[str, List[str]]:
        """
        Extract entities from user input (sectors, commodities, etc.)
        """
        entities = {
            "sectors": [],
            "commodities": [],
            "numbers": [],
            "actions": []
        }
        
        # Extract sector references (e.g., "sector 15", "15-A", etc.)
        sector_pattern = r'sector\s*(\d+[-]?[a-z]?)'
        sectors = re.findall(sector_pattern, user_input, re.IGNORECASE)
        entities["sectors"] = sectors
        
        # Extract commodity names
        commodities = ["ore", "organics", "equipment", "fuel", "luxury", "food", "technology", "colonists"]
        for commodity in commodities:
            if commodity in user_input.lower():
                entities["commodities"].append(commodity)
        
        # Extract numbers
        numbers = re.findall(r'\d+', user_input)
        entities["numbers"] = numbers
        
        return entities

    async def _generate_ai_response(self, intent_analysis: Dict[str, Any], 
                                  assistant: AIComprehensiveAssistant,
                                  context: ConversationContext) -> str:
        """
        Generate intelligent AI response based on intent analysis
        Coordinates responses across all game systems
        """
        primary_intent = intent_analysis["primary_intent"]
        entities = intent_analysis["entities"]
        
        try:
            if primary_intent == "trading":
                return await self._generate_trading_response(assistant, entities, context)
            elif primary_intent == "combat":
                return await self._generate_combat_response(assistant, entities, context)
            elif primary_intent == "colony":
                return await self._generate_colony_response(assistant, entities, context)
            elif primary_intent == "station":
                return await self._generate_station_response(assistant, entities, context)
            elif primary_intent == "strategic":
                return await self._generate_strategic_response(assistant, entities, context)
            elif primary_intent == "help":
                return await self._generate_help_response(assistant, entities, context)
            else:
                return await self._generate_general_response(assistant, entities, context)
                
        except Exception as e:
            logger.error(f"Error generating AI response: {e}")
            return "I encountered an issue processing your request. Could you please rephrase your question?"

    async def _generate_trading_response(self, assistant: AIComprehensiveAssistant,
                                       entities: Dict[str, List[str]], context: ConversationContext) -> str:
        """
        Generate trading-specific response using ARIA's intelligence
        """
        if not assistant.has_permission("trading"):
            return "I don't currently have access to trading information. Please check your AI assistant permissions."
        
        # Get trading recommendations using existing ARIA
        recommendations = await self._get_trading_recommendations(assistant, 3)
        
        if recommendations:
            response = "Based on current market analysis, here are my top trading recommendations:\n\n"
            for i, rec in enumerate(recommendations[:3], 1):
                response += f"{i}. {rec.title}\n"
                response += f"   Expected profit: {rec.expected_outcome.get('value', 0):,.0f} credits\n"
                response += f"   Confidence: {rec.confidence*100:.0f}%\n"
                response += f"   Risk: {rec.risk_assessment.value.replace('_', ' ').title()}\n\n"
            
            response += "Would you like detailed analysis on any of these opportunities?"
        else:
            response = "I'm currently analyzing market conditions. No specific trading opportunities meet my confidence threshold right now. Check back in a few minutes for updated recommendations."
        
        return response

    async def _generate_combat_response(self, assistant: AIComprehensiveAssistant,
                                      entities: Dict[str, List[str]], context: ConversationContext) -> str:
        """
        Generate combat tactical response
        """
        # Advisory responses (read-only analysis of the player's own holdings) are
        # open to any assistant — no per-domain permission gate.
        try:
            player = (await self.db.execute(
                select(Player).where(Player.id == assistant.player_id))).scalar_one()
            fleet_count = (await self.db.execute(
                select(func.count(Fleet.id)).where(
                    Fleet.commander_id == assistant.player_id,
                    Fleet.disbanded_at.is_(None)))).scalar() or 0
            try:
                recs = await self._get_combat_recommendations(assistant, 3)
            except Exception as rec_err:
                logger.warning(f"combat recommendations unavailable: {rec_err}")
                recs = []

            lines = [
                "Here's your combat picture, Commander:",
                f"• Military rank: {str(player.military_rank).replace('_', ' ').title()}",
                f"• Defense drones: {player.defense_drones}",
                f"• Active fleets: {fleet_count}",
            ]
            if recs:
                lines.append("")
                lines.append("Tactical recommendations:")
                for i, r in enumerate(recs, 1):
                    lines.append(f"{i}. {r.title} — {r.summary}")
            else:
                lines.append("")
                lines.append(
                    "No active battles. Keep your shields charged and drones stocked, watch your "
                    "sector contacts for hostiles, and only engage targets you can out-gun — note "
                    "that attacking reputable traders or fleeing escape pods costs you reputation."
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error building combat response: {e}")
            return (
                "Keep your shields up and your drones stocked. Engage only targets you can beat, "
                "disengage early if a fight turns against you, and remember that attacking innocents "
                "carries a reputation penalty."
            )

    async def _generate_colony_response(self, assistant: AIComprehensiveAssistant,
                                      entities: Dict[str, List[str]], context: ConversationContext) -> str:
        """
        Generate colonization response
        """
        # Advisory: read-only, open to any assistant.
        try:
            planets = (await self.db.execute(
                select(Planet).where(Planet.owner_id == assistant.player_id))).scalars().all()
            if not planets:
                return (
                    "You haven't founded any colonies yet. Land on an unclaimed world and claim it "
                    "to establish your first — you'll receive a founding grant and a level-1 citadel. "
                    "High-habitability oceanic and terran worlds make the strongest starts; pick up "
                    "colonists from a population hub like New Earth and ferry them out to settle."
                )
            lines = [f"You command {len(planets)} colon{'y' if len(planets) == 1 else 'ies'}:"]
            for p in planets[:5]:
                cap = int((p.population / p.max_population) * 100) if p.max_population else 0
                lines.append(f"• {p.name}: {p.population:,}/{p.max_population:,} colonists ({cap}% of capacity)")
            recs = await self._get_colonization_recommendations(assistant, 3)
            lines.append("")
            if recs:
                lines.append("Development advice:")
                for i, r in enumerate(recs, 1):
                    lines.append(f"{i}. {r.summary}")
            else:
                lines.append(
                    "Your colonies are near capacity. Raise habitability through terraforming to lift "
                    "the population ceiling, and invest in citadel defenses to protect your output."
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error building colony response: {e}")
            return (
                "For colony growth: ferry colonists from a population hub to your worlds, terraform to "
                "raise habitability and the population ceiling, and build citadel defenses to protect them."
            )

    async def _generate_station_response(self, assistant: AIComprehensiveAssistant,
                                    entities: Dict[str, List[str]], context: ConversationContext) -> str:
        """
        Generate station ownership response
        """
        # Advisory: read-only, open to any assistant.
        try:
            owned = (await self.db.execute(
                select(Station).where(Station.owner_id == assistant.player_id))).scalars().all()
            lines: List[str] = []
            if owned:
                lines.append(f"You own {len(owned)} station{'s' if len(owned) != 1 else ''}:")
                for s in owned[:5]:
                    lines.append(f"• {s.name} (sector {s.sector_id})")
            else:
                lines.append(
                    "You don't own any stations yet — owning one turns a trade hub into passive income "
                    "from transaction fees."
                )
            # Investment opportunities (the helper touches several optional Station
            # fields; degrade gracefully if any are absent on this deployment).
            try:
                recs = await self._get_station_recommendations(assistant, 3)
            except Exception as rec_err:
                logger.warning(f"station recommendations unavailable: {rec_err}")
                recs = []
            if recs:
                lines.append("")
                lines.append("Investment opportunities:")
                for i, r in enumerate(recs, 1):
                    lines.append(f"{i}. {r.summary}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error building station response: {e}")
            return (
                "Station ownership earns passive income from transaction fees — look for ownable ports "
                "in high-traffic sectors where the trade volume pays back the acquisition cost quickly."
            )

    async def _generate_strategic_response(self, assistant: AIComprehensiveAssistant,
                                         entities: Dict[str, List[str]], context: ConversationContext) -> str:
        """
        Generate strategic planning response
        """
        try:
            pos = await self._analyze_player_strategic_position(assistant.player_id)
            lines = [
                "Strategic overview across your operations:",
                f"• Credits: {pos['credits']:,}",
                f"• Stations owned: {pos['station_count']}",
                f"• Colonies: {pos['planet_count']}",
                f"• Fleets: {pos['fleet_count']}",
                f"• Diversification: {pos['strategic_diversity']}/3 domains active",
            ]
            recs = await self._get_strategic_recommendations(assistant, 3)
            lines.append("")
            lines.append("Strategic moves:")
            if recs:
                for i, r in enumerate(recs, 1):
                    lines.append(f"{i}. {r.title} — {r.summary}")
            else:
                tips = []
                if pos["credits"] > 1_000_000 and pos["station_count"] == 0:
                    tips.append("Your reserves are strong — acquiring a station would diversify into passive income.")
                if pos["planet_count"] == 0:
                    tips.append("You hold no colonies; founding one secures long-term production and growth.")
                if pos["strategic_diversity"] < 2:
                    tips.append("You're concentrated in one domain — spreading across trade, colonies, and fleets hedges risk.")
                if not tips:
                    tips.append("You're well diversified. Press your advantage where your rank and reputation open the best markets.")
                for i, tip in enumerate(tips, 1):
                    lines.append(f"{i}. {tip}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error building strategic response: {e}")
            return (
                "Think across systems: keep credits flowing from trade, convert surplus into colonies and "
                "stations for passive income, and maintain a fleet strong enough to defend it all. Diversify "
                "so no single setback can cripple you."
            )

    async def _generate_help_response(self, assistant: AIComprehensiveAssistant,
                                    entities: Dict[str, List[str]], context: ConversationContext) -> str:
        """
        Generate help response
        """
        return """I'm ARIA, your AI assistant for Sectorwars2102! I can help you with:

🔹 **Trading**: Market analysis, route optimization, profit recommendations
🔹 **Combat**: Tactical advice, fleet coordination (coming soon)
🔹 **Colonization**: Terraforming guidance, development planning (coming soon)  
🔹 **Station Management**: Investment analysis, revenue optimization (coming soon)
🔹 **Strategic Planning**: Cross-system coordination and long-term strategy (coming soon)

Try asking me:
• "What's the best trade route right now?"
• "Help me plan my next strategic move"
• "Should I buy that port in sector 15?"

What would you like help with today?"""

    async def _generate_general_response(self, assistant: AIComprehensiveAssistant,
                                       entities: Dict[str, List[str]], context: ConversationContext) -> str:
        """
        Generate general response for unclear intent
        """
        return "I'm here to help with your space trading strategy! You can ask me about trading opportunities, market analysis, strategic planning, or say 'help' to see what I can do."

    async def _log_conversation(self, assistant_id: uuid.UUID, user_input: str,
                               ai_response: str, context: ConversationContext):
        """
        Log conversation for audit and learning purposes
        GDPR-compliant with automatic expiration

        Takes assistant_id (not the ORM object) so it never touches an
        attribute that may have been expired by a mid-request commit.
        """
        try:
            conversation_log = AIConversationLog(
                assistant_id=assistant_id,
                session_id=uuid.UUID(context.session_id),
                conversation_type=context.conversation_type,
                interaction_sequence=len(context.conversation_history) + 1,
                user_input_sanitized=user_input,
                ai_response_text=ai_response,
                response_type="answer",
                response_confidence=0.8,  # Default confidence
                response_time_ms=100,  # Placeholder
                conversation_context={
                    "topic": context.current_topic,
                    # security_level may be a SecurityLevel enum or already a raw
                    # string depending on the entry point — handle both.
                    "security_level": getattr(context.security_level, "value", context.security_level)
                },
                privacy_level="standard",
                data_retention_days=365  # 1 year retention
            )
            
            self.db.add(conversation_log)
            # Commit handled by calling transaction
            
        except Exception as e:
            logger.error(f"Failed to log conversation: {e}")
            # Don't raise - logging failure shouldn't break conversation

    # =============================================================================
    # AI LEARNING AND PATTERN RECOGNITION
    # =============================================================================

    async def record_player_action(self, player_id: uuid.UUID, action_type: str, 
                                 action_data: Dict[str, Any], outcome: Dict[str, Any] = None):
        """
        Record player actions for AI learning and pattern recognition
        Secure data collection with validation
        """
        try:
            assistant = await self._validate_and_authenticate(player_id)
            
            # Validate and sanitize action data
            validated_data = self._validate_jsonb_data(action_data, max_size=16384)  # 16KB limit
            validated_outcome = self._validate_jsonb_data(outcome or {}, max_size=8192)  # 8KB limit
            
            # Store knowledge based on action type
            knowledge_domain = self._map_action_to_domain(action_type)
            
            knowledge = AICrossSystemKnowledge(
                assistant_id=assistant.id,
                knowledge_domain=knowledge_domain,
                knowledge_type=action_type,
                knowledge_data={
                    "action": validated_data,
                    "outcome": validated_outcome,
                    "timestamp": datetime.utcnow().isoformat()
                },
                confidence_score=0.7,  # Initial confidence
                data_source="player_action",
                source_metadata={
                    "player_id": str(player_id),
                    "action_type": action_type
                },
                security_classification=SecurityClassification.INTERNAL,
                data_sensitivity=DataSensitivity.LOW
            )
            
            self.db.add(knowledge)
            
            # Update assistant interaction count
            assistant.total_interactions += 1
            assistant.last_active = datetime.utcnow()
            
            await self._log_security_event(
                "pattern_learning", "info",
                f"Recorded player action: {action_type}",
                assistant_id=assistant.id, player_id=player_id,
                event_data={"action_type": action_type}
            )
            
        except Exception as e:
            logger.error(f"Error recording player action: {e}")
            await self._log_security_event(
                "pattern_learning", "error",
                f"Failed to record player action: {str(e)}",
                player_id=player_id
            )

    def _map_action_to_domain(self, action_type: str) -> str:
        """
        Map action types to AI knowledge domains
        """
        domain_mapping = {
            "trade": "trading",
            "market_transaction": "trading",
            "sector_travel": "strategic",
            "combat": "combat",
            "fleet_action": "combat",
            "planet_colonization": "colony",
            "terraforming": "colony",
            "station_purchase": "station",
            "station_management": "station"
        }
        
        return domain_mapping.get(action_type, "strategic")

    # =============================================================================
    # SYSTEM MAINTENANCE AND OPTIMIZATION
    # =============================================================================

    async def cleanup_expired_data(self) -> int:
        """
        Clean up expired AI data for GDPR compliance and performance
        """
        try:
            deleted_count = 0
            
            # Clean up expired conversations
            stmt = text("""
                DELETE FROM ai_conversation_logs 
                WHERE created_at + INTERVAL '1 day' * data_retention_days < NOW()
            """)
            result = await self.db.execute(stmt)
            deleted_count += result.rowcount
            
            # Clean up expired knowledge
            stmt = text("""
                DELETE FROM ai_cross_system_knowledge 
                WHERE expiry_date IS NOT NULL AND expiry_date < NOW()
            """)
            result = await self.db.execute(stmt)
            deleted_count += result.rowcount
            
            # Clean up old audit logs (keep 2 years for compliance)
            stmt = text("""
                DELETE FROM ai_security_audit_log 
                WHERE created_at < NOW() - INTERVAL '2 years'
                AND severity_level NOT IN ('error', 'critical')
            """)
            result = await self.db.execute(stmt)
            deleted_count += result.rowcount
            
            logger.info(f"Cleaned up {deleted_count} expired AI records")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error cleaning up expired AI data: {e}")
            return 0

    async def get_ai_performance_metrics(self, assistant_id: uuid.UUID) -> Dict[str, Any]:
        """
        Get comprehensive AI performance metrics for monitoring
        """
        try:
            assistant = await self.db.get(AIComprehensiveAssistant, assistant_id)
            if not assistant:
                raise ValueError("Assistant not found")
            
            # Get recommendation accuracy
            stmt = select(
                func.count(AIStrategicRecommendation.id).label("total_recommendations"),
                func.avg(AIStrategicRecommendation.outcome_accuracy).label("avg_accuracy"),
                func.avg(AIStrategicRecommendation.user_feedback_score).label("avg_satisfaction")
            ).where(
                AIStrategicRecommendation.assistant_id == assistant_id,
                AIStrategicRecommendation.outcome_tracked == True
            )
            result = await self.db.execute(stmt)
            rec_metrics = result.first()
            
            # Get learning pattern success rates
            stmt = select(
                func.count(AILearningPattern.id).label("total_patterns"),
                func.avg(AILearningPattern.success_rate).label("avg_success_rate")
            ).where(
                AILearningPattern.assistant_id == assistant_id,
                AILearningPattern.is_active == True
            )
            result = await self.db.execute(stmt)
            pattern_metrics = result.first()
            
            return {
                "assistant_id": str(assistant_id),
                "total_interactions": assistant.total_interactions,
                "api_usage": {
                    "quota": assistant.api_request_quota,
                    "used": assistant.api_requests_used,
                    "remaining": assistant.quota_remaining
                },
                "recommendation_metrics": {
                    "total": rec_metrics.total_recommendations or 0,
                    "accuracy": float(rec_metrics.avg_accuracy or 0),
                    "user_satisfaction": float(rec_metrics.avg_satisfaction or 0)
                },
                "learning_metrics": {
                    "total_patterns": pattern_metrics.total_patterns or 0,
                    "avg_success_rate": float(pattern_metrics.avg_success_rate or 0)
                },
                "security_level": assistant.security_level.value,
                "last_active": assistant.last_active.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting AI performance metrics: {e}")
            return {"error": "Unable to retrieve metrics"}


# =============================================================================
# UTILITY FUNCTIONS FOR SERVICE INTEGRATION
# =============================================================================

async def create_enhanced_ai_assistant(db: AsyncSession, player_id: uuid.UUID, 
                                     config: Dict[str, Any] = None) -> AIComprehensiveAssistant:
    """
    Create new enhanced AI assistant with secure defaults
    """
    config = config or {}
    
    assistant = AIComprehensiveAssistant(
        player_id=player_id,
        assistant_name=config.get("name", "ARIA"),
        personality_type=config.get("personality", "analytical"),
        security_level=config.get("security_level", SecurityLevel.STANDARD),
        access_permissions=config.get("permissions", {
            "trading": True,
            "combat": False,
            "colony": False,
            "station": False
        })
    )
    
    db.add(assistant)
    await db.flush()
    
    # Log creation
    audit_log = AISecurityAuditLog.log_event(
        "access", "info", 
        "Enhanced AI assistant created",
        assistant_id=assistant.id, player_id=player_id
    )
    db.add(audit_log)
    
    return assistant


def validate_ai_permission(assistant: AIComprehensiveAssistant, required_permission: str) -> bool:
    """
    Validate AI assistant has required permission for operation
    """
    return assistant.has_permission(required_permission)


def get_security_clearance_level(security_level: SecurityLevel) -> int:
    """
    Get numeric security clearance level for access control
    """
    levels = {
        SecurityLevel.BASIC: 1,
        SecurityLevel.STANDARD: 2,
        SecurityLevel.PREMIUM: 3,
        SecurityLevel.ENTERPRISE: 4
    }
    return levels.get(security_level, 1)