import uuid
import random
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any, Union
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.first_login import (
    FirstLoginSession, 
    DialogueExchange, 
    ShipPresentationOptions,
    PlayerFirstLoginState,
    ShipRarityConfig,
    ShipChoice,
    NegotiationSkillLevel,
    DialogueOutcome
)
from src.models.ship import Ship, ShipType, ShipSpecification
from src.services.ai_dialogue_service import (
    AIDialogueService,
    DialogueContext,
    ShipType as AIShipType,
    GuardMood
)
from src.services.ai_provider_service import get_ai_provider_service, ProviderType
from src.services.nickname_validation_service import validate_nickname
from src.utils.guard_personalities import get_guard_for_session
from src.core.ship_specifications_seeder import SHIP_SPECIFICATIONS

logger = logging.getLogger(__name__)


class FirstLoginCompletionError(Exception):
    """Raised by complete_first_login when the flow's side effects have
    already run for this player; carries an HTTP status hint (matches the
    ConstructionError convention in construction_service.py)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def get_ship_specifications(ship_choice: ShipChoice) -> Optional[Dict[str, Any]]:
    """
    Get ship specifications for a given ship choice.
    Returns detailed specs including cargo, speed, weapons, etc.
    """
    # Map ShipChoice to ShipType
    ship_type_map = {
        ShipChoice.ESCAPE_POD: ShipType.ESCAPE_POD,
        ShipChoice.LIGHT_FREIGHTER: ShipType.LIGHT_FREIGHTER,
        ShipChoice.CARGO_HAULER: ShipType.CARGO_HAULER,
        ShipChoice.FAST_COURIER: ShipType.FAST_COURIER,
        ShipChoice.SCOUT_SHIP: ShipType.SCOUT_SHIP,
    }

    ship_type = ship_type_map.get(ship_choice)
    if not ship_type:
        logger.warning(f"Unknown ship choice: {ship_choice}")
        return None

    specs = SHIP_SPECIFICATIONS.get(ship_type)
    if not specs:
        logger.warning(f"No specifications found for ship type: {ship_type}")
        return None

    return specs

# Initial dialogue prompts
INITIAL_GUARD_PROMPT = """The year is 2102. You find yourself in a bustling shipyard on the outskirts of the Callisto Colony. 
Your memory is hazy—a side effect of the cryo-sleep required for the journey here. 
A small orange cat darts between the landing gear of nearby ships, disappearing into the shadows. 
You're approaching what appears to be your escape pod when a stern-looking Security Guard blocks your path.

Guard: "Hold it right there! This area is restricted to registered pilots only. Which of these vessels belongs to you?"
"""

# Topics for guard questions
QUESTION_TOPICS = [
    "identity_verification",  # Registration name, clearance codes
    "arrival_details",        # When docked, who processed clearance
    "ship_knowledge",         # Technical specs, cargo capacity  
    "situational_awareness"   # Current protocols, restricted areas
]

# Ship configuration defaults
DEFAULT_SHIP_CONFIGS = [
    {
        "ship_type": ShipChoice.ESCAPE_POD,
        "rarity_tier": 1,
        "spawn_chance": 100,
        "base_credits": 1000,
        "weak_threshold": 0.3,
        "average_threshold": 0.3,
        "strong_threshold": 0.3
    },
    {
        "ship_type": ShipChoice.LIGHT_FREIGHTER,
        "rarity_tier": 2,
        "spawn_chance": 50,
        "base_credits": 2500,
        "weak_threshold": 0.4,  # Lowered from 0.7 - much easier to claim
        "average_threshold": 0.35,  # Lowered from 0.6
        "strong_threshold": 0.3  # Lowered from 0.5
    },
    {
        "ship_type": ShipChoice.SCOUT_SHIP,
        "rarity_tier": 3,
        "spawn_chance": 25,
        "base_credits": 2000,
        "weak_threshold": 0.55,  # Balanced for 40% win rate with decent roleplay
        "average_threshold": 0.50,  # Balanced for 65% win rate with good roleplay
        "strong_threshold": 0.45  # Easy win for skilled players (~85% win rate)
    },
    {
        "ship_type": ShipChoice.FAST_COURIER,
        "rarity_tier": 3,
        "spawn_chance": 20,
        "base_credits": 3000,
        "weak_threshold": 0.55,  # Lowered from 0.85
        "average_threshold": 0.5,  # Lowered from 0.75
        "strong_threshold": 0.45  # Lowered from 0.65
    },
    {
        "ship_type": ShipChoice.CARGO_HAULER,
        "rarity_tier": 4,
        "spawn_chance": 10,
        "base_credits": 5000,
        "weak_threshold": 0.65,  # Lowered from 0.9
        "average_threshold": 0.55,  # Lowered from 0.8
        "strong_threshold": 0.5  # Lowered from 0.7
    },
    {
        "ship_type": ShipChoice.DEFENDER,
        "rarity_tier": 5,
        "spawn_chance": 5,
        "base_credits": 7000,
        "weak_threshold": 0.75,  # Lowered from 0.95
        "average_threshold": 0.65,  # Lowered from 0.9
        "strong_threshold": 0.6  # Lowered from 0.8
    },
    {
        "ship_type": ShipChoice.COLONY_SHIP,
        "rarity_tier": 6,
        "spawn_chance": 3,
        "base_credits": 10000,
        "weak_threshold": 0.8,  # Lowered from 0.97
        "average_threshold": 0.7,  # Lowered from 0.92
        "strong_threshold": 0.65  # Lowered from 0.85
    },
    {
        "ship_type": ShipChoice.CARRIER,
        "rarity_tier": 7,
        "spawn_chance": 1,
        "base_credits": 15000,
        "weak_threshold": 0.85,  # Lowered from 0.99
        "average_threshold": 0.75,  # Lowered from 0.95
        "strong_threshold": 0.7  # Lowered from 0.9
    }
]

# Mapping between ShipChoice and ShipType
SHIP_CHOICE_TO_TYPE = {
    ShipChoice.ESCAPE_POD: ShipType.ESCAPE_POD,
    ShipChoice.LIGHT_FREIGHTER: ShipType.LIGHT_FREIGHTER,
    ShipChoice.SCOUT_SHIP: ShipType.SCOUT_SHIP,
    ShipChoice.FAST_COURIER: ShipType.FAST_COURIER,
    ShipChoice.CARGO_HAULER: ShipType.CARGO_HAULER,
    ShipChoice.DEFENDER: ShipType.DEFENDER,
    ShipChoice.COLONY_SHIP: ShipType.COLONY_SHIP,
    ShipChoice.CARRIER: ShipType.CARRIER
}

# Mapping from ShipChoice to AI service ShipType
SHIP_CHOICE_TO_AI_TYPE = {
    ShipChoice.ESCAPE_POD: AIShipType.ESCAPE_POD,
    ShipChoice.LIGHT_FREIGHTER: AIShipType.CARGO_HAULER,
    ShipChoice.SCOUT_SHIP: AIShipType.SCOUT_SHIP,
    ShipChoice.FAST_COURIER: AIShipType.SCOUT_SHIP,  # Similar to scout
    ShipChoice.CARGO_HAULER: AIShipType.CARGO_HAULER,
    ShipChoice.DEFENDER: AIShipType.PATROL_CRAFT,
    ShipChoice.COLONY_SHIP: AIShipType.CARGO_HAULER,  # Large ship similar to cargo
    ShipChoice.CARRIER: AIShipType.PATROL_CRAFT  # Military ship similar to patrol craft
}

class FirstLoginService:
    """Service for managing the first login experience"""
    
    def __init__(self, db: Session, ai_service: Optional[AIDialogueService] = None):
        self.db = db
        self.ai_service = ai_service or AIDialogueService()
        # Use the enhanced AI provider service for better fallback support
        try:
            from src.services.ai_provider_service import get_ai_provider_service
            self.ai_provider_service = get_ai_provider_service()
            logger.info("AI provider service initialized successfully")
        except Exception as e:
            logger.warning(f"Failed to initialize AI provider service: {e}. Using fallback only.")
            self.ai_provider_service = None

        # Multilingual support: lazily constructed so a translation subsystem
        # failure never blocks the first-login flow.
        self._multilingual_service = None

    def _get_multilingual_service(self):
        """Lazily build the multilingual AI service (defensive: None on failure)."""
        if self._multilingual_service is None:
            try:
                from src.services.multilingual_ai_service import MultilingualAIService
                from src.services.translation_service import TranslationService
                self._multilingual_service = MultilingualAIService(
                    self.db, self.ai_service, TranslationService(self.db)
                )
            except Exception as e:
                logger.warning(f"Multilingual service unavailable: {e}")
                self._multilingual_service = False  # sentinel: tried and failed
        return self._multilingual_service or None

    async def _localize_for_player(self, player_id: uuid.UUID, text: str) -> str:
        """
        Translate player-facing narration into the player's preferred language.
        Always returns usable text — falls back to the original on any failure.
        """
        if not text:
            return text
        try:
            player = self.db.query(Player).filter_by(id=player_id).first()
            if not player or not getattr(player, "user_id", None):
                return text
            multilingual = self._get_multilingual_service()
            if not multilingual:
                return text
            return await multilingual.translate_text_for_user(player.user_id, text)
        except Exception as e:
            logger.warning(f"Failed to localize first-login text: {e}")
            return text
    
    def initialize_ship_configs(self) -> None:
        """Initialize the default ship rarity configurations if they don't exist"""
        for config in DEFAULT_SHIP_CONFIGS:
            existing = self.db.query(ShipRarityConfig).filter_by(
                ship_type=config["ship_type"]
            ).first()
            
            if not existing:
                new_config = ShipRarityConfig(
                    ship_type=config["ship_type"],
                    rarity_tier=config["rarity_tier"],
                    spawn_chance=config["spawn_chance"],
                    base_credits=config["base_credits"],
                    weak_threshold=config["weak_threshold"],
                    average_threshold=config["average_threshold"],
                    strong_threshold=config["strong_threshold"]
                )
                self.db.add(new_config)
        
        self.db.commit()
    
    def get_player_first_login_state(self, player_id: uuid.UUID) -> Optional[PlayerFirstLoginState]:
        """Get the player's first login state, create it if it doesn't exist"""
        state = self.db.query(PlayerFirstLoginState).filter_by(player_id=player_id).first()
        
        if not state:
            # Create a new state for the player
            state = PlayerFirstLoginState(
                player_id=player_id,
                has_completed_first_login=False,
                attempts=0
            )
            self.db.add(state)
            self.db.commit()
            self.db.refresh(state)
        
        return state
    
    def should_show_first_login(self, player_id: uuid.UUID) -> bool:
        """Check if the player should see the first login experience"""
        player = self.db.query(Player).filter_by(id=player_id).first()

        if not player:
            return False

        # Check if this is really their first time
        state = self.get_player_first_login_state(player_id)
        return not state.has_completed_first_login
    
    def get_or_create_session(self, player_id: uuid.UUID) -> FirstLoginSession:
        """Get the player's current first login session or create a new one"""
        state = self.get_player_first_login_state(player_id)
        
        # If there's an active session, return it
        if state.current_session_id:
            session = self.db.query(FirstLoginSession).filter_by(id=state.current_session_id).first()
            if session and not session.completed_at:
                return session
        
        # No active session, create a new one
        session = FirstLoginSession(
            player_id=player_id,
            ai_service_used=False,  # Will be set to True if we use AI
            fallback_to_rules=True  # Default to rule-based until we use AI
        )
        self.db.add(session)
        self.db.flush()  # Get the ID without committing

        # Generate guard personality for this session (deterministic based on session ID)
        guard = get_guard_for_session(str(session.id))
        session.guard_name = guard.name
        session.guard_title = guard.title
        session.guard_trait = guard.trait
        session.guard_base_suspicion = guard.base_suspicion
        session.guard_description = guard.description

        logger.info(f"[FirstLogin:Session] Generated guard personality: {guard.title} {guard.name} ({guard.trait})")
        
        # Generate ship options for this session
        ship_options = self._generate_ship_options(session.id)
        self.db.add(ship_options)

        # Add initial dialogue exchange (will be updated with AI-generated or fallback prompt)
        # Temporarily use a placeholder; will be replaced immediately
        initial_exchange = DialogueExchange(
            session_id=session.id,
            sequence_number=1,
            npc_prompt="[Generating initial scene...]",  # Placeholder
            player_response="",  # Player hasn't responded yet
            topic="introduction"
        )
        self.db.add(initial_exchange)
        
        # Update the player's first login state
        state.current_session_id = session.id
        state.attempts += 1
        state.last_attempt_at = datetime.now()
        
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_session_with_history(self, player_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """
        Read-only assembly of a player's in-progress first-login session for
        resume: the session row plus its ordered, already-persisted
        DialogueExchange history (canon: first-login.md:135-139 persistence/
        resume — "reload mid-flow returns the full history"). Returns None
        when there is nothing to resume, in which case the caller falls back
        to get_or_create_session for a fresh one.

        A session counts as resumable for as long as the player hasn't
        finished the whole flow (state.has_completed_first_login) — not
        merely reached a dialogue outcome. completed_at is set as soon as
        the outcome is scored (_evaluate_dialogue_outcome), before /complete
        grants resources, so the outcome/completion screen must stay
        resumable too or a reload there would silently spin up a duplicate
        session with a new guard and new ships.
        """
        state = self.get_player_first_login_state(player_id)
        if not state.current_session_id or state.has_completed_first_login:
            return None

        session = self.db.query(FirstLoginSession).filter_by(id=state.current_session_id).first()
        if not session:
            return None

        exchanges = self.db.query(DialogueExchange).filter_by(
            session_id=session.id
        ).order_by(DialogueExchange.sequence_number).all()

        ship_options = self.db.query(ShipPresentationOptions).filter_by(session_id=session.id).first()
        available_ships = ship_options.available_ships if ship_options else [ShipChoice.ESCAPE_POD.name]

        current_step = "ship_selection"
        if session.ship_claimed:
            current_step = "dialogue" if not session.outcome else "completion"

        last_exchange = exchanges[-1] if exchanges else None
        # The exchange still awaiting a reply — None once the dialogue has
        # been fully answered and scored (no further question is created).
        pending_exchange = (
            last_exchange if (last_exchange and not last_exchange.player_response) else None
        )

        outcome = None
        if session.outcome:
            outcome = {
                "outcome": session.outcome.name,
                "awarded_ship": session.awarded_ship.name if session.awarded_ship else None,
                "starting_credits": session.starting_credits,
                "negotiation_skill": session.negotiation_skill.name if session.negotiation_skill else None,
                "final_persuasion_score": session.final_persuasion_score,
                "negotiation_bonus": bool(session.negotiation_bonus_flag),
                "notoriety_penalty": bool(session.notoriety_penalty),
                # The AI-narrated closing line is generated at outcome time,
                # not persisted anywhere — resume never re-invokes the AI
                # provider (no cost, no non-determinism, ARIA-LLM untouched).
                # The client falls back to its own canonical outcome message.
                "guard_response": None,
                # Carries the pending nickname-confirmation prompt across a
                # reload (WO-PUX-FLOGIN-NICKNAME) — a player who reaches the
                # outcome screen with an extracted name but reloads before
                # /complete must still see the confirm gate, not lose it.
                "extracted_player_name": session.extracted_player_name,
            }

        return {
            "session": session,
            "available_ships": available_ships,
            "current_step": current_step,
            "npc_prompt": last_exchange.npc_prompt if last_exchange else "ERROR: Missing initial prompt",
            "exchange_id": str(pending_exchange.id) if pending_exchange else None,
            "sequence_number": (
                pending_exchange.sequence_number if pending_exchange
                else (last_exchange.sequence_number if last_exchange else None)
            ),
            "outcome": outcome,
            "dialogue_history": [
                {
                    "npc_prompt": ex.npc_prompt,
                    "player_response": ex.player_response,
                    "sequence_number": ex.sequence_number,
                    "persuasiveness": ex.persuasiveness,
                    "confidence": ex.confidence,
                    "consistency": ex.consistency,
                }
                for ex in exchanges
            ],
        }

    async def generate_initial_prompt(self, session_id: uuid.UUID) -> str:
        """
        Generate AI-enhanced or fallback initial prompt for a session.
        This is called after session creation to populate the initial dialogue.

        Returns the generated prompt text.
        """
        session = self.db.query(FirstLoginSession).filter_by(id=session_id).first()
        if not session:
            raise ValueError(f"Invalid session ID: {session_id}")

        # Get ship options for this session
        ship_options = self.db.query(ShipPresentationOptions).filter_by(session_id=session_id).first()
        if not ship_options:
            raise ValueError(f"No ship options found for session {session_id}")

        initial_prompt = None
        ai_used = False

        # Try AI generation first (if AI provider available)
        if self.ai_provider_service:
            try:
                logger.info(f"[FirstLogin:Scene] Attempting AI generation for session {session_id}")
                scene_text, provider_used = await self.ai_provider_service.generate_initial_scene(
                    guard_name=session.guard_name,
                    guard_title=session.guard_title,
                    guard_trait=session.guard_trait,
                    guard_description=session.guard_description,
                    guard_base_suspicion=session.guard_base_suspicion,
                    available_ships=ship_options.available_ships
                )

                if scene_text and provider_used != ProviderType.MANUAL:
                    initial_prompt = scene_text
                    ai_used = True
                    session.ai_service_used = True
                    session.fallback_to_rules = False
                    logger.info(f"[FirstLogin:Scene] AI generation successful with {provider_used.value}")
                else:
                    logger.info(f"[FirstLogin:Scene] AI generation returned None, using fallback")
            except Exception as e:
                logger.warning(f"[FirstLogin:Scene] AI generation failed: {e}, using fallback")

        # Fallback to template if AI failed or unavailable
        if not initial_prompt:
            initial_prompt = INITIAL_GUARD_PROMPT
            session.fallback_to_rules = True
            logger.info(f"[FirstLogin:Scene] Using template fallback")

        # Localize narration into the player's preferred language (defensive)
        initial_prompt = await self._localize_for_player(session.player_id, initial_prompt)

        # Update the initial exchange with the generated prompt
        initial_exchange = self.db.query(DialogueExchange).filter_by(
            session_id=session_id,
            sequence_number=1
        ).first()

        if initial_exchange:
            initial_exchange.npc_prompt = initial_prompt
            self.db.commit()
            logger.info(f"[FirstLogin:Scene] Initial prompt set (AI={ai_used})")
        else:
            logger.error(f"[FirstLogin:Scene] Could not find initial exchange for session {session_id}")
            raise ValueError("Initial exchange not found")

        return initial_prompt

    def _generate_ship_options(self, session_id: uuid.UUID) -> ShipPresentationOptions:
        """Generate the ship options to present to the player"""
        # Load all ship configs
        ship_configs = self.db.query(ShipRarityConfig).all()
        
        # Always include the escape pod
        available_ships = [ShipChoice.ESCAPE_POD.name]
        
        # Generate a rarity roll (0-100)
        rarity_roll = random.randint(0, 100)
        
        # Determine the tier range based on the rarity roll (expanded to ensure enough ships)
        if rarity_roll >= 96:  # 5% chance for top tier
            tier_range = [4, 5, 6, 7]  # Include super rare ships
        elif rarity_roll >= 86:  # 10% chance for high tier
            tier_range = [3, 4, 5]  # Include higher tier ships
        elif rarity_roll >= 61:  # 25% chance for medium tier
            tier_range = [2, 3, 4]  # Include medium tier ships
        else:  # 60% chance for low tier
            tier_range = [2, 3]  # Always include at least tiers 2-3 to ensure 2+ ships
        
        # Select two additional ships from the determined tier range
        eligible_ships = [
            config.ship_type.name for config in ship_configs 
            if config.rarity_tier in tier_range and config.ship_type != ShipChoice.ESCAPE_POD
        ]
        
        if eligible_ships:
            # Use weighted selection based on spawn chance
            weights = [
                config.spawn_chance for config in ship_configs
                if config.ship_type.name in eligible_ships
            ]
            # Select 2 additional ships (or all available if less than 2)
            num_to_select = min(2, len(eligible_ships))
            additional_ships = random.choices(eligible_ships, weights=weights, k=num_to_select)
            
            # Remove duplicates while preserving order and ensuring we get 2 different ships
            unique_ships = []
            for ship in additional_ships:
                if ship not in unique_ships:
                    unique_ships.append(ship)
            
            # If we only got 1 unique ship and there are more available, try to get a second different one
            if len(unique_ships) < 2 and len(eligible_ships) > 1:
                remaining_ships = [ship for ship in eligible_ships if ship not in unique_ships]
                if remaining_ships:
                    remaining_weights = [
                        config.spawn_chance for config in ship_configs
                        if config.ship_type.name in remaining_ships
                    ]
                    second_ship = random.choices(remaining_ships, weights=remaining_weights, k=1)[0]
                    unique_ships.append(second_ship)
            
            available_ships.extend(unique_ships)
        
        # Create ship presentation options
        return ShipPresentationOptions(
            session_id=session_id,
            available_ships=available_ships,
            escape_pod_present=True,
            rarity_roll=rarity_roll,
            special_event_active=False,
            seed_value=str(uuid.uuid4())
        )
    
    def record_player_ship_claim(
        self, 
        session_id: uuid.UUID,
        claimed_ship: ShipChoice,
        player_response: str
    ) -> FirstLoginSession:
        """Record the player's ship choice and initial response"""
        session = self.db.query(FirstLoginSession).filter_by(id=session_id).first()
        
        if not session:
            raise ValueError(f"Invalid session ID: {session_id}")
        
        if session.completed_at:
            raise ValueError(f"Session already completed at {session.completed_at}")
        
        # Check if ship already claimed
        if session.ship_claimed:
            logger.warning(f"Session {session_id} already has ship claimed: {session.ship_claimed}, updating to {claimed_ship}")
        
        # Update the session with the claimed ship
        session.ship_claimed = claimed_ship
        
        # Update the dialogue exchange with the player's response
        exchange = self.db.query(DialogueExchange).filter_by(
            session_id=session_id,
            sequence_number=1
        ).first()
        
        if exchange:
            exchange.player_response = player_response
            
            # Basic analysis of the player's response (could be replaced with AI)
            analysis = self._analyze_player_response(player_response)
            exchange.persuasiveness = analysis.get("persuasiveness", 0.5)
            exchange.confidence = analysis.get("confidence", 0.5)
            exchange.consistency = analysis.get("consistency", 0.5)
            exchange.key_extracted_info = analysis.get("extracted_info", {})
            
            # Try to extract a name from the player's response
            extracted_name = self._extract_player_name(player_response)
            if extracted_name:
                session.extracted_player_name = extracted_name
                
        # Update the player's first login state
        state = self.get_player_first_login_state(session.player_id)
        state.claimed_ship = True
        
        try:
            self.db.commit()
            self.db.refresh(session)
        except Exception as e:
            logger.error(f"Failed to commit ship claim for session {session_id}: {e}")
            self.db.rollback()
            raise ValueError(f"Database error while claiming ship: {str(e)}")
        
        return session
    
    def _analyze_player_response(self, response: str) -> Dict[str, Any]:
        """
        Analyze a player's response for persuasiveness, confidence, and consistency
        This is a basic implementation that could be replaced with an AI service
        """
        # Basic analysis based on text length and structure
        words = response.split()
        word_count = len(words)
        
        # Very simple metrics for demonstration
        persuasiveness = min(0.3 + (word_count / 50), 0.9)  # Longer responses seem more persuasive, up to a point
        confidence = 0.5  # Default value
        
        # Look for confident language patterns
        confident_phrases = ["definitely", "certainly", "absolutely", "of course", "without a doubt"]
        if any(phrase in response.lower() for phrase in confident_phrases):
            confidence += 0.2
        
        # Look for specific details which increase persuasiveness
        detail_indicators = ["serial", "registry", "license", "clearance", "authorization", "docking", "cargo", "manifest"]
        detail_count = sum(1 for indicator in detail_indicators if indicator in response.lower())
        persuasiveness += (detail_count * 0.05)
        
        # Extract potential information
        extracted_info = {}
        
        # Clamp values to 0-1 range
        persuasiveness = max(0.0, min(1.0, persuasiveness))
        confidence = max(0.0, min(1.0, confidence))
        
        return {
            "persuasiveness": persuasiveness,
            "confidence": confidence,
            "consistency": 0.8,  # First response is always consistent since there's no history
            "extracted_info": extracted_info
        }
    
    def _extract_player_name(self, response: str) -> Optional[str]:
        """
        Extract a potential player name from their response
        This is a basic implementation that could be replaced with an AI service
        """
        # Look for common name patterns
        name_prefixes = ["I'm ", "my name is ", "captain ", "pilot ", "name's ", "call me "]
        
        for prefix in name_prefixes:
            if prefix.lower() in response.lower():
                index = response.lower().find(prefix.lower()) + len(prefix)
                # Extract words after the prefix until punctuation
                name_part = ""
                for char in response[index:]:
                    if char in ".,;:!?\n":
                        break
                    name_part += char
                
                # Clean up the extracted name
                name_part = name_part.strip()
                if name_part and len(name_part.split()) <= 3:  # Maximum 3 words for a name
                    return name_part
        
        return None
    
    def _build_dialogue_context(self, session: FirstLoginSession, exchanges: List[DialogueExchange]) -> DialogueContext:
        """Build dialogue context for AI service"""
        # Get dialogue history
        dialogue_history = []
        for exchange in exchanges:
            if exchange.topic != "introduction" and exchange.player_response:
                dialogue_history.append({
                    "guard": exchange.npc_prompt,
                    "player": exchange.player_response
                })
        
        # Extract inconsistencies from previous analyses
        inconsistencies = []
        for exchange in exchanges:
            if exchange.detected_contradictions:
                inconsistencies.extend(exchange.detected_contradictions)
        
        # Calculate negotiation skill level based on previous exchanges
        negotiation_scores = []
        for exchange in exchanges:
            if exchange.persuasiveness is not None:
                # Use persuasiveness as a proxy for negotiation skill
                negotiation_scores.append(exchange.persuasiveness)
        
        avg_negotiation = sum(negotiation_scores) / len(negotiation_scores) if negotiation_scores else 0.5
        
        # Determine guard mood based on session progress
        if session.outcome == DialogueOutcome.SUCCESS:
            guard_mood = GuardMood.CONVINCED
        elif len(inconsistencies) > 2:
            guard_mood = GuardMood.VERY_SUSPICIOUS
        elif inconsistencies:
            guard_mood = GuardMood.SUSPICIOUS
        else:
            guard_mood = GuardMood.NEUTRAL
        
        # Map ship choice to AI service ship type
        claimed_ship = SHIP_CHOICE_TO_AI_TYPE.get(session.ship_claimed, AIShipType.ESCAPE_POD)

        # Get actual ship name for display (what the player claimed)
        # Convert FAST_COURIER -> Fast Courier (proper title case)
        claimed_ship_display = session.ship_claimed.name.replace("_", " ").title() if session.ship_claimed else "Escape Pod"

        # Get ship specifications for the claimed ship
        ship_specs = get_ship_specifications(session.ship_claimed) if session.ship_claimed else None

        # Format ship specs for AI context (human-readable)
        ship_specs_text = None
        if ship_specs:
            ship_specs_text = f"""
Ship Type: {claimed_ship_display}
Base Cost: {ship_specs.get('base_cost', 0):,} credits
Max Cargo: {ship_specs.get('max_cargo', 0)} units
Speed: {ship_specs.get('speed', 0)} sectors/turn
Max Shields: {ship_specs.get('max_shields', 0)} points
Hull Points: {ship_specs.get('hull_points', 0)}
Attack Rating: {ship_specs.get('attack_rating', 0)}/10
Defense Rating: {ship_specs.get('defense_rating', 0)}/10
Evasion: {ship_specs.get('evasion', 0)}%
Max Drones: {ship_specs.get('max_drones', 0)}
Scanner Range: {ship_specs.get('scanner_range', 0)} sectors
Warp Capable: {'Yes' if ship_specs.get('warp_compatible', False) else 'No'}
Description: {ship_specs.get('description', 'N/A')}
""".strip()

        return DialogueContext(
            session_id=str(session.id),
            claimed_ship=claimed_ship,
            actual_ship=AIShipType.ESCAPE_POD,  # Always escape pod in this scenario
            dialogue_history=dialogue_history,
            inconsistencies=inconsistencies,
            guard_mood=guard_mood,
            negotiation_skill_level=avg_negotiation,
            player_name=session.extracted_player_name,
            security_protocol_level="standard",
            time_of_day="day_shift",
            claimed_ship_display_name=claimed_ship_display,  # Use actual ship name player claimed
            # Guard personality for AI-enhanced generation
            guard_name=session.guard_name,
            guard_title=session.guard_title,
            guard_trait=session.guard_trait,
            guard_description=session.guard_description,
            guard_base_suspicion=session.guard_base_suspicion,
            # Ship specifications for technical questions
            ship_specifications=ship_specs_text
        )
    
    async def generate_guard_question(self, session_id: uuid.UUID) -> Dict[str, Any]:
        """
        Generate the next guard question using AI service with fallback to rule-based logic
        Returns the question and metadata
        """
        session = self.db.query(FirstLoginSession).filter_by(id=session_id).first()
        
        if not session:
            logger.error(f"Invalid session ID in generate_guard_question: {session_id}")
            raise ValueError("Invalid session ID")
        
        # Ensure we have the latest session data
        try:
            self.db.refresh(session)
        except Exception as e:
            logger.warning(f"Could not refresh session: {e}")
            # Continue anyway, session might be detached
        
        # Get the current dialogue exchanges
        exchanges = self.db.query(DialogueExchange).filter_by(
            session_id=session_id
        ).order_by(DialogueExchange.sequence_number).all()
        
        # Determine the next sequence number
        next_sequence = len(exchanges) + 1
        
        # Try AI-powered question generation with enhanced provider fallback
        question = None
        topic = "ai_generated"  # Default topic for AI-generated questions
        ai_used = False
        provider_used = None
        
        if self.ai_provider_service:
            try:
                # Build context for AI service
                context = self._build_dialogue_context(session, exchanges)
                
                # Build analysis from the last response if available
                from src.services.ai_dialogue_service import ResponseAnalysis, GuardMood
                
                # Get the most recent exchange with a response to base our analysis on
                last_exchange_with_response = None
                for exchange in reversed(exchanges):
                    if exchange.player_response:
                        last_exchange_with_response = exchange
                        break
                
                if last_exchange_with_response:
                    # Use actual analysis data from the last exchange
                    last_analysis = ResponseAnalysis(
                        persuasiveness_score=last_exchange_with_response.persuasiveness or 0.5,
                        confidence_level=last_exchange_with_response.confidence or 0.5,
                        consistency_score=last_exchange_with_response.consistency or 0.5,
                        negotiation_skill=context.negotiation_skill_level,
                        detected_inconsistencies=last_exchange_with_response.detected_contradictions or [],
                        extracted_claims=last_exchange_with_response.key_extracted_info.get('claims', []) if last_exchange_with_response.key_extracted_info else [],
                        overall_believability=(last_exchange_with_response.persuasiveness or 0.5 + last_exchange_with_response.confidence or 0.5) / 2,
                        suggested_guard_mood=context.guard_mood
                    )
                else:
                    # First question - use neutral baseline
                    last_analysis = ResponseAnalysis(
                        persuasiveness_score=0.5,
                        confidence_level=0.5,
                        consistency_score=1.0,  # No inconsistencies yet
                        negotiation_skill=0.5,
                        detected_inconsistencies=[],
                        extracted_claims=[],
                        overall_believability=0.5,
                        suggested_guard_mood=GuardMood.NEUTRAL
                    )
                
                # Use enhanced AI provider service
                guard_response, provider_used = await self.ai_provider_service.generate_question(context, last_analysis)
                question = guard_response.dialogue_text
                ai_used = provider_used != ProviderType.MANUAL
                
                # Update session flags if we used AI
                if ai_used:
                    session.ai_service_used = True
                    session.fallback_to_rules = False
                else:
                    session.fallback_to_rules = True
                
                logger.info(f"Generated question using {provider_used.value} provider for session {session_id}")
                
            except Exception as e:
                logger.error(f"All AI providers failed for question generation in session {session_id}: {e}")
                # Fall back to rule-based generation
        else:
            logger.info(f"No AI provider service available, using rule-based generation for session {session_id}")
        
        # Fallback to rule-based generation if AI failed or unavailable
        if not question:
            # Choose a topic based on the ship claimed and what's been asked already
            asked_topics = [exchange.topic for exchange in exchanges if exchange.topic != "introduction"]
            remaining_topics = [topic for topic in QUESTION_TOPICS if topic not in asked_topics]
            
            # If all topics have been asked, choose a random one
            topic = random.choice(remaining_topics) if remaining_topics else random.choice(QUESTION_TOPICS)
            
            # Generate a question based on the topic and claimed ship
            question = self._generate_question_for_topic(session, topic, exchanges)
            
            if not ai_used:
                session.fallback_to_rules = True
                logger.info(f"Using rule-based question generation for session {session_id}")
        
        # Ensure we have a question (final fallback)
        if not question:
            logger.error(f"No question generated for session {session_id}, using emergency fallback")
            question = "Hold on, let me verify your credentials. What's your pilot registration number?"
            topic = "identity_verification"

        # Localize the guard question into the player's preferred language (defensive)
        question = await self._localize_for_player(session.player_id, question)

        # Create a new dialogue exchange with AI metadata
        # Store suspicion level if available from AI-generated response
        suspicion_to_store = None
        if ai_used and 'guard_response' in locals():
            suspicion_to_store = guard_response.suspicion_level

        exchange = DialogueExchange(
            session_id=session_id,
            sequence_number=next_sequence,
            npc_prompt=question,
            player_response="",  # Player hasn't responded yet
            topic=topic,
            ai_provider=provider_used.value if provider_used and ai_used else "fallback",
            current_suspicion=suspicion_to_store  # Store AI-calculated suspicion
        )
        self.db.add(exchange)
        
        # Commit the exchange to database
        try:
            self.db.commit()
            self.db.refresh(exchange)
        except Exception as e:
            logger.error(f"Failed to commit dialogue exchange: {e}")
            self.db.rollback()
            raise
        
        return {
            "exchange_id": exchange.id,
            "sequence_number": exchange.sequence_number,
            "question": question,
            "topic": topic,
            "ai_used": ai_used
        }
    
    def generate_guard_question_sync(self, session_id: uuid.UUID) -> Dict[str, Any]:
        """
        Generate the next guard question based on the conversation history
        Returns the question and metadata
        """
        session = self.db.query(FirstLoginSession).filter_by(id=session_id).first()
        
        if not session:
            raise ValueError("Invalid session ID")
        
        # Get the current dialogue exchanges
        exchanges = self.db.query(DialogueExchange).filter_by(
            session_id=session_id
        ).order_by(DialogueExchange.sequence_number).all()
        
        # Determine the next sequence number
        next_sequence = len(exchanges) + 1
        
        # Choose a topic based on the ship claimed and what's been asked already
        asked_topics = [exchange.topic for exchange in exchanges if exchange.topic != "introduction"]
        remaining_topics = [topic for topic in QUESTION_TOPICS if topic not in asked_topics]
        
        # If all topics have been asked, choose a random one
        topic = random.choice(remaining_topics) if remaining_topics else random.choice(QUESTION_TOPICS)
        
        # Generate a question based on the topic and claimed ship
        question = self._generate_question_for_topic(session, topic, exchanges)
        
        # Create a new dialogue exchange
        exchange = DialogueExchange(
            session_id=session_id,
            sequence_number=next_sequence,
            npc_prompt=question,
            player_response="",  # Player hasn't responded yet
            topic=topic
        )
        self.db.add(exchange)
        
        # Commit the exchange to database
        try:
            self.db.commit()
            self.db.refresh(exchange)
        except Exception as e:
            logger.error(f"Failed to commit dialogue exchange: {e}")
            self.db.rollback()
            raise
        
        return {
            "exchange_id": exchange.id,
            "sequence_number": exchange.sequence_number,
            "question": question,
            "topic": topic
        }
    
    def _generate_question_for_topic(
        self, 
        session: FirstLoginSession, 
        topic: str, 
        exchanges: List[DialogueExchange]
    ) -> str:
        """Generate a question for a specific topic based on conversation history"""
        # Get the claimed ship (or default to escape pod)
        claimed_ship = session.ship_claimed or ShipChoice.ESCAPE_POD
        
        # Questions by topic and ship type
        questions = {
            "identity_verification": {
                "default": [
                    "What's your pilot registration name?",
                    "What's your clearance code for this sector?",
                    "May I see your pilot's license ID number?"
                ],
                ShipChoice.CARGO_HAULER: [
                    "As a freighter captain, you should have a merchant guild ID. What is it?",
                    "What's your cargo hauling certification number?",
                    "Which shipping company do you represent?"
                ],
                ShipChoice.SCOUT_SHIP: [
                    "Scouts need special reconnaissance clearance. What's yours?",
                    "Which survey division are you attached to?",
                    "What's your scout classification code?"
                ]
            },
            "arrival_details": {
                "default": [
                    "When did you dock at this station?",
                    "Who processed your landing clearance?",
                    "What was your approach vector when you arrived?"
                ],
                ShipChoice.CARGO_HAULER: [
                    "Where was your last cargo picked up?",
                    "Which docking bay are you assigned to?",
                    "What's your delivery schedule for this shipment?"
                ],
                ShipChoice.DEFENDER: [
                    "What sector were you last patrolling?",
                    "Which security division dispatched you here?",
                    "What's your current assignment code?"
                ]
            },
            "ship_knowledge": {
                "default": [
                    "What's the maximum warp capacity of your vessel?",
                    "How old is your ship's registration?",
                    "What's your ship's registry identification?"
                ],
                ShipChoice.SCOUT_SHIP: [
                    "What's the maximum sensor range on your scout vessel?",
                    "What propulsion system does your scout use?",
                    "What's the scout ship's maximum sustainable speed?"
                ],
                ShipChoice.CARGO_HAULER: [
                    "What's your freighter's maximum cargo capacity?",
                    "What type of cargo shielding does your freighter use?",
                    "How many cargo bays does your ship have?"
                ]
            },
            "situational_awareness": {
                "default": [
                    "Why is your ship docked in this restricted area?",
                    "Do you have authorization for the outer rim transit lanes?",
                    "Are you aware of the current security protocols?"
                ],
                ShipChoice.ESCAPE_POD: [
                    "Escape pods should be registered with emergency services. Have you done that?",
                    "Who authorized your escape pod to dock at this specific bay?",
                    "Why were you using an escape pod to travel here?"
                ],
                ShipChoice.FAST_COURIER: [
                    "What's the priority classification of your current delivery?",
                    "Who's the recipient of your courier package?",
                    "What's your estimated delivery time?"
                ]
            }
        }
        
        # Get the questions for the topic
        topic_questions = questions.get(topic, {"default": ["What brings you to this station?"]})
        
        # Try to get ship-specific questions, fall back to default
        ship_questions = topic_questions.get(claimed_ship, topic_questions["default"])
        
        # If we're on the second or third question, make it more suspicious
        if len(exchanges) >= 3:
            question = random.choice(ship_questions)
            return f"That's interesting... {question} And this time, I want a straight answer."
        elif len(exchanges) >= 2:
            question = random.choice(ship_questions)
            return f"Hmm, I'm not sure I believe that. {question}"
        else:
            return f"Guard: \"{random.choice(ship_questions)}\""
    
    async def record_player_answer(
        self, 
        exchange_id: uuid.UUID, 
        player_response: str
    ) -> Dict[str, Any]:
        """Record the player's answer to a guard question with AI-powered analysis"""
        exchange = self.db.query(DialogueExchange).filter_by(id=exchange_id).first()
        
        if not exchange:
            raise ValueError("Invalid exchange ID")
        
        # Update the exchange with the player's response
        exchange.player_response = player_response
        
        # Get the session and previous exchanges
        session = self.db.query(FirstLoginSession).filter_by(id=exchange.session_id).first()
        previous_exchanges = self.db.query(DialogueExchange).filter(
            DialogueExchange.session_id == exchange.session_id,
            DialogueExchange.sequence_number < exchange.sequence_number
        ).all()
        
        # Try AI-powered analysis with enhanced provider fallback
        ai_analysis = None
        ai_used = False
        provider_used = None

        try:
            # Build context for AI analysis
            context = self._build_dialogue_context(session, previous_exchanges + [exchange])

            # Analyze player response with enhanced AI provider service
            logger.info(f"Attempting AI analysis for exchange {exchange_id}")
            ai_analysis, provider_used = await self.ai_provider_service.analyze_response(player_response, context)
            ai_used = provider_used != ProviderType.MANUAL

            # Store AI analysis in the exchange
            exchange.ai_analysis_data = {
                "persuasiveness_score": ai_analysis.persuasiveness_score,
                "confidence_level": ai_analysis.confidence_level,
                "consistency_score": ai_analysis.consistency_score,
                "negotiation_skill": ai_analysis.negotiation_skill,
                "detected_inconsistencies": ai_analysis.detected_inconsistencies,
                "extracted_claims": ai_analysis.extracted_claims,
                "overall_believability": ai_analysis.overall_believability,
                "suggested_guard_mood": ai_analysis.suggested_guard_mood.value,
                "provider_used": provider_used.value
            }

            # Set scores from AI analysis
            exchange.persuasiveness = ai_analysis.persuasiveness_score
            exchange.confidence = ai_analysis.confidence_level
            exchange.consistency = ai_analysis.consistency_score
            exchange.key_extracted_info = {"claims": ai_analysis.extracted_claims}
            exchange.detected_contradictions = ai_analysis.detected_inconsistencies

            # Extract player name from AI claims if not already set
            if not session.extracted_player_name:
                for claim in ai_analysis.extracted_claims:
                    if any(keyword in claim.lower() for keyword in ["name", "captain", "pilot"]):
                        # Try to extract name from the claim
                        extracted_name = self._extract_player_name(claim)
                        if extracted_name:
                            session.extracted_player_name = extracted_name
                            break

            # Update session flags
            session.ai_service_used = ai_used
            exchange.ai_service_used = ai_used
            exchange.fallback_to_rules = not ai_used

            logger.info(f"✓ Analysis completed using {provider_used.value} provider for exchange {exchange_id} | " +
                       f"Scores: P={ai_analysis.persuasiveness_score:.2f} C={ai_analysis.confidence_level:.2f} " +
                       f"Cons={ai_analysis.consistency_score:.2f} B={ai_analysis.overall_believability:.2f}")

        except Exception as e:
            logger.error(f"✗ All AI providers failed for analysis in exchange {exchange_id}: {e}")
            ai_used = False
            provider_used = ProviderType.MANUAL
            # Store error info for frontend debugging
            self.last_ai_error = str(e)
        
        # Fallback to rule-based analysis if AI failed or unavailable
        if not ai_used:
            analysis = self._analyze_player_response_in_context(player_response, previous_exchanges)
            exchange.persuasiveness = analysis.get("persuasiveness", 0.5)
            exchange.confidence = analysis.get("confidence", 0.5)
            exchange.consistency = analysis.get("consistency", 0.5)
            exchange.key_extracted_info = analysis.get("extracted_info", {})
            exchange.detected_contradictions = analysis.get("contradictions", [])
            
            # Traditional name extraction
            if not session.extracted_player_name and exchange.sequence_number > 1:
                extracted_name = self._extract_player_name(player_response)
                if extracted_name:
                    session.extracted_player_name = extracted_name
            
            exchange.fallback_to_rules = True
            logger.info(f"Using rule-based analysis for exchange {exchange_id}")
        
        # Check if we've completed enough exchanges for a decision
        completed_exchanges = len(previous_exchanges) + 1
        
        self.db.commit()
        self.db.refresh(exchange)

        # Calculate believability if not from AI (fallback calculation)
        if not ai_analysis:
            # Calculate believability from basic scores using weighted formula
            believability = (
                exchange.persuasiveness * 0.4 +
                exchange.confidence * 0.3 +
                exchange.consistency * 0.3
            )
        else:
            believability = ai_analysis.overall_believability

        # Build analysis response - ALWAYS include all fields
        analysis_data = {
            "persuasiveness": exchange.persuasiveness,
            "confidence": exchange.confidence,
            "consistency": exchange.consistency,
            "overall_believability": believability,  # ← ALWAYS present now
            "ai_used": ai_used,
            "provider": provider_used.value if provider_used else "unknown",  # ← Provider tracking
            "ai_error": getattr(self, 'last_ai_error', None) if not ai_used else None  # ← Error info for debugging
        }

        # Add AI-specific analysis if available
        if ai_analysis:
            analysis_data.update({
                "negotiation_skill": ai_analysis.negotiation_skill,
                "detected_inconsistencies": ai_analysis.detected_inconsistencies,
                "extracted_claims": ai_analysis.extracted_claims,
                "guard_mood": ai_analysis.suggested_guard_mood.value
            })

        # Calculate and update suspicion level
        # Suspicion should decrease with high believability, increase with low believability
        # Start from guard's base suspicion level or previous suspicion
        previous_suspicion = exchange.current_suspicion if exchange.current_suspicion is not None else session.guard_base_suspicion or 0.5

        # Calculate suspicion change based on believability
        # High believability (0.8+) → decrease suspicion significantly
        # Low believability (0.3-) → increase suspicion significantly
        # Medium believability → slight changes
        if believability >= 0.8:
            suspicion_change = -0.15  # Strong reduction
        elif believability >= 0.6:
            suspicion_change = -0.08  # Moderate reduction
        elif believability >= 0.4:
            suspicion_change = 0.0  # Neutral
        elif believability >= 0.2:
            suspicion_change = 0.10  # Moderate increase
        else:
            suspicion_change = 0.20  # Strong increase

        # Apply change and clamp to 0-1 range
        current_suspicion = max(0.0, min(1.0, previous_suspicion + suspicion_change))

        # Update the exchange with calculated suspicion
        exchange.current_suspicion = current_suspicion
        self.db.commit()  # Persist the suspicion update

        logger.info(f"Suspicion tracking: {previous_suspicion:.2f} → {current_suspicion:.2f} (believability={believability:.2f})")

        # Check for early termination conditions
        early_termination = False
        termination_reason = None

        # Early termination: Guard is too suspicious (caught red-handed)
        if current_suspicion > 0.85 and completed_exchanges >= 2:
            early_termination = True
            termination_reason = "high_suspicion"
            logger.info(f"Early termination triggered: High suspicion ({current_suspicion:.2f}) after {completed_exchanges} exchanges")

        # Early termination: Guard is convinced (smooth talker)
        elif current_suspicion < 0.20 and believability > 0.75 and completed_exchanges >= 3:
            early_termination = True
            termination_reason = "convinced"
            logger.info(f"Early termination triggered: Low suspicion ({current_suspicion:.2f}), high believability ({believability:.2f}) after {completed_exchanges} exchanges")

        # Normal completion after 5 questions
        is_final = completed_exchanges >= 5 or early_termination

        result = {
            "exchange_id": exchange.id,
            "analysis": analysis_data,
            "is_final": is_final,  # After 5 exchanges or early termination
            "early_termination": early_termination,
            "termination_reason": termination_reason if early_termination else None
        }

        # If this is the final exchange, evaluate the outcome
        if result["is_final"]:
            outcome = await self._evaluate_dialogue_outcome(session)
            result["outcome"] = outcome

            # Mark the player as having completed questions
            state = self.get_player_first_login_state(session.player_id)
            state.answered_questions = True
            self.db.commit()

        return result
    
    async def record_player_answer_sync(
        self,
        exchange_id: uuid.UUID,
        player_response: str
    ) -> Dict[str, Any]:
        """Async version of record_player_answer for backward compatibility (now async due to outcome generation)"""
        exchange = self.db.query(DialogueExchange).filter_by(id=exchange_id).first()
        
        if not exchange:
            raise ValueError("Invalid exchange ID")
        
        # Update the exchange with the player's response
        exchange.player_response = player_response
        
        # Get the session and previous exchanges
        session = self.db.query(FirstLoginSession).filter_by(id=exchange.session_id).first()
        previous_exchanges = self.db.query(DialogueExchange).filter(
            DialogueExchange.session_id == exchange.session_id,
            DialogueExchange.sequence_number < exchange.sequence_number
        ).all()
        
        # Use rule-based analysis for synchronous calls
        analysis = self._analyze_player_response_in_context(player_response, previous_exchanges)
        exchange.persuasiveness = analysis.get("persuasiveness", 0.5)
        exchange.confidence = analysis.get("confidence", 0.5)
        exchange.consistency = analysis.get("consistency", 0.5)
        exchange.key_extracted_info = analysis.get("extracted_info", {})
        exchange.detected_contradictions = analysis.get("contradictions", [])
        
        # If this is a later exchange and player name is not set, try to extract it
        if not session.extracted_player_name and exchange.sequence_number > 1:
            extracted_name = self._extract_player_name(player_response)
            if extracted_name:
                session.extracted_player_name = extracted_name
        
        # Mark as using fallback since this is synchronous
        exchange.fallback_to_rules = True
        
        # Check if we've completed enough exchanges for a decision
        completed_exchanges = len(previous_exchanges) + 1
        
        self.db.commit()
        self.db.refresh(exchange)
        
        result = {
            "exchange_id": exchange.id,
            "analysis": {
                "persuasiveness": exchange.persuasiveness,
                "confidence": exchange.confidence,
                "consistency": exchange.consistency,
                "ai_used": False
            },
            "is_final": completed_exchanges >= 3  # After 3 exchanges, make a decision
        }
        
        # If this is the final exchange, evaluate the outcome
        if result["is_final"]:
            outcome = await self._evaluate_dialogue_outcome(session)
            result["outcome"] = outcome
            
            # Mark the player as having completed questions
            state = self.get_player_first_login_state(session.player_id)
            state.answered_questions = True
            self.db.commit()
        
        return result
    
    def _analyze_player_response_in_context(
        self, 
        response: str, 
        previous_exchanges: List[DialogueExchange]
    ) -> Dict[str, Any]:
        """
        Analyze a player's response in the context of previous exchanges
        This is a basic implementation that could be replaced with an AI service
        """
        # Analyze the current response
        analysis = self._analyze_player_response(response)
        
        # Check for consistency with previous responses
        if previous_exchanges:
            consistency = 0.8  # Default consistency
            contradictions = []
            
            # Very basic consistency check based on word usage
            words_used = set()
            for exchange in previous_exchanges:
                if exchange.player_response:
                    exchange_words = set(exchange.player_response.lower().split())
                    words_used.update(exchange_words)
            
            # Check if current response uses different key terms
            current_words = set(response.lower().split())
            
            # Look for contradictions in these simple key terms
            contradiction_pairs = [
                ({"today", "this morning", "just now"}, {"yesterday", "last week", "last month"}),
                ({"new", "brand new"}, {"old", "used", "vintage"}),
                ({"bought", "purchased"}, {"inherited", "gifted", "found"})
            ]
            
            for previous, current in contradiction_pairs:
                previous_match = any(word in words_used for word in previous)
                current_match = any(word in current_words for word in current)
                
                if previous_match and current_match:
                    consistency -= 0.2
                    contradictions.append(f"Contradiction between {'/'.join(previous)} and {'/'.join(current)}")
            
            # Update the analysis
            analysis["consistency"] = max(0.0, min(1.0, consistency))
            analysis["contradictions"] = contradictions
        
        return analysis

    async def auto_approve_escape_pod(self, session_id: uuid.UUID) -> Dict[str, Any]:
        """
        Auto-approve escape pod claims without interrogation.
        Escape pods are low-value ships that don't warrant extensive questioning.
        """
        session = self.db.query(FirstLoginSession).filter_by(id=session_id).first()

        if not session:
            raise ValueError(f"Invalid session ID: {session_id}")

        logger.info(f"Auto-approving escape pod for session {session_id}")

        # Set outcome immediately - SUCCESS with escape pod
        session.negotiation_skill = NegotiationSkillLevel.AVERAGE
        session.final_persuasion_score = 0.5
        session.outcome = DialogueOutcome.SUCCESS
        session.awarded_ship = ShipChoice.ESCAPE_POD
        session.starting_credits = 1000  # Standard escape pod starting credits
        session.negotiation_bonus_flag = False
        session.notoriety_penalty = False

        # Mark dialogue as completed
        if not session.completed_at:
            session.completed_at = datetime.now()

        # Update the player's first login state
        state = self.get_player_first_login_state(session.player_id)
        state.answered_questions = True

        self.db.commit()
        self.db.refresh(session)

        # Generate guard response
        guard_response = (
            "An escape pod? *Guard looks sympathetic* "
            "Rough journey, I'd guess. Yeah, you can have it - no questions asked. "
            "Everyone deserves a second chance. Welcome to Callisto Colony, friend. "
            "Good luck out there."
        )

        logger.info(f"Escape pod auto-approved - granted immediately")

        # Localize the guard response into the player's preferred language (defensive)
        guard_response = await self._localize_for_player(session.player_id, guard_response)

        return {
            "outcome": {
                "outcome": "SUCCESS",
                "awarded_ship": "ESCAPE_POD",
                "starting_credits": 1000,
                "negotiation_skill": "AVERAGE",
                "final_persuasion_score": 0.5,
                "negotiation_bonus": False,
                "notoriety_penalty": False
            },
            "guard_response": guard_response
        }

    async def _evaluate_dialogue_outcome(self, session: FirstLoginSession) -> Dict[str, Any]:
        """Evaluate the dialogue outcome based on the player's performance"""
        logger.info(f"=" * 60)
        logger.info(f"EVALUATING DIALOGUE OUTCOME FOR SESSION {session.id}")
        logger.info(f"=" * 60)

        # Get all exchanges for the session
        exchanges = self.db.query(DialogueExchange).filter_by(
            session_id=session.id
        ).order_by(DialogueExchange.sequence_number).all()

        logger.info(f"Total exchanges: {len(exchanges)}")

        # Calculate average persuasiveness, confidence, and consistency
        persuasiveness_scores = [exchange.persuasiveness for exchange in exchanges if exchange.persuasiveness is not None]
        confidence_scores = [exchange.confidence for exchange in exchanges if exchange.confidence is not None]
        consistency_scores = [exchange.consistency for exchange in exchanges if exchange.consistency is not None]

        avg_persuasiveness = sum(persuasiveness_scores) / len(persuasiveness_scores) if persuasiveness_scores else 0.5
        avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.5
        avg_consistency = sum(consistency_scores) / len(consistency_scores) if consistency_scores else 0.5

        logger.info(f"Average Scores:")
        logger.info(f"  Persuasiveness: {avg_persuasiveness:.4f} (from {persuasiveness_scores})")
        logger.info(f"  Confidence: {avg_confidence:.4f} (from {confidence_scores})")
        logger.info(f"  Consistency: {avg_consistency:.4f} (from {consistency_scores})")

        # Calculate overall persuasion score (weighted)
        # Consistency is CRITICAL (50%) - lying/contradictions should be heavily penalized
        # Confidence (30%) and Persuasiveness (20%) are secondary
        final_persuasion_score = (
            avg_consistency * 0.5 +
            avg_confidence * 0.3 +
            avg_persuasiveness * 0.2
        )

        logger.info(f"Final Persuasion Score: {final_persuasion_score:.4f}")
        logger.info(f"  Formula: ({avg_consistency:.4f} * 0.5) + ({avg_confidence:.4f} * 0.3) + ({avg_persuasiveness:.4f} * 0.2)")

        # HARD-FAIL CHECKS: Instant denial for critical failures
        hard_fail_reason = None

        # Check 1: Consistency catastrophically low (confession/major contradiction)
        if avg_consistency < 0.3:
            hard_fail_reason = f"Critical consistency failure ({avg_consistency:.2f}) - Player confessed to lying or major contradictions detected"
            logger.warning(f"⚠️  HARD FAIL: {hard_fail_reason}")

        # Check 2: Multiple contradictions detected
        total_contradictions = sum(
            len(exchange.detected_contradictions) if exchange.detected_contradictions else 0
            for exchange in exchanges
        )
        if total_contradictions >= 3:
            hard_fail_reason = f"Multiple contradictions detected ({total_contradictions}) - Story fell apart"
            logger.warning(f"⚠️  HARD FAIL: {hard_fail_reason}")

        # Check 3: Any individual response with consistency < 0.2 (confession indicator)
        for exchange in exchanges:
            if exchange.consistency is not None and exchange.consistency < 0.2:
                hard_fail_reason = f"Confession detected (consistency {exchange.consistency:.2f} on question {exchange.sequence_number})"
                logger.warning(f"⚠️  HARD FAIL: {hard_fail_reason}")
                break

        # Determine negotiation skill level
        if final_persuasion_score >= 0.7:
            negotiation_skill = NegotiationSkillLevel.STRONG
        elif final_persuasion_score >= 0.4:
            negotiation_skill = NegotiationSkillLevel.AVERAGE
        else:
            negotiation_skill = NegotiationSkillLevel.WEAK

        logger.info(f"Negotiation Skill Level: {negotiation_skill.name}")

        # Get the claimed ship and ship config
        claimed_ship = session.ship_claimed or ShipChoice.ESCAPE_POD
        ship_config = self.db.query(ShipRarityConfig).filter_by(ship_type=claimed_ship).first()

        logger.info(f"Claimed Ship: {claimed_ship.name}")
        if ship_config:
            logger.info(f"Ship Config Thresholds:")
            logger.info(f"  WEAK: {ship_config.weak_threshold}")
            logger.info(f"  AVERAGE: {ship_config.average_threshold}")
            logger.info(f"  STRONG: {ship_config.strong_threshold}")
        else:
            logger.error(f"❌ NO SHIP CONFIG FOUND for {claimed_ship.name}!")

        # Determine the persuasion threshold for the claimed ship
        if negotiation_skill == NegotiationSkillLevel.STRONG:
            base_threshold = ship_config.strong_threshold
        elif negotiation_skill == NegotiationSkillLevel.AVERAGE:
            base_threshold = ship_config.average_threshold
        else:
            base_threshold = ship_config.weak_threshold

        # Apply guard personality modifier to threshold
        # Friendly guards are easier (lower threshold)
        # Strict/paranoid guards are harder (higher threshold)
        personality_modifier = 0.0
        if session.guard_base_suspicion <= 0.35:  # Friendly Veteran
            personality_modifier = -0.10  # 10% easier
        elif session.guard_base_suspicion >= 0.60:  # Strict Rule-Follower or Paranoid Newbie
            personality_modifier = +0.10  # 10% harder

        threshold = base_threshold + personality_modifier
        threshold = max(0.2, min(0.95, threshold))  # Clamp between 0.2 and 0.95

        logger.info(f"Base Threshold ({negotiation_skill.name}): {base_threshold}")
        logger.info(f"Guard Personality ({session.guard_trait}): {session.guard_base_suspicion:.2f} suspicion")
        logger.info(f"Personality Modifier: {personality_modifier:+.2f}")
        logger.info(f"Final Threshold: {threshold:.2f}")

        logger.info(f"Required Threshold ({negotiation_skill.name}): {threshold}")
        logger.info(f"Player Score: {final_persuasion_score:.4f}")
        logger.info(f"Comparison: {final_persuasion_score:.4f} >= {threshold} ? {final_persuasion_score >= threshold}")

        # Determine the outcome
        # HARD-FAIL overrides everything
        if hard_fail_reason:
            # Automatic failure for critical violations
            logger.error(f"❌ FORCING FAILURE DUE TO: {hard_fail_reason}")
            outcome = DialogueOutcome.FAILURE
            awarded_ship = ShipChoice.ESCAPE_POD
            starting_credits = 300  # Extra penalty for lying
            negotiation_bonus_flag = False
            notoriety_penalty = True  # Lying gets you flagged
        elif final_persuasion_score >= threshold:
            # Success - player gets the claimed ship
            outcome = DialogueOutcome.SUCCESS
            awarded_ship = claimed_ship
            starting_credits = ship_config.base_credits
            
            # Add negotiation bonus for strong negotiators with higher tier ships
            negotiation_bonus_flag = (
                negotiation_skill == NegotiationSkillLevel.STRONG and
                ship_config.rarity_tier >= 3
            )
            
            notoriety_penalty = False
        elif claimed_ship == ShipChoice.ESCAPE_POD:
            # Partial success - player gets the escape pod but with reduced credits
            outcome = DialogueOutcome.PARTIAL_SUCCESS
            awarded_ship = ShipChoice.ESCAPE_POD
            starting_credits = 800  # Reduced from 1000
            negotiation_bonus_flag = False
            notoriety_penalty = False
        else:
            # Failure - player attempted to claim a better ship but failed
            outcome = DialogueOutcome.FAILURE
            awarded_ship = ShipChoice.ESCAPE_POD
            starting_credits = 500  # Significant reduction
            negotiation_bonus_flag = False
            notoriety_penalty = True
        
        # Update the session with the outcome
        session.negotiation_skill = negotiation_skill
        session.final_persuasion_score = final_persuasion_score
        session.outcome = outcome
        session.awarded_ship = awarded_ship
        session.starting_credits = starting_credits
        session.negotiation_bonus_flag = negotiation_bonus_flag
        session.notoriety_penalty = notoriety_penalty

        # Mark dialogue as completed when outcome is determined
        # (Resources will be allocated later when player clicks "Start Game")
        if not session.completed_at:
            session.completed_at = datetime.now()

        logger.info(f"=" * 60)
        logger.info(f"FINAL OUTCOME: {outcome.name}")
        logger.info(f"  Awarded Ship: {awarded_ship.name}")
        logger.info(f"  Starting Credits: {starting_credits}")
        logger.info(f"  Negotiation Bonus: {negotiation_bonus_flag}")
        logger.info(f"  Notoriety Penalty: {notoriety_penalty}")
        logger.info(f"=" * 60)

        self.db.commit()
        self.db.refresh(session)
        
        # Generate AI-powered personalized outcome message
        guard_response = await self._generate_guard_outcome_response_async(session)

        # Localize the outcome message into the player's preferred language (defensive)
        guard_response = await self._localize_for_player(session.player_id, guard_response)

        return {
            "outcome": outcome.name,
            "awarded_ship": awarded_ship.name,
            "starting_credits": starting_credits,
            "negotiation_skill": negotiation_skill.name,
            "final_persuasion_score": final_persuasion_score,
            "negotiation_bonus": negotiation_bonus_flag,
            "notoriety_penalty": notoriety_penalty,
            "guard_response": guard_response,
            # Surfaced so the client can offer the nickname-confirmation
            # prompt (WO-PUX-FLOGIN-NICKNAME); complete_first_login is the
            # only place that ever writes it to Player.nickname, and only
            # once the player has explicitly confirmed it.
            "extracted_player_name": session.extracted_player_name,
        }

    async def _generate_guard_outcome_response_async(self, session: FirstLoginSession) -> str:
        """Generate AI-powered personalized guard response based on the dialogue outcome"""
        try:
            # Get all dialogue exchanges for this session
            exchanges = self.db.query(DialogueExchange).filter_by(
                session_id=session.id
            ).order_by(DialogueExchange.sequence_number).all()

            # Build conversation history
            conversation_history = []
            for exchange in exchanges:
                if exchange.player_response:
                    conversation_history.append({
                        'npc': exchange.npc_prompt,
                        'player': exchange.player_response
                    })

            # Build the outcome generation prompt
            prompts = self.ai_service.build_outcome_generation_prompt(
                guard_name=session.guard_name,
                guard_title=session.guard_title,
                guard_trait=session.guard_trait,
                outcome_type=session.outcome.name,
                claimed_ship=session.ship_claimed.name if session.ship_claimed else "ESCAPE_POD",
                awarded_ship=session.awarded_ship.name if session.awarded_ship else "ESCAPE_POD",
                final_score=session.final_persuasion_score or 0.0,
                negotiation_skill=session.negotiation_skill.name if session.negotiation_skill else "AVERAGE",
                conversation_history=conversation_history
            )

            # Generate AI response
            response, provider = await self.ai_provider_service.generate_outcome(prompts)
            logger.info(f"[AI-{provider.upper()}] Generated outcome response")

            return f"[AI-{provider.upper()}] {response}"

        except Exception as e:
            logger.error(f"Failed to generate AI outcome response: {e}", exc_info=True)
            # Fallback to static response
            return self._generate_guard_outcome_response_fallback(session)

    def _generate_guard_outcome_response_fallback(self, session: FirstLoginSession) -> str:
        """Fallback static response if AI generation fails"""
        claimed_ship = session.ship_claimed or ShipChoice.ESCAPE_POD
        awarded_ship = session.awarded_ship or ShipChoice.ESCAPE_POD

        if session.outcome == DialogueOutcome.SUCCESS:
            if claimed_ship == ShipChoice.ESCAPE_POD:
                return "[RULE-BASED] Everything seems to be in order. Your Escape Pod is cleared for departure."
            else:
                ship_type = claimed_ship.name.replace("_", " ").title()
                return f"[RULE-BASED] Alright, your credentials check out. Your {ship_type} is cleared for departure."
        elif session.outcome == DialogueOutcome.PARTIAL_SUCCESS:
            return "[RULE-BASED] There are some irregularities, but I'll clear your Escape Pod for departure."
        else:  # FAILURE
            ship_type = claimed_ship.name.replace("_", " ").title()
            return f"[RULE-BASED] Your story doesn't add up. You're getting the Escape Pod, not the {ship_type}."
    
    def complete_first_login(
        self,
        session_id: uuid.UUID,
        nickname_confirmed: bool = False,
        nickname_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Complete the first login process and grant the player their ship and credits.

        nickname_confirmed / nickname_override (WO-PUX-FLOGIN-NICKNAME):
        Player.nickname is written ONLY when the player explicitly confirmed
        the callsign AND it passes nickname_validation_service — never
        unconditionally from the dialogue extraction. Declining or failing
        validation never blocks completion (first-login.md:255); the
        rejection reason is returned instead so the client can offer a
        free-text retry.
        """
        session = self.db.query(FirstLoginSession).filter_by(id=session_id).first()

        if not session:
            raise ValueError("Invalid session ID")

        # Get the player
        player = self.db.query(Player).filter_by(id=session.player_id).first()

        if not player:
            raise ValueError(f"Player not found: {session.player_id}")

        # Idempotency guard (WO-PUX-FLOGIN-IDEMPOTENT): has_completed_first_login
        # is the only reliable marker here -- it is set exactly once, at the
        # very end of this method, after every side effect below has already
        # run. session.completed_at is NOT usable for this: it is already
        # stamped by _evaluate_dialogue_outcome as soon as the dialogue
        # outcome is scored, before /complete is ever called (see
        # get_session_with_history's docstring above), which made the old
        # `if not session.completed_at:` check below permanently dead. A
        # repeat call (double-click, client retry, reload race) must be a
        # true no-op: no ship delete/create, no credit re-grant, no ARIA
        # reset, no nickname write.
        state = self.get_player_first_login_state(player.id)
        if state.has_completed_first_login:
            raise FirstLoginCompletionError(400, "First login already completed")

        # SELF-HEALING: First Login should always give a clean slate
        # If there's stale data from previous testing/sessions, clean it up
        logger.info(f"Completing First Login for player {player.id} - cleaning up any stale data")

        # Delete ALL existing ships (they shouldn't exist during First Login)
        existing_ships = self.db.query(Ship).filter_by(owner_id=player.id).all()
        if existing_ships:
            logger.info(f"Deleting {len(existing_ships)} stale ships: {[s.name for s in existing_ships]}")
            for ship in existing_ships:
                self.db.delete(ship)
            self.db.flush()  # Ensure ships are deleted before creating new one

        # Mark the session as completed (if not already marked during dialogue evaluation)
        if not session.completed_at:
            session.completed_at = datetime.now()
        
        # Update the player with the awarded resources
        player.credits = session.starting_credits
        
        # Nickname capture — gated on explicit confirmation + validation
        # (canon: first-login.md:252-255). RETIRES the prior unconditional
        # `player.nickname = session.extracted_player_name` write: an
        # unconfirmed or failed-validation candidate never reaches the
        # column, and completion still proceeds either way — a declined or
        # rejected nickname is never a blocking failure.
        nickname_rejected_reason = None
        if nickname_confirmed:
            candidate = nickname_override or session.extracted_player_name
            ok, reason = validate_nickname(
                self.db, candidate, exclude_player_id=player.id
            )
            if ok:
                player.nickname = candidate
            else:
                nickname_rejected_reason = reason

        # Create the player's starter ship
        ship_type = SHIP_CHOICE_TO_TYPE.get(session.awarded_ship, ShipType.LIGHT_FREIGHTER)
        ship_name = f"{player.nickname or player.username}'s {ship_type.name.replace('_', ' ').title()}"

        # B3: copy the per-hull shield/armor mitigation fractions from the
        # ShipSpecification onto the Ship row — combat_service reads these off
        # the Ship, not the spec (matches ship_service.py:105-106). A missing
        # spec row degrades to 0.0/0.0 rather than blocking first-login.
        spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ship_type
        ).first()
        if not spec:
            logger.warning(
                f"No ShipSpecification found for starter ship type {ship_type}; "
                "defaulting shield_resistance/armor_rating to 0.0"
            )

        new_ship = Ship(
            name=ship_name,
            type=ship_type,
            owner_id=player.id,
            sector_id=player.current_sector_id,
            base_speed=1.0,  # Basic attributes
            current_speed=1.0,
            turn_cost=1,
            warp_capable=False,
            is_active=True,
            maintenance={"status": "good", "next_service": None},
            cargo={"capacity": 50, "used": 0, "contents": {}},
            combat={"shields": 10, "weapons": 5},
            shield_resistance=(getattr(spec, 'shield_resistance', None) or 0.0),
            armor_rating=(getattr(spec, 'armor_rating', None) or 0.0),
            is_flagship=True,
            purchase_value=session.starting_credits // 2,
            current_value=session.starting_credits // 2
        )
        self.db.add(new_ship)
        self.db.flush()  # Get the ID
        
        # Set as player's current ship
        player.current_ship_id = new_ship.id
        
        # Apply any bonuses or penalties from the dialogue outcome
        if session.negotiation_bonus_flag:
            # Reassign (not in-place mutation) + flag_modified so SQLAlchemy
            # detects the JSONB change (matches
            # emergent_reputation_service._store_throttle_bucket /
            # faction_service.apply_faction_rep_delta's history pattern) --
            # the prior in-place `player.settings["trade_bonus"] = ...` was
            # invisible to the ORM and silently dropped at commit.
            settings = dict(player.settings) if isinstance(player.settings, dict) else {}
            settings["trade_bonus"] = 0.1  # 10% better prices
            player.settings = settings
            flag_modified(player, "settings")

        # notoriety_penalty is already persisted on the session (line ~1611,
        # `session.notoriety_penalty`) and surfaced in the response below --
        # SYSTEMS/first-login.md:186 documents the lower 300-credit hard-fail
        # payout plus that persistent session flag as the only mechanical
        # penalty for deceptive play. No replacement write belongs here; the
        # removed `player.reputation = {"faction1": -10}` was a ghost write
        # into a dead JSONB store the canonical Reputation table never reads.

        # Update the player's first login state
        state = self.get_player_first_login_state(player.id)
        state.has_completed_first_login = True
        state.received_resources = True
        
        # Update the player's first login flag in the main record
        player.first_login = {"completed": True, "session_id": str(session.id)}

        # Initialize ARIA relationship — warm start from first interaction
        player.aria_relationship_score = 50
        player.aria_total_interactions = 1

        # WO-CG3 — award first-login special medals (Orange Cat Society / Honorary
        # Tabby) for a cat-mention session, inside this open transaction. Lazy import
        # avoids a circular dependency; defensive dispatcher never breaks completion.
        try:
            import src.services.medal_service as _medal_module
            _award_special = getattr(
                _medal_module, "check_and_award_first_login_special_medals", None
            )
            if _award_special is not None:
                _award_special(self.db, session.id)
        except Exception as _e:  # never break first-login completion on medal award
            logger.error("first-login special-medal award failed for %s: %s", player.id, _e)

        self.db.commit()
        
        return {
            "player_id": str(player.id),
            "nickname": player.nickname,
            "credits": player.credits,
            "ship": {
                "id": str(new_ship.id),
                "name": new_ship.name,
                "type": new_ship.type.name
            },
            "negotiation_bonus": session.negotiation_bonus_flag,
            "notoriety_penalty": session.notoriety_penalty,
            "nickname_rejected_reason": nickname_rejected_reason,
        }
    
    def reset_player_session(self, session_id: uuid.UUID) -> None:
        """Reset a player's first login session, deleting all progress"""
        session = self.db.query(FirstLoginSession).filter_by(id=session_id).first()
        
        if not session:
            logger.warning(f"Attempted to reset non-existent session: {session_id}")
            return
        
        # Get the player ID before deleting
        player_id = session.player_id
        
        # Delete all dialogue exchanges for this session
        self.db.query(DialogueExchange).filter_by(session_id=session_id).delete()
        
        # Delete the session itself
        self.db.delete(session)
        
        # Reset the player's first login state
        state = self.get_player_first_login_state(player_id)
        state.current_session_id = None
        state.claimed_ship = False
        state.answered_questions = False
        state.received_resources = False
        state.tutorial_started = False
        # Don't reset attempts - this tracks total attempts across resets
        
        self.db.commit()
        logger.info(f"Reset first login session {session_id} for player {player_id}")