"""
Terraforming service for managing planetary habitability improvements.

This service handles starting, tracking, processing, cancelling,
and completing terraforming projects on player-owned planets.
"""

from typing import Dict, Any, Optional
from uuid import UUID
from datetime import datetime, timedelta, UTC
from sqlalchemy.orm import Session
from sqlalchemy import and_
import logging

from src.models.player import Player
from src.models.planet import Planet, PlanetStatus, player_planets

logger = logging.getLogger(__name__)

# Terraforming configuration constants
TERRAFORMING_CANCEL_REFUND = 0.50     # 50% refund on cancellation
TERRAFORMING_MAX_HABITABILITY = 100   # Maximum habitability score
TERRAFORMING_MIN_TARGET = 90          # Planets at or above this don't need terraforming
TERRAFORMING_BASE_INCREMENT = 1       # Minimum habitability gain per tick
TERRAFORMING_MAX_INCREMENT = 3        # Maximum habitability gain per tick
TERRAFORMING_POPULATION_SCALE = 1000  # Population per additional increment point

# 5-level terraforming system with escalating costs and rewards
TERRAFORMING_LEVELS = {
    1: {"name": "Basic Atmospheric", "cost": 100000, "duration_hours": 72, "habitability_boost": 10, "organics_cost": 500, "equipment_cost": 200},
    2: {"name": "Climate Stabilization", "cost": 250000, "duration_hours": 120, "habitability_boost": 15, "organics_cost": 1500, "equipment_cost": 500},
    3: {"name": "Ecosystem Seeding", "cost": 500000, "duration_hours": 168, "habitability_boost": 20, "organics_cost": 3000, "equipment_cost": 1000},
    4: {"name": "Biome Engineering", "cost": 1000000, "duration_hours": 240, "habitability_boost": 25, "organics_cost": 5000, "equipment_cost": 2000},
    5: {"name": "Full Terraformation", "cost": 2000000, "duration_hours": 336, "habitability_boost": 30, "organics_cost": 10000, "equipment_cost": 5000},
}


class TerraformingService:
    """Service for managing planetary terraforming operations."""

    def __init__(self, db: Session):
        self.db = db

    def _get_owned_planet(self, planet_id: UUID, player_id: UUID) -> Planet:
        """Retrieve a planet and verify it is owned by the given player."""
        planet = self.db.query(Planet).join(
            player_planets,
            Planet.id == player_planets.c.planet_id
        ).filter(
            and_(
                Planet.id == planet_id,
                player_planets.c.player_id == player_id
            )
        ).first()

        if not planet:
            raise ValueError("Planet not found or not owned by player")

        return planet

    def start_terraforming(
        self,
        planet_id: UUID,
        player_id: UUID,
        level: int = 1,
        target_habitability: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Begin terraforming a planet the player owns at the specified level.

        The 5-level terraforming system offers increasing habitability boosts
        at escalating credit and resource costs:
          Level 1: Basic Atmospheric       (+10 habitability)
          Level 2: Climate Stabilization   (+15 habitability)
          Level 3: Ecosystem Seeding       (+20 habitability)
          Level 4: Biome Engineering        (+25 habitability)
          Level 5: Full Terraformation     (+30 habitability)

        Args:
            planet_id: The planet to terraform
            player_id: The owning player
            level: Terraforming level 1-5 (default: 1)
            target_habitability: Desired habitability score (overrides level boost if provided)

        Raises:
            ValueError: If preconditions are not met

        Returns:
            Dict with terraforming project details
        """
        # Validate level
        if level not in TERRAFORMING_LEVELS:
            raise ValueError(
                f"Invalid terraforming level {level}. Must be 1-5."
            )

        level_config = TERRAFORMING_LEVELS[level]
        planet = self._get_owned_planet(planet_id, player_id)

        # Validate planet is eligible for terraforming
        if planet.habitability_score >= TERRAFORMING_MIN_TARGET:
            raise ValueError(
                f"Planet habitability is already {planet.habitability_score}%. "
                f"Terraforming is only available for planets below {TERRAFORMING_MIN_TARGET}%."
            )

        if planet.terraforming_active:
            raise ValueError("A terraforming project is already active on this planet")

        # Calculate target habitability from level boost or explicit target
        boost = level_config["habitability_boost"]
        if target_habitability is None:
            target_habitability = min(
                TERRAFORMING_MAX_HABITABILITY,
                planet.habitability_score + boost
            )
        else:
            target_habitability = min(target_habitability, TERRAFORMING_MAX_HABITABILITY)

        if target_habitability <= planet.habitability_score:
            raise ValueError(
                f"Target habitability ({target_habitability}) must be higher "
                f"than current ({planet.habitability_score})"
            )

        # Lock player for credit deduction race prevention
        player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
        if not player:
            raise ValueError("Player not found")

        credit_cost = level_config["cost"]
        if player.credits < credit_cost:
            raise ValueError(
                f"Insufficient credits. Level {level} ({level_config['name']}) "
                f"requires {credit_cost:,} credits, have {player.credits:,}"
            )

        # Check planet resource stocks (organics and equipment)
        organics_cost = level_config["organics_cost"]
        equipment_cost = level_config["equipment_cost"]

        if (planet.organics or 0) < organics_cost:
            raise ValueError(
                f"Insufficient organics on planet. Level {level} ({level_config['name']}) "
                f"requires {organics_cost:,} organics, planet has {planet.organics or 0:,}"
            )

        if (planet.equipment or 0) < equipment_cost:
            raise ValueError(
                f"Insufficient equipment on planet. Level {level} ({level_config['name']}) "
                f"requires {equipment_cost:,} equipment, planet has {planet.equipment or 0:,}"
            )

        # Deduct credits from player
        player.credits -= credit_cost

        # Deduct resources from planet
        planet.organics = (planet.organics or 0) - organics_cost
        planet.equipment = (planet.equipment or 0) - equipment_cost

        # Set terraforming state
        now = datetime.now(UTC)
        planet.terraforming_active = True
        planet.terraforming_target = target_habitability
        planet.terraforming_start_time = now
        planet.terraforming_progress = 0.0
        planet.status = PlanetStatus.TERRAFORMING

        # Store terraforming metadata in active_events for refund/completion tracking
        # We use the JSONB active_events column to persist the level details
        terraforming_meta = {
            "type": "terraforming",
            "level": level,
            "level_name": level_config["name"],
            "credit_cost": credit_cost,
            "organics_cost": organics_cost,
            "equipment_cost": equipment_cost,
            "habitability_boost": boost,
            "duration_hours": level_config["duration_hours"],
            "started_at": now.isoformat()
        }
        current_events = list(planet.active_events or [])
        # Remove any stale terraforming events
        current_events = [e for e in current_events if not (isinstance(e, dict) and e.get("type") == "terraforming")]
        current_events.append(terraforming_meta)
        planet.active_events = current_events

        self.db.commit()
        self.db.refresh(planet)
        self.db.refresh(player)

        estimated_completion = now + timedelta(hours=level_config["duration_hours"])

        logger.info(
            f"Terraforming L{level} ({level_config['name']}) started on planet "
            f"{planet.name} (id={planet.id}) by player {player_id}. "
            f"Target: {target_habitability}%, cost: {credit_cost} credits + "
            f"{organics_cost} organics + {equipment_cost} equipment"
        )

        return {
            "success": True,
            "planetId": str(planet.id),
            "planetName": planet.name,
            "level": level,
            "levelName": level_config["name"],
            "currentHabitability": planet.habitability_score,
            "targetHabitability": target_habitability,
            "habitabilityBoost": boost,
            "progress": 0.0,
            "creditCost": credit_cost,
            "organicsCost": organics_cost,
            "equipmentCost": equipment_cost,
            "durationHours": level_config["duration_hours"],
            "estimatedCompletion": estimated_completion.isoformat(),
            "creditsRemaining": player.credits,
            "startedAt": planet.terraforming_start_time.isoformat()
        }

    def get_terraforming_status(
        self,
        planet_id: UUID,
        player_id: UUID
    ) -> Dict[str, Any]:
        """
        Check the current terraforming progress on a planet.

        Returns:
            Dict with current terraforming state
        """
        planet = self._get_owned_planet(planet_id, player_id)

        # Advance time-based progress against the level's duration; this
        # lazily completes projects whose duration has fully elapsed.
        if planet.terraforming_active:
            completed = self._apply_time_progress(planet)
            self.db.commit()
            if completed:
                self.db.refresh(planet)

        if not planet.terraforming_active:
            return {
                "active": False,
                "planetId": str(planet.id),
                "planetName": planet.name,
                "currentHabitability": planet.habitability_score,
                "terraformingTarget": None,
                "progress": None,
                "startedAt": None,
                "estimatedTicksRemaining": None,
                "availableLevels": self.get_terraforming_levels()
            }

        # Calculate estimated ticks remaining
        habitability_remaining = planet.terraforming_target - planet.habitability_score
        avg_increment = self._calculate_increment(planet)
        estimated_ticks = max(1, int(habitability_remaining / avg_increment)) if avg_increment > 0 else None

        return {
            "active": True,
            "planetId": str(planet.id),
            "planetName": planet.name,
            "currentHabitability": planet.habitability_score,
            "terraformingTarget": planet.terraforming_target,
            "progress": round(planet.terraforming_progress, 2),
            "startedAt": planet.terraforming_start_time.isoformat() if planet.terraforming_start_time else None,
            "estimatedTicksRemaining": estimated_ticks,
            "populationBonus": self._get_population_bonus_description(planet)
        }

    def process_terraforming_tick(self, planet_id: UUID) -> Dict[str, Any]:
        """
        Advance terraforming progress by one tick.

        Called during turn/tick processing. Each tick increases
        habitability by 1-3 points based on population assigned to
        the planet.

        Returns:
            Dict with tick processing results
        """
        planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
        if not planet:
            raise ValueError("Planet not found")

        if not planet.terraforming_active:
            return {
                "processed": False,
                "reason": "No active terraforming project"
            }

        # Calculate increment based on population
        increment = self._calculate_increment(planet)

        old_habitability = planet.habitability_score
        new_habitability = min(
            planet.terraforming_target,
            planet.habitability_score + increment
        )
        planet.habitability_score = new_habitability

        # Progress is percentage of the gap already closed toward the target.
        # We track it as (current - start) / (target - start) * 100,
        # but since we don't persist the start value, use current / target as
        # an approximation that reaches 100% when habitability == target.
        total_gap = planet.terraforming_target
        if total_gap > 0:
            planet.terraforming_progress = min(
                100.0,
                (new_habitability / total_gap) * 100.0
            )

        result = {
            "processed": True,
            "planetId": str(planet.id),
            "planetName": planet.name,
            "increment": increment,
            "oldHabitability": old_habitability,
            "newHabitability": new_habitability,
            "progress": round(planet.terraforming_progress, 2),
            "completed": False
        }

        # Check if terraforming is complete
        if new_habitability >= planet.terraforming_target:
            completion_result = self._complete_terraforming(planet)
            result["completed"] = True
            result["completionDetails"] = completion_result

        self.db.commit()

        logger.info(
            f"Terraforming tick on planet {planet.name}: "
            f"{old_habitability} -> {new_habitability} (+{increment})"
        )

        return result

    def cancel_terraforming(
        self,
        planet_id: UUID,
        player_id: UUID
    ) -> Dict[str, Any]:
        """
        Cancel an active terraforming project with a partial refund.

        The player receives 50% of the original credit cost back.
        Resource costs (organics, equipment) are NOT refunded as they
        have already been consumed by the terraforming process.

        Returns:
            Dict with cancellation details
        """
        planet = self._get_owned_planet(planet_id, player_id)

        if not planet.terraforming_active:
            raise ValueError("No active terraforming project on this planet")

        # Retrieve terraforming metadata from active_events
        terra_meta = self._get_terraforming_meta(planet)
        original_credit_cost = terra_meta.get("credit_cost", 0) if terra_meta else 0

        # Calculate refund (50% of credit cost)
        refund_amount = int(original_credit_cost * TERRAFORMING_CANCEL_REFUND)

        # Credit the refund
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            raise ValueError("Player not found")

        player.credits += refund_amount

        # Clear terraforming state
        planet.terraforming_active = False
        planet.terraforming_target = None
        planet.terraforming_start_time = None
        planet.terraforming_progress = 0.0

        # Remove terraforming event from active_events
        current_events = list(planet.active_events or [])
        planet.active_events = [e for e in current_events if not (isinstance(e, dict) and e.get("type") == "terraforming")]

        # Restore planet status based on current state
        if planet.colonists > 0 or planet.population > 0:
            planet.status = PlanetStatus.COLONIZED
        elif planet.habitability_score > 0:
            planet.status = PlanetStatus.HABITABLE
        else:
            planet.status = PlanetStatus.UNINHABITABLE

        self.db.commit()
        self.db.refresh(player)

        level_info = terra_meta.get("level", "unknown") if terra_meta else "unknown"
        logger.info(
            f"Terraforming L{level_info} cancelled on planet {planet.name} "
            f"(id={planet.id}) by player {player_id}. "
            f"Refund: {refund_amount} credits (50% of {original_credit_cost})"
        )

        return {
            "success": True,
            "planetId": str(planet.id),
            "planetName": planet.name,
            "cancelledLevel": terra_meta.get("level") if terra_meta else None,
            "cancelledLevelName": terra_meta.get("level_name") if terra_meta else None,
            "originalCreditCost": original_credit_cost,
            "refundAmount": refund_amount,
            "creditsAfterRefund": player.credits,
            "currentHabitability": planet.habitability_score
        }

    def complete_terraforming(self, planet_id: UUID) -> Dict[str, Any]:
        """
        Public method to force-complete terraforming on a planet.

        Typically called internally when target is reached, but can
        be invoked directly for admin/testing purposes.
        """
        planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
        if not planet:
            raise ValueError("Planet not found")

        if not planet.terraforming_active:
            raise ValueError("No active terraforming project on this planet")

        result = self._complete_terraforming(planet)
        self.db.commit()
        return result

    # --- Public helpers ---

    @staticmethod
    def get_terraforming_levels() -> Dict[int, Dict[str, Any]]:
        """
        Return the full terraforming levels configuration for API exposure.

        Each level includes: name, credit cost, duration in hours,
        habitability boost, and resource costs (organics, equipment).
        """
        return {
            level: {
                "level": level,
                "name": config["name"],
                "creditCost": config["cost"],
                "durationHours": config["duration_hours"],
                "habitabilityBoost": config["habitability_boost"],
                "organicsCost": config["organics_cost"],
                "equipmentCost": config["equipment_cost"],
            }
            for level, config in TERRAFORMING_LEVELS.items()
        }

    # --- Private helpers ---

    def _complete_terraforming(self, planet: Planet) -> Dict[str, Any]:
        """
        Internal method to finalize a terraforming project.

        Applies the habitability boost from the terraforming level and
        recalculates the planet's max population based on the new
        habitability score (higher habitability supports more population).
        """
        # Retrieve level metadata before clearing
        terra_meta = self._get_terraforming_meta(planet)
        boost = terra_meta.get("habitability_boost", 0) if terra_meta else 0
        level = terra_meta.get("level", 0) if terra_meta else 0
        level_name = terra_meta.get("level_name", "Unknown") if terra_meta else "Unknown"

        # Apply the habitability gain (capped at 100). The tick path may
        # already have incremented habitability toward terraforming_target;
        # raising to the stored target (rather than re-adding the boost)
        # avoids double-applying the gain when both paths run.
        old_habitability = planet.habitability_score
        if planet.terraforming_target:
            target = min(TERRAFORMING_MAX_HABITABILITY, planet.terraforming_target)
        else:
            target = min(TERRAFORMING_MAX_HABITABILITY, planet.habitability_score + boost)
        planet.habitability_score = max(planet.habitability_score, target)
        final_habitability = planet.habitability_score

        # Recompute the demographic ceiling per ADR-0035: "Canonical formula:
        # `max_population = habitability_score × 1,000`. ... The recompute is
        # a *trigger* fired by the habitability mutation — it evaluates the
        # formula afresh, never multiplicatively shrinks the prior value."
        # `max_colonists` is NOT touched here: it is citadel-bound, never
        # modified by terraforming or habitability changes (ADR-0035).
        from src.services.planetary_service import max_population_for
        planet.max_population = max_population_for(planet.habitability_score)

        # Boost population growth rate based on habitability improvement
        if planet.habitability_score >= 80:
            planet.population_growth = max(planet.population_growth, 2.0)
        elif planet.habitability_score >= 60:
            planet.population_growth = max(planet.population_growth, 1.5)
        elif planet.habitability_score >= 40:
            planet.population_growth = max(planet.population_growth, 1.0)

        # Clear terraforming state
        planet.terraforming_active = False
        planet.terraforming_target = None
        planet.terraforming_start_time = None
        planet.terraforming_progress = 100.0

        # Remove terraforming event from active_events
        current_events = list(planet.active_events or [])
        planet.active_events = [e for e in current_events if not (isinstance(e, dict) and e.get("type") == "terraforming")]

        # Update planet status
        if planet.colonists > 0 or planet.population > 0:
            planet.status = PlanetStatus.COLONIZED
        else:
            planet.status = PlanetStatus.HABITABLE

        logger.info(
            f"Terraforming L{level} ({level_name}) complete on planet "
            f"{planet.name} (id={planet.id}). "
            f"Habitability: {old_habitability}% -> {final_habitability}% (+{boost})"
        )

        return {
            "planetId": str(planet.id),
            "planetName": planet.name,
            "level": level,
            "levelName": level_name,
            "previousHabitability": old_habitability,
            "finalHabitability": final_habitability,
            "habitabilityGained": final_habitability - old_habitability,
            "maxPopulation": planet.max_population,
            "populationGrowthRate": planet.population_growth,
            "status": planet.status.value
        }

    def _apply_time_progress(self, planet: Planet) -> bool:
        """
        Advance terraforming_progress from elapsed wall-clock time against
        the level's duration (terraforming.md ladder: 72h / 120h / 168h /
        240h / 336h for levels 1-5), and complete the project when the full
        duration has elapsed.

        This is the lazy counterpart to the tick path: no scheduler invokes
        process_terraforming_tick today, so duration-based completion keeps
        projects honest whenever their status is read.

        Returns True if the project completed.
        """
        if not planet.terraforming_active or not planet.terraforming_start_time:
            return False

        terra_meta = self._get_terraforming_meta(planet)
        duration_hours = (terra_meta or {}).get("duration_hours")
        if not duration_hours or duration_hours <= 0:
            return False

        start = planet.terraforming_start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        elapsed_hours = (datetime.now(UTC) - start).total_seconds() / 3600.0

        if elapsed_hours >= duration_hours:
            self._complete_terraforming(planet)
            return True

        # Time-based progress never regresses progress earned via ticks
        planet.terraforming_progress = max(
            planet.terraforming_progress or 0.0,
            min(100.0, (elapsed_hours / duration_hours) * 100.0)
        )
        return False

    def _get_terraforming_meta(self, planet: Planet) -> Optional[Dict[str, Any]]:
        """
        Retrieve the terraforming metadata stored in the planet's
        active_events JSONB column.

        Returns:
            The terraforming event dict, or None if not found.
        """
        for event in (planet.active_events or []):
            if isinstance(event, dict) and event.get("type") == "terraforming":
                return event
        return None

    def _calculate_increment(self, planet: Planet) -> int:
        """
        Calculate the habitability increment for one tick based on
        population.

        Base increment is 1. For every TERRAFORMING_POPULATION_SCALE
        colonists/population, add 1 more point, up to
        TERRAFORMING_MAX_INCREMENT.
        """
        population = max(planet.colonists or 0, planet.population or 0)
        bonus = int(population / TERRAFORMING_POPULATION_SCALE)
        increment = min(
            TERRAFORMING_MAX_INCREMENT,
            TERRAFORMING_BASE_INCREMENT + bonus
        )
        return max(TERRAFORMING_BASE_INCREMENT, increment)

    def _get_population_bonus_description(self, planet: Planet) -> str:
        """
        Return a human-readable description of the population bonus
        for terraforming speed.
        """
        increment = self._calculate_increment(planet)
        population = max(planet.colonists or 0, planet.population or 0)

        if increment >= TERRAFORMING_MAX_INCREMENT:
            return f"Maximum speed ({increment} points/tick) with {population} population"
        else:
            next_threshold = (increment - TERRAFORMING_BASE_INCREMENT + 1) * TERRAFORMING_POPULATION_SCALE
            return (
                f"Current speed: {increment} points/tick. "
                f"Need {next_threshold} population for +1 speed"
            )
