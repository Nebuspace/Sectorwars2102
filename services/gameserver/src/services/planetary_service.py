"""
Planetary management service for handling planet operations.

This service manages planetary colonization, resource allocation,
building construction, defenses, and sieges.
"""

from typing import Dict, Any, Optional, List
from uuid import UUID, uuid4
from datetime import datetime, timedelta, UTC
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
import logging

from src.core.game_time import canonical_hours_since
from src.models.player import Player
from src.models.planet import Planet, player_planets
from src.models.sector import Sector
from src.models.ship import Ship
from src.models.genesis_device import GenesisDevice, GenesisType, GenesisStatus, PlanetFormation
from src.models.team import Team

logger = logging.getLogger(__name__)

# Siege configuration constants
SIEGE_TURNS_THRESHOLD = 3       # Consecutive turns enemies must be present to trigger siege
SIEGE_MORALE_LOSS_PER_TURN = 5  # Morale % lost per turn under siege
SIEGE_PRODUCTION_PENALTY = 0.25 # 25% production reduction during siege
DEFENSE_UPGRADE_COST = 1000     # Credits per defense level
DEFENSE_MAX_LEVEL = 10          # Maximum defense level
# Per-unit credit cost to ADD planetary defense units, mirroring the price the
# player-client DefenseConfiguration UI already shows and gates affordability on
# (DEFENSE_TYPES: turrets 500, shields 1000, drones/fighters 2000). The server
# must charge these or the UI's "you can afford this" is a lie and defenses are
# free (an economic faucet). Reducing units is free (no refund).
DEFENSE_UNIT_COST = {"turrets": 500, "shields": 1000, "fighters": 2000}
# Canon: DOCS/API/v1/sectors-planets.aispec — siege morale loss is
# "mitigated by 0.05 × defense_level", i.e. 5% damage reduction per level
DEFENSE_DAMAGE_REDUCTION_PER_LEVEL = 0.05

# Lazy siege cadence. Canon (FEATURES/planets/defense.md "Siege") defines the
# per-turn effects (SIEGE_MORALE_LOSS_PER_TURN, defense mitigation) but never
# a wall-clock length for a siege "turn" — apply_siege_effects was written
# for a turn-processing scheduler that does not exist. NO-CANON: one siege
# turn = 24 canonical hours (one canonical day), matching the daily cadence
# of production and colonist growth; an undefended planet (100 morale,
# 5/turn) becomes capture-vulnerable after ~20 canonical days under siege.
# Runs through GAME_TIME_SCALE like every other duration.
SIEGE_TURN_HOURS = 24.0

# Shield Generator Levels (0-10)
# Uses planet.defense_shields to track generator level, planet.shields for strength
SHIELD_GENERATOR_MAX_LEVEL = 10
SHIELD_GENERATOR_LEVELS = {
    0: {"name": "No Shields", "strength": 0, "regen_per_hour": 0, "cost": 0},
    1: {"name": "Basic Shield", "strength": 1000, "regen_per_hour": 100, "cost": 50000},
    2: {"name": "Reinforced Shield", "strength": 2500, "regen_per_hour": 250, "cost": 100000},
    3: {"name": "Military Shield", "strength": 5000, "regen_per_hour": 500, "cost": 200000},
    4: {"name": "Advanced Shield", "strength": 10000, "regen_per_hour": 1000, "cost": 350000},
    5: {"name": "Heavy Shield", "strength": 15000, "regen_per_hour": 1500, "cost": 500000},
    6: {"name": "Fortress Shield", "strength": 25000, "regen_per_hour": 2500, "cost": 750000},
    7: {"name": "Citadel Shield", "strength": 35000, "regen_per_hour": 3500, "cost": 1000000},
    8: {"name": "Planetary Shield", "strength": 50000, "regen_per_hour": 5000, "cost": 1500000},
    9: {"name": "Quantum Shield", "strength": 65000, "regen_per_hour": 6500, "cost": 2000000},
    10: {"name": "Impervious Shield", "strength": 75000, "regen_per_hour": 7500, "cost": 3000000},
}

# Canon daily colonist growth (FEATURES/planets/colonization.md "Population
# growth"): colonist_rate = colonists × 0.01 × (habitability_score / 100),
# i.e. base growth = 1% per day, scaled linearly by habitability.
DAILY_GROWTH_BASE = 0.01
SECONDS_PER_DAY = 86400.0


def max_colonists_for(citadel_level: int) -> int:
    """Citadel-tier workforce ceiling per ADR-0035.

    "`max_colonists` — citadel-tier workforce cap. Driven by citadel level,
    with the per-tier values defined by `citadel_service.CITADEL_LEVELS`:
    L1 Outpost = 1,000, L2 = 5,000, L3 = 15,000, L4 = 50,000,
    L5 Planetary Capital = 200,000."

    Note: CITADEL_LEVELS stores this tier value under the legacy key
    "max_population", but per ADR-0035 it governs max_colonists (the
    workforce cap), never the habitability-derived demographic cap.
    """
    from src.services.citadel_service import CITADEL_LEVELS
    level = citadel_level or 0
    info = CITADEL_LEVELS.get(level, CITADEL_LEVELS[0])
    return info["max_population"]


def max_population_for(habitability_score: int) -> int:
    """Habitability-derived demographic ceiling per ADR-0035.

    "Canonical formula: `max_population = habitability_score × 1,000`."
    Recomputed (fresh evaluation, never a multiplicative shrink) whenever
    habitability changes — e.g. terraforming completion.
    """
    return max(0, habitability_score or 0) * 1000


class PlanetaryService:
    """Service for managing planetary operations."""
    
    def __init__(self, db: Session):
        self.db = db
        
    def get_player_planets(self, player_id: UUID) -> List[Dict[str, Any]]:
        """Get all planets owned by a player."""
        # Get planets through the association table
        planets = self.db.query(Planet).join(
            player_planets,
            Planet.id == player_planets.c.planet_id
        ).filter(
            player_planets.c.player_id == player_id
        ).all()
        
        result = []
        for planet in planets:
            planet_data = self._format_planet_data(planet)
            result.append(planet_data)
            
        return result
        
    def get_planet_details(self, planet_id: UUID, player_id: UUID) -> Dict[str, Any]:
        """Get detailed information about a specific planet."""
        # Verify planet ownership
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

        # Lazily apply colonist growth accrued since the last read
        changed = self.apply_population_growth(planet)

        # Siege validity check BEFORE decay (S2): re-evaluate whether the siege
        # still holds (enemies present AND owner absent) before applying any
        # morale loss. Canon (defense.md "Siege") requires owner ABSENCE for a
        # siege — so the owner standing on the planet (the common case for this
        # owner-facing detail read) must LIFT the siege, not decay morale. A
        # stale siege whose enemies have left also lifts here rather than
        # bleeding morale on every detail fetch. _detect_siege commits its own
        # state change; only the remaining lazy mutations need our commit.
        if planet.under_siege:
            self._detect_siege(planet, planet.owner_id or player_id)
        # Apply accrued morale decay only if the siege survived validation.
        if planet.under_siege:
            changed = self.advance_siege(planet) or changed
        if changed:
            self.db.commit()

        return self._format_planet_data(planet)

    def apply_population_growth(self, planet: Planet) -> bool:
        """Lazily apply canon colonist growth since planet.last_growth_at.

        Canon daily formula (FEATURES/planets/colonization.md "Population
        growth"): colonist_rate = colonists × 0.01 × (habitability_score/100),
        pro-rated here by elapsed wall-clock time.

        Ceilings enforced per ADR-0035 ("Runtime invariants"):
          - colonists ≤ max_colonists (citadel cap)
          - population ≤ max_population (habitability cap)
          - colonists ≤ population (working-age subset)

        Anchor pattern (mirrors turn-regen): only the time that produced
        whole colonists is consumed from the anchor; the fractional
        remainder stays banked so slow-growing colonies are never robbed
        of sub-colonist progress. The anchor is never reset without the
        accrued growth being applied first.

        Returns True if any state changed (growth applied or anchor
        initialized/advanced) so callers know to commit.
        """
        now = datetime.now(UTC)

        if planet.last_growth_at is None:
            # First read since the column landed: anchor now, accrue later.
            planet.last_growth_at = now
            return True

        anchor = planet.last_growth_at
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)

        elapsed_seconds = (now - anchor).total_seconds()
        if elapsed_seconds <= 0:
            return False

        # Siege halts population growth (colonization.md "Other growth
        # modifiers"); besieged time yields nothing, so advance the anchor.
        if planet.under_siege:
            planet.last_growth_at = now
            return True

        colonists = planet.colonists or 0
        habitability = max(planet.habitability_score or 0, 0)
        rate_per_day = colonists * DAILY_GROWTH_BASE * (habitability / 100.0)
        if rate_per_day <= 0:
            # Nothing can grow (no colonists or zero habitability); keep the
            # anchor current so future colonists don't grow retroactively.
            planet.last_growth_at = now
            return True

        # Dual ceilings (ADR-0035): growth stops at whichever cap binds first.
        workforce_cap = (
            max_colonists_for(planet.citadel_level)
            if (planet.citadel_level or 0) >= 1
            else (planet.max_colonists or 0)
        )
        ceiling = min(workforce_cap, max_population_for(planet.habitability_score))
        headroom = ceiling - colonists
        if headroom <= 0:
            # Already at (or beyond, via legacy data) the ceiling — banked
            # time is worthless, advance the anchor.
            planet.last_growth_at = now
            return True

        rate_per_second = rate_per_day / SECONDS_PER_DAY
        gained = int(rate_per_second * elapsed_seconds)
        if gained <= 0:
            # Not enough elapsed time for a whole colonist yet — leave the
            # anchor untouched so the remainder keeps accruing.
            return False

        if gained >= headroom:
            # Ceiling reached: surplus accrual is discarded, anchor moves to now.
            gained = headroom
            planet.last_growth_at = now
        else:
            # Consume only the whole-colonist time; bank the remainder.
            seconds_consumed = gained / rate_per_second
            planet.last_growth_at = anchor + timedelta(seconds=seconds_consumed)

        planet.colonists = colonists + gained
        # Simplification: total demographic tracks the workforce floor
        # (population = max(population, colonists)); dependents beyond the
        # workforce are not modeled yet. The growth ceiling above already
        # respects max_population, and pre-existing populations are never
        # shrunk here.
        planet.population = max(planet.population or 0, planet.colonists)

        logger.debug(
            f"Lazy growth on planet {planet.id}: +{gained} colonists "
            f"(now {planet.colonists}, ceiling {ceiling})"
        )
        return True
        
    def allocate_colonists(
        self,
        planet_id: UUID,
        player_id: UUID,
        fuel: int,
        organics: int,
        equipment: int
    ) -> Dict[str, Any]:
        """Allocate colonists to different production areas."""
        # Verify ownership
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
            
        # Validate allocation totals
        total_allocated = fuel + organics + equipment
        if total_allocated > planet.colonists:
            raise ValueError(f"Cannot allocate {total_allocated} colonists, only {planet.colonists} available")
            
        # Update allocations
        planet.fuel_allocation = fuel
        planet.organics_allocation = organics
        planet.equipment_allocation = equipment
        
        # Calculate production rates based on allocations
        production_rates = self._calculate_production_rates(planet)
        
        self.db.commit()
        self.db.refresh(planet)
        
        return {
            "success": True,
            "allocations": {
                "fuel": planet.fuel_allocation,
                "organics": planet.organics_allocation,
                "equipment": planet.equipment_allocation,
                "unused": planet.colonists - total_allocated
            },
            "productionRates": production_rates
        }
        
    def upgrade_building(
        self,
        planet_id: UUID,
        player_id: UUID,
        building_type: str,
        target_level: int
    ) -> Dict[str, Any]:
        """Upgrade a building on a planet."""
        # Verify ownership
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
            
        # Get current building level
        current_level = self._get_building_level(planet, building_type)
        
        if target_level <= current_level:
            raise ValueError(f"Target level must be higher than current level ({current_level})")
            
        # Calculate upgrade cost
        cost = self._calculate_upgrade_cost(building_type, current_level, target_level)
        
        # Lock player for credit deduction
        player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
        if player.credits < cost["credits"]:
            raise ValueError("Insufficient credits for upgrade")
            
        # Deduct cost
        player.credits -= cost["credits"]
        
        # Update building level
        self._set_building_level(planet, building_type, target_level)
        
        # Calculate completion time (1 hour per level)
        completion_time = datetime.utcnow() + timedelta(hours=(target_level - current_level))
        
        self.db.commit()
        
        return {
            "success": True,
            "buildingType": building_type,
            "newLevel": target_level,
            "completionTime": completion_time.isoformat(),
            "cost": cost
        }
        
    def update_defenses(
        self,
        planet_id: UUID,
        player_id: UUID,
        turrets: Optional[int] = None,
        shields: Optional[int] = None,
        fighters: Optional[int] = None
    ) -> Dict[str, Any]:
        """Update planetary defenses."""
        # Verify ownership
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

        # Lock the planet row before reading its defense counts: the cost is a
        # read-then-overwrite (absolute targets), so without the lock two
        # concurrent saves could each price against the same stale baseline and
        # the player-row lock would then serialize a double deduction (a
        # player-harming overcharge). Lock order here is planet→player; no other
        # method locks player→planet-row, so this cannot deadlock.
        planet = self.db.query(Planet).filter(
            Planet.id == planet.id
        ).with_for_update().first()

        # Price the upgrade: only ADDED units cost credits (decreases are free,
        # no refund). Mirrors the client DefenseConfiguration cost so the UI's
        # affordability gate is honest. Without this, defenses are free.
        new_turrets = max(0, turrets) if turrets is not None else planet.defense_turrets
        new_shields = max(0, shields) if shields is not None else planet.defense_shields
        new_fighters = max(0, fighters) if fighters is not None else planet.defense_fighters
        cost = (
            DEFENSE_UNIT_COST["turrets"] * max(0, new_turrets - (planet.defense_turrets or 0))
            + DEFENSE_UNIT_COST["shields"] * max(0, new_shields - (planet.defense_shields or 0))
            + DEFENSE_UNIT_COST["fighters"] * max(0, new_fighters - (planet.defense_fighters or 0))
        )

        if cost > 0:
            # Lock the player row before reading/deducting credits (economic
            # integrity — same pattern as the rest of this service).
            player = self.db.query(Player).filter(
                Player.id == player_id
            ).with_for_update().first()
            if not player:
                raise ValueError("Player not found")
            if (player.credits or 0) < cost:
                raise ValueError(
                    f"Insufficient credits: defense upgrade costs {cost:,}, "
                    f"you have {int(player.credits or 0):,}"
                )
            player.credits -= cost

        # Update defenses if provided.
        # Note: the Planet model has no defense_drones column; deployed
        # fighters (defense_fighters) are the drone-equivalent here.
        if turrets is not None:
            planet.defense_turrets = new_turrets
        if shields is not None:
            planet.defense_shields = new_shields
        if fighters is not None:
            planet.defense_fighters = new_fighters

        # Calculate total defense power
        defense_power = (
            planet.defense_turrets * 10 +
            planet.defense_shields * 5 +
            planet.defense_fighters * 2
        )

        self.db.commit()
        self.db.refresh(planet)

        return {
            "success": True,
            "defenses": {
                "turrets": planet.defense_turrets,
                "shields": planet.defense_shields,
                "drones": planet.defense_fighters
            },
            "defensePower": defense_power,
            "creditsSpent": cost
        }
        
    def deploy_genesis_device(
        self,
        player_id: UUID,
        sector_id: UUID,
        planet_name: str,
        planet_type: str
    ) -> Dict[str, Any]:
        """Deploy a genesis device to create a new planet."""
        # Check if player has genesis devices
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            raise ValueError("Player not found")
            
        if player.genesis_devices <= 0:
            raise ValueError("No genesis devices available")
            
        # Verify sector exists
        sector = self.db.query(Sector).filter(Sector.id == sector_id).first()
        if not sector:
            raise ValueError("Sector not found")
            
        # Check if sector already has maximum planets (let's say 5)
        existing_planets = self.db.query(func.count(Planet.id)).filter(
            Planet.sector_id == sector_id
        ).scalar()
        
        if existing_planets >= 5:
            raise ValueError("Sector already has maximum number of planets")
            
        # Create genesis device deployment
        genesis = GenesisDevice(
            player_id=player_id,
            sector_id=sector_id,
            genesis_type=planet_type,
            status=GenesisStatus.DEPLOYED,
            deployed_at=datetime.utcnow()
        )
        
        # Deployment takes 24 hours
        deployment_time = 24 * 3600  # seconds
        completion_time = datetime.utcnow() + timedelta(seconds=deployment_time)
        
        # Create planet formation record
        formation = PlanetFormation(
            genesis_device_id=genesis.id,
            sector_id=sector_id,
            planet_name=planet_name,
            planet_type=planet_type,
            started_at=datetime.utcnow(),
            completion_at=completion_time
        )
        
        # Deduct genesis device
        player.genesis_devices -= 1
        
        # Create the planet immediately for gameplay purposes
        planet = Planet(
            name=planet_name,
            sector_id=sector_id,
            planet_type=planet_type,
            colonists=100,  # Start with 100 colonists
            max_colonists=1000,  # L1-scale default per ADR-0035
            fuel_ore=100,
            organics=100,
            equipment=100,
            drones=0
        )
        
        self.db.add(genesis)
        self.db.add(formation)
        self.db.add(planet)
        self.db.commit()
        self.db.refresh(planet)
        
        # Add planet to player's planets
        self.db.execute(
            player_planets.insert().values(
                player_id=player_id,
                planet_id=planet.id
            )
        )
        self.db.commit()
        
        return {
            "success": True,
            "planetId": str(planet.id),
            "deploymentTime": deployment_time,
            "genesisDevicesRemaining": player.genesis_devices
        }
        
    def set_specialization(
        self,
        planet_id: UUID,
        player_id: UUID,
        specialization: str
    ) -> Dict[str, Any]:
        """Set planet specialization."""
        # Verify ownership
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
            
        # Validate specialization
        valid_specializations = ["agricultural", "industrial", "military", "research", "balanced"]
        if specialization not in valid_specializations:
            raise ValueError(f"Invalid specialization. Must be one of: {valid_specializations}")
            
        planet.specialization = specialization
        
        # Calculate bonuses based on specialization
        bonuses = self._calculate_specialization_bonuses(specialization)
        
        self.db.commit()
        
        return {
            "success": True,
            "specialization": specialization,
            "bonuses": bonuses
        }
        
    def get_siege_status(self, planet_id: UUID, player_id: UUID) -> Dict[str, Any]:
        """Get siege status of a planet with live detection."""
        # Verify ownership
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

        # Settle accrued morale decay BEFORE re-evaluating siege validity (S1):
        # if the siege is about to lift this read, the turns that already
        # elapsed under it must still be applied — detecting first would clear
        # siege_started_at and silently forgive that decay.
        siege_advanced = planet.under_siege and self.advance_siege(planet)

        # Run siege detection to get current state (may lift the siege)
        siege_info = self._detect_siege(planet, player_id)

        if siege_advanced:
            # advance_siege mutated morale/siege_turns; _detect_siege already
            # committed its own changes, so persist the decay too.
            self.db.commit()

        if not planet.under_siege:
            return {
                "underSiege": False,
                "siegeDetails": None,
                "morale": planet.morale,
                "defenseLevel": planet.defense_level or 0,
                "isVulnerable": planet.morale <= 0
            }

        return {
            "underSiege": True,
            "siegeDetails": {
                "siegeStartedAt": planet.siege_started_at.isoformat() if planet.siege_started_at else None,
                # Display turns = turns actually applied since onset (S4):
                # siege_turns carries the escalation threshold as a baseline,
                # so subtract it so the client shows "siege turns elapsed", not
                # the internal counter that starts at SIEGE_TURNS_THRESHOLD.
                "siegeTurns": max(0, (planet.siege_turns or 0) - SIEGE_TURNS_THRESHOLD),
                "attackerId": str(planet.siege_attacker_id) if planet.siege_attacker_id else None,
                "enemyShips": siege_info.get("enemy_ship_count", 0),
                "effects": {
                    "moraleLossPerTurn": SIEGE_MORALE_LOSS_PER_TURN,
                    "productionPenalty": f"{int(SIEGE_PRODUCTION_PENALTY * 100)}%",
                    "populationGrowthHalted": True,
                    "tradeDisrupted": True
                }
            },
            "morale": planet.morale,
            "defenseLevel": planet.defense_level or 0,
            "isVulnerable": planet.morale <= 0
        }

    def check_and_update_siege(self, planet_id: UUID) -> Dict[str, Any]:
        """
        Check siege conditions for a planet and update its state.
        This should be called during turn processing.
        Returns the updated siege state.
        """
        planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
        if not planet:
            raise ValueError("Planet not found")

        # Get the planet owner ID
        owner_record = self.db.query(player_planets.c.player_id).filter(
            player_planets.c.planet_id == planet_id
        ).first()

        if not owner_record:
            # Unowned planet cannot be sieged
            return {"underSiege": False, "changed": False}

        owner_id = owner_record[0]

        # Settle accrued morale decay BEFORE re-evaluating siege validity (S1),
        # so a siege that lifts this turn still applies the elapsed decay rather
        # than forgiving it when _detect_siege clears siege_started_at.
        siege_advanced = planet.under_siege and self.advance_siege(planet)

        siege_info = self._detect_siege(planet, owner_id)

        if siege_advanced:
            # _detect_siege committed its own changes; persist the decay too.
            self.db.commit()

        return {
            "underSiege": planet.under_siege,
            "changed": siege_info.get("state_changed", False) or siege_advanced,
            "morale": planet.morale,
            "isVulnerable": planet.morale <= 0
        }

    def apply_siege_effects(self, planet_id: UUID) -> Dict[str, Any]:
        """
        Apply per-turn siege effects to a planet.
        Call this during turn processing for planets under siege.
        Returns the effects that were applied.
        """
        planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
        if not planet:
            raise ValueError("Planet not found")

        if not planet.under_siege:
            return {"applied": False, "reason": "Planet is not under siege"}

        effects_applied = self._apply_siege_turn(planet)

        self.db.commit()

        return {
            "applied": True,
            "effects": effects_applied
        }

    def _apply_siege_turn(self, planet: Planet) -> Dict[str, Any]:
        """
        One siege turn's effects (canon numbers from defense.md "Siege").
        Mutates the planet; the caller commits.
        """
        effects_applied = {}

        # 1. Morale decreases by SIEGE_MORALE_LOSS_PER_TURN per turn
        old_morale = planet.morale
        # Higher defense level reduces morale loss
        defense_reduction = (planet.defense_level or 0) * 0.05  # 5% less morale loss per defense level
        effective_morale_loss = max(1, int(SIEGE_MORALE_LOSS_PER_TURN * (1.0 - defense_reduction)))
        planet.morale = max(0, planet.morale - effective_morale_loss)
        effects_applied["moraleLoss"] = old_morale - planet.morale
        effects_applied["newMorale"] = planet.morale

        # 2. Population growth halted (handled in _calculate_production_rates via siege check)
        effects_applied["populationGrowthHalted"] = True

        # 3. Production reduced by 25% (handled in _calculate_production_rates via siege check)
        effects_applied["productionReduced"] = True
        effects_applied["productionPenalty"] = f"{int(SIEGE_PRODUCTION_PENALTY * 100)}%"

        # 4. Check if planet becomes vulnerable (morale at 0)
        if planet.morale <= 0:
            effects_applied["vulnerable"] = True
            logger.warning(
                f"Planet {planet.name} (id={planet.id}) morale has dropped to 0 - "
                f"planet is now vulnerable to capture"
            )

        # Increment siege turn counter
        planet.siege_turns = (planet.siege_turns or 0) + 1
        effects_applied["siegeTurns"] = planet.siege_turns

        return effects_applied

    def advance_siege(self, planet: Planet) -> bool:
        """
        Advance-on-read siege progression: apply every siege turn accrued
        since the siege began (no scheduler calls apply_siege_effects, so
        reads keep besieged planets honest — same lazy pattern as colonist
        growth and terraforming).

        Anchor arithmetic: `siege_turns` doubles as the applied-turn marker.
        At siege onset _detect_siege leaves it exactly at
        SIEGE_TURNS_THRESHOLD (the escalation counter that triggered the
        siege), so turns applied since onset = siege_turns - threshold.
        Elapsed turns derive from siege_started_at via canonical hours
        (GAME_TIME_SCALE-aware) at SIEGE_TURN_HOURS per turn.

        Mutates the planet (morale, siege_turns); caller commits.
        Returns True if any turns were applied.
        """
        if not planet.under_siege or not planet.siege_started_at:
            return False

        elapsed_turns = int(
            canonical_hours_since(planet.siege_started_at) // SIEGE_TURN_HOURS
        )
        applied_turns = max(0, (planet.siege_turns or 0) - SIEGE_TURNS_THRESHOLD)
        pending = elapsed_turns - applied_turns
        if pending <= 0:
            return False

        # Morale floors at 0, so very old sieges converge quickly; the cap
        # only guards against pathological anchors.
        applied = min(pending, 1000)
        for _ in range(applied):
            self._apply_siege_turn(planet)

        # Report the capped, actually-applied count (S4) — logging `pending`
        # would overstate the work for a pathologically old anchor.
        logger.info(
            f"Lazy siege advance on planet {planet.name} (id={planet.id}): "
            f"{applied} turn(s) applied, morale now {planet.morale}"
        )
        return True

    def upgrade_defense(
        self,
        planet_id: UUID,
        player_id: UUID
    ) -> Dict[str, Any]:
        """
        Upgrade a planet's defense level by one.
        Costs DEFENSE_UPGRADE_COST credits per level.
        Max defense level is DEFENSE_MAX_LEVEL.
        Each level adds DEFENSE_DAMAGE_REDUCTION_PER_LEVEL damage reduction during siege.
        """
        # Verify ownership
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

        current_level = planet.defense_level or 0

        if current_level >= DEFENSE_MAX_LEVEL:
            raise ValueError(f"Defense is already at maximum level ({DEFENSE_MAX_LEVEL})")

        # Cost scales with level: base_cost * (current_level + 1)
        upgrade_cost = DEFENSE_UPGRADE_COST * (current_level + 1)

        # Lock player for credit deduction
        player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
        if not player:
            raise ValueError("Player not found")

        if player.credits < upgrade_cost:
            raise ValueError(
                f"Insufficient credits. Need {upgrade_cost}, have {player.credits}"
            )

        # Deduct credits and upgrade
        player.credits -= upgrade_cost
        new_level = current_level + 1
        planet.defense_level = new_level

        # Calculate new damage reduction
        damage_reduction = new_level * DEFENSE_DAMAGE_REDUCTION_PER_LEVEL

        self.db.commit()
        self.db.refresh(planet)
        self.db.refresh(player)

        return {
            "success": True,
            "defenseLevel": new_level,
            "maxLevel": DEFENSE_MAX_LEVEL,
            "damageReduction": f"{int(damage_reduction * 100)}%",
            "creditsCost": upgrade_cost,
            "creditsRemaining": player.credits,
            "nextUpgradeCost": DEFENSE_UPGRADE_COST * (new_level + 1) if new_level < DEFENSE_MAX_LEVEL else None
        }

    def upgrade_shield_generator(
        self,
        planet_id: UUID,
        player_id: UUID
    ) -> Dict[str, Any]:
        """
        Upgrade a planet's shield generator by one level.

        Shield generators provide planetary shields that absorb damage during
        attacks and sieges. Each level increases shield strength, regeneration
        rate, and cost. Uses planet.defense_shields to track the generator level
        and planet.shields for the current shield strength value.

        Levels 0-10, with costs ranging from 50,000 to 3,000,000 credits.
        """
        # Lock planet + verify ownership to prevent concurrent upgrade races
        planet = self.db.query(Planet).join(
            player_planets,
            Planet.id == player_planets.c.planet_id
        ).filter(
            and_(
                Planet.id == planet_id,
                player_planets.c.player_id == player_id
            )
        ).with_for_update().first()

        if not planet:
            raise ValueError("Planet not found or not owned by player")

        current_level = planet.defense_shields or 0

        if current_level >= SHIELD_GENERATOR_MAX_LEVEL:
            raise ValueError(
                f"Shield generator is already at maximum level ({SHIELD_GENERATOR_MAX_LEVEL})"
            )

        next_level = current_level + 1
        next_level_info = SHIELD_GENERATOR_LEVELS[next_level]
        upgrade_cost = next_level_info["cost"]

        # Lock player for credit deduction
        player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
        if not player:
            raise ValueError("Player not found")

        if player.credits < upgrade_cost:
            raise ValueError(
                f"Insufficient credits. Need {upgrade_cost:,}, have {player.credits:,}"
            )

        # Deduct credits and upgrade
        player.credits -= upgrade_cost
        planet.defense_shields = next_level
        planet.shields = next_level_info["strength"]

        self.db.commit()
        self.db.refresh(planet)
        self.db.refresh(player)

        # Determine next upgrade info (if not at max)
        further_upgrade_cost = None
        if next_level < SHIELD_GENERATOR_MAX_LEVEL:
            further_upgrade_cost = SHIELD_GENERATOR_LEVELS[next_level + 1]["cost"]

        logger.info(
            f"Shield generator upgraded to level {next_level} "
            f"({next_level_info['name']}) on planet {planet.name} (id={planet.id})"
        )

        return {
            "success": True,
            "shieldGenerator": {
                "level": next_level,
                "maxLevel": SHIELD_GENERATOR_MAX_LEVEL,
                "name": next_level_info["name"],
                "strength": next_level_info["strength"],
                "regenPerHour": next_level_info["regen_per_hour"],
            },
            "creditsCost": upgrade_cost,
            "creditsRemaining": player.credits,
            "nextUpgradeCost": further_upgrade_cost,
        }

    def get_defense_info(self, planet_id: UUID) -> Dict[str, Any]:
        """
        Get comprehensive defense information for a planet.

        Returns shield generator status, defense level, turret and fighter
        counts, and the cost to upgrade shields to the next level.
        Does not require ownership -- useful for scouting and admin views.
        """
        planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
        if not planet:
            raise ValueError("Planet not found")

        # Shield generator info
        shield_level = planet.defense_shields or 0
        shield_info = SHIELD_GENERATOR_LEVELS.get(shield_level, SHIELD_GENERATOR_LEVELS[0])

        # Next level upgrade cost
        next_upgrade_cost = None
        next_level_info = None
        if shield_level < SHIELD_GENERATOR_MAX_LEVEL:
            next_level_info = SHIELD_GENERATOR_LEVELS[shield_level + 1]
            next_upgrade_cost = next_level_info["cost"]

        # Defense level info
        defense_level = planet.defense_level or 0
        damage_reduction = defense_level * DEFENSE_DAMAGE_REDUCTION_PER_LEVEL

        return {
            "planetId": str(planet.id),
            "planetName": planet.name,
            "shieldGenerator": {
                "level": shield_level,
                "maxLevel": SHIELD_GENERATOR_MAX_LEVEL,
                "name": shield_info["name"],
                "strength": shield_info["strength"],
                "currentShields": planet.shields or 0,
                "regenPerHour": shield_info["regen_per_hour"],
                "nextUpgrade": {
                    "level": shield_level + 1,
                    "name": next_level_info["name"],
                    "strength": next_level_info["strength"],
                    "regenPerHour": next_level_info["regen_per_hour"],
                    "cost": next_upgrade_cost,
                } if next_level_info else None,
            },
            "defenseLevel": defense_level,
            "maxDefenseLevel": DEFENSE_MAX_LEVEL,
            "damageReduction": f"{int(damage_reduction * 100)}%",
            "turrets": planet.defense_turrets or 0,
            "fighters": planet.defense_fighters or 0,
        }

    def lift_siege(self, planet_id: UUID) -> Dict[str, Any]:
        """
        Lift a siege from a planet. Called when:
        - Enemy ships leave the sector
        - Planet owner wins combat in the sector
        """
        planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
        if not planet:
            raise ValueError("Planet not found")

        if not planet.under_siege:
            return {"success": True, "message": "Planet was not under siege"}

        # Settle pending morale decay BEFORE clearing siege state (S1): the
        # turns that elapsed while the siege stood are earned and must be
        # applied; clearing siege_started_at first would discard them.
        self.advance_siege(planet)

        planet.under_siege = False
        planet.siege_started_at = None
        planet.siege_attacker_id = None
        planet.siege_turns = 0

        self.db.commit()

        logger.info(f"Siege lifted on planet {planet.name} (id={planet.id})")

        return {
            "success": True,
            "message": f"Siege on {planet.name} has been lifted",
            "currentMorale": planet.morale
        }

    # Helper methods

    def _detect_siege(self, planet: Planet, owner_id: UUID) -> Dict[str, Any]:
        """
        Detect whether a planet should be under siege based on
        enemy ship presence in the planet's sector.

        Siege conditions:
        1. Enemy ships are in the planet's sector
        2. The planet owner is NOT present in the sector
        3. Enemies have been present for SIEGE_TURNS_THRESHOLD+ consecutive turns

        Updates the planet's siege state and returns detection info.
        """
        result = {"state_changed": False, "enemy_ship_count": 0}

        # Find enemy ships in the planet's sector
        # An enemy is any player who is not the planet owner
        # and not on the same team as the planet owner
        owner = self.db.query(Player).filter(Player.id == owner_id).first()
        if not owner:
            return result

        # Get all ships in the planet's sector that don't belong to the owner
        enemy_ships = self.db.query(Ship).filter(
            and_(
                Ship.sector_id == planet.sector_id,
                Ship.owner_id != owner_id,
                # NPC hulls excluded explicitly — previously only an accidental side effect of the NULL owner_id failing the != comparison
                Ship.is_npc == False,
                Ship.is_active == True,
                Ship.is_destroyed == False
            )
        ).all()

        # Filter out teammates if owner is on a team
        if owner.team_id:
            # Get team member IDs
            team_member_ids = [
                p.id for p in self.db.query(Player.id).filter(
                    Player.team_id == owner.team_id
                ).all()
            ]
            enemy_ships = [s for s in enemy_ships if s.owner_id not in team_member_ids]

        result["enemy_ship_count"] = len(enemy_ships)

        # Check if planet owner is present in the sector
        owner_present = owner.current_sector_id == planet.sector_id

        if len(enemy_ships) > 0 and not owner_present:
            # Enemies are present and owner is absent
            if not planet.under_siege:
                # Track escalation toward siege via siege_turns counter
                planet.siege_turns = (planet.siege_turns or 0) + 1

                if planet.siege_turns >= SIEGE_TURNS_THRESHOLD:
                    # Siege begins
                    planet.under_siege = True
                    planet.siege_started_at = datetime.utcnow()
                    # Record the first enemy ship's owner as the attacker
                    planet.siege_attacker_id = enemy_ships[0].owner_id
                    # Pin the counter to exactly the threshold at onset (S4):
                    # advance_siege derives applied_turns as
                    # siege_turns - threshold, so any escalation overshoot left
                    # here would be mistaken for already-applied decay turns,
                    # silently bypassing morale loss on a re-siege after a lift.
                    planet.siege_turns = SIEGE_TURNS_THRESHOLD
                    result["state_changed"] = True
                    logger.info(
                        f"Siege begun on planet {planet.name} (id={planet.id}) "
                        f"by player {planet.siege_attacker_id} with {len(enemy_ships)} ships"
                    )
            # If already under siege, state stays the same (effects applied by apply_siege_effects)
        else:
            # No enemies present, or owner is present -- lift siege if active
            if planet.under_siege:
                planet.under_siege = False
                planet.siege_started_at = None
                planet.siege_attacker_id = None
                planet.siege_turns = 0
                result["state_changed"] = True
                logger.info(f"Siege lifted on planet {planet.name} (id={planet.id})")
            elif planet.siege_turns and planet.siege_turns > 0:
                # Reset turn counter if enemies left before siege triggered
                planet.siege_turns = 0
                result["state_changed"] = True

        self.db.commit()
        return result
    
    def _format_planet_data(self, planet: Planet) -> Dict[str, Any]:
        """Format planet data for API response."""
        sector = planet.sector if planet.sector else None

        # Calculate production rates (siege effects are factored in automatically)
        production_rates = self._calculate_production_rates(planet)

        # Get building data
        buildings = self._get_buildings_data(planet)

        # Calculate unused colonists
        total_allocated = (
            (planet.fuel_allocation or 0) +
            (planet.organics_allocation or 0) +
            (planet.equipment_allocation or 0)
        )

        # Build siege details if under siege
        siege_details = None
        if planet.under_siege:
            siege_details = {
                "siegeStartedAt": planet.siege_started_at.isoformat() if planet.siege_started_at else None,
                # Applied-turns display (S4): subtract the threshold baseline.
                "siegeTurns": max(0, (planet.siege_turns or 0) - SIEGE_TURNS_THRESHOLD),
                "attackerId": str(planet.siege_attacker_id) if planet.siege_attacker_id else None,
                "effects": {
                    "moraleLossPerTurn": SIEGE_MORALE_LOSS_PER_TURN,
                    "productionPenalty": f"{int(SIEGE_PRODUCTION_PENALTY * 100)}%",
                    "populationGrowthHalted": True,
                    "tradeDisrupted": True
                }
            }

        # Calculate defense power and damage reduction
        defense_level = planet.defense_level or 0
        damage_reduction = defense_level * DEFENSE_DAMAGE_REDUCTION_PER_LEVEL

        # Calculate habitability effects
        habitability_effects = self.get_habitability_effects(planet)

        # Build terraforming details if active
        terraforming_details = None
        if planet.terraforming_active:
            terraforming_details = {
                "active": True,
                "target": planet.terraforming_target,
                "progress": round(planet.terraforming_progress or 0.0, 2),
                "startedAt": planet.terraforming_start_time.isoformat() if planet.terraforming_start_time else None
            }

        return {
            "id": str(planet.id),
            "name": planet.name,
            "sectorId": str(planet.sector_id) if planet.sector_id else None,
            "sectorName": sector.name if sector else "Unknown",
            "planetType": planet.planet_type or "terran",
            "colonists": planet.colonists,
            "maxColonists": habitability_effects["effectiveMaxColonists"],
            "baseMaxColonists": habitability_effects["baseMaxColonists"],
            # Dual-ceiling demographic side (ADR-0035) for the colony UI
            "population": planet.population or 0,
            "maxPopulation": max_population_for(planet.habitability_score),
            "isPopulationHub": bool(planet.is_population_hub),
            "habitability": {
                "score": planet.habitability_score,
                "effectiveMaxColonists": habitability_effects["effectiveMaxColonists"],
                "growthMultiplier": habitability_effects["growthMultiplier"],
                "moraleBonus": habitability_effects["moraleBonus"]
            },
            "morale": planet.morale,
            "productionRates": production_rates,
            "allocations": {
                "fuel": planet.fuel_allocation or 0,
                "organics": planet.organics_allocation or 0,
                "equipment": planet.equipment_allocation or 0,
                "unused": planet.colonists - total_allocated
            },
            "buildings": buildings,
            "defenses": {
                "turrets": planet.defense_turrets or 0,
                "shields": planet.defense_shields or 0,
                # No defense_drones column on Planet; fighters fill that role
                "drones": planet.defense_fighters or 0,
                "defenseLevel": defense_level,
                "maxDefenseLevel": DEFENSE_MAX_LEVEL,
                "damageReduction": f"{int(damage_reduction * 100)}%"
            },
            "terraforming": terraforming_details,
            "underSiege": planet.under_siege,
            "siegeDetails": siege_details,
            "isVulnerable": planet.morale <= 0
        }
        
    def _calculate_production_rates(self, planet: Planet) -> Dict[str, float]:
        """Calculate production rates based on allocations, buildings, habitability, and siege state."""
        base_rate = 10  # Base production per colonist per day

        # Get building levels
        factory_level = planet.factory_level or 0
        farm_level = planet.farm_level or 0
        mine_level = planet.mine_level or 0

        # Calculate rates with building bonuses
        fuel_rate = (planet.fuel_allocation or 0) * base_rate * (1 + mine_level * 0.1)
        organics_rate = (planet.organics_allocation or 0) * base_rate * (1 + farm_level * 0.1)
        equipment_rate = (planet.equipment_allocation or 0) * base_rate * (1 + factory_level * 0.1)

        # Colonist growth rate (1% per day base), scaled by habitability
        habitability = max(planet.habitability_score or 0, 1)
        habitability_multiplier = habitability / 100.0
        colonist_rate = planet.colonists * 0.01 * habitability_multiplier

        # Apply specialization bonuses
        if planet.specialization:
            bonuses = self._calculate_specialization_bonuses(planet.specialization)
            production_bonus = bonuses["production"]

            fuel_rate *= production_bonus.get("fuel", 1.0)
            organics_rate *= production_bonus.get("organics", 1.0)
            equipment_rate *= production_bonus.get("equipment", 1.0)
            colonist_rate *= production_bonus.get("colonists", 1.0)

        # Citadel passive production bonus: +5% per citadel level (citadels.md
        # "Per-level passive bonuses"). Applies to commodity output, not growth.
        citadel_level = planet.citadel_level or 0
        if citadel_level > 0:
            citadel_multiplier = 1 + 0.05 * citadel_level
            fuel_rate *= citadel_multiplier
            organics_rate *= citadel_multiplier
            equipment_rate *= citadel_multiplier

        # Apply siege effects
        if planet.under_siege:
            # Production output reduced by 25%
            siege_multiplier = 1.0 - SIEGE_PRODUCTION_PENALTY
            fuel_rate *= siege_multiplier
            organics_rate *= siege_multiplier
            equipment_rate *= siege_multiplier
            # Population growth halted during siege
            colonist_rate = 0.0

        return {
            "fuel": round(fuel_rate, 2),
            "organics": round(organics_rate, 2),
            "equipment": round(equipment_rate, 2),
            "colonists": round(colonist_rate, 2)
        }

    def get_habitability_effects(self, planet: Planet) -> Dict[str, Any]:
        """
        Calculate the effects of habitability on a planet's capacity and morale.

        Effects:
        - Max population capacity: base_capacity * (habitability / 100)
        - Population growth rate: multiplied by (habitability / 100)
        - Colony morale bonus: +1% per 10 habitability points above 50
        """
        habitability = max(planet.habitability_score or 0, 0)
        habitability_ratio = habitability / 100.0

        # Workforce-side limiter (ADR-0035): base cap is citadel-bound;
        # fall back to the citadel-tier ceiling when the column is unset.
        base_max_colonists = planet.max_colonists or max_colonists_for(planet.citadel_level or 0)
        effective_max_colonists = int(base_max_colonists * habitability_ratio)

        # Population growth multiplier
        growth_multiplier = habitability_ratio

        # Morale bonus: +1% per 10 habitability points above 50
        morale_bonus = 0
        if habitability > 50:
            morale_bonus = int((habitability - 50) / 10)

        return {
            "habitabilityScore": habitability,
            "effectiveMaxColonists": effective_max_colonists,
            "baseMaxColonists": base_max_colonists,
            "growthMultiplier": round(growth_multiplier, 2),
            "moraleBonus": morale_bonus
        }
        
    def _get_buildings_data(self, planet: Planet) -> List[Dict[str, Any]]:
        """Get building data for a planet."""
        buildings = []
        
        # Factory
        if planet.factory_level and planet.factory_level > 0:
            buildings.append({
                "type": "factory",
                "level": planet.factory_level,
                "upgrading": False,
                "completionTime": None
            })
            
        # Farm
        if planet.farm_level and planet.farm_level > 0:
            buildings.append({
                "type": "farm",
                "level": planet.farm_level,
                "upgrading": False,
                "completionTime": None
            })
            
        # Mine
        if planet.mine_level and planet.mine_level > 0:
            buildings.append({
                "type": "mine",
                "level": planet.mine_level,
                "upgrading": False,
                "completionTime": None
            })
            
        # Defense
        if planet.defense_level and planet.defense_level > 0:
            buildings.append({
                "type": "defense",
                "level": planet.defense_level,
                "upgrading": False,
                "completionTime": None
            })
            
        # Research
        if planet.research_level and planet.research_level > 0:
            buildings.append({
                "type": "research",
                "level": planet.research_level,
                "upgrading": False,
                "completionTime": None
            })
            
        return buildings
        
    def _get_building_level(self, planet: Planet, building_type: str) -> int:
        """Get current level of a building."""
        building_map = {
            "factory": planet.factory_level or 0,
            "farm": planet.farm_level or 0,
            "mine": planet.mine_level or 0,
            "defense": planet.defense_level or 0,
            "research": planet.research_level or 0
        }
        return building_map.get(building_type, 0)
        
    def _set_building_level(self, planet: Planet, building_type: str, level: int):
        """Set building level."""
        if building_type == "factory":
            planet.factory_level = level
        elif building_type == "farm":
            planet.farm_level = level
        elif building_type == "mine":
            planet.mine_level = level
        elif building_type == "defense":
            planet.defense_level = level
        elif building_type == "research":
            planet.research_level = level
            
    def _calculate_upgrade_cost(self, building_type: str, current_level: int, target_level: int) -> Dict[str, Any]:
        """Calculate cost to upgrade a building."""
        base_cost = 1000
        cost_per_level = base_cost * (target_level - current_level) * (target_level + current_level) // 2
        
        return {
            "credits": cost_per_level,
            "resources": {
                "equipment": cost_per_level // 100
            }
        }
        
    def _calculate_specialization_bonuses(self, specialization: str) -> Dict[str, Any]:
        """Calculate bonuses based on planet specialization."""
        bonuses = {
            "agricultural": {
                "production": {"fuel": 0.8, "organics": 1.5, "equipment": 0.8, "colonists": 1.2},
                "defense": 0.9,
                "research": 0.8
            },
            "industrial": {
                "production": {"fuel": 0.9, "organics": 0.8, "equipment": 1.5, "colonists": 0.9},
                "defense": 1.0,
                "research": 0.9
            },
            "military": {
                "production": {"fuel": 0.9, "organics": 0.9, "equipment": 1.1, "colonists": 0.8},
                "defense": 1.5,
                "research": 0.8
            },
            "research": {
                "production": {"fuel": 0.8, "organics": 0.8, "equipment": 0.9, "colonists": 0.9},
                "defense": 0.8,
                "research": 1.5
            },
            "balanced": {
                "production": {"fuel": 1.0, "organics": 1.0, "equipment": 1.0, "colonists": 1.0},
                "defense": 1.0,
                "research": 1.0
            }
        }
        
        return bonuses.get(specialization, bonuses["balanced"])