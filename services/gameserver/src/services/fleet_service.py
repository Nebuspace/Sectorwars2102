"""
Fleet battle service for managing fleet operations and battles.

This service handles fleet creation, management, battle simulation,
and coordination between multiple ships in organized formations.
"""

from typing import List, Optional, Dict, Any, Tuple
from uuid import UUID, uuid4
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import and_, or_, func
import random
import logging

from src.models.fleet import (
    Fleet, FleetMember, FleetBattle, FleetBattleCasualty,
    FleetRole, FleetStatus, BattlePhase
)
from src.models.ship import Ship
from src.models.player import Player
from src.models.team import Team
from src.models.sector import Sector
from src.models.combat_log import CombatLog, CombatOutcome

logger = logging.getLogger(__name__)


class FleetService:
    """Service for managing fleet operations and battles."""

    def __init__(self, db: Session):
        self.db = db

    # ---- Ship combat JSONB helpers ----

    def _get_ship_combat_stat(self, ship: Ship, stat: str, default: int = 0) -> int:
        """Safely read a value from the ship's combat JSONB field."""
        combat = ship.combat if isinstance(ship.combat, dict) else {}
        return combat.get(stat, default)

    def _set_ship_combat_stat(self, ship: Ship, stat: str, value: int) -> None:
        """Safely write a value into the ship's combat JSONB field and flag it dirty."""
        if not isinstance(ship.combat, dict):
            ship.combat = {}
        ship.combat[stat] = value
        flag_modified(ship, "combat")

    def _recalculate_fleet_stats(self, fleet: Fleet) -> None:
        """
        Recalculate aggregated fleet stats from member ships.

        This replaces Fleet.calculate_stats() which references non-existent
        Ship attributes. We read from the combat JSONB and ship columns instead.
        """
        if not fleet.members:
            fleet.total_ships = 0
            fleet.total_firepower = 0
            fleet.total_shields = 0
            fleet.total_hull = 0
            fleet.average_speed = 0.0
            return

        fleet.total_ships = len(fleet.members)
        fleet.total_firepower = sum(
            self._get_ship_combat_stat(m.ship, "attack_rating", 0)
            for m in fleet.members if m.ship
        )
        fleet.total_shields = sum(
            self._get_ship_combat_stat(m.ship, "shields", 0)
            for m in fleet.members if m.ship
        )
        fleet.total_hull = sum(
            self._get_ship_combat_stat(m.ship, "hull", 0)
            for m in fleet.members if m.ship
        )
        speeds = [m.ship.current_speed for m in fleet.members if m.ship]
        fleet.average_speed = sum(speeds) / len(speeds) if speeds else 0.0

    # Fleet Management Methods

    def create_fleet(
        self,
        team_id: UUID,
        name: str,
        commander_id: Optional[UUID] = None,
        formation: str = "standard"
    ) -> Fleet:
        """Create a new fleet for a team."""
        # Validate team exists
        team = self.db.query(Team).filter(Team.id == team_id).first()
        if not team:
            raise ValueError(f"Team {team_id} not found")

        # Validate commander if provided
        if commander_id:
            commander = self.db.query(Player).filter(
                and_(Player.id == commander_id, Player.team_id == team_id)
            ).first()
            if not commander:
                raise ValueError(f"Commander {commander_id} not found or not in team")

        # Create fleet
        fleet = Fleet(
            team_id=team_id,
            commander_id=commander_id,
            name=name,
            formation=formation,
            status=FleetStatus.FORMING.value
        )

        self.db.add(fleet)
        self.db.commit()
        self.db.refresh(fleet)

        logger.info(f"Created fleet {fleet.id} for team {team_id}")
        return fleet

    def add_ship_to_fleet(
        self,
        fleet_id: UUID,
        ship_id: UUID,
        role: FleetRole = FleetRole.ATTACKER
    ) -> FleetMember:
        """Add a ship to a fleet."""
        # Validate fleet
        fleet = self.db.query(Fleet).filter(Fleet.id == fleet_id).first()
        if not fleet:
            raise ValueError(f"Fleet {fleet_id} not found")

        if fleet.status not in [FleetStatus.FORMING.value, FleetStatus.READY.value]:
            raise ValueError(f"Fleet is not accepting new members (status: {fleet.status})")

        # Validate ship
        ship = self.db.query(Ship).filter(Ship.id == ship_id).first()
        if not ship:
            raise ValueError(f"Ship {ship_id} not found")

        # Check ship owner's team matches fleet team (NPC hulls have no owner)
        if ship.owner is None or ship.owner.team_id != fleet.team_id:
            raise ValueError("Ship and fleet must belong to the same team")

        # Check if ship is already in a fleet
        existing = self.db.query(FleetMember).filter(
            FleetMember.ship_id == ship_id
        ).first()
        if existing:
            raise ValueError(f"Ship is already in fleet {existing.fleet_id}")

        # Add ship to fleet
        member = FleetMember(
            fleet_id=fleet_id,
            ship_id=ship_id,
            player_id=ship.owner_id,
            role=role.value,
            position=fleet.total_ships  # Add at end
        )

        self.db.add(member)

        # Update fleet stats
        self._recalculate_fleet_stats(fleet)

        self.db.commit()
        self.db.refresh(member)

        logger.info(f"Added ship {ship_id} to fleet {fleet_id}")
        return member

    def remove_ship_from_fleet(self, fleet_id: UUID, ship_id: UUID) -> bool:
        """Remove a ship from a fleet."""
        member = self.db.query(FleetMember).filter(
            and_(
                FleetMember.fleet_id == fleet_id,
                FleetMember.ship_id == ship_id
            )
        ).first()

        if not member:
            return False

        fleet = member.fleet
        self.db.delete(member)

        # Recalculate fleet stats
        self._recalculate_fleet_stats(fleet)

        # Disband fleet if no ships remain
        if fleet.total_ships == 0:
            fleet.status = FleetStatus.DISBANDED.value
            fleet.disbanded_at = datetime.utcnow()

        self.db.commit()

        logger.info(f"Removed ship {ship_id} from fleet {fleet_id}")
        return True

    def set_fleet_formation(self, fleet_id: UUID, formation: str) -> Fleet:
        """Change fleet formation."""
        fleet = self.db.query(Fleet).filter(Fleet.id == fleet_id).first()
        if not fleet:
            raise ValueError(f"Fleet {fleet_id} not found")

        fleet.formation = formation
        self.db.commit()
        self.db.refresh(fleet)

        return fleet

    def set_fleet_commander(self, fleet_id: UUID, commander_id: UUID) -> Fleet:
        """Assign a new fleet commander."""
        fleet = self.db.query(Fleet).filter(Fleet.id == fleet_id).first()
        if not fleet:
            raise ValueError(f"Fleet {fleet_id} not found")

        # Validate commander is in the fleet
        member = self.db.query(FleetMember).filter(
            and_(
                FleetMember.fleet_id == fleet_id,
                FleetMember.player_id == commander_id
            )
        ).first()

        if not member:
            raise ValueError("Commander must be a member of the fleet")

        fleet.commander_id = commander_id
        self.db.commit()
        self.db.refresh(fleet)

        return fleet

    def move_fleet(self, fleet_id: UUID, sector_id: UUID) -> Fleet:
        """Move an entire fleet to a new sector."""
        fleet = self.db.query(Fleet).filter(Fleet.id == fleet_id).first()
        if not fleet:
            raise ValueError(f"Fleet {fleet_id} not found")

        if fleet.status == FleetStatus.IN_BATTLE.value:
            raise ValueError("Cannot move fleet during battle")

        # Validate sector
        sector = self.db.query(Sector).filter(Sector.id == sector_id).first()
        if not sector:
            raise ValueError(f"Sector {sector_id} not found")

        # Move all member ships — Ship.sector_id is an Integer (sector_number)
        for member in fleet.members:
            if member.ship:
                member.ship.sector_id = sector.sector_id

        fleet.sector_id = sector_id
        self.db.commit()
        self.db.refresh(fleet)

        logger.info(f"Moved fleet {fleet_id} to sector {sector_id}")
        return fleet

    def disband_fleet(self, fleet_id: UUID) -> bool:
        """Disband a fleet."""
        fleet = self.db.query(Fleet).filter(Fleet.id == fleet_id).first()
        if not fleet:
            return False

        if fleet.status == FleetStatus.IN_BATTLE.value:
            raise ValueError("Cannot disband fleet during battle")

        fleet.status = FleetStatus.DISBANDED.value
        fleet.disbanded_at = datetime.utcnow()

        # Remove all members
        self.db.query(FleetMember).filter(
            FleetMember.fleet_id == fleet_id
        ).delete()

        self.db.commit()

        logger.info(f"Disbanded fleet {fleet_id}")
        return True

    # Fleet Battle Methods

    def initiate_battle(
        self,
        attacker_fleet_id: UUID,
        defender_fleet_id: UUID
    ) -> FleetBattle:
        """Initiate a battle between two fleets."""
        # Validate fleets
        attacker = self.db.query(Fleet).filter(Fleet.id == attacker_fleet_id).first()
        defender = self.db.query(Fleet).filter(Fleet.id == defender_fleet_id).first()

        if not attacker or not defender:
            raise ValueError("Invalid fleet IDs")

        if attacker.status == FleetStatus.IN_BATTLE.value:
            raise ValueError("Attacker fleet is already in battle")

        if defender.status == FleetStatus.IN_BATTLE.value:
            raise ValueError("Defender fleet is already in battle")

        if attacker.sector_id != defender.sector_id:
            raise ValueError("Fleets must be in the same sector")

        # Create battle record
        battle = FleetBattle(
            attacker_fleet_id=attacker_fleet_id,
            defender_fleet_id=defender_fleet_id,
            sector_id=attacker.sector_id,
            phase=BattlePhase.PREPARATION.value,
            attacker_ships_initial=attacker.total_ships,
            defender_ships_initial=defender.total_ships
        )

        # Update fleet statuses
        attacker.status = FleetStatus.IN_BATTLE.value
        defender.status = FleetStatus.IN_BATTLE.value

        self.db.add(battle)
        self.db.commit()
        self.db.refresh(battle)

        logger.info(f"Initiated fleet battle {battle.id}")

        # Start preparation phase
        self._execute_preparation_phase(battle)

        return battle

    def _execute_preparation_phase(self, battle: FleetBattle):
        """Execute the preparation phase of battle."""
        battle.phase = BattlePhase.ENGAGEMENT.value

        # Log preparation events
        events = [{
            "timestamp": datetime.utcnow().isoformat(),
            "phase": "preparation",
            "event": "Battle initiated",
            "attacker_fleet": str(battle.attacker_fleet_id),
            "defender_fleet": str(battle.defender_fleet_id)
        }]

        battle.battle_log = events
        self.db.commit()

    def simulate_battle_round(self, battle_id: UUID) -> Dict[str, Any]:
        """
        Simulate one round of fleet battle.

        Each round: every active ship in both fleets fires at a random
        enemy ship. Damage is based on attack_rating from the ship's
        combat JSONB, modified by fleet formation bonuses and morale.
        Ships whose hull drops to 0 are destroyed. Ships below 30% hull
        may retreat.

        Returns a dict with round results including damage dealt,
        ships destroyed/retreated, and remaining counts per side.
        """
        # Lock battle row to prevent concurrent round simulation
        battle = self.db.query(FleetBattle).filter(FleetBattle.id == battle_id).with_for_update().first()
        if not battle:
            raise ValueError(f"Battle {battle_id} not found")

        if battle.ended_at:
            raise ValueError("Battle has already ended")

        attacker = battle.attacker_fleet
        defender = battle.defender_fleet

        # Get active ships (hull > 0, not destroyed)
        attacker_ships = self._get_active_fleet_ships(attacker)
        defender_ships = self._get_active_fleet_ships(defender)

        if not attacker_ships or not defender_ships:
            return self._end_battle(battle)

        # Calculate fleet formation bonuses
        attacker_bonus = self._calculate_formation_bonus(attacker)
        defender_bonus = self._calculate_formation_bonus(defender)

        # Determine round number from existing log
        current_log = battle.battle_log if isinstance(battle.battle_log, list) else []
        round_number = len(current_log) + 1

        # Simulate combat round
        round_results = {
            "round": round_number,
            "attacker_damage": 0,
            "defender_damage": 0,
            "ships_destroyed": [],
            "ships_retreated": []
        }

        # Attackers fire at defenders
        for ship in attacker_ships:
            if random.random() < 0.7 and defender_ships:  # 70% hit chance
                damage = self._calculate_ship_damage(ship, attacker_bonus)
                target = random.choice(defender_ships)
                self._apply_damage_to_ship(target, damage, battle, round_results)
                round_results["attacker_damage"] += damage
                # Remove destroyed ships mid-round
                defender_ships = [s for s in defender_ships if (self._get_ship_combat_stat(s, "hull", 0) or 0) > 0]

        # Refresh defender list (some may have been destroyed this round)
        active_defender_ships = [
            s for s in defender_ships
            if self._get_ship_combat_stat(s, "hull") > 0
        ]

        # Defenders return fire at attackers
        for ship in active_defender_ships:
            if random.random() < 0.7 and attacker_ships:  # 70% hit chance
                damage = self._calculate_ship_damage(ship, defender_bonus)
                target = random.choice(attacker_ships)
                self._apply_damage_to_ship(target, damage, battle, round_results)
                round_results["defender_damage"] += damage
                # Remove destroyed ships mid-round
                attacker_ships = [s for s in attacker_ships if (self._get_ship_combat_stat(s, "hull", 0) or 0) > 0]

        # Update battle damage statistics
        battle.attacker_damage_dealt = (battle.attacker_damage_dealt or 0) + round_results["attacker_damage"]
        battle.defender_damage_dealt = (battle.defender_damage_dealt or 0) + round_results["defender_damage"]
        battle.total_damage_dealt = (battle.attacker_damage_dealt or 0) + (battle.defender_damage_dealt or 0)

        # Append round to battle log (must reassign for JSONB change detection)
        updated_log = list(current_log)
        updated_log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "phase": battle.phase,
            "round": round_results["round"],
            "results": round_results
        })
        battle.battle_log = updated_log
        flag_modified(battle, "battle_log")

        # Check for battle end conditions
        if self._should_end_battle(battle, attacker, defender):
            return self._end_battle(battle)

        # Progress battle phase based on round count
        if round_number > 5 and battle.phase == BattlePhase.ENGAGEMENT.value:
            battle.phase = BattlePhase.MAIN_BATTLE.value
        elif round_number > 15 and battle.phase == BattlePhase.MAIN_BATTLE.value:
            battle.phase = BattlePhase.PURSUIT.value

        self.db.commit()

        # Count remaining active ships after this round
        attacker_remaining = len(self._get_active_fleet_ships(attacker))
        defender_remaining = len(self._get_active_fleet_ships(defender))

        return {
            "battle_id": str(battle.id),
            "phase": battle.phase,
            "round": round_results["round"],
            "attacker_remaining": attacker_remaining,
            "defender_remaining": defender_remaining,
            "round_results": round_results,
            "battle_ongoing": True
        }

    def get_battle_status(self, battle_id: UUID) -> Dict[str, Any]:
        """
        Return the current state of a fleet battle.

        Provides a snapshot including phase, remaining ships on each side,
        cumulative damage, casualties, and whether the battle is still active.
        """
        battle = self.db.query(FleetBattle).filter(FleetBattle.id == battle_id).first()
        if not battle:
            raise ValueError(f"Battle {battle_id} not found")

        is_active = battle.ended_at is None

        # Count remaining ships if fleets still exist
        attacker_remaining = 0
        defender_remaining = 0
        if battle.attacker_fleet:
            attacker_remaining = len(self._get_active_fleet_ships(battle.attacker_fleet))
        if battle.defender_fleet:
            defender_remaining = len(self._get_active_fleet_ships(battle.defender_fleet))

        # Gather casualty summary
        casualties = self.db.query(FleetBattleCasualty).filter(
            FleetBattleCasualty.battle_id == battle_id
        ).all()

        attacker_casualties = [c for c in casualties if c.was_attacker]
        defender_casualties = [c for c in casualties if not c.was_attacker]

        result = {
            "battle_id": str(battle.id),
            "phase": battle.phase,
            "is_active": is_active,
            "started_at": battle.started_at.isoformat() if battle.started_at else None,
            "ended_at": battle.ended_at.isoformat() if battle.ended_at else None,
            "winner": battle.winner,
            "sector_id": str(battle.sector_id) if battle.sector_id else None,
            "attacker_fleet_id": str(battle.attacker_fleet_id) if battle.attacker_fleet_id else None,
            "defender_fleet_id": str(battle.defender_fleet_id) if battle.defender_fleet_id else None,
            "attacker": {
                "ships_initial": battle.attacker_ships_initial or 0,
                "ships_remaining": attacker_remaining,
                "ships_destroyed": battle.attacker_ships_destroyed or 0,
                "ships_retreated": battle.attacker_ships_retreated or 0,
                "damage_dealt": battle.attacker_damage_dealt or 0,
                "formation": battle.attacker_fleet.formation if battle.attacker_fleet else None,
            },
            "defender": {
                "ships_initial": battle.defender_ships_initial or 0,
                "ships_remaining": defender_remaining,
                "ships_destroyed": battle.defender_ships_destroyed or 0,
                "ships_retreated": battle.defender_ships_retreated or 0,
                "damage_dealt": battle.defender_damage_dealt or 0,
                "formation": battle.defender_fleet.formation if battle.defender_fleet else None,
            },
            "total_damage_dealt": battle.total_damage_dealt or 0,
            "credits_looted": battle.credits_looted or 0,
            "rounds_completed": len(battle.battle_log) if isinstance(battle.battle_log, list) else 0,
            "casualties": {
                "attacker": [
                    {
                        "ship_name": c.ship_name,
                        "ship_type": c.ship_type,
                        "destroyed": c.destroyed,
                        "retreated": c.retreated,
                        "damage_taken": c.damage_taken or 0,
                    }
                    for c in attacker_casualties
                ],
                "defender": [
                    {
                        "ship_name": c.ship_name,
                        "ship_type": c.ship_type,
                        "destroyed": c.destroyed,
                        "retreated": c.retreated,
                        "damage_taken": c.damage_taken or 0,
                    }
                    for c in defender_casualties
                ],
            },
        }

        return result

    def _get_active_fleet_ships(self, fleet: Fleet) -> List[Ship]:
        """Get all active (non-destroyed) ships in a fleet."""
        active = []
        for member in fleet.members:
            if member.ship and not member.ship.is_destroyed:
                hull = self._get_ship_combat_stat(member.ship, "hull", 0)
                if hull > 0:
                    active.append(member.ship)
        return active

    def _calculate_formation_bonus(self, fleet: Fleet) -> Dict[str, float]:
        """
        Calculate combat bonuses based on fleet formation.

        Formation modifiers (per spec):
          - aggressive: +15% attack, -15% defense
          - defensive:  -15% attack, +15% defense
          - flanking:   +10% attack, -10% defense
          - turtle:     -40% attack, +40% defense
          - standard:   no modifier

        Morale scales both multipliers (100 morale = 1.0x, 50 morale = 0.5x).
        """
        bonuses = {
            "standard":   {"attack": 1.0,  "defense": 1.0},
            "aggressive": {"attack": 1.15, "defense": 0.85},
            "defensive":  {"attack": 0.85, "defense": 1.15},
            "flanking":   {"attack": 1.1,  "defense": 0.9},
            "turtle":     {"attack": 0.6,  "defense": 1.4}
        }

        # Copy so we don't mutate the template dict
        formation_bonus = dict(bonuses.get(fleet.formation, bonuses["standard"]))

        # Scale by morale (0-100 mapped to 0.0-1.0)
        morale_modifier = (fleet.morale or 100) / 100.0
        formation_bonus["attack"] *= morale_modifier
        formation_bonus["defense"] *= morale_modifier

        return formation_bonus

    def _calculate_ship_damage(self, ship: Ship, fleet_bonus: Dict[str, float]) -> int:
        """
        Calculate damage output for a ship.

        Uses attack_rating from the ship's combat JSONB as base firepower.
        Each gun-equivalent deals 10 base damage, scaled by formation attack bonus
        and a random variance of +/- 20%.
        """
        attack_rating = self._get_ship_combat_stat(ship, "attack_rating", 1)
        base_damage = attack_rating * 10
        damage = int(base_damage * fleet_bonus["attack"])

        # Random variance +/- 20%
        damage = int(damage * random.uniform(0.8, 1.2))

        return max(1, damage)

    def _apply_damage_to_ship(
        self,
        ship: Ship,
        damage: int,
        battle: FleetBattle,
        round_results: Dict[str, Any]
    ):
        """Apply damage to a ship, reducing shields first then hull."""
        # Apply target's defense formation bonus to reduce incoming damage
        member = self.db.query(FleetMember).filter(
            FleetMember.ship_id == ship.id
        ).first()

        if member:
            fleet = member.fleet
            defense_bonus = self._calculate_formation_bonus(fleet)["defense"]
            # Higher defense = less damage taken
            damage = max(1, int(damage / defense_bonus))

        current_shields = self._get_ship_combat_stat(ship, "shields", 0)
        current_hull = self._get_ship_combat_stat(ship, "hull", 0)
        max_hull = self._get_ship_combat_stat(ship, "max_hull", current_hull)

        # First absorb damage with shields
        if current_shields > 0:
            shield_damage = min(damage, current_shields)
            current_shields -= shield_damage
            self._set_ship_combat_stat(ship, "shields", current_shields)
            damage -= shield_damage

        # Remaining damage hits hull
        if damage > 0:
            current_hull -= damage

            if current_hull <= 0:
                current_hull = 0
                self._set_ship_combat_stat(ship, "hull", 0)
                ship.is_destroyed = True
                self._record_ship_casualty(ship, battle, destroyed=True)
                round_results["ships_destroyed"].append({
                    "ship_id": str(ship.id),
                    "ship_name": ship.name,
                    "player": ship.owner.username if ship.owner else "Unknown"
                })
            else:
                self._set_ship_combat_stat(ship, "hull", current_hull)

                # Check for retreat (hull below 30% of max)
                if max_hull > 0 and current_hull < max_hull * 0.3:
                    if random.random() < 0.3:  # 30% chance to retreat when heavily damaged
                        self._record_ship_casualty(ship, battle, destroyed=False)
                        round_results["ships_retreated"].append({
                            "ship_id": str(ship.id),
                            "ship_name": ship.name,
                            "player": ship.owner.username if ship.owner else "Unknown"
                        })

    def _record_ship_casualty(
        self,
        ship: Ship,
        battle: FleetBattle,
        destroyed: bool
    ):
        """Record a ship casualty in the battle via FleetBattleCasualty."""
        member = self.db.query(FleetMember).filter(
            FleetMember.ship_id == ship.id
        ).first()

        if not member:
            return

        is_attacker = member.fleet_id == battle.attacker_fleet_id

        max_hull = self._get_ship_combat_stat(ship, "max_hull", 0)
        current_hull = self._get_ship_combat_stat(ship, "hull", 0)

        # ship.type is an enum — store its string value
        ship_type_str = ship.type.value if hasattr(ship.type, 'value') else str(ship.type)

        casualty = FleetBattleCasualty(
            battle_id=battle.id,
            ship_id=ship.id,
            player_id=ship.owner_id,
            fleet_id=member.fleet_id,
            ship_name=ship.name,
            ship_type=ship_type_str,
            was_attacker=is_attacker,
            destroyed=destroyed,
            retreated=not destroyed,
            damage_taken=max_hull - current_hull,
            battle_phase=battle.phase
        )

        self.db.add(casualty)

        # Update battle statistics
        if destroyed:
            if is_attacker:
                battle.attacker_ships_destroyed = (battle.attacker_ships_destroyed or 0) + 1
            else:
                battle.defender_ships_destroyed = (battle.defender_ships_destroyed or 0) + 1
        else:
            if is_attacker:
                battle.attacker_ships_retreated = (battle.attacker_ships_retreated or 0) + 1
            else:
                battle.defender_ships_retreated = (battle.defender_ships_retreated or 0) + 1

        # Remove ship from fleet if destroyed
        if destroyed:
            self.remove_ship_from_fleet(member.fleet_id, ship.id)

    def _should_end_battle(
        self,
        battle: FleetBattle,
        attacker: Fleet,
        defender: Fleet
    ) -> bool:
        """Check if battle should end."""
        # No ships left on one side
        attacker_ships = self._get_active_fleet_ships(attacker)
        defender_ships = self._get_active_fleet_ships(defender)

        if not attacker_ships or not defender_ships:
            return True

        # Morale collapsed (below 20%)
        if (attacker.morale or 100) < 20 or (defender.morale or 100) < 20:
            return True

        # Too many casualties (> 70% losses)
        attacker_losses = (battle.attacker_ships_destroyed or 0) + (battle.attacker_ships_retreated or 0)
        defender_losses = (battle.defender_ships_destroyed or 0) + (battle.defender_ships_retreated or 0)

        attacker_initial = battle.attacker_ships_initial or 1
        defender_initial = battle.defender_ships_initial or 1

        if attacker_losses > attacker_initial * 0.7:
            return True
        if defender_losses > defender_initial * 0.7:
            return True

        # Battle timeout (30 rounds)
        log_length = len(battle.battle_log) if isinstance(battle.battle_log, list) else 0
        if log_length > 30:
            return True

        return False

    def _end_battle(self, battle: FleetBattle) -> Dict[str, Any]:
        """End a fleet battle and determine the winner."""
        battle.ended_at = datetime.utcnow()
        battle.phase = BattlePhase.AFTERMATH.value

        # Calculate remaining forces
        attacker = battle.attacker_fleet
        defender = battle.defender_fleet

        attacker_ships = self._get_active_fleet_ships(attacker) if attacker else []
        defender_ships = self._get_active_fleet_ships(defender) if defender else []

        attacker_strength = sum(
            self._get_ship_combat_stat(s, "hull", 0) + self._get_ship_combat_stat(s, "shields", 0)
            for s in attacker_ships
        )
        defender_strength = sum(
            self._get_ship_combat_stat(s, "hull", 0) + self._get_ship_combat_stat(s, "shields", 0)
            for s in defender_ships
        )

        # Determine winner — need a decisive 1.5x advantage, otherwise draw
        if attacker_strength > defender_strength * 1.5:
            battle.winner = "attacker"
        elif defender_strength > attacker_strength * 1.5:
            battle.winner = "defender"
        elif len(attacker_ships) > 0 and len(defender_ships) == 0:
            battle.winner = "attacker"
        elif len(defender_ships) > 0 and len(attacker_ships) == 0:
            battle.winner = "defender"
        else:
            battle.winner = "draw"

        # Calculate loot for winner (10% of loser team treasury)
        if battle.winner == "attacker" and defender and defender.team:
            loot = (defender.team.treasury_credits or 0) // 10
            battle.credits_looted = loot
            if attacker and attacker.team:
                attacker.team.treasury_credits = (attacker.team.treasury_credits or 0) + loot
            if loot > 0:
                defender.team.treasury_credits = (defender.team.treasury_credits or 0) - loot

        elif battle.winner == "defender" and attacker and attacker.team:
            loot = (attacker.team.treasury_credits or 0) // 10
            battle.credits_looted = loot
            if defender and defender.team:
                defender.team.treasury_credits = (defender.team.treasury_credits or 0) + loot
            if loot > 0:
                attacker.team.treasury_credits = (attacker.team.treasury_credits or 0) - loot

        # Update fleet statuses
        if attacker:
            attacker.status = FleetStatus.READY.value
            attacker.last_battle = datetime.utcnow()
            attacker.morale = max(10, (attacker.morale or 100) - 20)

        if defender:
            defender.status = FleetStatus.READY.value
            defender.last_battle = datetime.utcnow()
            defender.morale = max(10, (defender.morale or 100) - 20)

        # Append aftermath entry to battle log
        current_log = list(battle.battle_log) if isinstance(battle.battle_log, list) else []
        current_log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "phase": "aftermath",
            "event": "Battle ended",
            "winner": battle.winner,
            "credits_looted": battle.credits_looted or 0,
            "final_statistics": {
                "attacker_ships_destroyed": battle.attacker_ships_destroyed or 0,
                "defender_ships_destroyed": battle.defender_ships_destroyed or 0,
                "total_damage": battle.total_damage_dealt or 0
            }
        })
        battle.battle_log = current_log
        flag_modified(battle, "battle_log")

        self.db.commit()

        duration = str(battle.ended_at - battle.started_at) if battle.started_at else "unknown"

        return {
            "battle_id": str(battle.id),
            "winner": battle.winner,
            "duration": duration,
            "attacker_losses": (battle.attacker_ships_destroyed or 0) + (battle.attacker_ships_retreated or 0),
            "defender_losses": (battle.defender_ships_destroyed or 0) + (battle.defender_ships_retreated or 0),
            "credits_looted": battle.credits_looted or 0,
            "battle_ongoing": False
        }

    # Query Methods

    def get_team_fleets(self, team_id: UUID) -> List[Fleet]:
        """Get all fleets for a team."""
        return self.db.query(Fleet).filter(
            and_(
                Fleet.team_id == team_id,
                Fleet.status != FleetStatus.DISBANDED.value
            )
        ).all()

    def get_player_fleets(self, player_id: UUID) -> List[Fleet]:
        """Get all fleets where player has ships."""
        fleet_ids = self.db.query(FleetMember.fleet_id).filter(
            FleetMember.player_id == player_id
        ).distinct().subquery()

        return self.db.query(Fleet).filter(
            and_(
                Fleet.id.in_(fleet_ids),
                Fleet.status != FleetStatus.DISBANDED.value
            )
        ).all()

    def get_sector_fleets(self, sector_id: UUID) -> List[Fleet]:
        """Get all fleets in a sector."""
        return self.db.query(Fleet).filter(
            and_(
                Fleet.sector_id == sector_id,
                Fleet.status != FleetStatus.DISBANDED.value
            )
        ).all()

    def get_fleet_battles(
        self,
        fleet_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None,
        active_only: bool = False
    ) -> List[FleetBattle]:
        """Get fleet battles with filters."""
        query = self.db.query(FleetBattle)

        if fleet_id:
            query = query.filter(
                or_(
                    FleetBattle.attacker_fleet_id == fleet_id,
                    FleetBattle.defender_fleet_id == fleet_id
                )
            )

        if team_id:
            # Need to join with Fleet to filter by team
            query = query.join(
                Fleet,
                or_(
                    Fleet.id == FleetBattle.attacker_fleet_id,
                    Fleet.id == FleetBattle.defender_fleet_id
                )
            ).filter(Fleet.team_id == team_id)

        if active_only:
            query = query.filter(FleetBattle.ended_at.is_(None))

        return query.order_by(FleetBattle.started_at.desc()).all()
