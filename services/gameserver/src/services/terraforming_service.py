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

from src.core.game_time import GAME_TIME_SCALE, canonical_hours_since, scaled_deadline
from src.services.structures import _via_settle_guard
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

    def _get_owned_planet(self, planet_id: UUID, player_id: UUID, lock: bool = False) -> Planet:
        """Retrieve a planet and verify it is owned by the given player.

        When `lock` is set the planet row is taken FOR UPDATE (with
        populate_existing so the identity-map copy reflects the locked row);
        used on the advance-on-read path so two concurrent reads cannot both
        apply the same accrued terraforming ticks (double-award / lost-update).
        """
        query = self.db.query(Planet).join(
            player_planets,
            Planet.id == player_planets.c.planet_id
        ).filter(
            and_(
                Planet.id == planet_id,
                player_planets.c.player_id == player_id
            )
        )
        if lock:
            query = query.populate_existing().with_for_update(of=Planet)
        planet = query.first()

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
            "started_at": now.isoformat(),
            # Lazy-tick bookkeeping (see _advance_terraforming):
            # start_habitability anchors honest progress math; last_tick_at
            # is the advance-on-read anchor (mirrors planet.last_growth_at).
            "start_habitability": planet.habitability_score,
            "last_tick_at": now.isoformat()
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
        # Lock the planet row on the advance path: the lazy reconciliation
        # mutates habitability/progress, so concurrent status reads must
        # serialize to avoid both applying the same accrued ticks (T5).
        planet = self._get_owned_planet(planet_id, player_id, lock=True)

        # Advance-on-read via the CRT spine (WO-K1a cutover): settle() applies every
        # population-scaled terraforming tick (+ the other clocks, each idempotent on its own
        # anchor) and completes the project if the target is reached.
        from src.services.structures import settle
        if settle(planet, db=self.db).changed:
            self.db.commit()
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

        # Level identity from the project metadata (additive — lets clients
        # render the real ladder entry instead of inventing one)
        terra_meta = self._get_terraforming_meta(planet) or {}

        # Intelligibility (T6): derive the canonical tick period and an absolute
        # estimated completion so the client can render a real countdown instead
        # of opaque "~N ticks". TICK_PERIOD = duration / total_points (same
        # derivation as _advance_terraforming). estimated_completion runs from
        # the lazy anchor through scaled_deadline so dev-time compression
        # (GAME_TIME_SCALE) is reflected. Additive fields; None when metadata
        # is insufficient (legacy projects).
        tick_period_hours = None
        estimated_completion = None
        duration_hours = terra_meta.get("duration_hours")
        target = min(TERRAFORMING_MAX_HABITABILITY, planet.terraforming_target)
        start_habitability = terra_meta.get("start_habitability")
        if duration_hours and duration_hours > 0:
            if isinstance(start_habitability, int) and target > start_habitability:
                total_points = target - start_habitability
            else:
                total_points = max(1, terra_meta.get("habitability_boost") or 1)
            tick_period_hours = duration_hours / total_points

            if estimated_ticks is not None:
                # Remaining canonical hours from the lazy anchor (last applied
                # tick), so the countdown excludes time already banked.
                anchor_raw = terra_meta.get("last_tick_at") or terra_meta.get("started_at")
                anchor = None
                if anchor_raw:
                    try:
                        anchor = datetime.fromisoformat(anchor_raw)
                    except (TypeError, ValueError):
                        anchor = None
                if anchor is None:
                    anchor = planet.terraforming_start_time
                if anchor is not None:
                    if anchor.tzinfo is None:
                        anchor = anchor.replace(tzinfo=UTC)
                    remaining_canonical_hours = estimated_ticks * tick_period_hours
                    estimated_completion = scaled_deadline(
                        remaining_canonical_hours, anchor
                    ).isoformat()

        return {
            "active": True,
            "planetId": str(planet.id),
            "planetName": planet.name,
            "level": terra_meta.get("level"),
            "levelName": terra_meta.get("level_name"),
            "currentHabitability": planet.habitability_score,
            "terraformingTarget": planet.terraforming_target,
            "progress": round(planet.terraforming_progress, 2),
            "startedAt": planet.terraforming_start_time.isoformat() if planet.terraforming_start_time else None,
            "estimatedTicksRemaining": estimated_ticks,
            "tickPeriodHours": round(tick_period_hours, 4) if tick_period_hours is not None else None,
            "estimatedCompletion": estimated_completion,
            "populationBonus": self._get_population_bonus_description(planet)
        }

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

        # Arbitrage fix: cancellation must NOT bank the partial habitability
        # the lazy-advance path accrued. Canon's terraforming gain is a single
        # integer addition applied at COMPLETION (terraforming.md: the boost
        # lands when the project finishes); mid-project ticks are bookkeeping,
        # not earned terrain. Without this revert a player could start L5,
        # let it advance a few points, cancel for a 50% refund, repeat, and
        # ratchet habitability upward for ~half price — a pure arbitrage loop.
        # Restore the recorded start_habitability so cancellation yields ZERO
        # net habitability gain. (max_population is recomputed below to track
        # the reverted score, ADR-0035.)
        start_habitability = terra_meta.get("start_habitability") if terra_meta else None
        if isinstance(start_habitability, int):
            planet.habitability_score = min(planet.habitability_score, start_habitability)
            self._recompute_max_population(planet)

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

    def settle_terraforming(self, planet: Planet) -> bool:
        """Settle banked terraforming ticks at the CURRENT population rate (T2).

        Advance-and-commit entry point for callers that are about to mutate a
        planet's population (e.g. the colonist embark/disembark route). The
        lazy advance samples population at reconciliation time, so banked ticks
        must be reconciled BEFORE the population change lands — otherwise ticks
        earned under the OLD population settle at the NEW rate (retroactive
        rate change). Call this on the already-loaded (and ideally row-locked)
        planet before applying the transfer.

        Returns True if any terraforming state advanced.
        """
        if not planet.terraforming_active:
            return False
        # CRT WO-K1a cutover: settle() reconciles banked terraforming ticks (at the current
        # population rate) — and the other idempotent clocks — BEFORE the caller applies the
        # population transfer (§5.4).
        from src.services.structures import settle
        changed = settle(planet, db=self.db).changed
        if changed:
            self.db.commit()
            self.db.refresh(planet)
        return changed

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

    def _recompute_max_population(self, planet: Planet) -> None:
        """Re-evaluate the habitability-derived demographic ceiling (ADR-0035).

        "Canonical formula: `max_population = habitability_score × 1,000`. ...
        The recompute is a *trigger* fired by the habitability mutation — it
        evaluates the formula afresh, never multiplicatively shrinks the prior
        value." Lazy import avoids a module-load cycle with planetary_service.
        `max_colonists` (citadel-bound) is never touched here.
        """
        from src.services.planetary_service import max_population_for
        planet.max_population = max_population_for(planet.habitability_score)

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
        self._recompute_max_population(planet)

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

    def _advance_terraforming(self, planet: Planet, *, _via_settle: bool = False) -> bool:
        """
        Lazily apply every population-scaled terraforming tick accrued since
        the project last advanced (advance-on-read — the codebase pattern
        used by citadel upgrades, construction, and colonist growth). Also
        driven on a fixed cadence by the npc_scheduler planetary-advance
        sweep so progress no longer depends on the owner re-reading the
        planet; both paths are time-accurate and idempotent, so they
        reconcile cleanly regardless of which runs first.

        Tick-period derivation — this reconciles terraforming.md's two
        progress models honestly:

          * The level ladder gives each project a canonical duration D hours
            (72/120/168/240/336 for L1-L5) and a habitability boost B points.
          * The canon tick model (terraforming.md "Tick-based progression")
            advances 1 point/tick base for a < 1,000-population planet,
            +1 per 1,000 population, capped at 3 points/tick.
          * Neither the doc nor ADR-0002 defines the tick's wall length, so
            we derive it from the constraint that a minimum-speed planet
            (< 1,000 pop, 1 point/tick) completes in EXACTLY the documented
            level duration:  TICK_PERIOD = D / total_points  canonical hours.
          * Populous planets earn 2-3 points per tick and therefore finish
            proportionally (2-3x) faster — the doc's population-scaling
            intent, expressed in time.

        total_points is the real gap at project start (target may be capped
        at 100, making it smaller than B); legacy projects without the
        start_habitability marker fall back to B.

        Elapsed time runs through GAME_TIME_SCALE (canonical hours), so dev
        compression applies uniformly. Only the wall-clock time the applied
        ticks consumed is taken from the anchor; the sub-tick remainder
        stays banked (mirrors the colonist-growth anchor pattern).

        Returns True if any state changed (caller commits).

        ``_via_settle`` (CRT spine): True from structures.settle() step 2; reads its OWN canonical
        anchor (active_events['terraforming']['last_tick_at']) as shipped — no spine ``now`` in.
        """
        _via_settle_guard("_advance_terraforming", _via_settle)
        if not planet.terraforming_active or not planet.terraforming_start_time:
            return False
        if not planet.terraforming_target:
            return False

        terra_meta = self._get_terraforming_meta(planet)
        duration_hours = (terra_meta or {}).get("duration_hours")
        if not duration_hours or duration_hours <= 0:
            return False

        target = min(TERRAFORMING_MAX_HABITABILITY, planet.terraforming_target)

        # Legacy/odd state: already at or past the target — finish now.
        if planet.habitability_score >= target:
            self._complete_terraforming(planet)
            return True

        # Total points the project must earn (honest gap at start)
        start_habitability = terra_meta.get("start_habitability")
        if isinstance(start_habitability, int) and target > start_habitability:
            total_points = target - start_habitability
        else:
            total_points = max(1, terra_meta.get("habitability_boost") or 1)

        tick_period_hours = duration_hours / total_points

        # Anchor: last advance, falling back to project start for projects
        # that predate the marker
        anchor = None
        anchor_raw = terra_meta.get("last_tick_at") or terra_meta.get("started_at")
        if anchor_raw:
            try:
                anchor = datetime.fromisoformat(anchor_raw)
            except (TypeError, ValueError):
                anchor = None
        if anchor is None:
            anchor = planet.terraforming_start_time
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)

        ticks = int(canonical_hours_since(anchor) // tick_period_hours)
        if ticks <= 0:
            # Not a full tick yet — leave the anchor so the remainder accrues
            return False

        # Population is sampled at reconciliation time; mid-window population
        # changes are approximated by the current rate (same simplification
        # the lazy colonist-growth path makes for habitability changes).
        increment = self._calculate_increment(planet)
        old_habitability = planet.habitability_score
        new_habitability = min(target, old_habitability + ticks * increment)
        planet.habitability_score = new_habitability
        # ADR-0035: every habitability mutation re-fires the demographic-ceiling
        # trigger. _complete_terraforming recomputes on its own path; the
        # partial-advance path must too, so the colony's max_population tracks
        # mid-project gains rather than only updating at completion.
        self._recompute_max_population(planet)

        if new_habitability >= target:
            completion = self._complete_terraforming(planet)
            logger.info(
                f"Lazy terraforming advance completed project on planet "
                f"{planet.name} (id={planet.id}): {old_habitability} -> "
                f"{completion['finalHabitability']} ({ticks} ticks)"
            )
            return True

        # Honest progress when the start marker exists; otherwise keep the
        # tick path's current/target approximation. Never regress.
        if isinstance(start_habitability, int) and target > start_habitability:
            fraction = (new_habitability - start_habitability) / (target - start_habitability)
        else:
            fraction = new_habitability / target if target > 0 else 0.0
        planet.terraforming_progress = max(
            planet.terraforming_progress or 0.0,
            min(100.0, fraction * 100.0)
        )

        # Consume only the wall-clock time the applied ticks represent
        wall_hours_consumed = (ticks * tick_period_hours) / GAME_TIME_SCALE
        self._set_terraforming_meta_field(
            planet, "last_tick_at",
            (anchor + timedelta(hours=wall_hours_consumed)).isoformat()
        )

        logger.info(
            f"Lazy terraforming advance on planet {planet.name}: "
            f"{old_habitability} -> {new_habitability} "
            f"({ticks} ticks x {increment} pts, period {tick_period_hours:.2f}h)"
        )
        return True

    def _set_terraforming_meta_field(self, planet: Planet, key: str, value: Any) -> None:
        """
        Update one field of the terraforming entry in active_events.

        JSONB mutation pattern: rebuild the list with a copied dict and
        reassign the column so SQLAlchemy sees the change. No-op when no
        terraforming entry exists (e.g. after completion removed it).
        """
        events = []
        for event in (planet.active_events or []):
            if isinstance(event, dict) and event.get("type") == "terraforming":
                updated = dict(event)
                updated[key] = value
                events.append(updated)
            else:
                events.append(event)
        planet.active_events = events

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
