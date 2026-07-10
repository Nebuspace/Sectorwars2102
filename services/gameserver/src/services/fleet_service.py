"""
Fleet battle service for managing fleet operations and battles.

This service handles fleet creation, management, battle simulation,
and coordination between multiple ships in organized formations.
"""

from typing import List, Optional, Dict, Any, Tuple, TYPE_CHECKING
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
from src.models.treasury_transaction import TreasuryTransaction
from src.models.sector import Sector
from src.models.combat_log import CombatLog, CombatOutcome

if TYPE_CHECKING:
    # Forward-ref-only imports for type annotations — resolved at runtime via
    # local imports inside the methods that use them (no import-time cost).
    from src.models.station import Station
    from src.services.personal_reputation_service import PersonalReputationService

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

    def _compute_coordination_bonus(self, fleet: Fleet) -> float:
        """
        Compute the static multi-ship coordination bonus for a fleet.

        Per ADR-0061 S-I3 and FEATURES/gameplay/fleet-tactics.md:
            coord_bonus = min(0.20, max(0.0, (ships - 2) * 0.025))

        1-2 ships → 0%, 3 → 2.5%, 5 → 7.5%, 8 → 15%, 10+ → 20% (capped).
        The bonus is static — recomputed only on roster-change events
        (member added / removed / KIA) — and the combat resolver reads the
        cached fleet.coordination_bonus value as an OUTER attack multiplier.
        """
        ships = fleet.total_ships or 0
        return min(0.20, max(0.0, (ships - 2) * 0.025))

    def get_coordination_bonus(self, ship_id: UUID) -> float:
        """
        Return the cached coordination_bonus of the fleet a ship is
        currently enrolled in as a FleetMember, or 0.0 if the ship isn't
        fleet-enrolled.

        Ship-keyed (not player-keyed): the bonus belongs to whichever SHIP
        is actually fighting, not to who owns it -- a player piloting a
        ship that ISN'T fleet-enrolled gets no bonus even if they
        separately own an enrolled ship elsewhere, and an NPC-controlled
        ship (never a FleetMember -- fleets are Team-owned player
        structures) transparently resolves to 0.0 with no special-casing
        needed by the caller.

        Reads the SAME static value _compute_coordination_bonus writes on
        roster-change events (create/add/remove/KIA) -- never recomputes
        here, per fleet-coordination.md:95 ("the combat resolver reads the
        cached value").

        Defensive on the whole read (mirrors combat_service._medal_combat_
        damage_bonus / _sector_combat_modifier): a fleet-lookup hiccup —
        including a caller whose session/mock isn't wired for a FleetMember
        query at all — must never break combat resolution, so any failure
        degrades to 0.0 rather than propagating. The isinstance checks below
        are deliberate, not just a None-guard: a permissive test double
        (e.g. an unconfigured MagicMock session used elsewhere in the combat
        suite for unrelated purposes) can return a truthy, numeric-coercible
        stand-in for `.first()` without ever raising, silently smuggling in
        a nonzero bonus — isinstance anchors this to a genuine FleetMember
        row backed by a genuine Fleet, never a stand-in that merely "looks"
        truthy.
        """
        try:
            member = self.db.query(FleetMember).filter(
                FleetMember.ship_id == ship_id
            ).first()
            if not isinstance(member, FleetMember) or not isinstance(member.fleet, Fleet):
                return 0.0
            return max(0.0, float(member.fleet.coordination_bonus or 0.0))
        except Exception as e:  # never let a fleet-lookup hiccup break combat
            logger.error(
                "Coordination-bonus read failed (continuing without): %s", e
            )
            return 0.0

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

        # Coordination bonus is static; a freshly-created fleet has 0 ships,
        # so it starts at 0.0 and is recomputed when ships are added.
        fleet.coordination_bonus = self._compute_coordination_bonus(fleet)

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
        # Roster changed → recompute static coordination bonus (ADR-0061 S-I3)
        fleet.coordination_bonus = self._compute_coordination_bonus(fleet)

        self.db.commit()
        self.db.refresh(member)

        logger.info(f"Added ship {ship_id} to fleet {fleet_id}")
        return member

    def remove_ship_from_fleet(self, fleet_id: UUID, ship_id: UUID) -> bool:
        """Remove a ship from a fleet.

        If the removed member held the FLAGSHIP role, promotes a successor
        in the SAME transaction (WO-FLEET-CASUALTY-SUCCESSION,
        fleet-tactics.md:80 — "leadership transitions to the next-most-senior
        member"). This is the single shared removal path for BOTH a manual
        removal (fleets.py route) and a combat KIA (_record_ship_casualty),
        so hooking succession here covers both triggers without duplication.
        See _promote_flagship_successor for the NO-CANON seniority kernel.
        """
        member = self.db.query(FleetMember).filter(
            and_(
                FleetMember.fleet_id == fleet_id,
                FleetMember.ship_id == ship_id
            )
        ).first()

        if not member:
            return False

        fleet = member.fleet
        was_flagship = (member.role or "") == FleetRole.FLAGSHIP.value
        fallen_pilot_id = member.player_id
        self.db.delete(member)

        # Recalculate fleet stats
        self._recalculate_fleet_stats(fleet)
        # Roster changed (manual removal OR mid-battle KIA via
        # _record_ship_casualty → remove_ship_from_fleet). Recompute the
        # static coordination bonus from the surviving roster (ADR-0061 S-I3).
        # For a KIA mid-combat this recompute takes effect at the next round
        # boundary, which is exactly the ADR's required timing.
        fleet.coordination_bonus = self._compute_coordination_bonus(fleet)

        # Disband fleet if no ships remain
        if fleet.total_ships == 0:
            fleet.status = FleetStatus.DISBANDED.value
            fleet.disbanded_at = datetime.utcnow()
        elif was_flagship:
            self._promote_flagship_successor(fleet, fallen_pilot_id)

        self.db.commit()

        logger.info(f"Removed ship {ship_id} from fleet {fleet_id}")
        return True

    def _promote_flagship_successor(
        self,
        fleet: Fleet,
        fallen_pilot_id: Optional[UUID],
    ) -> Optional[FleetMember]:
        """Promote the next-most-senior surviving member to FLAGSHIP after
        the prior flagship's FleetMember row was removed (destroyed OR
        manually removed — see remove_ship_from_fleet, the single caller).

        NO-CANON KERNEL (flagged for a DECISIONS.md ruling): fleet-tactics.md
        only says "leadership transitions to the next-most-senior member"
        (target spec) without defining "seniority". This kernel treats
        seniority as earliest FleetMember.joined_at, ties broken by the
        lowest member id (str-compared UUID — a stable, deterministic
        tie-break, not a canon ruling). Swapping the definition later is a
        one-line change to the sort key below.

        If the fallen flagship's pilot (fallen_pilot_id — the removed
        member's OWN player_id, who is the commander under normal operation
        per "The commander is automatically the flagship's pilot") held
        Fleet.commander_id, command transfers to the promoted member's
        player_id in the SAME transaction — the fleet is never left without
        both a flagship AND a commander while a successor exists.

        Queries FleetMember fresh (not fleet.members) because the caller's
        session.delete() on the old flagship member is only guaranteed to be
        reflected via SQLAlchemy's autoflush-on-query, not in an
        already-loaded in-memory relationship collection.

        Returns the promoted FleetMember, or None if no member remains (the
        caller only reaches this branch when fleet.total_ships > 0, so this
        is a defensive no-op guard, not the expected path).
        """
        remaining = self.db.query(FleetMember).filter(
            FleetMember.fleet_id == fleet.id
        ).all()
        if not remaining:
            return None

        successor = min(
            remaining,
            key=lambda m: (m.joined_at or datetime.min, str(m.id))
        )
        successor.role = FleetRole.FLAGSHIP.value

        transferred_command = False
        if fallen_pilot_id is not None and fleet.commander_id == fallen_pilot_id:
            fleet.commander_id = successor.player_id
            transferred_command = True

        logger.info(
            "Flagship succession: fleet %s promotes member %s (player %s) to "
            "FLAGSHIP%s",
            fleet.id, successor.id, successor.player_id,
            " + transferred command" if transferred_command else "",
        )
        return successor

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

    # Fleet Supply Methods

    # ---- Resupply kernel constants (NO-CANON — FLAGGED for DECISIONS Pending) ----
    #
    # fleet-tactics.md "Supply" states only: "Supply replenishes when the fleet
    # is docked at a friendly station, at a rate proportional to station class."
    # It is explicitly marked 📐 design-only and gives NO credit cost and NO
    # per-class numbers. The values below are a SENSIBLE KERNEL, not canon, and
    # must be reconciled with a Max ruling (see report → DECISIONS Pending
    # "fleet-station-resupply cost+rate"). They are intentionally named so a
    # future canon swap is a one-line change.
    SUPPLY_MAX = 100                      # Fleet.supply_level upper bound (model: 0-100)
    RESUPPLY_COST_PER_POINT = 50         # credits charged per supply POINT restored
    # "rate proportional to station class": a single resupply visit can raise
    # supply by at most (base + class * step) points. A Class-0/1 outpost tops
    # up slowly; a Class-11 hub can fully refill in one visit. This is the
    # canon "rate proportional to station class" expressed as a per-action
    # restore CEILING (the fleet still pays per point actually restored).
    RESUPPLY_BASE_RESTORE = 20           # min points any friendly station restores per visit
    RESUPPLY_RESTORE_PER_CLASS = 8       # additional restore-ceiling points per station class

    def _max_restore_for_station(self, station: "Station") -> int:
        """Per-visit restore CEILING for a station, scaling with its class.

        NO-CANON kernel (see RESUPPLY_* constants). station_class is a
        StationClass enum whose .value is the 0-11 integer class.
        """
        try:
            station_class = station.station_class.value if station.station_class is not None else 0
        except AttributeError:
            station_class = int(station.station_class or 0)
        return self.RESUPPLY_BASE_RESTORE + self.RESUPPLY_RESTORE_PER_CLASS * int(station_class)

    def resupply_fleet(self, fleet_id: UUID, player_id: UUID) -> Dict[str, Any]:
        """Pay credits to raise a docked fleet's supply_level back toward max.

        CORRECTION (WO-FLEET-CASUALTY-SUCCESSION): this method previously
        claimed to be "the recovery counterpart to the WO-R decay tick" —
        that decay tick does not exist. Grep-verified: Fleet.supply_level is
        written NOWHERE else in the codebase (no scheduler job, no per-round
        combat write, no admin override) — this method is the SOLE writer of
        Fleet.supply_level, in EITHER direction. Supply never drops on its
        own; it starts at the model default (100) and only ever moves via a
        resupply purchase here. Whether supply SHOULD decay over time (and if
        so, by what rate/trigger) is an open design question tracked as
        WO-FLEET-SUPPLY-SINK — that is where the decay question lives, not
        here. DOES NOT touch decay (there is none) or _calculate_formation_bonus.

        Requirements (all enforced; reject before mutating any state):
          - The fleet exists and is not disbanded.
          - The requesting player is a MEMBER of the fleet.
          - The fleet is not IN_BATTLE.
          - The player is DOCKED at a station (player.is_docked + current_port_id),
            and that station is in the fleet's sector (the fleet is at the dock).
          - The player has enough credits for the restore being purchased.

        Cost + restore-rate numbers are a NO-CANON kernel (see RESUPPLY_*).

        Returns: {fleet_id, supply_level (new), supply_restored, credits_spent,
                  station_id, station_class, credits_remaining}.
        Raises ValueError on any precondition failure.
        """
        # Lock the fleet row so concurrent resupply/decay/battle transitions are
        # serialized against this top-up.
        fleet = self.db.query(Fleet).filter(
            Fleet.id == fleet_id
        ).with_for_update().first()
        if not fleet:
            raise ValueError(f"Fleet {fleet_id} not found")

        if fleet.status == FleetStatus.DISBANDED.value:
            raise ValueError("Cannot resupply a disbanded fleet")

        if fleet.status == FleetStatus.IN_BATTLE.value:
            raise ValueError("Cannot resupply a fleet during battle")

        # Requesting player must be a member of the fleet (owner check mirrored
        # by the route's auth/ownership gate; enforced here too for safety).
        membership = self.db.query(FleetMember).filter(
            and_(
                FleetMember.fleet_id == fleet_id,
                FleetMember.player_id == player_id
            )
        ).first()
        if not membership:
            raise ValueError("Only a member of the fleet can resupply it")

        # Row-lock the paying player; read docking + credits under the lock.
        player = self.db.query(Player).filter(
            Player.id == player_id
        ).with_for_update().first()
        if not player:
            raise ValueError(f"Player {player_id} not found")

        # The fleet is "docked" when its paying member is docked at a station
        # that sits in the fleet's sector. Fleet.sector_id is a Station/Sector
        # UUID; Station.sector_id is the integer sector number, so we resolve
        # the docked station and verify it is the fleet's location.
        if not player.is_docked or player.current_port_id is None:
            raise ValueError("You must be docked at a station to resupply the fleet")

        from src.models.station import Station
        station = self.db.query(Station).filter(
            Station.id == player.current_port_id
        ).first()
        if not station:
            raise ValueError("Docked station not found")

        # Verify the fleet is at this dock. The fleet tracks a Sector UUID
        # (Fleet.sector_id → sectors.id); the station carries that same UUID in
        # sector_uuid. If the fleet has no sector recorded we fall back to the
        # player's integer sector vs the station's integer sector_id.
        fleet_at_station = False
        if fleet.sector_id is not None and station.sector_uuid is not None:
            fleet_at_station = fleet.sector_id == station.sector_uuid
        else:
            fleet_at_station = player.current_sector_id == station.sector_id
        if not fleet_at_station:
            raise ValueError("The fleet is not docked at your station")

        current_supply = fleet.supply_level if fleet.supply_level is not None else self.SUPPLY_MAX

        # Idempotent / safe at (or above) full supply: reject as a no-op so the
        # player is never charged for nothing.
        if current_supply >= self.SUPPLY_MAX:
            raise ValueError("Fleet supply is already full")

        # How much CAN this station restore in one visit (class-scaled ceiling),
        # bounded by the headroom to SUPPLY_MAX.
        headroom = self.SUPPLY_MAX - current_supply
        restore_ceiling = self._max_restore_for_station(station)
        desired_restore = min(headroom, restore_ceiling)

        # How much can the player AFFORD (charged per point restored)? Restore
        # only the points they can pay for — never partially mutate then fail.
        credits = player.credits or 0
        affordable_points = credits // self.RESUPPLY_COST_PER_POINT
        if affordable_points <= 0:
            raise ValueError("Insufficient credits to resupply the fleet")

        supply_restored = int(min(desired_restore, affordable_points))
        if supply_restored <= 0:
            # Defensive: nothing to do (e.g. headroom rounded to 0).
            raise ValueError("No supply could be restored")

        cost = supply_restored * self.RESUPPLY_COST_PER_POINT

        # Apply: charge credits, raise supply. This is the ONLY place supply
        # rises — combat's 0-supply block at initiate_battle clears the moment
        # supply_level goes above 0.
        player.credits = credits - cost
        fleet.supply_level = current_supply + supply_restored

        station_class_value = None
        try:
            station_class_value = (
                station.station_class.value if station.station_class is not None else None
            )
        except AttributeError:
            station_class_value = int(station.station_class) if station.station_class is not None else None

        self.db.commit()
        self.db.refresh(fleet)

        logger.info(
            "Resupplied fleet %s at station %s (class %s): +%d supply (now %d) for %d cr by player %s",
            fleet_id, station.id, station_class_value, supply_restored,
            fleet.supply_level, cost, player_id,
        )

        return {
            "fleet_id": str(fleet.id),
            "supply_level": fleet.supply_level,
            "supply_restored": supply_restored,
            "credits_spent": cost,
            "credits_remaining": player.credits,
            "station_id": str(station.id),
            "station_class": station_class_value,
        }

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

        # Supply (fleet-tactics.md): a fleet at 0 supply cannot initiate combat.
        if (attacker.supply_level or 0) <= 0:
            raise ValueError("Attacker fleet is out of supply and cannot initiate combat")

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
        combat JSONB, modified by the firing fleet's formation/supply
        multiplier and the outer coordination bonus (ADR-0061 S-I3). Morale
        was removed from the damage stack (WO-BS, reverts WO-AS — combat-morale
        coupling retired per Max). Ships whose hull drops to 0 are destroyed.
        Ships below 30% hull may retreat.

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
            "ships_retreated": [],
            # Per-shot ledger (WO-FLEET-CASUALTY-SUCCESSION): {ship_id, damage,
            # killed} for every LANDED shot this round, keyed by the FIRING
            # ship. Persisted verbatim into FleetBattle.battle_log alongside
            # the rest of round_results (existing append below), and summed
            # by _ship_battle_contribution to populate
            # FleetBattleCasualty.damage_dealt/kills when a ship becomes a
            # casualty. A miss never reaches this list (a miss deals no
            # damage, so it has nothing to attribute).
            "shots": [],
        }

        # Attackers fire at defenders
        for ship in attacker_ships:
            if random.random() < 0.7 and defender_ships:  # 70% hit chance
                damage = self._calculate_ship_damage(ship, attacker_bonus, attacker)
                target = random.choice(defender_ships)
                target_destroyed = self._apply_damage_to_ship(target, damage, battle, round_results)
                round_results["attacker_damage"] += damage
                round_results["shots"].append({
                    "ship_id": str(ship.id), "damage": damage, "killed": target_destroyed
                })
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
                damage = self._calculate_ship_damage(ship, defender_bonus, defender)
                target = random.choice(attacker_ships)
                target_destroyed = self._apply_damage_to_ship(target, damage, battle, round_results)
                round_results["defender_damage"] += damage
                round_results["shots"].append({
                    "ship_id": str(ship.id), "damage": damage, "killed": target_destroyed
                })
                # Remove destroyed ships mid-round
                attacker_ships = [s for s in attacker_ships if (self._get_ship_combat_stat(s, "hull", 0) or 0) > 0]

        # Update battle damage statistics
        battle.attacker_damage_dealt = (battle.attacker_damage_dealt or 0) + round_results["attacker_damage"]
        battle.defender_damage_dealt = (battle.defender_damage_dealt or 0) + round_results["defender_damage"]
        battle.total_damage_dealt = (battle.attacker_damage_dealt or 0) + (battle.defender_damage_dealt or 0)

        # FULLY INERT (WO-BS2, reverts WO-AS): the per-round supply-driven morale
        # decrement was removed (WO-BS), and as of WO-BS2 EVERY remaining combat
        # morale write/read is gone too — the flagship -30, the post-battle -20,
        # and the < 20 morale-collapse battle-end check. Max ruled Fleet.morale
        # has NO gameplay value at all: it participates in neither combat DAMAGE
        # nor battle DURATION. The combat path now writes/reads Fleet.morale
        # NOWHERE. The Fleet.morale COLUMN is kept (non-destructive, no migration)
        # but is purely cosmetic — only the admin adjust helper (another file)
        # touches it, for display. Do not re-introduce a combat-path morale write.

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

    # DEPRECATED + REMOVED — Fleet.morale is now FULLY INERT in combat
    # (WO-BS2, reverts WO-AS). The former ``_morale_factor`` helper mapped
    # Fleet.morale to an outer ``(1 + morale_modifier)`` combat multiplier
    # (ADR-0061 S-I3). Max ruled the combat-morale coupling CUT entirely — the
    # ADR-0061 morale clause is retired. WO-BS removed the damage coupling (this
    # helper + its attack/defense applications) and the per-round attrition
    # decrement; WO-BS2 removed the LAST residual coupling — the battle-DURATION
    # path — by deleting the flagship -30, the post-battle -20, and the < 20
    # morale-collapse battle-end check. The combat path now writes/reads
    # Fleet.morale NOWHERE (identical battle outcome AND duration at morale
    # 100 / 50 / 0). The Fleet.morale COLUMN is intentionally kept
    # (non-destructive, no migration) but is cosmetic only — touched solely by
    # the admin adjust helper (another file) for display.

    def _calculate_formation_bonus(self, fleet: Fleet) -> Dict[str, float]:
        """
        Calculate combat bonuses based on fleet FORMATION (and supply) only.

        Formation modifiers (per spec):
          - aggressive: +15% attack, -15% defense
          - defensive:  -15% attack, +15% defense
          - flanking:   +10% attack, -10% defense
          - turtle:     -40% attack, +40% defense
          - standard:   no modifier

        MORALE IS NOT APPLIED ANYWHERE in combat. The combat-morale coupling was
        retired per Max (WO-BS, reverts WO-AS; ADR-0061 S-I3 morale clause
        retired) — fleet combat damage no longer depends on Fleet.morale. The
        supply penalty below is a separate fleet-tactics.md factor and is
        unaffected by that removal.
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

        # Supply penalty (fleet-tactics.md Supply): above 50 no penalty, 25-50
        # -5%, below 25 -15% — to BOTH attack and defense. supply_level defaults
        # to 100 (full) if unset. This is the only fleet-level combat factor that
        # remains here; morale was removed from combat entirely (WO-BS).
        supply = fleet.supply_level if fleet.supply_level is not None else 100
        if supply < 25:
            supply_factor = 0.85
        elif supply <= 50:
            supply_factor = 0.95
        else:
            supply_factor = 1.0
        formation_bonus["attack"] *= supply_factor
        formation_bonus["defense"] *= supply_factor

        return formation_bonus

    def _calculate_ship_damage(
        self,
        ship: Ship,
        fleet_bonus: Dict[str, float],
        fleet: Optional[Fleet] = None,
    ) -> int:
        """
        Calculate damage output for a ship.

        Uses attack_rating from the ship's combat JSONB as base firepower.
        Each gun-equivalent deals 10 base damage, scaled by formation attack bonus
        and a random variance of +/- 20%.

        The coordination bonus is the only outer attack multiplier in the damage
        stack:
            final = base
                  × formation_attack          # formation + supply only
                  × (1 + coordination_bonus)   # static, ADR-0061 S-I3
                  × variance
        MORALE WAS REMOVED from this stack (WO-BS, reverts WO-AS; ADR-0061 S-I3
        morale clause retired per Max): combat damage no longer depends on
        Fleet.morale, so damage is identical at morale 100 / 50 / 0.
        """
        attack_rating = self._get_ship_combat_stat(ship, "attack_rating", 1)
        base_damage = attack_rating * 10
        damage = int(base_damage * fleet_bonus["attack"])

        # Static coordination bonus (outer attack multiplier, ADR-0061 S-I3).
        # Read the cached value off the live fleet; clamp defensively. Morale is
        # NO LONGER a factor here (WO-BS, reverts WO-AS — combat-morale coupling
        # retired per Max): damage is independent of Fleet.morale.
        coordination_bonus = 0.0
        if fleet is not None:
            coordination_bonus = max(0.0, fleet.coordination_bonus or 0.0)
        damage = int(damage * (1 + coordination_bonus))

        # Random variance +/- 20%
        damage = int(damage * random.uniform(0.8, 1.2))

        return max(1, damage)

    def _apply_damage_to_ship(
        self,
        ship: Ship,
        damage: int,
        battle: FleetBattle,
        round_results: Dict[str, Any]
    ) -> bool:
        """Apply damage to a ship, reducing shields first then hull.

        Returns True iff THIS call destroyed the ship — the caller (the two
        fire loops in simulate_battle_round) uses it to attribute kill credit
        to the FIRING ship's own "shots" ledger entry (WO-FLEET-CASUALTY-
        SUCCESSION), separate from this ship's own casualty bookkeeping below.
        """
        # Apply target's defense formation bonus to reduce incoming damage
        member = self.db.query(FleetMember).filter(
            FleetMember.ship_id == ship.id
        ).first()

        if member:
            fleet = member.fleet
            # Formation (+ supply) defense multiplier ONLY. Morale was removed
            # from the defense math (WO-BS, reverts WO-AS — combat-morale
            # coupling retired per Max): incoming damage no longer depends on
            # Fleet.morale.
            defense_bonus = self._calculate_formation_bonus(fleet)["defense"]
            # Higher defense = less damage taken. Guard against a 0 multiplier so
            # we never divide by zero — at/near-zero defense the target takes
            # full incoming damage.
            if defense_bonus > 0:
                damage = max(1, int(damage / defense_bonus))
            # Defender role: +10% damage absorption when targeted
            # (fleet-tactics.md role assignments).
            if (member.role or "") == FleetRole.DEFENDER.value:
                damage = max(1, int(damage * 0.9))

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
                # NOTE: do NOT pre-set ship.is_destroyed here. The shared
                # destruction handler (ShipService.destroy_ship), invoked from
                # _record_ship_casualty, owns the is_destroyed flag along with
                # the escape-pod swap, emergency-cargo transfer, and insurance
                # payout — giving FLEET kills parity with SOLO kills.
                self._record_ship_casualty(ship, battle, destroyed=True, round_results=round_results)
                round_results["ships_destroyed"].append({
                    "ship_id": str(ship.id),
                    "ship_name": ship.name,
                    "player": ship.owner.username if ship.owner else "Unknown"
                })
                return True
            else:
                self._set_ship_combat_stat(ship, "hull", current_hull)

                # Check for retreat (hull below 30% of max)
                if max_hull > 0 and current_hull < max_hull * 0.3:
                    if random.random() < 0.3:  # 30% chance to retreat when heavily damaged
                        self._record_ship_casualty(ship, battle, destroyed=False, round_results=round_results)
                        round_results["ships_retreated"].append({
                            "ship_id": str(ship.id),
                            "ship_name": ship.name,
                            "player": ship.owner.username if ship.owner else "Unknown"
                        })

        return False

    def _ship_battle_contribution(
        self,
        ship_id: UUID,
        battle: FleetBattle,
        round_results: Optional[Dict[str, Any]],
    ) -> Tuple[int, int]:
        """Sum a ship's OWN damage_dealt + kills across the whole battle so
        far: every prior round already persisted in battle.battle_log, plus
        shots fired so far in the CURRENT (in-progress, not-yet-appended)
        round. Read-only, no DB access — pure over battle.battle_log +
        round_results, both plain JSON-shaped Python structures.

        Used by _record_ship_casualty to populate
        FleetBattleCasualty.damage_dealt/kills when THIS ship itself becomes
        a casualty (WO-FLEET-CASUALTY-SUCCESSION). A ship that never fires a
        landed shot (or whose shots are all misses) correctly resolves to
        (0, 0).

        NOTE (discovered during design, not a defect introduced here): a
        fleet-battle round always fires attackers before defenders, so the
        side that ultimately WINS a decisive engagement necessarily retains
        at least one living, undamaged-this-round member whose own
        contribution is never captured by any casualty row — casualties are
        recorded only for ships that are destroyed or retreat (invariant 9,
        fleet-coordination.md), never for survivors. This means SUM(damage_
        dealt) across a battle's casualty rows will generally be <=
        battle.attacker_damage_dealt + battle.defender_damage_dealt, with
        equality only when every ship that ever landed a shot also became a
        casualty. This is a structural property of the round-resolution
        algorithm (out of this WO's scope to change), not a bug in this
        accumulation.
        """
        sid = str(ship_id)
        damage_dealt = 0
        kills = 0

        prior_log = battle.battle_log if isinstance(battle.battle_log, list) else []
        all_rounds = list(prior_log) + [{"results": round_results or {}}]
        for entry in all_rounds:
            shots = (entry.get("results") or {}).get("shots") or []
            for shot in shots:
                if shot.get("ship_id") == sid:
                    damage_dealt += int(shot.get("damage", 0) or 0)
                    if shot.get("killed"):
                        kills += 1

        return damage_dealt, kills

    def _record_ship_casualty(
        self,
        ship: Ship,
        battle: FleetBattle,
        destroyed: bool,
        round_results: Optional[Dict[str, Any]] = None,
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

        # WO-FLEET-CASUALTY-SUCCESSION: this SHIP's own accumulated damage
        # dealt + kills scored (as a FIRER) up to this casualty event — was
        # always hard-0 before (never populated). See
        # _ship_battle_contribution for the accumulation + its documented
        # survivor-asymmetry caveat.
        damage_dealt, kills = self._ship_battle_contribution(ship.id, battle, round_results)

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
            damage_dealt=damage_dealt,
            kills=kills,
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

        # Flagship destruction: the former one-shot -30 to Fleet.morale on
        # flagship loss is REMOVED (WO-BS2, reverts WO-AS). Max ruled Fleet.morale
        # has NO gameplay value at all — neither combat DAMAGE (cut in WO-BS) nor
        # battle DURATION (this WO). Combat now writes Fleet.morale NOWHERE. The
        # Fleet.morale COLUMN is intentionally kept (non-destructive, no migration)
        # but is fully INERT/COSMETIC — only the admin adjust helper (another file)
        # touches it, for display. Do not re-introduce any combat-path morale write.

        # Route the actual hull destruction through the SAME shared handler the
        # SOLO combat path uses (ShipService.destroy_ship). This is what gives
        # FLEET kills parity on the destruction side: escape-pod ejection,
        # 10% emergency-cargo transfer, and — the dead-end this fixes — the
        # INSURANCE payout to the registered owner. Previously a fleet KIA only
        # flipped is_destroyed and recorded a casualty row, so a hull insured
        # under ship-insurance.md never paid out when it died in a fleet battle.
        #
        # The destroyer is the opposing fleet's commander (the destroyed ship's
        # fleet is the attacker iff is_attacker, so the killer is the other
        # side). destroy_ship currently makes no use of the destroyer arg, but
        # it is passed for signature/audit parity with the solo path.
        #
        # Ordering: destroy_ship runs BEFORE remove_ship_from_fleet because the
        # latter commits the session; destroy_ship itself never commits, so its
        # owner relocation + insurance credit ride along in the same commit.
        # Escape pods are indestructible (mirrors CombatService): skip them so a
        # pod that bottoms out at hull 0 isn't fed to destroy_ship (which would
        # no-op anyway) and the casualty/removal bookkeeping still runs.
        if destroyed:
            from src.services.ship_service import ShipService
            if not ShipService(self.db).is_ship_indestructible(ship):
                killer_fleet = (
                    battle.defender_fleet if is_attacker else battle.attacker_fleet
                )
                destroyer = killer_fleet.commander if killer_fleet else None

                # Snapshot the dead hull's registered owner BEFORE destroy_ship
                # runs (destroy_ship repoints the OWNER's current_ship to the
                # escape pod; ship.owner itself is unchanged, but we capture it
                # explicitly to mirror the solo path's "read the dead hull" rule).
                original_owner = ship.owner

                ShipService(self.db).destroy_ship(ship, destroyer=destroyer, cause="combat")

                # WO-FLEETWRECK: spawn the salvageable Cargo Wreck for a FLEET
                # kill, giving fleet battles the SAME loot-drop the solo combat
                # path already produces. Previously a fleet KIA stripped only the
                # 10% emergency cargo to the escape pod (inside destroy_ship) and
                # the rest of the hold silently vanished — real loot-loss vs the
                # "single destruction code path" claim in fleet-coordination.md.
                #
                # Reuse CombatService._spawn_cargo_wreck — the single wreck-spawn
                # kernel (canon: DATA_MODELS/cargo-wrecks.md). It is called AFTER
                # destroy_ship, exactly like the solo path (_handle_ship_destruction),
                # so it reads the dead hull's LEFTOVER cargo["contents"] — i.e. the
                # unrescued remainder after the 10% pod transfer — and drops the
                # FULL remaining cargo as one wreck (no partial-recovery roll; the
                # recovery-band/damage_type decision is PARKED behind Max — see the
                # deep-dive escalation, combat_service.py:3949). CombatService(db)
                # construction is cheap (stores db + a ShipService) and it never
                # commits — the wreck is staged via begin_nested + flush; the
                # outer transaction (committed by remove_ship_from_fleet, below)
                # persists it. The killing fleet's commander is attributed as the
                # killing-blow pilot (ADR-0055 S-F2; honored only for COMBAT).
                # Best-effort already: _spawn_cargo_wreck guards its own body, so
                # a wreck hiccup can never abort the kill or its rewards.
                from src.services.combat_service import CombatService
                CombatService(self.db)._spawn_cargo_wreck(
                    destroyed_ship=ship,
                    cause="combat",
                    original_owner=original_owner,
                    killing_blow_pilot=destroyer,
                )

                # WO-C2 (fleet-kill-attribution option (b)): on a FLEET kill,
                # split the per-kill REPUTATION + BOUNTY across the killing
                # fleet's participating members — mirroring the SOLO destroy-ship
                # hooks in CombatService, but with EACH member's bounty share
                # resolved through their OWN per-(hunter,target) claim ledger.
                # The fleet total is ENTITLEMENT-BOUNDED (a member who already
                # claimed this target contributes ZERO), so it may be LESS than a
                # solo single-kill — that is canon-correct, and it makes
                # collector-rotation alt-farming impossible (each alt is capped by
                # its own ledger). Runs inside the SAME `not indestructible` guard
                # as the
                # insurance payout so it fires exactly ONCE per genuinely
                # destroyed (non-escape-pod) ship — escape-pod "kills" are
                # skipped here just as the indestructible guard skips them for
                # destroy_ship. The killing fleet's roster is untouched by this
                # casualty (only the DEAD ship's member, on the opposing fleet,
                # is removed below), so reading killer_fleet's members now is
                # safe and complete.
                self._distribute_fleet_kill_rewards(ship, killer_fleet)

        # Remove ship from fleet if destroyed
        if destroyed:
            self.remove_ship_from_fleet(member.fleet_id, ship.id)

    def _distribute_fleet_kill_rewards(
        self,
        killed_ship: Ship,
        killer_fleet: Optional[Fleet],
    ) -> None:
        """Split the per-kill REPUTATION + BOUNTY for a FLEET kill across the
        killing fleet's participating members (WO-C2, fleet-kill-attribution
        option (b)).

        Mirrors the SOLO destroy-ship hooks in
        ``CombatService._resolve_combat`` (defeat_bounty_target / attack_innocent
        / defend bounty pot) but DIVIDES the award across the killing fleet's
        distinct participating players. Best-effort: a reward hiccup never breaks
        battle resolution (matches the solo try/except discipline).

        BOUNTY — per-member ledger resolution (closes the alt-farm faucet).
        Canon (DECISIONS.md fleet-kill-attribution): "each contributing member's
        share is bounded by their own unclaimed entitlement, reconciling with the
        once-per-(hunter,target) bounty_claims dedup from system-bounty-anti-
        faucet." We do NOT pay the whole pot to one collector and shuffle credits
        — that REOPENS the faucet: rotating the collector role across colluding
        alts would re-mint the full system bounty, because the non-collectors
        never burn their own per-(hunter,target) claim. Instead each DISTINCT
        participating player resolves their OWN even share through their OWN
        ledger via ``BountyService.collect_bounty_share``: a member with an
        existing PAID system claim against this target gets ZERO, and every
        member who IS paid writes their own PAID claim row + is credited under a
        lock on their own Player row. Consequence (canon-correct, NOT a bug): the
        fleet total may be LESS than a solo single-kill — a member who already
        claimed this target contributes nothing. We do NOT force total == solo.

        SPLIT METHOD — EVEN SPLIT (canon fallback, FLAGGED). The proportional-to-
        damage default would require per-participant damage dealt to THIS killed
        ship, which the fleet battle model does NOT track: combat damage is
        attributed only per-FLEET (FleetBattle.attacker_damage_dealt /
        defender_damage_dealt) and per-CASUALTY (damage that casualty *dealt*),
        never per-(attacker-ship → specific-target). Each hit in
        ``simulate_battle_round`` picks a random target and discards the firing
        ship's identity. Until a per-attacker→per-target damage ledger exists, we
        EVEN-SPLIT among the distinct participating PLAYERS of the killing fleet.
        The split method is a tunable knob; swapping to proportional only requires
        that ledger plus a weight map here.

        Why split per distinct PLAYER (not per member ship): a player flying 3
        ships in one fleet must not collect 3× — that is exactly the alt-padding
        faucet the per-member ledger forbids.
        """
        try:
            killed_player_id = killed_ship.owner_id
            if killed_player_id is None or killer_fleet is None:
                return

            # Distinct participating players of the killing fleet (dedup so a
            # multi-ship pilot gets ONE share, never one-per-hull). Exclude the
            # killed player defensively (a fleet should never contain its own
            # victim, but never award the corpse a share of its own bounty).
            participant_ids: List[UUID] = []
            seen: set = set()
            for m in killer_fleet.members:
                pid = m.player_id
                if pid is None or pid == killed_player_id or pid in seen:
                    continue
                seen.add(pid)
                participant_ids.append(pid)

            if not participant_ids:
                return

            n = len(participant_ids)

            from src.services.personal_reputation_service import PersonalReputationService
            from src.services.bounty_service import BountyService

            rep_service = PersonalReputationService(self.db)
            bounty_service = BountyService(self.db)

            # --- BOUNTY: per-member ledger resolution -------------------------
            # Lock the killed (target) player's row ONCE for the whole loop so
            # the JSONB read/clear and reputation reads are serialized against
            # concurrent kills (collect_bounty_share locks each HUNTER row, not
            # the target — the target lock belongs here, mirroring solo's
            # collect_bounty which locks both).
            killed_player = self.db.query(Player).filter(
                Player.id == killed_player_id
            ).with_for_update().first()

            had_bounty = False
            # Designate the LAST participant to claim the pay-once-then-cleared
            # player-placed pot AND to ZERO the stored system pot (WO-BN). The
            # designated member must be LAST so every other member reads the full
            # pre-zero system pot for its even-split share before it is emptied —
            # collect_bounty_share documents this ordering contract.
            for idx, pid in enumerate(participant_ids):
                share_result = bounty_service.collect_bounty_share(
                    hunter_id=pid,
                    target_id=killed_player_id,
                    num_participants=n,
                    claim_player_pot=(idx == n - 1),
                )
                if share_result.get("had_bounty"):
                    had_bounty = True
                if (share_result.get("paid", 0) or 0) > 0:
                    # This member is a heroic bounty hunter for THIS kill — award
                    # the full +100 defeat_bounty_target to them individually,
                    # exactly as the solo path awards +100 to the hunter who
                    # collected. Members paid ZERO (already-claimed dedup) get
                    # no +100, mirroring solo leaving that case alone.
                    rep_service.adjust_reputation(
                        pid, 100, "defeat_bounty_target"
                    )

            if not had_bounty:
                # Target carried NO bounty at all — the fleet gunned down a
                # genuine innocent. Split the -100 attack_innocent penalty across
                # the participants (the whole fleet shares the infamy). The
                # per-player [-1000, 1000] clamp is acceptable; we do not claim
                # exact total conservation for reputation.
                #
                # Grey-flag exemption (WO-BL), mirrored from the solo PvP path
                # (combat_service attack_player): if the killed target is GREY,
                # each participant who individually qualifies for that grey kind's
                # exemption (station_attack → anyone; player_attack → only
                # good-standing) is bringing a flagged aggressor to justice and is
                # EXCLUDED from the penalty split. Only the non-exempt participants
                # share the -100. The penalty-free check is per-attacker, so we
                # partition rather than all-or-nothing.
                from src.services.grey_flag_service import (
                    GreyFlagService,
                    GREY_KIND_PLAYER_ATTACK,
                    attack_is_penalty_free,
                    is_good_standing,
                )
                penalized_ids: List[UUID] = []
                exempt_count = 0
                for pid in participant_ids:
                    member = (
                        self.db.query(Player).filter(Player.id == pid).first()
                        if killed_player is not None else None
                    )
                    if (member is not None and killed_player is not None
                            and attack_is_penalty_free(member, killed_player)):
                        exempt_count += 1
                        continue
                    penalized_ids.append(pid)
                if exempt_count:
                    logger.info(
                        "Grey-flag exemption (fleet): %d of %d participants killed "
                        "grey target %s penalty-free (kind=%s) — excluded from the "
                        "attack_innocent split (WO-BL)",
                        exempt_count, len(participant_ids), killed_player_id,
                        getattr(killed_player, "grey_kind", None),
                    )
                if penalized_ids:
                    self._split_reputation(
                        rep_service, penalized_ids, -100, "attack_innocent"
                    )
                    # Grey-flag SET (WO-BL), symmetric with the solo PvP path:
                    # aggressing on a GOOD-STANDING player marks each NON-exempt
                    # participant grey for 1h ("player_attack"). Only fires when the
                    # killed target was good-standing (gunning down an already-grey/
                    # outlaw player is not a fresh open-season offense), and only on
                    # the participants who actually ate the penalty (an exempt
                    # justice-bringer does not become grey for the kill). MAX rule
                    # inside set_grey. Best-effort: never break battle resolution.
                    if (killed_player is not None
                            and is_good_standing(killed_player)):
                        grey_service = GreyFlagService(self.db)
                        for pid in penalized_ids:
                            member = (
                                self.db.query(Player)
                                .filter(Player.id == pid)
                                .with_for_update()
                                .first()
                            )
                            if member is not None:
                                grey_service.set_grey(
                                    member, GREY_KIND_PLAYER_ATTACK
                                )
            # else: had_bounty True — at least one bounty existed. Members who
            # collected got +100 above; members blocked by their own dedup got
            # neither +100 nor -100, exactly as the solo path leaves the
            # already-claimed-criminal case alone (neither heroic nor innocent-
            # slaughter).

            # NOTE on escape pods + police engagement: escape-pod "kills" never
            # reach this method (the indestructible guard at the call site skips
            # them, mirroring solo's separate -500 kill_escape_pod penalty path,
            # which is a PENALTY not a reward to split — deferred, see report).
            # Police engagement routing (attack_innocent / wanted_status spawn)
            # is the CombatService PvP path's concern and is intentionally NOT
            # duplicated here for fleet kills (separate WO if desired).

        except Exception as e:  # never break battle resolution on a reward hiccup
            logger.error("Failed fleet-kill reward distribution: %s", e)

    @staticmethod
    def _split_reputation(
        rep_service: "PersonalReputationService",
        participant_ids: List[UUID],
        total_amount: int,
        reason: str,
    ) -> None:
        """Apply ``total_amount`` reputation split EVENLY across participants so
        the summed delta EXACTLY equals the solo single-kill award. Integer
        remainder (and its sign) is folded into the first participant's share so
        no reputation is created or lost in the division."""
        n = len(participant_ids)
        if n == 0 or total_amount == 0:
            return
        # int() truncates toward zero, keeping the remainder same-signed as the
        # total; the remainder is added back to the first share → exact sum.
        base = int(total_amount / n)
        remainder = total_amount - base * n
        for idx, pid in enumerate(participant_ids):
            share = base + (remainder if idx == 0 else 0)
            if share != 0:
                rep_service.adjust_reputation(pid, share, reason)

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

        # Morale-collapse battle-end check REMOVED (WO-BS2, reverts WO-AS). The
        # former ``if (attacker.morale or 100) < 20 or (defender.morale ...) < 20``
        # gated battle DURATION on Fleet.morale. Max ruled Fleet.morale fully
        # inert — it no longer participates in combat damage OR duration — so this
        # condition is gone. Termination is now guaranteed entirely by the
        # morale-independent end conditions below: (1) side annihilation handled
        # above (no active ships on a side), (2) > 70% casualties on either side,
        # and (3) the 30-round timeout. No morale read remains in the combat path.

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

    def _record_combat_loot(self, actor, team, loot, *, won, battle) -> None:
        """Ledger a combat-loot treasury move (WO-TT review HIGH). Staged in the
        SAME txn as the treasury mutation (the _end_battle caller commits). team
        None / loot<=0 → no-op."""
        if team is None or loot <= 0:
            return
        self.db.add(TreasuryTransaction(
            team_id=team.id,
            resource_type="credits",
            kind=TreasuryTransaction.KIND_COMBAT_LOOT,
            amount=loot,
            balance_after=(team.treasury_credits or 0),
            actor_player_id=getattr(actor, "id", None),
            reason=f"Combat loot {'won' if won else 'lost'} (battle {getattr(battle, 'id', None)})",
        ))

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
                # WO-TT review HIGH: ledger the combat-loot treasury moves (same txn).
                self._record_combat_loot(attacker, attacker.team if attacker else None, loot, won=True, battle=battle)
                self._record_combat_loot(attacker, defender.team, loot, won=False, battle=battle)

        elif battle.winner == "defender" and attacker and attacker.team:
            loot = (attacker.team.treasury_credits or 0) // 10
            battle.credits_looted = loot
            if defender and defender.team:
                defender.team.treasury_credits = (defender.team.treasury_credits or 0) + loot
            if loot > 0:
                attacker.team.treasury_credits = (attacker.team.treasury_credits or 0) - loot
                # WO-TT review HIGH: ledger the combat-loot treasury moves (same txn).
                self._record_combat_loot(defender, defender.team if defender else None, loot, won=True, battle=battle)
                self._record_combat_loot(defender, attacker.team, loot, won=False, battle=battle)

        # Update fleet statuses. The former post-battle -20 to Fleet.morale is
        # REMOVED (WO-BS2, reverts WO-AS). Max ruled Fleet.morale fully inert —
        # it participates in neither combat damage nor battle duration — so the
        # combat path writes Fleet.morale NOWHERE. The column is kept
        # (non-destructive, no migration) but is cosmetic only (admin display).
        if attacker:
            attacker.status = FleetStatus.READY.value
            attacker.last_battle = datetime.utcnow()

        if defender:
            defender.status = FleetStatus.READY.value
            defender.last_battle = datetime.utcnow()

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
