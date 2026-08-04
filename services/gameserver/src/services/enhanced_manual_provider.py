"""
Enhanced Manual Provider with Cat Boost and Ship Tier Logic

This provider serves as the ultimate fallback when AI services are unavailable,
providing sophisticated rule-based dialogue simulation that includes:
- Cat mention detection and persuasion boost
- Ship tier-based difficulty scaling  
- Dynamic guard personality simulation
- Contextual response generation
"""

import random
import re
import logging
from typing import Dict, List
from dataclasses import dataclass

from src.services.ai_dialogue_service import (
    DialogueContext, ResponseAnalysis, GuardResponse, GuardMood, ShipType
)
from src.services.ai_provider_service import AIProvider, ProviderType

logger = logging.getLogger(__name__)


@dataclass
class GuardPersonality:
    """Simulated guard personality traits"""
    suspicion_base: float  # Base suspicion level (0.0-1.0)
    strictness: float      # How strict about rules (0.0-1.0)
    experience: float      # Years on the job affects questions (0.0-1.0)
    patience: float        # How long before getting annoyed (0.0-1.0)
    cat_affinity: float    # How much they like cats (affects cat boost)


class ShipTierInfo:
    """Information about ship tiers for difficulty scaling"""
    
    # Ship tier mapping (higher = more valuable/suspicious)
    SHIP_TIERS = {
        ShipType.ESCAPE_POD: 1,
        ShipType.SCOUT_SHIP: 3,
        ShipType.CARGO_HAULER: 4,
        ShipType.MINING_VESSEL: 3,
        ShipType.PATROL_CRAFT: 5,
        ShipType.LUXURY_YACHT: 6
    }
    
    # Base difficulty thresholds for each tier
    TIER_DIFFICULTY = {
        1: 0.3,  # Escape Pod - easy to claim
        2: 0.5,  # Light ships
        3: 0.6,  # Medium ships  
        4: 0.7,  # Heavy ships
        5: 0.8,  # Military ships
        6: 0.9,  # Luxury/rare ships
        7: 0.95  # Legendary ships
    }
    
    @classmethod
    def get_ship_tier(cls, ship_type: ShipType) -> int:
        """Get tier number for ship type"""
        return cls.SHIP_TIERS.get(ship_type, 3)
    
    @classmethod
    def get_base_difficulty(cls, ship_type: ShipType) -> float:
        """Get base difficulty threshold for ship type"""
        tier = cls.get_ship_tier(ship_type)
        return cls.TIER_DIFFICULTY.get(tier, 0.6)


class CatBoostDetector:
    """Detects cat mentions and calculates persuasion boost"""
    
    # Cat-related phrases for detection - specific patterns to avoid false positives
    CAT_PHRASES = [
        r"\bkitten\b", r"\bkittens\b", r"\bkitty\b", r"\bkitties\b",
        r"\bfeline\b", r"\bfelines\b", r"\borange cat\b",
        r"\bwhiskers\b", r"\bpurr\b", r"\bpurring\b", r"\bmeow\b",
        r"\bmeowing\b", r"\btabby\b", r"\bstray\b"
    ]
    
    # Special handling for "cat" and "cats" to avoid cargo/category false positives
    CAT_WORD_PATTERNS = [
        r"\bcat\b(?!\s*(?:alog|egory|astrophe|erpillar|hedral|egories))",  # cat but not catalog, category, etc.
        r"\bcats\b(?!\s*(?:alog|egory|astrophe|erpillar|hedral|egories))"   # cats but not in compound words
    ]
    
    # Context phrases that enhance cat boost
    CONTEXT_ENHANCERS = [
        r"cute", r"adorable", r"sweet", r"friendly", r"orange",
        r"little", r"small", r"darting", r"hiding", r"shadows"
    ]
    
    @classmethod
    def detect_cat_mention(cls, text: str) -> bool:
        """Detect if text mentions cats"""
        text_lower = text.lower()
        
        # Simple approach: check for cat/cats but exclude if it's part of cargo
        words = text_lower.split()
        
        # Direct cat words (not part of other words)
        cat_words = ['cat', 'cats', 'kitten', 'kittens', 'kitty', 'kitties', 'feline', 'felines']
        for word in words:
            # Remove punctuation for comparison
            clean_word = re.sub(r'[^\w]', '', word)
            if clean_word in cat_words:
                return True
        
        # Check for other cat-related phrases
        if any(re.search(phrase, text_lower) for phrase in cls.CAT_PHRASES):
            return True
            
        return False
    
    @classmethod
    def calculate_cat_boost(cls, text: str, guard_personality: GuardPersonality) -> float:
        """Calculate persuasion boost from cat mentions"""
        if not cls.detect_cat_mention(text):
            return 0.0
        
        text_lower = text.lower()
        
        # Base cat boost (15% as specified in requirements)
        base_boost = 0.15
        
        # Enhanced boost for context phrases
        context_bonus = 0.0
        for enhancer in cls.CONTEXT_ENHANCERS:
            if re.search(enhancer, text_lower):
                context_bonus += 0.02  # 2% per relevant context word
        
        # Guard personality affects cat boost
        personality_modifier = guard_personality.cat_affinity
        
        # Calculate final boost
        total_boost = base_boost + context_bonus
        total_boost *= personality_modifier
        
        # Cap at 25% maximum boost
        return min(0.25, total_boost)


class DynamicResponseGenerator:
    """Generates contextual guard responses based on situation"""
    
    def __init__(self):
        self.question_templates = self._build_question_templates()
        self.response_templates = self._build_response_templates()
    
    def _build_question_templates(self) -> Dict[str, Dict[int, List[str]]]:
        """Build question templates by topic and ship tier"""
        return {
            "identity_verification": {
                1: [  # Escape Pod
                    "What's your emergency evacuation ID number?",
                    "Which ship did you evacuate from?",
                    "What's your registered home station?"
                ],
                3: [  # Medium ships  
                    "What's your pilot certification class?",
                    "Which academy did you graduate from?",
                    "What's your ship's registration number?"
                ],
                4: [  # Heavy ships
                    "What's your commercial hauling license number?",
                    "Which shipping consortium do you work for?",
                    "What's your cargo manifest authorization code?"
                ],
                5: [  # Military/high tier
                    "What's your military service branch and rank?",
                    "Which admiral authorized your mission here?",
                    "What's your classified clearance level?"
                ]
            },
            "arrival_details": {
                1: [
                    "When did your ship's emergency beacon activate?",
                    "What was the nature of your emergency?",
                    "Who coordinated your rescue?"
                ],
                3: [
                    "What's your flight plan registration number?",
                    "Which traffic controller cleared your approach?",
                    "What was your departure time from last station?"
                ],
                4: [
                    "What's your cargo delivery schedule?",
                    "Which loading dock were you assigned?",
                    "Who signed off on your manifest?"
                ],
                5: [
                    "What's your mission briefing classification?",
                    "Which sector command dispatched you?",
                    "What's your operational timeline?"
                ]
            },
            "ship_knowledge": {
                1: [
                    "What's the maximum life support duration?",
                    "How many emergency rations are aboard?",
                    "What's the distress beacon frequency?"
                ],
                3: [
                    "What's your ship's maximum warp factor?",
                    "How many crew stations does it have?",
                    "What's the fuel consumption rate?"
                ],
                4: [
                    "What's the maximum cargo tonnage?",
                    "How many loading bays are there?",
                    "What's the crane lifting capacity?"
                ],
                5: [
                    "What's the weapons system classification?",
                    "How many crew quarters are aboard?",
                    "What's the shield generator rating?"
                ]
            },
            "situational_awareness": {
                1: [
                    "Why didn't you dock at the emergency bay?",
                    "Have you reported to medical for evacuation screening?",
                    "Do you need psychological counseling services?"
                ],
                3: [
                    "Are you aware of the current navigation hazards?",
                    "Have you filed your departure customs forms?",
                    "Do you have insurance coverage for this vessel?"
                ],
                4: [
                    "Are you aware of the cargo inspection protocols?",
                    "Have you declared any hazardous materials?",
                    "Do you have the required safety certifications?"
                ],
                5: [
                    "Are you aware of the current threat condition level?",
                    "Have you undergone the required security screening?",
                    "Do you have authorization for armed vessel operations?"
                ]
            }
        }
    
    def _build_response_templates(self) -> Dict[str, List[str]]:
        """Build response templates for different guard moods"""
        return {
            "neutral": [
                "I see. {question}",
                "Interesting. {question}",
                "Alright. {question}",
                "Let me check something. {question}"
            ],
            "suspicious": [
                "Hmm, that's odd. {question}",
                "Something doesn't add up. {question}",
                "I'm not entirely convinced. {question}",
                "That raises some questions. {question}"
            ],
            "very_suspicious": [
                "Wait just a minute here. {question}",
                "I don't believe that for a second. {question}",
                "Your story has more holes than Swiss cheese. {question}",
                "Nice try, but I wasn't born yesterday. {question}"
            ],
            "convinced": [
                "Your documentation appears to be in order. {question}",
                "That checks out with our records. {question}",
                "I appreciate your cooperation. {question}",
                "Everything seems legitimate. {question}"
            ],
            "annoyed": [
                "Look, I don't have all day. {question}",
                "Stop wasting my time. {question}",
                "Answer the question directly. {question}",
                "I'm losing patience here. {question}"
            ]
        }
    
    def generate_question(self, context: DialogueContext, topic: str, guard_personality: GuardPersonality) -> str:
        """Generate a contextual question based on ship tier and situation"""
        ship_tier = ShipTierInfo.get_ship_tier(context.claimed_ship)
        
        # Get appropriate tier questions, fall back to lower tier if not available
        tier_questions = None
        for tier in [ship_tier, 3, 1]:  # Try exact tier, then medium, then basic
            if tier in self.question_templates.get(topic, {}):
                tier_questions = self.question_templates[topic][tier]
                break
        
        if not tier_questions:
            # Ultimate fallback
            tier_questions = ["What can you tell me about that?"]
        
        base_question = random.choice(tier_questions)
        
        # Wrap in guard response based on mood
        mood_templates = self.response_templates.get(context.guard_mood.value, self.response_templates["neutral"])
        response_template = random.choice(mood_templates)
        
        return response_template.format(question=base_question)


class EnhancedManualProvider(AIProvider):
    """Enhanced manual provider with sophisticated rule-based logic"""
    
    def __init__(self, config):
        self.config = config
        self.response_generator = DynamicResponseGenerator()
        
        # Create a default guard personality
        self.guard_personality = GuardPersonality(
            suspicion_base=0.4,
            strictness=0.6,
            experience=0.7,
            patience=0.5,
            cat_affinity=0.8  # Most guards like cats
        )
    
    def is_available(self) -> bool:
        """Manual provider is always available"""
        return True
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MANUAL
    
    async def analyze_response(self, response: str, context: DialogueContext) -> ResponseAnalysis:
        """Analyze player response with enhanced rule-based logic"""
        
        # Basic text analysis
        words = response.split()
        word_count = len(words)
        char_count = len(response)
        
        # Base scores
        persuasiveness = self._calculate_base_persuasiveness(response, word_count)
        confidence = self._calculate_confidence(response)
        consistency = self._calculate_consistency(response, context)
        negotiation_skill = self._calculate_negotiation_skill(response, context)
        
        # Apply cat boost if detected
        cat_boost = CatBoostDetector.calculate_cat_boost(response, self.guard_personality)
        if cat_boost > 0:
            persuasiveness = min(1.0, persuasiveness + cat_boost)
            confidence = min(1.0, confidence + cat_boost * 0.5)  # Secondary boost to confidence
            logger.info(f"Cat boost applied: +{cat_boost:.2f} to persuasiveness")
        
        # Apply ship tier difficulty modifier
        ship_difficulty = ShipTierInfo.get_base_difficulty(context.claimed_ship)
        difficulty_modifier = 1.0 - (ship_difficulty - 0.3) * 0.5  # Scale difficulty impact
        persuasiveness *= difficulty_modifier
        
        # Extract claims and detect inconsistencies
        extracted_claims = self._extract_claims(response)
        inconsistencies = self._detect_inconsistencies(response, context, extracted_claims)
        
        # Calculate overall believability
        believability = (persuasiveness * 0.4 + confidence * 0.3 + consistency * 0.3)
        believability = max(0.0, min(1.0, believability))
        
        # Suggest guard mood based on analysis
        suggested_mood = self._suggest_guard_mood(believability, inconsistencies, context)
        
        return ResponseAnalysis(
            persuasiveness_score=persuasiveness,
            confidence_level=confidence,
            consistency_score=consistency,
            negotiation_skill=negotiation_skill,
            detected_inconsistencies=inconsistencies,
            extracted_claims=extracted_claims,
            overall_believability=believability,
            suggested_guard_mood=suggested_mood
        )
    
    async def generate_question(self, context: DialogueContext, analysis: ResponseAnalysis) -> GuardResponse:
        """Generate guard question using rule-based logic"""
        
        # Choose topic based on dialogue history and ship type
        topic = self._choose_question_topic(context)
        
        # Generate appropriate question
        question = self.response_generator.generate_question(context, topic, self.guard_personality)
        
        # Determine guard mood and suspicion
        mood = analysis.suggested_guard_mood
        suspicion_level = 1.0 - analysis.overall_believability
        
        # Add variety to questions based on exchange count
        exchange_count = len(context.dialogue_history)
        if exchange_count >= 2:
            if suspicion_level > 0.7:
                question = f"Look, I've heard enough. {question} And I want the truth this time."
            elif suspicion_level > 0.4:
                question = f"Your story keeps changing. {question}"
        
        return GuardResponse(
            dialogue_text=question,
            mood=mood,
            suspicion_level=suspicion_level,
            is_final_decision=exchange_count >= 3,  # Make decision after 3 questions
            outcome="continue" if exchange_count < 3 else "evaluate"
        )
    
    def _calculate_base_persuasiveness(self, response: str, word_count: int) -> float:
        """Calculate base persuasiveness score"""
        # Start with length-based scoring
        if word_count < 3:
            base_score = 0.2
        elif word_count < 10:
            base_score = 0.4
        elif word_count < 25:
            base_score = 0.6
        else:
            base_score = 0.7
        
        # Bonus for specific details
        detail_keywords = [
            "serial", "number", "code", "id", "registration", "license",
            "captain", "commander", "officer", "station", "sector",
            "authorization", "clearance", "manifest", "cargo", "duty"
        ]
        
        response_lower = response.lower()
        detail_count = sum(1 for keyword in detail_keywords if keyword in response_lower)
        detail_bonus = min(0.2, detail_count * 0.03)
        
        # Bonus for confident language
        confident_phrases = [
            "certainly", "absolutely", "definitely", "of course",
            "without a doubt", "obviously", "clearly", "indeed"
        ]
        confidence_bonus = 0.1 if any(phrase in response_lower for phrase in confident_phrases) else 0
        
        return min(1.0, base_score + detail_bonus + confidence_bonus)
    
    def _calculate_confidence(self, response: str) -> float:
        """Calculate confidence level"""
        response_lower = response.lower()
        
        # Start with neutral confidence
        confidence = 0.5
        
        # Positive confidence indicators
        confident_words = ["yes", "sure", "absolutely", "definitely", "certain"]
        uncertain_words = ["maybe", "perhaps", "possibly", "might", "think", "guess"]
        
        confident_count = sum(1 for word in confident_words if word in response_lower)
        uncertain_count = sum(1 for word in uncertain_words if word in response_lower)
        
        confidence += confident_count * 0.1
        confidence -= uncertain_count * 0.15
        
        # Penalty for excessive hedging
        if response_lower.count("um") + response_lower.count("uh") > 2:
            confidence -= 0.2
        
        return max(0.0, min(1.0, confidence))
    
    def _calculate_consistency(self, response: str, context: DialogueContext) -> float:
        """Calculate consistency with previous responses"""
        if not context.dialogue_history:
            return 0.8  # First response is automatically consistent
        
        consistency = 0.8
        response_words = set(response.lower().split())
        
        # Check for contradictions with previous claims
        for exchange in context.dialogue_history:
            if "player" in exchange:
                previous_words = set(exchange["player"].lower().split())
                
                # Simple contradiction detection
                contradiction_pairs = [
                    ({"today", "this morning"}, {"yesterday", "last week"}),
                    ({"new", "brand new"}, {"old", "used", "vintage"}),
                    ({"bought", "purchased"}, {"inherited", "found", "stolen"})
                ]
                
                for positive_set, negative_set in contradiction_pairs:
                    prev_has_positive = bool(positive_set & previous_words)
                    curr_has_negative = bool(negative_set & response_words)
                    
                    if prev_has_positive and curr_has_negative:
                        consistency -= 0.2
        
        return max(0.0, min(1.0, consistency))
    
    def _calculate_negotiation_skill(self, response: str, context: DialogueContext) -> float:
        """Calculate negotiation skill level"""
        skill = 0.5  # Base skill
        response_lower = response.lower()
        
        # Good negotiation indicators
        if any(phrase in response_lower for phrase in ["understand", "appreciate", "respect"]):
            skill += 0.1
        
        if any(phrase in response_lower for phrase in ["protocol", "procedure", "regulation"]):
            skill += 0.1
        
        # Check for deflection/redirection techniques
        if any(phrase in response_lower for phrase in ["speaking of", "by the way", "actually"]):
            skill += 0.05
        
        # Penalty for aggression
        if any(phrase in response_lower for phrase in ["stupid", "idiot", "waste of time"]):
            skill -= 0.2
        
        return max(0.0, min(1.0, skill))
    
    def _extract_claims(self, response: str) -> List[str]:
        """Extract specific claims from player response"""
        claims = []
        response_lower = response.lower()
        
        # Name claims
        name_patterns = [r"my name is (\w+)", r"i'm (\w+)", r"captain (\w+)"]
        for pattern in name_patterns:
            matches = re.findall(pattern, response_lower)
            claims.extend([f"Name: {match}" for match in matches])
        
        # ID/Number claims  
        id_patterns = [r"(\w+\-\d+)", r"id (\d+)", r"number (\d+)"]
        for pattern in id_patterns:
            matches = re.findall(pattern, response)
            claims.extend([f"ID: {match}" for match in matches])
        
        # Location claims
        if any(word in response_lower for word in ["station", "sector", "system"]):
            claims.append("Location claim made")
        
        return claims
    
    def _detect_inconsistencies(self, response: str, context: DialogueContext, claims: List[str]) -> List[str]:
        """Detect inconsistencies in player's story"""
        inconsistencies = []
        
        # Add existing inconsistencies from context
        inconsistencies.extend(context.inconsistencies)
        
        # Simple inconsistency detection based on ship type mismatch
        response_lower = response.lower()
        claimed_ship = context.claimed_ship
        
        if claimed_ship == ShipType.CARGO_HAULER:
            if any(word in response_lower for word in ["military", "weapons", "combat"]):
                inconsistencies.append("Mentioned military features for cargo ship")
        elif claimed_ship == ShipType.SCOUT_SHIP:
            if any(word in response_lower for word in ["cargo", "freight", "tons"]):
                inconsistencies.append("Mentioned cargo features for scout ship")
        
        return inconsistencies
    
    def _suggest_guard_mood(self, believability: float, inconsistencies: List[str], context: DialogueContext) -> GuardMood:
        """Suggest appropriate guard mood based on analysis"""
        if len(inconsistencies) > 2:
            return GuardMood.VERY_SUSPICIOUS
        elif believability < 0.3:
            return GuardMood.VERY_SUSPICIOUS
        elif believability < 0.5 or inconsistencies:
            return GuardMood.SUSPICIOUS
        elif believability > 0.8:
            return GuardMood.CONVINCED
        else:
            return GuardMood.NEUTRAL
    
    def _choose_question_topic(self, context: DialogueContext) -> str:
        """Choose appropriate question topic based on context"""
        topics = ["identity_verification", "arrival_details", "ship_knowledge", "situational_awareness"]
        
        # Get topics already covered
        covered_topics = set()
        for exchange in context.dialogue_history:
            if "topic" in exchange:
                covered_topics.add(exchange["topic"])
        
        # Prefer uncovered topics
        remaining_topics = [t for t in topics if t not in covered_topics]
        if remaining_topics:
            return random.choice(remaining_topics)
        
        # If all covered, choose based on ship type preference
        ship_tier = ShipTierInfo.get_ship_tier(context.claimed_ship)
        if ship_tier >= 4:
            return "ship_knowledge"  # Focus on technical details for expensive ships
        else:
            return random.choice(topics)