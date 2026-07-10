"""
Enhanced AI Provider Service with Robust Fallback Chain

This service provides a unified interface for multiple AI providers with intelligent
fallback logic. It prioritizes OpenAI (cheaper) over Anthropic (quality), with 
sophisticated manual fallback that includes cat boost and ship tier logic.
"""

import asyncio
import logging
import os
import random
import re
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Tuple, Union
from dataclasses import dataclass
from enum import Enum

from src.services.ai_dialogue_service import (
    DialogueContext, ResponseAnalysis, GuardResponse, GuardMood, ShipType
)

logger = logging.getLogger(__name__)

# Try imports with graceful fallbacks
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI library not available")

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("Anthropic library not available")


class ProviderType(Enum):
    """Available AI provider types"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    MANUAL = "manual"


@dataclass
class AIGenerationMetadata:
    """Metadata captured from AI generation for logging and debugging"""
    provider: str  # 'openai', 'anthropic', 'fallback'
    system_prompt: str
    user_prompt: str
    raw_response: str
    response_time_ms: int
    estimated_cost_usd: float
    tokens_used: int = 0
    model_used: str = ""


@dataclass
class ProviderConfig:
    """Configuration for AI providers"""
    primary_provider: ProviderType = ProviderType.OPENAI
    secondary_provider: ProviderType = ProviderType.ANTHROPIC
    fallback_provider: ProviderType = ProviderType.MANUAL

    # OpenAI settings
    openai_model: str = "gpt-3.5-turbo"
    openai_temperature: float = 0.7

    # Anthropic settings
    anthropic_model: str = "claude-3-sonnet-20240229"
    anthropic_temperature: float = 0.7

    # Retry settings
    max_retries: int = 2
    retry_delay: float = 1.0


class AIProvider(ABC):
    """Abstract base class for AI providers"""
    
    @abstractmethod
    async def analyze_response(self, response: str, context: DialogueContext) -> ResponseAnalysis:
        """Analyze player response for persuasiveness and consistency"""
        pass
    
    @abstractmethod
    async def generate_question(self, context: DialogueContext, analysis: ResponseAnalysis) -> GuardResponse:
        """Generate guard question based on context and analysis"""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is available and configured"""
        pass
    
    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Return the provider type"""
        pass


class OpenAIProvider(AIProvider):
    """OpenAI GPT provider implementation"""
    
    def __init__(self, config: ProviderConfig):
        self.config = config
        self.api_key = os.getenv("OPENAI_API_KEY")
        if self.api_key and OPENAI_AVAILABLE:
            openai.api_key = self.api_key
    
    def is_available(self) -> bool:
        return OPENAI_AVAILABLE and bool(self.api_key)
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.OPENAI
    
    async def analyze_response(self, response: str, context: DialogueContext) -> ResponseAnalysis:
        """Analyze player response using OpenAI"""
        if not self.is_available():
            raise ValueError("OpenAI provider not available")
        
        # Build analysis prompt
        prompt = self._build_analysis_prompt(response, context)
        
        try:
            client = openai.OpenAI(api_key=self.api_key)
            completion = client.chat.completions.create(
                model=self.config.openai_model,
                messages=[
                    {"role": "system", "content": "You are an expert analyst evaluating dialogue for a space trading game."},
                    {"role": "user", "content": prompt}
                ],
                temperature=self.config.openai_temperature,
                max_tokens=500
            )
            
            analysis_text = completion.choices[0].message.content
            return self._parse_analysis_response(analysis_text, context)
            
        except Exception as e:
            logger.error(f"OpenAI analysis failed: {e}")
            raise
    
    async def generate_question(self, context: DialogueContext, analysis: ResponseAnalysis) -> GuardResponse:
        """Generate guard question using OpenAI with enhanced prompts"""
        if not self.is_available():
            raise ValueError("OpenAI provider not available")

        # Use enhanced prompt system if guard personality available
        if hasattr(context, 'guard_name') and context.guard_name:
            prompts = self._build_enhanced_question_prompt(context, analysis)

            try:
                client = openai.OpenAI(api_key=self.api_key)
                completion = client.chat.completions.create(
                    model=self.config.openai_model,
                    messages=[
                        {"role": "system", "content": prompts["system"]},
                        {"role": "user", "content": prompts["user"]}
                    ],
                    temperature=self.config.openai_temperature,
                    max_tokens=300  # More tokens for dynamic ending detection
                )

                response_text = completion.choices[0].message.content
                return self._parse_enhanced_question_response(response_text, context, analysis)

            except Exception as e:
                logger.error(f"OpenAI enhanced question generation failed: {e}")
                raise
        else:
            # Fallback to basic prompt (legacy support)
            prompt = self._build_question_prompt(context, analysis)

            try:
                client = openai.OpenAI(api_key=self.api_key)
                completion = client.chat.completions.create(
                    model=self.config.openai_model,
                    messages=[
                        {"role": "system", "content": "You are a suspicious security guard in a 2102 space station shipyard."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=self.config.openai_temperature,
                    max_tokens=200
                )

                question_text = completion.choices[0].message.content
                return self._parse_question_response(question_text, context, analysis)

            except Exception as e:
                logger.error(f"OpenAI question generation failed: {e}")
                raise
    
    def _build_analysis_prompt(self, response: str, context: DialogueContext) -> str:
        """Build prompt for response analysis"""
        return f"""
Analyze this player response in a space station security scenario:

Player's Response: "{response}"
Claimed Ship: {context.claimed_ship.value.replace("_", " ").title()}
Guard Mood: {context.guard_mood.value}
Previous Inconsistencies: {len(context.inconsistencies)}

Rate on scale 0.0-1.0:
1. Persuasiveness (how convincing)
2. Confidence (how sure they sound)  
3. Consistency (matches previous claims)
4. Negotiation skill (quality of argument)
5. Overall believability

Also extract:
- Any specific claims made
- Contradictions with previous statements
- Suggested guard mood response

Format as JSON with keys: persuasiveness, confidence, consistency, negotiation_skill, 
believability, claims, inconsistencies, guard_mood
"""
    
    def _build_enhanced_question_prompt(self, context: DialogueContext, analysis: ResponseAnalysis) -> Dict[str, str]:
        """Build enhanced prompt with guard personality and conversation history"""
        from src.services.ai_prompts import FirstLoginAIPrompts

        # Calculate current scores
        current_persuasiveness = analysis.persuasiveness_score
        current_confidence = analysis.confidence_level
        current_consistency = analysis.consistency_score
        current_believability = analysis.overall_believability

        # Get conversation history count
        question_count = len([ex for ex in context.dialogue_history if ex.get('player')])

        # Convert dialogue history to simple dict format
        history_list = [
            {"npc": ex.get('npc', ''), "player": ex.get('player', '')}
            for ex in context.dialogue_history
        ]

        # Use the actual claimed ship name (what player sees) instead of mapped AIShipType
        claimed_ship_name = context.claimed_ship_display_name or context.claimed_ship.value.replace("_", " ").title()

        return FirstLoginAIPrompts.build_question_generation_prompt(
            guard_name=context.guard_name,
            guard_title=context.guard_title,
            guard_trait=context.guard_trait,
            guard_description=context.guard_description,
            guard_base_suspicion=context.guard_base_suspicion,
            claimed_ship=claimed_ship_name,
            ship_tier=self._get_ship_tier(context.claimed_ship),
            conversation_history=history_list,
            current_believability=current_believability,
            current_persuasiveness=current_persuasiveness,
            current_confidence=current_confidence,
            current_consistency=current_consistency,
            detected_contradictions=analysis.detected_inconsistencies,
            question_count=question_count
        )

    def _parse_enhanced_question_response(self, response_text: str, context: DialogueContext, analysis: ResponseAnalysis) -> GuardResponse:
        """Parse enhanced AI response - detects DECISION: for dynamic ending"""
        response_text = response_text.strip()

        # Check if AI wants to end the conversation
        if response_text.upper().startswith("DECISION:"):
            # Extract decision and reasoning
            decision_text = response_text[9:].strip()  # Remove "DECISION:"

            # Determine if approve or deny based on keywords
            is_approval = any(word in decision_text.upper() for word in ["APPROVE", "CONVINCED", "BELIEVE", "LET THEM", "GRANT"])

            # Create final guard response
            return GuardResponse(
                dialogue_text=decision_text,
                mood=GuardMood.CONVINCED if is_approval else GuardMood.VERY_SUSPICIOUS,
                suspicion_level=0.0 if is_approval else 1.0,
                is_final_decision=True,  # Signal that conversation should end
                outcome="success" if is_approval else "failure"
            )
        else:
            # Normal question - continue conversation
            mood = self._determine_mood(analysis.overall_believability)
            suspicion = 1.0 - analysis.overall_believability  # Inverse of believability
            return GuardResponse(
                dialogue_text=response_text,
                mood=mood,
                suspicion_level=suspicion,
                is_final_decision=False,
                outcome="continue"
            )

    def _build_question_prompt(self, context: DialogueContext, analysis: ResponseAnalysis) -> str:
        """Build prompt for question generation (legacy fallback)"""
        ship_value = self._get_ship_tier(context.claimed_ship)
        return f"""
You are a security guard questioning someone claiming to own a {context.claimed_ship.value.replace("_", " ").title()}.
This is a Tier {ship_value} ship (higher = more valuable/suspicious).

Current situation:
- Guard mood: {context.guard_mood.value}
- Player believability: {analysis.overall_believability:.1f}/1.0
- Inconsistencies found: {len(analysis.detected_inconsistencies)}
- Security protocol: {context.security_protocol_level}

Generate a follow-up question that:
1. Matches guard's current suspicion level
2. Is appropriate for someone claiming this ship type
3. Probes for specific details to verify their claim
4. Escalates pressure if inconsistencies detected

Keep response under 150 characters. Be direct and authoritative.
"""

    def _determine_mood(self, believability: float) -> GuardMood:
        """Determine guard mood based on believability score"""
        if believability < 0.3:
            return GuardMood.VERY_SUSPICIOUS
        elif believability < 0.6:
            return GuardMood.SUSPICIOUS
        elif believability > 0.8:
            return GuardMood.CONVINCED
        else:
            return GuardMood.NEUTRAL
    
    def _parse_analysis_response(self, analysis_text: str, context: DialogueContext) -> ResponseAnalysis:
        """Parse OpenAI analysis response into structured data"""
        try:
            # Try to extract JSON from response
            import json
            
            # Look for JSON in the response
            json_match = re.search(r'\{.*\}', analysis_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                # Fallback parsing
                data = self._fallback_parse_analysis(analysis_text)
            
            # Ensure inconsistencies is always a list (AI sometimes returns 0 instead of [])
            inconsistencies_raw = data.get('inconsistencies', [])
            inconsistencies = inconsistencies_raw if isinstance(inconsistencies_raw, list) else []

            # Ensure claims is always a list
            claims_raw = data.get('claims', [])
            claims = claims_raw if isinstance(claims_raw, list) else []

            return ResponseAnalysis(
                persuasiveness_score=float(data.get('persuasiveness', 0.5)),
                confidence_level=float(data.get('confidence', 0.5)),
                consistency_score=float(data.get('consistency', 0.5)),
                negotiation_skill=float(data.get('negotiation_skill', 0.5)),
                detected_inconsistencies=inconsistencies,
                extracted_claims=claims,
                overall_believability=float(data.get('believability', 0.5)),
                suggested_guard_mood=GuardMood(data.get('guard_mood', 'neutral'))
            )
            
        except Exception as e:
            logger.error(f"Failed to parse OpenAI analysis: {e}")
            # Return default analysis
            return ResponseAnalysis(
                persuasiveness_score=0.5,
                confidence_level=0.5,
                consistency_score=0.5,
                negotiation_skill=0.5,
                detected_inconsistencies=[],
                extracted_claims=[],
                overall_believability=0.5,
                suggested_guard_mood=GuardMood.NEUTRAL
            )
    
    def _parse_question_response(self, question_text: str, context: DialogueContext, analysis: ResponseAnalysis) -> GuardResponse:
        """Parse OpenAI question response into structured data"""
        # Clean up the response
        question = question_text.strip().strip('"')
        
        # Determine guard mood based on analysis
        if analysis.overall_believability < 0.3:
            mood = GuardMood.VERY_SUSPICIOUS
        elif analysis.overall_believability < 0.6:
            mood = GuardMood.SUSPICIOUS
        elif analysis.overall_believability > 0.8:
            mood = GuardMood.CONVINCED
        else:
            mood = GuardMood.NEUTRAL
        
        return GuardResponse(
            dialogue_text=question,
            mood=mood,
            suspicion_level=1.0 - analysis.overall_believability,
            is_final_decision=False,
            outcome="continue"
        )
    
    def _fallback_parse_analysis(self, text: str) -> Dict[str, Any]:
        """Fallback parsing when JSON extraction fails"""
        # Extract numbers from text
        numbers = re.findall(r'(\d+\.?\d*)', text)
        scores = [float(n) for n in numbers if 0 <= float(n) <= 1]
        
        return {
            'persuasiveness': scores[0] if len(scores) > 0 else 0.5,
            'confidence': scores[1] if len(scores) > 1 else 0.5,
            'consistency': scores[2] if len(scores) > 2 else 0.5,
            'negotiation_skill': scores[3] if len(scores) > 3 else 0.5,
            'believability': scores[4] if len(scores) > 4 else 0.5,
            'claims': [],
            'inconsistencies': [],
            'guard_mood': 'neutral'
        }
    
    def _get_ship_tier(self, ship_type: ShipType) -> int:
        """Get ship tier for difficulty scaling"""
        tier_map = {
            ShipType.ESCAPE_POD: 1,
            ShipType.SCOUT_SHIP: 3,
            ShipType.CARGO_HAULER: 4,
            ShipType.MINING_VESSEL: 3,
            ShipType.PATROL_CRAFT: 5,
            ShipType.LUXURY_YACHT: 6
        }
        return tier_map.get(ship_type, 3)


class AnthropicProvider(AIProvider):
    """Anthropic Claude provider implementation"""
    
    def __init__(self, config: ProviderConfig):
        self.config = config
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if self.api_key and ANTHROPIC_AVAILABLE:
            self.client = anthropic.Anthropic(api_key=self.api_key)
        else:
            self.client = None
    
    def is_available(self) -> bool:
        return ANTHROPIC_AVAILABLE and bool(self.api_key)
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.ANTHROPIC
    
    async def analyze_response(self, response: str, context: DialogueContext) -> ResponseAnalysis:
        """Analyze player response using Anthropic Claude"""
        if not self.is_available():
            raise ValueError("Anthropic provider not available")
        
        prompt = self._build_analysis_prompt(response, context)
        
        try:
            message = self.client.messages.create(
                model=self.config.anthropic_model,
                max_tokens=500,
                temperature=self.config.anthropic_temperature,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            analysis_text = message.content[0].text
            return self._parse_analysis_response(analysis_text, context)
            
        except Exception as e:
            logger.error(f"Anthropic analysis failed: {e}")
            raise
    
    async def generate_question(self, context: DialogueContext, analysis: ResponseAnalysis) -> GuardResponse:
        """Generate guard question using Anthropic Claude with enhanced prompts"""
        if not self.is_available():
            raise ValueError("Anthropic provider not available")

        # Use same enhanced/basic prompt logic as OpenAI
        if hasattr(context, 'guard_name') and context.guard_name:
            # Use OpenAI provider's helper methods (shared logic)
            openai_helper = OpenAIProvider(self.config)
            prompts = openai_helper._build_enhanced_question_prompt(context, analysis)

            try:
                message = self.client.messages.create(
                    model=self.config.anthropic_model,
                    max_tokens=300,
                    temperature=self.config.anthropic_temperature,
                    system=prompts["system"],
                    messages=[
                        {"role": "user", "content": prompts["user"]}
                    ]
                )

                response_text = message.content[0].text
                return openai_helper._parse_enhanced_question_response(response_text, context, analysis)

            except Exception as e:
                logger.error(f"Anthropic enhanced question generation failed: {e}")
                raise
        else:
            # Legacy fallback
            prompt = self._build_question_prompt(context, analysis)

            try:
                message = self.client.messages.create(
                    model=self.config.anthropic_model,
                    max_tokens=200,
                    temperature=self.config.anthropic_temperature,
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )

                question_text = message.content[0].text
                return self._parse_question_response(question_text, context, analysis)

            except Exception as e:
                logger.error(f"Anthropic question generation failed: {e}")
                raise
    
    # Reuse OpenAI methods for prompt building and parsing
    def _build_analysis_prompt(self, response: str, context: DialogueContext) -> str:
        return OpenAIProvider._build_analysis_prompt(self, response, context)
    
    def _build_question_prompt(self, context: DialogueContext, analysis: ResponseAnalysis) -> str:
        return OpenAIProvider._build_question_prompt(self, context, analysis)
    
    def _parse_analysis_response(self, analysis_text: str, context: DialogueContext) -> ResponseAnalysis:
        return OpenAIProvider._parse_analysis_response(self, analysis_text, context)
    
    def _parse_question_response(self, question_text: str, context: DialogueContext, analysis: ResponseAnalysis) -> GuardResponse:
        return OpenAIProvider._parse_question_response(self, question_text, context, analysis)

    def _get_ship_tier(self, ship_type: ShipType) -> int:
        """Get ship tier for difficulty scaling - shared with OpenAI"""
        return OpenAIProvider._get_ship_tier(self, ship_type)


class AIProviderService:
    """Enhanced AI Provider Service with robust fallback chain"""

    def __init__(self, config: Optional[ProviderConfig] = None):
        self.config = config or ProviderConfig()

        # Initialize providers in priority order
        self.providers: List[AIProvider] = []

        # Store metadata from last generation for logging
        self.last_generation_metadata: Optional[AIGenerationMetadata] = None
        
        # Add providers based on configuration
        if self.config.primary_provider == ProviderType.OPENAI:
            self.providers.append(OpenAIProvider(self.config))
        elif self.config.primary_provider == ProviderType.ANTHROPIC:
            self.providers.append(AnthropicProvider(self.config))
        
        if self.config.secondary_provider == ProviderType.OPENAI:
            self.providers.append(OpenAIProvider(self.config))
        elif self.config.secondary_provider == ProviderType.ANTHROPIC:
            self.providers.append(AnthropicProvider(self.config))
        
        # Always add manual fallback last
        from src.services.enhanced_manual_provider import EnhancedManualProvider
        self.providers.append(EnhancedManualProvider(self.config))
        
        # Remove duplicates while preserving order
        seen = set()
        unique_providers = []
        for provider in self.providers:
            if provider.provider_type not in seen:
                unique_providers.append(provider)
                seen.add(provider.provider_type)
        self.providers = unique_providers
        
        logger.info(f"Initialized AI provider service with {len(self.providers)} providers: {[p.provider_type.value for p in self.providers]}")
    
    async def analyze_response(self, response: str, context: DialogueContext) -> Tuple[ResponseAnalysis, ProviderType]:
        """Analyze player response with fallback chain"""
        last_error = None
        
        for provider in self.providers:
            if not provider.is_available():
                logger.debug(f"Provider {provider.provider_type.value} not available, skipping")
                continue
            
            try:
                logger.debug(f"Attempting analysis with {provider.provider_type.value}")
                analysis = await provider.analyze_response(response, context)
                logger.info(f"Analysis successful with {provider.provider_type.value}")
                return analysis, provider.provider_type
                
            except Exception as e:
                logger.warning(f"Analysis failed with {provider.provider_type.value}: {e}")
                last_error = e
                
                # Add retry delay for non-manual providers
                if provider.provider_type != ProviderType.MANUAL:
                    await asyncio.sleep(self.config.retry_delay)
        
        # If we get here, all providers failed
        logger.error(f"All providers failed for analysis. Last error: {last_error}")
        raise RuntimeError(f"All AI providers failed: {last_error}")
    
    async def generate_question(self, context: DialogueContext, analysis: ResponseAnalysis) -> Tuple[GuardResponse, ProviderType]:
        """Generate guard question with fallback chain"""
        last_error = None
        
        for provider in self.providers:
            if not provider.is_available():
                logger.debug(f"Provider {provider.provider_type.value} not available, skipping")
                continue
            
            try:
                logger.debug(f"Attempting question generation with {provider.provider_type.value}")
                response = await provider.generate_question(context, analysis)
                logger.info(f"Question generation successful with {provider.provider_type.value}")
                return response, provider.provider_type
                
            except Exception as e:
                logger.warning(f"Question generation failed with {provider.provider_type.value}: {e}")
                last_error = e
                
                # Add retry delay for non-manual providers
                if provider.provider_type != ProviderType.MANUAL:
                    await asyncio.sleep(self.config.retry_delay)
        
        # If we get here, all providers failed
        logger.error(f"All providers failed for question generation. Last error: {last_error}")
        raise RuntimeError(f"All AI providers failed: {last_error}")
    
    async def generate_initial_scene(
        self,
        guard_name: str,
        guard_title: str,
        guard_trait: str,
        guard_description: str,
        guard_base_suspicion: float,
        available_ships: List[str]
    ) -> Tuple[str, ProviderType]:
        """
        Generate AI-powered initial scene with guard personality.
        Falls back through provider chain.
        Returns: (scene_text, provider_used)
        """
        from src.services.ai_prompts import FirstLoginAIPrompts

        prompts = FirstLoginAIPrompts.build_initial_scene_prompt(
            guard_name, guard_title, guard_trait, guard_description,
            guard_base_suspicion, available_ships
        )

        last_error = None

        for provider in self.providers:
            if not provider.is_available():
                continue

            # Skip manual provider for scene generation (no template exists)
            if provider.provider_type == ProviderType.MANUAL:
                continue

            try:
                logger.debug(f"Attempting scene generation with {provider.provider_type.value}")

                # Call the AI directly with our custom prompts
                if provider.provider_type == ProviderType.OPENAI:
                    scene_text = await self._call_openai_custom(prompts, max_tokens=300)
                elif provider.provider_type == ProviderType.ANTHROPIC:
                    scene_text = await self._call_anthropic_custom(prompts, max_tokens=300)
                else:
                    continue

                logger.info(f"Scene generation successful with {provider.provider_type.value}")
                return scene_text.strip(), provider.provider_type

            except Exception as e:
                logger.warning(f"Scene generation failed with {provider.provider_type.value}: {e}")
                last_error = e
                if provider.provider_type != ProviderType.MANUAL:
                    await asyncio.sleep(self.config.retry_delay)

        # All AI providers failed, return None to trigger manual fallback
        logger.error(f"All AI providers failed for scene generation: {last_error}")
        return None, ProviderType.MANUAL

    async def generate_outcome_text(
        self,
        guard_name: str,
        guard_title: str,
        guard_trait: str,
        outcome_type: str,
        claimed_ship: str,
        awarded_ship: str,
        final_score: float,
        negotiation_skill: str,
        conversation_history: List[Dict[str, str]]
    ) -> Tuple[str, ProviderType]:
        """
        Generate AI-powered outcome text with guard personality.
        Falls back through provider chain.
        Returns: (outcome_text, provider_used)
        """
        from src.services.ai_prompts import FirstLoginAIPrompts

        prompts = FirstLoginAIPrompts.build_outcome_generation_prompt(
            guard_name, guard_title, guard_trait, outcome_type,
            claimed_ship, awarded_ship, final_score, negotiation_skill,
            conversation_history
        )

        last_error = None

        for provider in self.providers:
            if not provider.is_available():
                continue

            # Skip manual provider (no template)
            if provider.provider_type == ProviderType.MANUAL:
                continue

            try:
                logger.debug(f"Attempting outcome generation with {provider.provider_type.value}")

                if provider.provider_type == ProviderType.OPENAI:
                    outcome_text = await self._call_openai_custom(prompts, max_tokens=200)
                elif provider.provider_type == ProviderType.ANTHROPIC:
                    outcome_text = await self._call_anthropic_custom(prompts, max_tokens=200)
                else:
                    continue

                logger.info(f"Outcome generation successful with {provider.provider_type.value}")
                return outcome_text.strip(), provider.provider_type

            except Exception as e:
                logger.warning(f"Outcome generation failed with {provider.provider_type.value}: {e}")
                last_error = e
                if provider.provider_type != ProviderType.MANUAL:
                    await asyncio.sleep(self.config.retry_delay)

        # All AI providers failed
        logger.error(f"All AI providers failed for outcome generation: {last_error}")
        return None, ProviderType.MANUAL

    async def generate_chat_reply(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int = 400,
    ) -> Tuple[Optional[str], ProviderType]:
        """WO-ARIA-CHAT-LLM — generic chat-completion entry point for
        ARIA's LLM-backed chat mode. Same chain-walk/skip-MANUAL/retry-
        delay shape as generate_initial_scene/generate_outcome_text
        (this class's own established generic-custom-prompt idiom) —
        reuses _call_openai_custom/_call_anthropic_custom rather than
        inventing new provider-call code, so the canon primary/secondary/
        fallback ordering (aria.md:130-141) is identical across every
        custom-prompt caller in this service.

        Returns (reply_text, provider_used) on success, or (None,
        ProviderType.MANUAL) when every AI provider is unavailable or
        failed — the caller (EnhancedAIService's LLM-path attempt) treats
        None as "fall back to the template engine", never raises through
        here. max_tokens=400 is [NO-CANON] — a reasonable chat-reply
        budget between generate_outcome_text's 200 and roughly double
        generate_initial_scene's 300; no token budget is specified
        anywhere in canon for this new path."""
        prompts = {"system": system_prompt, "user": user_prompt}
        last_error = None

        for provider in self.providers:
            if not provider.is_available():
                continue

            # No chat template lives in this service — the MANUAL provider
            # has nothing to generate; the CALLER owns the template-engine
            # fallback (mirrors generate_initial_scene/generate_outcome_text).
            if provider.provider_type == ProviderType.MANUAL:
                continue

            try:
                logger.debug(f"Attempting chat reply with {provider.provider_type.value}")

                if provider.provider_type == ProviderType.OPENAI:
                    reply_text = await self._call_openai_custom(prompts, max_tokens=max_tokens)
                elif provider.provider_type == ProviderType.ANTHROPIC:
                    reply_text = await self._call_anthropic_custom(prompts, max_tokens=max_tokens)
                else:
                    continue

                logger.info(f"Chat reply successful with {provider.provider_type.value}")
                return reply_text.strip(), provider.provider_type

            except Exception as e:
                logger.warning(f"Chat reply failed with {provider.provider_type.value}: {e}")
                last_error = e
                if provider.provider_type != ProviderType.MANUAL:
                    await asyncio.sleep(self.config.retry_delay)

        # All AI providers failed or unavailable.
        logger.error(f"All AI providers failed for chat reply: {last_error}")
        return None, ProviderType.MANUAL

    async def _call_openai_custom(self, prompts: Dict[str, str], max_tokens: int = 300) -> str:
        """Helper to call OpenAI with custom prompts"""
        if not OPENAI_AVAILABLE:
            raise ValueError("OpenAI not available")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key not configured")

        client = openai.OpenAI(api_key=api_key)
        completion = client.chat.completions.create(
            model=self.config.openai_model,
            messages=[
                {"role": "system", "content": prompts["system"]},
                {"role": "user", "content": prompts["user"]}
            ],
            temperature=self.config.openai_temperature,
            max_tokens=max_tokens
        )
        return completion.choices[0].message.content

    async def _call_anthropic_custom(self, prompts: Dict[str, str], max_tokens: int = 300) -> str:
        """Helper to call Anthropic with custom prompts"""
        if not ANTHROPIC_AVAILABLE:
            raise ValueError("Anthropic not available")

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("Anthropic API key not configured")

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=self.config.anthropic_model,
            max_tokens=max_tokens,
            temperature=self.config.anthropic_temperature,
            system=prompts["system"],
            messages=[
                {"role": "user", "content": prompts["user"]}
            ]
        )
        return message.content[0].text

    def get_available_providers(self) -> List[ProviderType]:
        """Get list of currently available providers"""
        return [p.provider_type for p in self.providers if p.is_available()]
    
    def is_ai_available(self) -> bool:
        """Check if any AI provider (non-manual) is available"""
        return any(p.is_available() and p.provider_type != ProviderType.MANUAL for p in self.providers)


# Global instance for the service
_ai_provider_service: Optional[AIProviderService] = None


def get_ai_provider_service() -> AIProviderService:
    """Get or create the global AI provider service instance"""
    global _ai_provider_service
    if _ai_provider_service is None:
        # Load configuration from environment
        config = ProviderConfig(
            primary_provider=ProviderType(os.getenv("AI_PROVIDER_PRIMARY", "openai")),
            secondary_provider=ProviderType(os.getenv("AI_PROVIDER_SECONDARY", "anthropic")),
            fallback_provider=ProviderType(os.getenv("AI_PROVIDER_FALLBACK", "manual")),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-3-sonnet-20240229")
        )
        _ai_provider_service = AIProviderService(config)
    
    return _ai_provider_service