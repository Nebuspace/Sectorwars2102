import json
import logging
import uuid
import random
import math
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import and_, or_

from src.models.player import Player
from src.models.ship import Ship, ShipStatus, ShipType, ShipSpecification
from src.models.sector import Sector
from src.models.combat import CombatType, CombatResult
from src.models.combat_log import CombatLog, CombatOutcome
from src.models.drone import Drone, DroneDeployment
from src.models.planet import Planet
from src.models.region import RegionType
from src.models.station import Station
from src.services.ship_service import ShipService
from src.services.ranking_service import RankingService
from src.services.turn_service import spend_turns

logger = logging.getLogger(__name__)


# Map the engine's CombatResult enum onto the outcome strings the combat_logs
# table actually stores (see CombatOutcome / migration c138b33baec4). The
# outcome column only has 5 values, so fled results collapse to "escaped" and
# mutual destruction collapses to "draw" (closest real value — no dedicated
# outcome exists in the schema).
COMBAT_RESULT_TO_OUTCOME: Dict[CombatResult, str] = {
    CombatResult.ATTACKER_VICTORY: CombatOutcome.ATTACKER_WIN.value,
    CombatResult.DEFENDER_VICTORY: CombatOutcome.DEFENDER_WIN.value,
    CombatResult.DRAW: CombatOutcome.DRAW.value,
    CombatResult.ATTACKER_FLED: CombatOutcome.ESCAPED.value,
    CombatResult.DEFENDER_FLED: CombatOutcome.ESCAPED.value,
    CombatResult.MUTUAL_DESTRUCTION: CombatOutcome.DRAW.value,
    CombatResult.ABANDONED: CombatOutcome.DRAW.value,
}


class CombatService:
    """Service for managing combat in the game."""

    # Weapon effectiveness multipliers based on ship type matchups
    SHIP_COMBAT_MODIFIERS = {
        # (attacker_type, defender_type): damage_multiplier
        (ShipType.DEFENDER, ShipType.CARGO_HAULER): 1.5,      # Military vs trade
        (ShipType.DEFENDER, ShipType.LIGHT_FREIGHTER): 1.3,   # Military vs light trade
        (ShipType.FAST_COURIER, ShipType.CARRIER): 0.7,       # Light vs heavy
        (ShipType.SCOUT_SHIP, ShipType.CARRIER): 0.5,         # Scout vs capital
        (ShipType.CARRIER, ShipType.SCOUT_SHIP): 1.8,         # Capital vs scout
        (ShipType.CARRIER, ShipType.FAST_COURIER): 1.5,       # Capital vs light
        (ShipType.COLONY_SHIP, ShipType.DEFENDER): 0.5,       # Colony ship weak in combat
    }

    # Weapon type effectiveness against different defenses
    WEAPON_TYPES = {
        "laser": {"base_damage": 1.0, "shield_effectiveness": 0.8, "hull_effectiveness": 1.0, "description": "Standard energy weapon"},
        "plasma": {"base_damage": 1.2, "shield_effectiveness": 1.2, "hull_effectiveness": 0.9, "description": "High-energy plasma bolts"},
        "missile": {"base_damage": 1.5, "shield_effectiveness": 0.6, "hull_effectiveness": 1.5, "description": "Physical projectile, bypasses some shields"},
        "emp": {"base_damage": 0.5, "shield_effectiveness": 2.0, "hull_effectiveness": 0.3, "description": "Electromagnetic pulse, devastating to shields"},
    }

    # Default weapon type by ship type
    SHIP_DEFAULT_WEAPONS = {
        ShipType.ESCAPE_POD: "laser",
        ShipType.LIGHT_FREIGHTER: "laser",
        ShipType.CARGO_HAULER: "laser",
        ShipType.FAST_COURIER: "laser",
        ShipType.SCOUT_SHIP: "emp",
        ShipType.COLONY_SHIP: "laser",
        ShipType.DEFENDER: "plasma",
        ShipType.CARRIER: "missile",
        ShipType.WARP_JUMPER: "plasma",
        # Canon (police-forces.md Interdictor stat tables): Marshal
        # Interdictor "Default weapon: Laser" (~line 140); Sentinel
        # Interdictor "Default weapon: Plasma" (~line 157).
        ShipType.NPC_MARSHAL_INTERDICTOR: "laser",
        ShipType.NPC_SENTINEL_INTERDICTOR: "plasma",
    }

    # Ship types that get a bonus to escape chance due to speed/agility
    FAST_ESCAPE_SHIP_TYPES = {ShipType.FAST_COURIER, ShipType.SCOUT_SHIP}

    def __init__(self, db: Session):
        self.db = db
        self.ship_service = ShipService(db)
    
    def attack_player(self, attacker_id: uuid.UUID, defender_id: uuid.UUID) -> Dict[str, Any]:
        """Initiate ship-to-ship combat between two players."""
        # Self-attack guard (combat-resolver.md Invariant 5:
        # attacker.id != defender.id). Reject before any lock/charge.
        if attacker_id == defender_id:
            return {"success": False, "message": "You cannot attack yourself"}

        # Get players with row locks to prevent concurrent combat race conditions
        attacker = self.db.query(Player).filter(Player.id == attacker_id).with_for_update().first()
        defender = self.db.query(Player).filter(Player.id == defender_id).with_for_update().first()

        if not attacker or not defender:
            return {"success": False, "message": "Player not found"}

        # Check if attacker has an active ship
        if not attacker.current_ship:
            return {"success": False, "message": "Attacker has no active ship"}

        # Check if defender has an active ship
        if not defender.current_ship:
            return {"success": False, "message": "Defender has no active ship"}

        # Lock both current_ship rows as well as the player rows, mirroring the
        # NPC path's ship lock (attack_npc_ship locks the target Ship FOR
        # UPDATE). Without this the combat JSONB (hull/shields) read+mutated
        # below is racy against a concurrent fight on the same ship. Single
        # ordered query (by id) to keep a deterministic lock order and avoid
        # deadlock between two symmetric attacks.
        ship_ids = sorted(
            {attacker.current_ship_id, defender.current_ship_id},
            key=lambda sid: str(sid),
        )
        self.db.query(Ship).filter(
            Ship.id.in_(ship_ids)
        ).order_by(Ship.id).populate_existing().with_for_update().all()

        # Escape pods are indestructible — the resolver rejects them as valid
        # targets at the validation step before any turn charge or damage roll
        # (combat-resolver.md S-V1: "target.type == 'escape_pod' raises
        # ERR_INVALID_TARGET with escape_pods_are_indestructible"). Closes the
        # kick-them-while-they're-down vector.
        if defender.current_ship.type == ShipType.ESCAPE_POD:
            return {"success": False, "message": "escape_pods_are_indestructible"}

        # A Warp Jumper harmonizing into a gate focus is invulnerable for the
        # harmonization window (ADR-0029 / warp-gates.md Phase 3: "Hostile
        # attack during harmonization is a no-op"). Reject before any turn
        # charge — no turns are consumed on a rejected attack.
        if defender.current_ship.status == ShipStatus.HARMONIZING:
            return {
                "success": False,
                "message": "That ship is harmonizing into a warp gate focus "
                           "and is invulnerable",
            }

        # Check if players are in the same sector
        if attacker.current_sector_id != defender.current_sector_id:
            return {"success": False, "message": "Target is not in your sector"}
        
        # Look up attack turn cost from DEFENDER's ship specification
        # Cost reflects difficulty of attacking that ship type (e.g., escape pod = 10,000 turns)
        defender_spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == defender.current_ship.type
        ).first()
        turn_cost = getattr(defender_spec, 'attack_turn_cost', None) or 2
        if attacker.turns < turn_cost:
            return {"success": False, "message": f"Not enough turns to initiate combat (need {turn_cost})"}
        
        # Attacker cannot initiate combat while docked or landed (mirrors
        # the other combat entry points)
        if attacker.is_docked or attacker.is_landed:
            return {"success": False, "message": "Cannot attack while docked at a port or landed on a planet"}

        # Get current sector for location and rules
        sector = self.db.query(Sector).filter(Sector.sector_id == attacker.current_sector_id).first()
        
        # Check if combat is allowed in this sector (could have special rules)
        if not self._is_combat_allowed(sector, attacker, defender):
            return {"success": False, "message": "Combat is not allowed in this sector"}
        
        # Resolve combat
        combat_result = self._resolve_ship_combat(attacker, defender, sector)
        
        # Consume turns
        spend_turns(attacker, turn_cost)
        
        # Create combat log (snapshots taken before destruction/drone updates)
        attacker_ship = attacker.current_ship
        defender_ship = defender.current_ship
        combat_log = CombatLog(
            combat_type=CombatType.SHIP_VS_SHIP.value,
            outcome=COMBAT_RESULT_TO_OUTCOME[combat_result["result"]],
            sector_id=sector.sector_id,
            sector_uuid=sector.id,
            attacker_id=attacker.id,
            attacker_ship_id=attacker.current_ship_id,
            attacker_ship_name=attacker_ship.name if attacker_ship else None,
            attacker_ship_type=attacker_ship.type.value if attacker_ship else None,
            defender_id=defender.id,
            defender_ship_id=defender.current_ship_id,
            defender_ship_name=defender_ship.name if defender_ship else None,
            defender_ship_type=defender_ship.type.value if defender_ship else None,
            rounds=combat_result["rounds"],
            attacker_drones=attacker.defense_drones,
            defender_drones=defender.defense_drones,
            attacker_drones_lost=combat_result["attacker_drones_lost"],
            defender_drones_lost=combat_result["defender_drones_lost"],
            attacker_damage_dealt=combat_result["attacker_damage_dealt"],
            defender_damage_dealt=combat_result["defender_damage_dealt"],
            cargo_looted=combat_result["cargo_stolen"] or None,
            combat_log=json.dumps(combat_result["combat_details"]),
            ended_at=datetime.now()
        )

        self.db.add(combat_log)

        # Handle cargo theft BEFORE destruction — destroy_ship swaps the
        # loser into an escape pod, so transferring afterwards would loot the
        # pod's emergency cargo instead of the defeated ship's hold
        if combat_result["cargo_stolen"]:
            self._transfer_cargo(defender_ship, attacker_ship, combat_result["cargo_stolen"])

        # Snapshot the defender's PRE-destruction ship type. _handle_ship_destruction
        # swaps the destroyed defender into an escape pod, so reading
        # defender.current_ship.type AFTER the swap would always see ESCAPE_POD
        # and misfire the kill_escape_pod reputation penalty on every kill. The
        # penalty must be evaluated against the ship that was actually destroyed.
        defender_pre_destruction_type = (
            defender.current_ship.type if defender.current_ship else None
        )

        # Apply combat effects
        if combat_result["defender_ship_destroyed"]:
            self._handle_ship_destruction(defender, attacker, "combat")

        if combat_result["attacker_ship_destroyed"]:
            self._handle_ship_destruction(attacker, defender, "combat")

        # Update drone counts
        if combat_result["attacker_drones_lost"] > 0:
            attacker.defense_drones = max(0, attacker.defense_drones - combat_result["attacker_drones_lost"])

        if combat_result["defender_drones_lost"] > 0:
            defender.defense_drones = max(0, defender.defense_drones - combat_result["defender_drones_lost"])
        
        # Update last_combat timestamp for sector
        sector.last_combat = datetime.now()

        # Award rank points for combat victory
        rank_result = None
        try:
            ranking_service = RankingService(self.db)
            if combat_result["result"] == CombatResult.ATTACKER_VICTORY:
                points = RankingService.calculate_combat_points(
                    attacker.military_rank, defender.military_rank
                )
                rank_result = ranking_service.award_rank_points(
                    attacker.id, points, "combat_victory"
                )
            elif combat_result["result"] == CombatResult.DEFENDER_VICTORY:
                points = RankingService.calculate_combat_points(
                    defender.military_rank, attacker.military_rank
                )
                rank_result = ranking_service.award_rank_points(
                    defender.id, points, "combat_victory"
                )
        except Exception as e:
            logger.error("Failed to award rank points after combat: %s", e)

        # ARIA consciousness + medal hooks for the victor
        try:
            winner = attacker if combat_result["result"] == CombatResult.ATTACKER_VICTORY else (
                defender if combat_result["result"] == CombatResult.DEFENDER_VICTORY else None
            )
            if winner:
                winner.aria_total_interactions += 1
                # Check consciousness thresholds (50→L2, 150→L3, 400→L4, 1000→L5)
                thresholds = {50: (2, 1.1), 150: (3, 1.2), 400: (4, 1.35), 1000: (5, 1.5)}
                for threshold, (level, multiplier) in thresholds.items():
                    if winner.aria_total_interactions >= threshold and winner.aria_consciousness_level < level:
                        winner.aria_consciousness_level = level
                        winner.aria_bonus_multiplier = multiplier
                # Check combat medals. NPC kills (defender_id NULL) are
                # excluded so they can't be farmed for medals — the canon
                # NPC-kill reward hooks (npc-scheduler.md KIA step 8) are
                # deliberately deferred, not invented here.
                from src.services.medal_service import MedalService
                victory_count = self.db.query(CombatLog).filter(
                    CombatLog.defender_id.isnot(None),
                    ((CombatLog.attacker_id == winner.id) & (CombatLog.outcome == CombatOutcome.ATTACKER_WIN.value)) |
                    ((CombatLog.defender_id == winner.id) & (CombatLog.outcome == CombatOutcome.DEFENDER_WIN.value))
                ).count()
                medal_service = MedalService(self.db)
                medal_service.check_combat_medals(winner.id, victory_count)
        except Exception as e:
            logger.error("Failed ARIA/medal hooks after combat: %s", e)

        # Personal reputation + bounty hooks
        attacked_innocent = False
        try:
            from src.services.personal_reputation_service import PersonalReputationService
            from src.services.bounty_service import BountyService
            rep_service = PersonalReputationService(self.db)

            if combat_result["result"] == CombatResult.ATTACKER_VICTORY:
                # Attacker won — check if defender had bounties
                bounty_service = BountyService(self.db)
                bounty_result = bounty_service.collect_bounty(attacker.id, defender.id)
                if bounty_result.get("total_collected", 0) > 0:
                    rep_service.adjust_reputation(attacker.id, 100, "defeat_bounty_target")
                else:
                    # Attacked an innocent (no bounty) — reputation penalty
                    rep_service.adjust_reputation(attacker.id, -100, "attack_innocent")
                    attacked_innocent = True
                # Check if the DESTROYED defender ship was an escape pod —
                # evaluated against the pre-destruction snapshot, not
                # defender.current_ship (which is now the post-kill pod).
                if defender_pre_destruction_type == ShipType.ESCAPE_POD:
                    rep_service.adjust_reputation(attacker.id, -500, "kill_escape_pod")
            elif combat_result["result"] == CombatResult.DEFENDER_VICTORY:
                # Defender successfully defended — reputation boost
                rep_service.adjust_reputation(defender.id, 50, "defend_against_attacker")
        except Exception as e:
            logger.error("Failed reputation/bounty hooks after combat: %s", e)

        # Police engagement routing (ADR-0042 / police-forces.md). Two
        # in-jurisdiction triggers fire from PvP combat today: an active
        # Wanted Status (rep ≤ −500) on the aggressor, and the
        # attack_innocent rep trigger above (code path: attacker victory
        # with no bounty on the defender). route_engagement is
        # best-effort and never raises into combat resolution.
        police_response = None
        try:
            from src.services import npc_engagement_service
            if (attacker.personal_reputation or 0) <= -500:
                engagement = npc_engagement_service.route_engagement(
                    self.db, attacker, "wanted_status", sector
                )
                police_response = npc_engagement_service.engagement_summary(
                    engagement, self.db
                ) or police_response
            if attacked_innocent:
                engagement = npc_engagement_service.route_engagement(
                    self.db, attacker, "attack_innocent", sector
                )
                police_response = npc_engagement_service.engagement_summary(
                    engagement, self.db
                ) or police_response
        except Exception as e:
            logger.error("Failed police engagement routing after combat: %s", e)

        # Commit changes
        self.db.commit()

        result_dict = {
            "success": True,
            "message": combat_result["message"],
            "combat_result": combat_result["result"].name,
            "combat_details": combat_result["combat_details"],
            "turns_consumed": turn_cost,
            "turns_remaining": attacker.turns,
            "combat_log_id": str(combat_log.id)
        }
        if police_response:
            result_dict["police_response"] = police_response
        if rank_result and rank_result.get("success"):
            result_dict["rank_points_awarded"] = rank_result["points_awarded"]
            result_dict["promoted"] = rank_result["promoted"]
            if rank_result["promoted"]:
                result_dict["new_rank"] = rank_result["new_rank"]

        return result_dict

    def attack_npc_ship(self, attacker_id: uuid.UUID, ship_id: uuid.UUID) -> Dict[str, Any]:
        """Initiate combat between a player and an NPC-controlled ship.

        The defender is a Ship row with no owning Player (owner_id is NULL /
        is_npc flag set), so there are no defender turn costs and no defender
        reputation/rank hooks. Attacker-side rank/bounty hooks are skipped
        for NPC kills. The one canon-numeric reputation hook IS applied:
        killing a Federation Marshal Interdictor crashes the attacker's
        Terran Federation reputation by -250 per kill (police-forces.md).
        Sentinel kills canonically crash Galactic Concord standing, but
        canon gives no numeric value and the CONCORD FactionType is
        Design-only — deferred with a logged TODO, not invented.
        """
        attacker = self.db.query(Player).filter(Player.id == attacker_id).with_for_update().first()
        if not attacker:
            return {"success": False, "message": "Player not found"}

        npc_ship = self.db.query(Ship).filter(Ship.id == ship_id).with_for_update().first()
        if not npc_ship or npc_ship.is_destroyed:
            return {"success": False, "message": "Target ship not found"}

        if not attacker.current_ship:
            return {"success": False, "message": "Attacker has no active ship"}

        if attacker.current_sector_id != npc_ship.sector_id:
            return {"success": False, "message": "Target is not in your sector"}

        # Attack turn cost comes from the defender ship's specification,
        # exactly as in player-vs-player combat
        defender_spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == npc_ship.type
        ).first()
        turn_cost = getattr(defender_spec, 'attack_turn_cost', None) or 2
        if attacker.turns < turn_cost:
            return {"success": False, "message": f"Not enough turns to initiate combat (need {turn_cost})"}

        if attacker.is_docked or attacker.is_landed:
            return {"success": False, "message": "Cannot attack while docked at a port or landed on a planet"}

        sector = self.db.query(Sector).filter(Sector.sector_id == attacker.current_sector_id).first()

        if not self._is_combat_allowed(sector, attacker, None):
            return {"success": False, "message": "Combat is not allowed in this sector"}

        # Resolve combat against the NPC ship via the standard resolution path
        combat_result = self._resolve_ship_combat(attacker, None, sector, defender_ship=npc_ship)

        # Consume turns
        spend_turns(attacker, turn_cost)

        # NPC kills yield the FULL haul. Win the fight and you take ALL the
        # credits and goods the NPC was carrying — a loaded merchant captain is
        # a genuine prize, unlike the random partial salvage of player-vs-player
        # combat. Done BEFORE the combat log so the log records the true haul.
        # Cargo is still capped to the attacker's free hold by _transfer_cargo
        # (a hold has finite volume); credits are not (a wallet has none). The
        # trader spawn seed is deliberately small, so this loots genuinely-earned
        # profit rather than minting a large seed into the economy.
        looted_credits = 0
        if combat_result["result"] == CombatResult.ATTACKER_VICTORY:
            full_contents = (npc_ship.cargo or {}).get("contents") or {}
            combat_result["cargo_stolen"] = {
                resource: int(qty)
                for resource, qty in full_contents.items()
                if isinstance(qty, (int, float)) and qty > 0
            }
            from src.models.npc_character import NPCCharacter as _NPCCharacterLoot
            looted_npc = (
                self.db.query(_NPCCharacterLoot)
                .filter(_NPCCharacterLoot.ship_id == npc_ship.id)
                .with_for_update()
                .first()
            )
            if looted_npc is not None and (looted_npc.credits or 0) > 0:
                looted_credits = int(looted_npc.credits)
                attacker.credits = (attacker.credits or 0) + looted_credits
                looted_npc.credits = 0

            # Notoriety consequence: gunning down a REPUTABLE merchant is a
            # crime — the canon attack_innocent penalty (−100, mirroring PvP).
            # An UNSCRUPULOUS / NOTORIOUS trader (notoriety ≥ threshold) is a
            # lawful target — no penalty. Raiders are always fair game;
            # marshals carry their own faction penalty further below.
            if looted_npc is not None:
                from src.models.npc_character import NPCArchetype as _Arch
                from src.services.npc_spawn_service import LAWFUL_TARGET_THRESHOLD
                if (looted_npc.archetype == _Arch.TRADER
                        and (looted_npc.notoriety or 0) < LAWFUL_TARGET_THRESHOLD):
                    try:
                        from src.services.personal_reputation_service import (
                            PersonalReputationService,
                        )
                        PersonalReputationService(self.db).adjust_reputation(
                            attacker.id, -100, "attack_innocent"
                        )
                    except Exception as e:
                        logger.error("Failed innocent-trader reputation hook: %s", e)

        # Create combat log — defender_id stays NULL (no Player behind the
        # ship); name/type snapshots preserve who was fought
        attacker_ship = attacker.current_ship
        combat_log = CombatLog(
            combat_type=CombatType.SHIP_VS_SHIP.value,
            outcome=COMBAT_RESULT_TO_OUTCOME[combat_result["result"]],
            sector_id=sector.sector_id,
            sector_uuid=sector.id,
            attacker_id=attacker.id,
            attacker_ship_id=attacker.current_ship_id,
            attacker_ship_name=attacker_ship.name if attacker_ship else None,
            attacker_ship_type=attacker_ship.type.value if attacker_ship else None,
            defender_id=None,
            defender_ship_id=npc_ship.id,
            defender_ship_name=npc_ship.name,
            defender_ship_type=npc_ship.type.value,
            rounds=combat_result["rounds"],
            attacker_drones=attacker.defense_drones,
            defender_drones=0,
            attacker_drones_lost=combat_result["attacker_drones_lost"],
            defender_drones_lost=combat_result["defender_drones_lost"],
            attacker_damage_dealt=combat_result["attacker_damage_dealt"],
            defender_damage_dealt=combat_result["defender_damage_dealt"],
            cargo_looted=combat_result["cargo_stolen"] or None,
            combat_log=json.dumps(combat_result["combat_details"]),
            ended_at=datetime.now()
        )

        self.db.add(combat_log)

        # Handle cargo salvage before the wreck is finalized
        if combat_result["cargo_stolen"]:
            self._transfer_cargo(npc_ship, attacker.current_ship, combat_result["cargo_stolen"])

        # Apply combat effects
        if combat_result["defender_ship_destroyed"]:
            npc_ship.is_destroyed = True
            npc_ship.is_active = False
            npc_ship.status = ShipStatus.DESTROYED
            # Notify the NPC lifecycle system (delivered by the NPC slice).
            # Lazy import + ImportError guard so combat still resolves if the
            # module is absent in this deployment.
            dead_npc = None
            try:
                from src.services.npc_spawn_service import handle_npc_ship_destroyed
                dead_npc = handle_npc_ship_destroyed(
                    self.db,
                    npc_ship.id,
                    killed_by_player_id=attacker.id,
                    combat_log_id=combat_log.id,
                )
            except ImportError:
                logger.warning(
                    "npc_spawn_service not available — NPC ship %s destroyed without KIA processing",
                    npc_ship.id
                )

            # Police-kill reputation hook (police-forces.md). Sync helper —
            # flush only, folded into this method's single commit below; the
            # async FactionService.update_reputation would commit
            # mid-transaction and fire WebSocket sends from a sync path.
            if dead_npc is not None:
                from src.models.npc_character import NPCArchetype
                if dead_npc.archetype == NPCArchetype.LAW_ENFORCEMENT:
                    if dead_npc.faction_code == "terran_federation":
                        from src.models.faction import FactionType
                        from src.services.faction_service import apply_faction_rep_delta
                        # Canon: killing a Marshal Interdictor crashes
                        # Federation reputation by -250 per kill. The
                        # second clause of police-forces.md:75 — the kill
                        # also "immediately escalates the response squad"
                        # (the wanted-status/standing consequence on top
                        # of the rep delta) — is a named canon deferral to
                        # the NPC engagement-routing/scheduler slice; only
                        # the rep delta is applied here.
                        apply_faction_rep_delta(
                            self.db,
                            attacker.id,
                            FactionType.FEDERATION,
                            -250,
                            reason=f"Marshal kill ({dead_npc.display_name})",
                        )
                    elif dead_npc.faction_code == "galactic_concord":
                        # TODO(canon gap): Sentinel kills crash Galactic
                        # Concord standing (police-forces.md), but canon
                        # gives NO numeric value and the CONCORD
                        # FactionType is Design-only — deferred, not
                        # invented.
                        logger.info(
                            "Sentinel kill by player %s (%s) — Galactic Concord "
                            "standing loss deferred (canon numeric gap)",
                            attacker.id, dead_npc.display_name,
                        )

        if combat_result["attacker_ship_destroyed"]:
            self._handle_ship_destruction(attacker, None, "combat")

        # Update attacker drone count
        if combat_result["attacker_drones_lost"] > 0:
            attacker.defense_drones = max(0, attacker.defense_drones - combat_result["attacker_drones_lost"])

        # Update last_combat timestamp for sector
        sector.last_combat = datetime.now()

        # Police engagement routing (ADR-0042 / police-forces.md): a
        # direct attack on a law-enforcement officer brings the
        # Marshal-Captain personally; killing a Sentinel escalates the
        # response squad from 4 to 6. Best-effort — never breaks combat.
        police_response = None
        try:
            from src.models.npc_character import NPCArchetype as _NPCArchetype
            from src.models.npc_character import NPCCharacter as _NPCCharacter
            from src.services import npc_engagement_service

            target_npc = (
                self.db.query(_NPCCharacter)
                .filter(_NPCCharacter.ship_id == npc_ship.id)
                .first()
            )
            if (
                target_npc is not None
                and target_npc.archetype == _NPCArchetype.LAW_ENFORCEMENT
            ):
                sentinel_killed = (
                    combat_result["defender_ship_destroyed"]
                    and target_npc.faction_code == "galactic_concord"
                )
                engagement = npc_engagement_service.route_engagement(
                    self.db,
                    attacker,
                    "sentinel_killed" if sentinel_killed else "attack_police",
                    sector,
                    squad_size_override=6 if sentinel_killed else None,
                    include_captain=True,
                )
                police_response = npc_engagement_service.engagement_summary(
                    engagement, self.db
                )
        except Exception as e:
            logger.error("Failed police engagement routing after NPC combat: %s", e)

        # Commit changes
        self.db.commit()

        result_dict = {
            "success": True,
            "message": combat_result["message"],
            "combat_result": combat_result["result"].name,
            "combat_details": combat_result["combat_details"],
            "turns_consumed": turn_cost,
            "turns_remaining": attacker.turns,
            "combat_log_id": str(combat_log.id),
            "credits_looted": looted_credits,
            "cargo_looted": combat_result["cargo_stolen"] or {},
        }
        if police_response:
            result_dict["police_response"] = police_response
        return result_dict

    def attack_sector_drones(self, attacker_id: uuid.UUID, sector_id: int) -> Dict[str, Any]:
        """Attack drones deployed in a sector."""
        # Get attacker
        attacker = self.db.query(Player).filter(Player.id == attacker_id).first()
        if not attacker:
            return {"success": False, "message": "Player not found"}
        
        # Check if attacker has an active ship
        if not attacker.current_ship:
            return {"success": False, "message": "No active ship selected"}
        
        # Check if player is in the target sector
        if attacker.current_sector_id != sector_id:
            return {"success": False, "message": "You must be in the sector to attack its drones"}
        
        # Get sector
        sector = self.db.query(Sector).filter(Sector.sector_id == sector_id).first()
        if not sector:
            return {"success": False, "message": "Sector not found"}
        
        # Check if there are drones to attack
        if sector.drones_present <= 0:
            return {"success": False, "message": "No drones present in this sector"}
        
        # Check if attacker has enough turns
        turn_cost = 2  # Base cost for drone combat
        if attacker.turns < turn_cost:
            return {"success": False, "message": "Not enough turns to attack sector drones"}
        
        # Check if player is docked or landed
        if attacker.is_docked or attacker.is_landed:
            return {"success": False, "message": "Cannot attack while docked at a port or landed on a planet"}
        
        # Get drone deployments in this sector
        deployments = self.db.query(DroneDeployment).filter(
            DroneDeployment.sector_id == sector_id,
            DroneDeployment.is_active == True
        ).all()
        
        if not deployments:
            return {"success": False, "message": "No active drone deployments found in this sector"}
        
        # Snapshot starting drone totals before combat mutates deployments
        starting_sector_drones = sum(d.drone_count for d in deployments)

        # Resolve combat against drones
        combat_result = self._resolve_drone_combat(attacker, sector, deployments)

        # Consume turns
        spend_turns(attacker, turn_cost)

        # Create combat log
        attacker_ship = attacker.current_ship
        combat_log = CombatLog(
            combat_type=CombatType.SHIP_VS_DRONES.value,
            outcome=COMBAT_RESULT_TO_OUTCOME[combat_result["result"]],
            sector_id=sector.sector_id,
            sector_uuid=sector.id,
            attacker_id=attacker.id,
            attacker_ship_id=attacker.current_ship_id,
            attacker_ship_name=attacker_ship.name if attacker_ship else None,
            attacker_ship_type=attacker_ship.type.value if attacker_ship else None,
            defender_id=None,  # No specific defender for sector drones
            rounds=combat_result["rounds"],
            attacker_drones=attacker.defense_drones,
            defender_drones=starting_sector_drones,
            attacker_drones_lost=combat_result["attacker_drones_lost"],
            defender_drones_lost=combat_result["defender_drones_lost"],
            combat_log=json.dumps(combat_result["combat_details"]),
            ended_at=datetime.now()
        )
        
        self.db.add(combat_log)
        
        # Apply combat effects
        if combat_result["attacker_ship_destroyed"]:
            self._handle_ship_destruction(attacker, None, "drone_combat")
        
        # Update drone counts
        if combat_result["attacker_drones_lost"] > 0:
            attacker.defense_drones = max(0, attacker.defense_drones - combat_result["attacker_drones_lost"])
        
        # Update deployments and sector drone count
        new_sector_drone_count = 0
        for deployment_update in combat_result["deployment_updates"]:
            deployment_id = deployment_update["deployment_id"]
            drones_lost = deployment_update["drones_lost"]
            
            deployment = next((d for d in deployments if str(d.id) == deployment_id), None)
            if deployment:
                deployment.drones_lost += drones_lost
                deployment.drone_count = max(0, deployment.drone_count - drones_lost)
                deployment.last_combat = datetime.now()
                
                # If all drones are lost, deactivate the deployment
                if deployment.drone_count <= 0:
                    deployment.is_active = False
                else:
                    new_sector_drone_count += deployment.drone_count
        
        # Update sector drone count
        sector.drones_present = new_sector_drone_count
        sector.last_combat = datetime.now()
        
        # Commit changes
        self.db.commit()
        
        return {
            "success": True,
            "message": combat_result["message"],
            "combat_result": combat_result["result"].name,
            "combat_details": combat_result["combat_details"],
            "drones_destroyed": combat_result["defender_drones_lost"],
            "drones_remaining": new_sector_drone_count,
            "turns_consumed": turn_cost,
            "turns_remaining": attacker.turns,
            "combat_log_id": str(combat_log.id)
        }
    
    def attack_planet(self, attacker_id: uuid.UUID, planet_id: uuid.UUID) -> Dict[str, Any]:
        """Attack a planet."""
        # Get attacker with a row lock to prevent concurrent turn deduction
        # races (mirrors attack_player / attack_npc_ship)
        attacker = self.db.query(Player).filter(Player.id == attacker_id).with_for_update().first()
        if not attacker:
            return {"success": False, "message": "Player not found"}
        
        # Check if attacker has an active ship
        if not attacker.current_ship:
            return {"success": False, "message": "No active ship selected"}
        
        # Get planet
        planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
        if not planet:
            return {"success": False, "message": "Planet not found"}

        # Formation-window protection (genesis-devices.md §Formation-window
        # protection): a planet still forming cannot be attacked. The landing
        # path already guards this; the attack path must too.
        if planet.formation_status == 'forming':
            return {"success": False, "message": "This planet is still forming and cannot be attacked"}

        # Check if player is in the planet's sector
        if attacker.current_sector_id != planet.sector_id:
            return {"success": False, "message": "You must be in the planet's sector to attack it"}
        
        # Check if planet has an owner
        if not planet.owner:
            return {"success": False, "message": "Cannot attack an unowned planet"}
        
        # Check if attacker is the owner
        planet_owner = planet.owner[0] if planet.owner else None
        if planet_owner and planet_owner.id == attacker.id:
            return {"success": False, "message": "Cannot attack your own planet"}
        
        # Check if attacker has enough turns
        turn_cost = 3  # Higher cost for attacking planets
        if attacker.turns < turn_cost:
            return {"success": False, "message": "Not enough turns to attack planet"}
        
        # Check if player is docked or landed
        if attacker.is_docked or attacker.is_landed:
            return {"success": False, "message": "Cannot attack while docked at a port or landed on a planet"}
        
        # Get sector for location context
        sector = self.db.query(Sector).filter(Sector.sector_id == attacker.current_sector_id).first()
        
        # Resolve combat against planet
        combat_result = self._resolve_planet_combat(attacker, planet, planet_owner)
        
        # Consume turns
        spend_turns(attacker, turn_cost)
        
        # Create combat log
        attacker_ship = attacker.current_ship
        combat_log = CombatLog(
            combat_type=CombatType.SHIP_VS_PLANET.value,
            outcome=COMBAT_RESULT_TO_OUTCOME[combat_result["result"]],
            sector_id=sector.sector_id,
            sector_uuid=sector.id,
            attacker_id=attacker.id,
            attacker_ship_id=attacker.current_ship_id,
            attacker_ship_name=attacker_ship.name if attacker_ship else None,
            attacker_ship_type=attacker_ship.type.value if attacker_ship else None,
            defender_id=planet_owner.id if planet_owner else None,
            planet_id=planet.id,
            rounds=combat_result["rounds"],
            attacker_drones=attacker.defense_drones,
            attacker_drones_lost=combat_result["attacker_drones_lost"],
            defender_drones_lost=combat_result["defender_drones_lost"],
            combat_log=json.dumps(combat_result["combat_details"]),
            ended_at=datetime.now()
        )
        
        self.db.add(combat_log)
        
        # Apply combat effects
        if combat_result["attacker_ship_destroyed"]:
            self._handle_ship_destruction(attacker, None, "planet_defense")
        
        # Update drone counts
        if combat_result["attacker_drones_lost"] > 0:
            attacker.defense_drones = max(0, attacker.defense_drones - combat_result["attacker_drones_lost"])
        
        # Update planet defenses
        planet.defense_level = max(0, planet.defense_level - combat_result["planet_damage"])
        
        # If planet was captured, transfer ownership
        if combat_result["planet_captured"]:
            self._transfer_planet_ownership(planet, attacker)
        
        # Update last_attacked timestamp for planet
        planet.last_attacked = datetime.now()

        # Update last_combat timestamp for sector
        sector.last_combat = datetime.now()

        # Chartered-planet protection: assaulting a publicly-chartered planet is
        # a reputation offense. Apply the -50 personal-reputation penalty once
        # per attack, here — attack_planet is the single definitive entry point
        # for a planet attack (resolution happens inside _resolve_planet_combat,
        # not a per-round loop), so this fires exactly once and only after the
        # attack has passed every guard and definitively proceeded (rejected
        # attacks return earlier and never reach this line). The penalty is
        # charged for the act of assaulting a chartered planet regardless of
        # outcome/capture. active_events is a JSONB column that defaults to [] —
        # only a dict with an explicit 'chartered' registration_status triggers
        # the penalty. Best-effort: never break combat resolution.
        events = planet.active_events
        if isinstance(events, dict) and events.get("registration_status") == "chartered":
            try:
                from src.services.personal_reputation_service import PersonalReputationService
                PersonalReputationService(self.db).adjust_reputation(
                    attacker.id, -50, "attacked_chartered_planet"
                )
            except Exception as e:
                logger.error("Failed chartered-planet reputation hook: %s", e)

        # Commit changes
        self.db.commit()

        return {
            "success": True,
            "message": combat_result["message"],
            "combat_result": combat_result["result"].name,
            "combat_details": combat_result["combat_details"],
            "planet_captured": combat_result["planet_captured"],
            "turns_consumed": turn_cost,
            "turns_remaining": attacker.turns,
            "combat_log_id": str(combat_log.id)
        }
    
    def attack_port(self, attacker_id: uuid.UUID, station_id: uuid.UUID) -> Dict[str, Any]:
        """Attack a space station.

        WARNING: not wired to any player route — port assault is disabled
        this pass (economically sensitive: it transfers port ownership).
        The Station model currently has no defense_level / shields /
        defense_weapons columns, so this path cannot resolve until the
        station-defense schema lands (canon gap: station defense stats).
        """
        # Get attacker
        attacker = self.db.query(Player).filter(Player.id == attacker_id).first()
        if not attacker:
            return {"success": False, "message": "Player not found"}
        
        # Check if attacker has an active ship
        if not attacker.current_ship:
            return {"success": False, "message": "No active ship selected"}
        
        # Get port
        station = self.db.query(Station).filter(Station.id == station_id).first()
        if not station:
            return {"success": False, "message": "Station not found"}
        
        # Check if player is in the port's sector
        if attacker.current_sector_id != station.sector_id:
            return {"success": False, "message": "You must be in the port's sector to attack it"}
        
        # Check if port has an owner
        if not station.owner:
            return {"success": False, "message": "Cannot attack an unowned port"}
        
        # Check if attacker is the owner
        port_owner = station.owner[0] if station.owner else None
        if port_owner and port_owner.id == attacker.id:
            return {"success": False, "message": "Cannot attack your own port"}
        
        # Check if attacker has enough turns
        turn_cost = 3  # Higher cost for attacking ports
        if attacker.turns < turn_cost:
            return {"success": False, "message": "Not enough turns to attack port"}
        
        # Check if player is docked or landed
        if attacker.is_docked or attacker.is_landed:
            return {"success": False, "message": "Cannot attack while docked at a port or landed on a planet"}
        
        # Get sector for location context
        sector = self.db.query(Sector).filter(Sector.sector_id == attacker.current_sector_id).first()
        
        # Resolve combat against port
        combat_result = self._resolve_port_combat(attacker, station, port_owner)

        # Consume turns
        spend_turns(attacker, turn_cost)

        # Create combat log
        attacker_ship = attacker.current_ship
        combat_log = CombatLog(
            combat_type=CombatType.SHIP_VS_PORT.value,
            outcome=COMBAT_RESULT_TO_OUTCOME[combat_result["result"]],
            sector_id=sector.sector_id,
            sector_uuid=sector.id,
            attacker_id=attacker.id,
            attacker_ship_id=attacker.current_ship_id,
            attacker_ship_name=attacker_ship.name if attacker_ship else None,
            attacker_ship_type=attacker_ship.type.value if attacker_ship else None,
            defender_id=port_owner.id if port_owner else None,
            port_id=station.id,
            rounds=combat_result["rounds"],
            attacker_drones=attacker.defense_drones,
            attacker_drones_lost=combat_result["attacker_drones_lost"],
            defender_drones_lost=combat_result["defender_drones_lost"],
            combat_log=json.dumps(combat_result["combat_details"]),
            ended_at=datetime.now()
        )
        
        self.db.add(combat_log)
        
        # Apply combat effects
        if combat_result["attacker_ship_destroyed"]:
            self._handle_ship_destruction(attacker, None, "port_defense")
        
        # Update drone counts
        if combat_result["attacker_drones_lost"] > 0:
            attacker.defense_drones = max(0, attacker.defense_drones - combat_result["attacker_drones_lost"])
        
        # Update port defenses
        station.defense_level = max(0, station.defense_level - combat_result["port_damage"])
        
        # If port was captured, transfer ownership
        if combat_result["port_captured"]:
            self._transfer_port_ownership(station, attacker)
        
        # Update last_attacked timestamp for port
        station.last_attacked = datetime.now()
        
        # Update last_combat timestamp for sector
        sector.last_combat = datetime.now()
        
        # Commit changes
        self.db.commit()
        
        return {
            "success": True,
            "message": combat_result["message"],
            "combat_result": combat_result["result"].name,
            "combat_details": combat_result["combat_details"],
            "port_captured": combat_result["port_captured"],
            "turns_consumed": turn_cost,
            "turns_remaining": attacker.turns,
            "combat_log_id": str(combat_log.id)
        }
    
    def deploy_drones(self, player_id: uuid.UUID, sector_id: int, drone_count: int, 
                      pattern: str = "defensive") -> Dict[str, Any]:
        """Deploy drones in a sector for defense."""
        # Get player
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {"success": False, "message": "Player not found"}
        
        # Check if player has enough drones
        if player.defense_drones < drone_count:
            return {"success": False, "message": f"Not enough defense drones. Have: {player.defense_drones}, Need: {drone_count}"}
        
        # Check if player is in the sector
        if player.current_sector_id != sector_id:
            return {"success": False, "message": "Can only deploy drones in your current sector"}
        
        # Get sector
        sector = self.db.query(Sector).filter(Sector.sector_id == sector_id).first()
        if not sector:
            return {"success": False, "message": "Sector not found"}
        
        # Check existing deployments
        existing_deployment = self.db.query(DroneDeployment).filter(
            DroneDeployment.player_id == player_id,
            DroneDeployment.sector_id == sector_id,
            DroneDeployment.is_active == True
        ).first()
        
        # Turn cost for deploying drones
        turn_cost = 1
        
        if player.turns < turn_cost:
            return {"success": False, "message": "Not enough turns to deploy drones"}
        
        # If there's an existing deployment, add to it
        if existing_deployment:
            existing_deployment.drone_count += drone_count
            existing_deployment.pattern = pattern  # Update pattern
            
            # Create new deployment log
            deployment_log = {
                "action": "add_drones",
                "previous_count": existing_deployment.drone_count - drone_count,
                "added_count": drone_count,
                "new_count": existing_deployment.drone_count,
                "timestamp": datetime.now().isoformat()
            }
            
            # If there are existing logs, append to them
            if hasattr(existing_deployment, 'deployment_log') and existing_deployment.deployment_log:
                existing_deployment.deployment_log.append(deployment_log)
            else:
                existing_deployment.deployment_log = [deployment_log]
            
            message = f"Added {drone_count} drones to existing deployment in Sector {sector_id}"
            deployment_id = existing_deployment.id
        else:
            # Create new deployment
            new_deployment = DroneDeployment(
                player_id=player_id,
                sector_id=sector_id,
                drone_count=drone_count,
                pattern=pattern,
                is_active=True,
                deployment_log=[{
                    "action": "initial_deployment",
                    "count": drone_count,
                    "pattern": pattern,
                    "timestamp": datetime.now().isoformat()
                }]
            )
            
            self.db.add(new_deployment)
            self.db.flush()  # Get the ID
            
            message = f"Deployed {drone_count} drones in Sector {sector_id}"
            deployment_id = new_deployment.id
        
        # Update player's drone count
        player.defense_drones -= drone_count
        
        # Update sector's drone count
        sector.drones_present = (sector.drones_present or 0) + drone_count
        
        # Consume turns
        spend_turns(player, turn_cost)
        
        # Commit changes
        self.db.commit()
        
        return {
            "success": True,
            "message": message,
            "deployment_id": str(deployment_id),
            "drone_count": drone_count,
            "sector_id": sector_id,
            "pattern": pattern,
            "drones_remaining": player.defense_drones,
            "turns_consumed": turn_cost,
            "turns_remaining": player.turns
        }
    
    def recall_drones(self, player_id: uuid.UUID, sector_id: int, 
                     drone_count: Optional[int] = None) -> Dict[str, Any]:
        """Recall drones from a sector."""
        # Get player
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {"success": False, "message": "Player not found"}
        
        # Check if player is in the sector
        if player.current_sector_id != sector_id:
            return {"success": False, "message": "Can only recall drones from your current sector"}
        
        # Get sector
        sector = self.db.query(Sector).filter(Sector.sector_id == sector_id).first()
        if not sector:
            return {"success": False, "message": "Sector not found"}
        
        # Find player's drone deployment in this sector
        deployment = self.db.query(DroneDeployment).filter(
            DroneDeployment.player_id == player_id,
            DroneDeployment.sector_id == sector_id,
            DroneDeployment.is_active == True
        ).first()
        
        if not deployment:
            return {"success": False, "message": "No active drone deployment found in this sector"}
        
        # Turn cost for recalling drones
        turn_cost = 1
        
        if player.turns < turn_cost:
            return {"success": False, "message": "Not enough turns to recall drones"}
        
        # Determine how many drones to recall
        available_drones = deployment.drone_count - deployment.drones_lost
        
        if drone_count is None or drone_count >= available_drones:
            # Recall all drones
            drones_to_recall = available_drones
            deployment.is_active = False
            message = f"Recalled all {drones_to_recall} drones from Sector {sector_id}"
        else:
            # Recall specific number
            drones_to_recall = drone_count
            deployment.drone_count -= drones_to_recall
            message = f"Recalled {drones_to_recall} drones from Sector {sector_id}"
        
        # Update player's drone count
        player.defense_drones += drones_to_recall
        
        # Update sector's drone count
        sector.drones_present = max(0, (sector.drones_present or 0) - drones_to_recall)
        
        # Create deployment log
        if hasattr(deployment, 'deployment_log') and deployment.deployment_log:
            deployment.deployment_log.append({
                "action": "recall_drones",
                "previous_count": deployment.drone_count + drones_to_recall,
                "recalled_count": drones_to_recall,
                "new_count": deployment.drone_count,
                "timestamp": datetime.now().isoformat()
            })
        
        # Consume turns
        spend_turns(player, turn_cost)
        
        # Commit changes
        self.db.commit()
        
        return {
            "success": True,
            "message": message,
            "drones_recalled": drones_to_recall,
            "drones_remaining_in_sector": deployment.drone_count if deployment.is_active else 0,
            "player_drones": player.defense_drones,
            "turns_consumed": turn_cost,
            "turns_remaining": player.turns
        }
    
    def get_combat_log(self, combat_log_id: uuid.UUID) -> Dict[str, Any]:
        """Get detailed information about a combat log."""
        # Get combat log
        log = self.db.query(CombatLog).filter(CombatLog.id == combat_log_id).first()
        if not log:
            return {"success": False, "message": "Combat log not found"}
        
        # Format into a detailed report
        attacker = self.db.query(Player).filter(Player.id == log.attacker_id).first()
        defender = self.db.query(Player).filter(Player.id == log.defender_id).first() if log.defender_id else None

        report = {
            "id": str(log.id),
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "combat_type": log.combat_type,
            "combat_result": log.outcome,
            "sector_id": log.sector_id,
            "attacker": {
                "id": str(log.attacker_id),
                "name": attacker.username if attacker else "Unknown",
                "ship_name": log.attacker_ship_name or "Unknown",
                "ship_type": log.attacker_ship_type or "Unknown",
                "drones_lost": log.attacker_drones_lost,
                # No dedicated column — a defender_win means the attacker's
                # ship was lost (mutual destruction is stored as a draw)
                "ship_destroyed": log.outcome == CombatOutcome.DEFENDER_WIN.value
            },
            "rounds": log.rounds,
            "details": self._parse_combat_details(log)
        }

        # Add defender details if applicable
        if log.combat_type == CombatType.SHIP_VS_SHIP.value:
            report["defender"] = {
                "id": str(log.defender_id) if log.defender_id else None,
                "name": defender.username if defender else (log.defender_ship_name or "Unknown"),
                "ship_name": log.defender_ship_name or "Unknown",
                "ship_type": log.defender_ship_type or "Unknown",
                "drones_lost": log.defender_drones_lost,
                "ship_destroyed": log.outcome == CombatOutcome.ATTACKER_WIN.value
            }
        elif log.combat_type == CombatType.SHIP_VS_PLANET.value:
            planet = self.db.query(Planet).filter(Planet.id == log.planet_id).first()
            report["target"] = {
                "type": "planet",
                "id": str(log.planet_id) if log.planet_id else None,
                "name": planet.name if planet else "Unknown",
                "owner_id": str(log.defender_id) if log.defender_id else None,
                "owner_name": defender.username if defender else "Unowned"
            }
        elif log.combat_type == CombatType.SHIP_VS_PORT.value:
            station = self.db.query(Station).filter(Station.id == log.port_id).first()
            report["target"] = {
                "type": "station",
                "id": str(log.port_id) if log.port_id else None,
                "name": station.name if station else "Unknown",
                "owner_id": str(log.defender_id) if log.defender_id else None,
                "owner_name": defender.username if defender else "Unowned"
            }
        elif log.combat_type == CombatType.SHIP_VS_DRONES.value:
            report["target"] = {
                "type": "drones",
                "sector_id": log.sector_id,
                "drones_lost": log.defender_drones_lost
            }
        
        return {
            "success": True,
            "combat_log": report
        }
    
    def get_player_combat_history(self, player_id: uuid.UUID, limit: int = 10) -> Dict[str, Any]:
        """Get a player's recent combat history."""
        # Get player
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {"success": False, "message": "Player not found"}
        
        # Get combat logs where player was attacker or defender
        logs = self.db.query(CombatLog).filter(
            or_(
                CombatLog.attacker_id == player_id,
                CombatLog.defender_id == player_id
            )
        ).order_by(CombatLog.timestamp.desc()).limit(limit).all()
        
        # Format results
        combat_history = []
        for log in logs:
            # Get opponent info
            opponent_id = log.defender_id if log.attacker_id == player_id else log.attacker_id
            opponent = self.db.query(Player).filter(Player.id == opponent_id).first() if opponent_id else None
            
            is_attacker = log.attacker_id == player_id
            entry = {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "combat_type": log.combat_type,
                "role": "attacker" if is_attacker else "defender",
                "result": log.outcome,
                "sector_id": log.sector_id,
                "drones_lost": log.attacker_drones_lost if is_attacker else log.defender_drones_lost,
                # Derived: losing side's ship was destroyed (no dedicated column)
                "ship_destroyed": (
                    log.outcome == CombatOutcome.DEFENDER_WIN.value if is_attacker
                    else log.outcome == CombatOutcome.ATTACKER_WIN.value
                )
            }

            # Add target/opponent details
            if log.combat_type == CombatType.SHIP_VS_SHIP.value:
                entry["opponent"] = {
                    "id": str(opponent_id) if opponent_id else None,
                    "name": opponent.username if opponent else (
                        log.defender_ship_name if is_attacker and log.defender_ship_name else "Unknown"
                    )
                }
            elif log.combat_type == CombatType.SHIP_VS_PLANET.value:
                planet = self.db.query(Planet).filter(Planet.id == log.planet_id).first()
                entry["target"] = {
                    "type": "planet",
                    "id": str(log.planet_id) if log.planet_id else None,
                    "name": planet.name if planet else "Unknown"
                }
            elif log.combat_type == CombatType.SHIP_VS_PORT.value:
                station = self.db.query(Station).filter(Station.id == log.port_id).first()
                entry["target"] = {
                    "type": "station",
                    "id": str(log.port_id) if log.port_id else None,
                    "name": station.name if station else "Unknown"
                }
            elif log.combat_type == CombatType.SHIP_VS_DRONES.value:
                entry["target"] = {
                    "type": "drones",
                    "sector_id": log.sector_id
                }
            
            combat_history.append(entry)
        
        return {
            "success": True,
            "combat_history": combat_history,
            "count": len(combat_history)
        }
    
    @staticmethod
    def _parse_combat_details(log: CombatLog) -> List[Dict[str, Any]]:
        """Parse the round-by-round combat_details JSON stored in the
        combat_log Text column. Returns an empty list when absent/corrupt."""
        if not log.combat_log:
            return []
        try:
            details = json.loads(log.combat_log)
        except (ValueError, TypeError):
            logger.warning("Combat log %s has unparseable combat details", log.id)
            return []
        return details if isinstance(details, list) else []

    def _is_combat_allowed(self, sector: Sector, attacker: Player, defender: Optional[Player]) -> bool:
        """Check if combat is allowed in a sector based on rules.

        defender is None for NPC-defender combat — sector rules apply the
        same way regardless of who is being attacked.

        Canon basis: the police/safe-zone docs describe Terran space as
        Federation-patrolled safe space, so combat is disallowed in
        terran_space regions. Player-owned regions and the Central Nexus
        are open space, and sectors whose cluster/region chain doesn't
        resolve default to allowed (frontier behavior).
        """
        # Resolve region type null-safely — Region.region_type holds
        # RegionType values (terran_space / player_owned / central_nexus)
        region_type = None
        if sector is not None and sector.cluster is not None and sector.cluster.region is not None:
            region_type = sector.cluster.region.region_type

        # Terran space is patrolled safe space — no combat
        if region_type == RegionType.TERRAN_SPACE:
            return False

        # player_owned, central_nexus, or unresolved region: combat allowed
        return True

    def _ensure_combat_state(self, ship: Ship) -> Dict[str, Any]:
        """Return the ship's live combat-state dict, seeding any missing
        shield/hull keys from the ship's ShipSpecification — the same source
        npc_spawn_service and ShipService.create_ship seed Ship.combat from,
        so no constants are invented here.

        The returned dict IS ship.combat (mutated in place by the resolver);
        callers must flag_modified(ship, "combat") after the battle so the
        in-place JSONB mutation is persisted.
        """
        if ship.combat is None:
            ship.combat = {}
        combat = ship.combat

        needed = ("shields", "max_shields", "hull", "max_hull")
        if any(key not in combat for key in needed):
            spec = self.db.query(ShipSpecification).filter(
                ShipSpecification.type == ship.type
            ).first()
            if spec is not None:
                # Mirror the npc_spawn_service / ShipService seeding shape
                combat.setdefault("max_shields", spec.max_shields or 0)
                combat.setdefault("shields", combat["max_shields"])
                combat.setdefault("max_hull", spec.hull_points or 1)
                combat.setdefault("hull", combat["max_hull"])
                combat.setdefault("shield_recharge_rate", spec.shield_recharge_rate)
                combat.setdefault("evasion", spec.evasion)
                combat.setdefault("attack_rating", spec.attack_rating)
                combat.setdefault("defense_rating", spec.defense_rating)
            else:
                # Pathological: no specification row for this ship type.
                # Guard rails only (not game constants): zero shields and a
                # 1-point hull floor so the hull<=0 destruction check can't
                # turn missing data into an instant kill.
                logger.warning(
                    "Ship %s (%s) has no ShipSpecification — seeding minimal "
                    "combat state", ship.id, ship.type
                )
                combat.setdefault("max_shields", 0)
                combat.setdefault("shields", 0)
                combat.setdefault("max_hull", 1)
                combat.setdefault("hull", 1)

        # Data-anomaly guard: a live (non-destroyed) ship persisted at
        # hull <= 0 — e.g. an indestructible escape pod that "lost" a fight
        # but was never actually destroyed. Clamp to a 1-point hull so the
        # next battle doesn't open with an instant destruction.
        if not ship.is_destroyed and (combat.get("hull") or 0) <= 0:
            combat["hull"] = 1

        # Floors on entry — never operate on negative pools
        combat["shields"] = max(0, combat.get("shields") or 0)
        return combat

    @staticmethod
    def _apply_weapon_damage(
        damage: float,
        weapon: Dict[str, Any],
        target_combat: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply one weapon hit per the canon damage stack
        (combat-resolver.md "Damage stack — order of operations"):

            shield_hit = min(damage, shields) * weapon.shield_effectiveness
            residual   = damage - min(damage, shields)
            hull_hit   = residual * weapon.hull_effectiveness
            critical   = (RNG < 0.05) ? hull_hit * 0.5 : 0

        Shields absorb first, the residual bleeds into hull, and a 5%
        critical adds half the hull hit again. The canon stack's
        shield_resistance / armor_rating terms are not part of the seeded
        Ship.combat JSONB today, so they resolve to 0 here (flagged, not
        invented). The canon defense-drones passive (-5% per 10 drones) is
        structurally moot in this resolver: drones are a discrete screen
        layer that must be fully destroyed before any ship hit lands, so
        the defender's drone count is always 0 when this runs.

        Floors: shields and hull are never written below 0. Mutates
        target_combat in place and returns the hit summary.
        """
        shields = max(0.0, float(target_combat.get("shields") or 0))
        hull = max(0.0, float(target_combat.get("hull") or 0))

        absorbed = min(damage, shields)
        shield_hit = absorbed * weapon["shield_effectiveness"]
        residual = damage - absorbed
        hull_hit = residual * weapon["hull_effectiveness"]

        critical = 0.0
        if hull_hit > 0 and random.random() < 0.05:
            critical = hull_hit * 0.5

        new_shields = max(0.0, shields - shield_hit)
        new_hull = max(0.0, hull - hull_hit - critical)
        target_combat["shields"] = round(new_shields, 1)
        target_combat["hull"] = round(new_hull, 1)

        return {
            "shield_damage": round(shields - new_shields, 1),
            "hull_damage": round(hull - new_hull, 1),
            "critical": critical > 0,
            "shields_remaining": target_combat["shields"],
            "hull_remaining": target_combat["hull"],
            # Destruction is decided on the UNROUNDED hull. Rounding the stored
            # value to 1 decimal could round a tiny positive residual (e.g.
            # 0.04 → 0.0) into a false kill, or a value like 0.04 staying
            # positive — read the true depletion, not the display value.
            "destroyed": new_hull <= 0,
        }

    def _resolve_ship_combat(
        self,
        attacker: Player,
        defender: Optional[Player],
        sector: Sector,
        defender_ship: Optional[Ship] = None
    ) -> Dict[str, Any]:
        """Resolve ship-to-ship combat.

        defender is the defending Player for PvP combat. For NPC combat,
        defender is None and defender_ship is the NPC-controlled Ship — the
        NPC fights with its ship alone (no rank damage bonus, no player-owned
        defense drones).
        """
        # Get ships and equipment
        attacker_ship = attacker.current_ship
        if defender is not None:
            defender_ship = defender.current_ship
            defender_name = defender.username
            defender_drones = defender.defense_drones
            defender_bonuses = RankingService.get_rank_bonuses(defender.military_rank)
            defender_damage_mult = 1.0 + (defender_bonuses["combat_damage_bonus_percent"] / 100.0)
        else:
            if defender_ship is None:
                raise ValueError("NPC combat requires a defender_ship")
            defender_name = defender_ship.name
            defender_drones = 0
            defender_damage_mult = 1.0

        # Get rank combat bonus for the attacker
        attacker_bonuses = RankingService.get_rank_bonuses(attacker.military_rank)
        attacker_damage_mult = 1.0 + (attacker_bonuses["combat_damage_bonus_percent"] / 100.0)

        # Combat parameters
        attacker_drones = attacker.defense_drones
        attacker_attack = self._calculate_attack_power(attacker_ship, attacker_drones)
        defender_defense = self._calculate_defense_power(defender_ship, defender_drones)

        # Live shield/hull pools — seeded from ShipSpecification when a
        # ship's combat JSONB lacks keys. These dicts ARE the ships' combat
        # columns; the resolver mutates them in place and flag_modified()s
        # them after the battle so attrition persists.
        attacker_combat = self._ensure_combat_state(attacker_ship)
        defender_combat = self._ensure_combat_state(defender_ship)

        # Track combat details
        round_number = 0
        attacker_drones_lost = 0
        defender_drones_lost = 0
        # Real damage tallies (shield + hull dealt by each side across all
        # rounds) — these feed CombatLog.attacker_damage_dealt /
        # defender_damage_dealt, which otherwise read their default 0 under
        # the pool-depletion model.
        attacker_damage_dealt = 0.0
        defender_damage_dealt = 0.0
        attacker_ship_destroyed = False
        defender_ship_destroyed = False
        fled_result = None  # Set to CombatResult.ATTACKER_FLED or DEFENDER_FLED if someone escapes
        combat_details = []
        
        # Combat continues until one side is defeated or retreats
        while (not attacker_ship_destroyed and not defender_ship_destroyed):
            round_number += 1
            
            # Add round header
            combat_details.append({
                "round": round_number,
                "message": f"Combat Round {round_number}"
            })
            
            # Attacker's turn
            if not attacker_ship_destroyed:
                # Calculate chance to hit
                hit_chance = min(0.8, attacker_attack / (defender_defense * 1.5) * 0.6)
                
                # Random element
                if random.random() < hit_chance:
                    # Successful hit
                    # Determine if attacking drones or ship
                    # Determine attacker weapon type
                    atk_weapon_name = self.SHIP_DEFAULT_WEAPONS.get(attacker_ship.type, "laser")
                    atk_weapon = self.WEAPON_TYPES[atk_weapon_name]

                    if defender_drones > 0:
                        # Attack drones first (shield layer) — apply shield_effectiveness
                        raw_destroyed = random.randint(1, min(3, defender_drones))
                        drones_destroyed = max(1, int(raw_destroyed * atk_weapon["shield_effectiveness"]))
                        drones_destroyed = min(drones_destroyed, defender_drones)
                        defender_drones -= drones_destroyed
                        defender_drones_lost += drones_destroyed
                        combat_details.append({
                            "round": round_number,
                            "actor": "attacker",
                            "action": "drone_attack",
                            "message": f"{attacker.username}'s {atk_weapon_name} destroyed {drones_destroyed} of {defender_name}'s drones",
                            "drones_destroyed": drones_destroyed,
                            "weapon_type": atk_weapon_name,
                            "weapon_effectiveness": atk_weapon["shield_effectiveness"]
                        })
                    else:
                        # Attack ship — canon damage stack (combat-resolver.md):
                        # base roll × rank bonus × type matchup × weapon base
                        # damage, then shields absorb first and the residual
                        # bleeds into hull. Destruction is hull <= 0 — real
                        # depletion, not a destruction-chance dice roll.
                        base_damage = random.randint(1, 10)
                        type_modifier = self.SHIP_COMBAT_MODIFIERS.get(
                            (attacker_ship.type, defender_ship.type), 1.0
                        )
                        # Attack-drones offensive bonus: +5% per 10 attack
                        # drones (combat-resolver.md:83 "attack_drones_modifier
                        # +5% per 10 drones"). Uses the in-loop attacker drone
                        # count so it decays as drones are lost mid-fight.
                        attacker_drone_mult = 1 + 0.05 * (attacker_drones // 10)
                        damage = (
                            base_damage * attacker_damage_mult * type_modifier
                            * atk_weapon["base_damage"] * attacker_drone_mult
                        )
                        hit = self._apply_weapon_damage(damage, atk_weapon, defender_combat)
                        attacker_damage_dealt += hit["shield_damage"] + hit["hull_damage"]

                        if hit["destroyed"]:
                            defender_ship_destroyed = True
                            # NPC defenders (defender is None) have no escape
                            # pod to eject into — the vessel is simply
                            # destroyed. Player defenders eject to a pod.
                            destroy_flavor = (
                                "destroying the vessel" if defender is None
                                else "forcing ejection"
                            )
                            combat_details.append({
                                "round": round_number,
                                "actor": "attacker",
                                "action": "ship_destroyed",
                                "message": f"{attacker.username}'s {atk_weapon_name} critically damaged {defender_name}'s ship, {destroy_flavor}",
                                "weapon_type": atk_weapon_name,
                                "shield_damage": hit["shield_damage"],
                                "hull_damage": hit["hull_damage"],
                                "critical_hit": hit["critical"]
                            })
                        else:
                            modifier_note = f" (x{type_modifier:.1f} type advantage)" if type_modifier != 1.0 else ""
                            crit_note = " [CRITICAL HIT]" if hit["critical"] else ""
                            combat_details.append({
                                "round": round_number,
                                "actor": "attacker",
                                "action": "ship_attack",
                                "message": (
                                    f"{attacker.username}'s {atk_weapon_name} hit {defender_name}'s ship for "
                                    f"{hit['shield_damage']} shield / {hit['hull_damage']} hull damage"
                                    f"{crit_note}{modifier_note}"
                                ),
                                "weapon_type": atk_weapon_name,
                                "shield_damage": hit["shield_damage"],
                                "hull_damage": hit["hull_damage"],
                                "critical_hit": hit["critical"],
                                "defender_shields": hit["shields_remaining"],
                                "defender_hull": hit["hull_remaining"]
                            })
                else:
                    # Miss
                    combat_details.append({
                        "round": round_number,
                        "actor": "attacker",
                        "action": "miss",
                        "message": f"{attacker.username}'s attack missed {defender_name}'s ship"
                    })

            # Check if combat is over
            if defender_ship_destroyed:
                break

            # Defender's turn
            if not defender_ship_destroyed:
                # Calculate chance to hit
                hit_chance = min(0.8, defender_defense / (attacker_attack * 1.5) * 0.6)

                # Random element
                if random.random() < hit_chance:
                    # Successful hit
                    # Determine if attacking drones or ship
                    # Determine defender weapon type
                    def_weapon_name = self.SHIP_DEFAULT_WEAPONS.get(defender_ship.type, "laser")
                    def_weapon = self.WEAPON_TYPES[def_weapon_name]

                    if attacker_drones > 0:
                        # Attack drones first (shield layer) — apply shield_effectiveness
                        raw_destroyed = random.randint(1, min(3, attacker_drones))
                        drones_destroyed = max(1, int(raw_destroyed * def_weapon["shield_effectiveness"]))
                        drones_destroyed = min(drones_destroyed, attacker_drones)
                        attacker_drones -= drones_destroyed
                        attacker_drones_lost += drones_destroyed
                        combat_details.append({
                            "round": round_number,
                            "actor": "defender",
                            "action": "drone_attack",
                            "message": f"{defender_name}'s {def_weapon_name} destroyed {drones_destroyed} of {attacker.username}'s drones",
                            "drones_destroyed": drones_destroyed,
                            "weapon_type": def_weapon_name,
                            "weapon_effectiveness": def_weapon["shield_effectiveness"]
                        })
                    else:
                        # Attack ship — same canon damage stack, symmetric
                        # to the attacker's turn: shields absorb first,
                        # residual into hull, destruction at hull <= 0.
                        base_damage = random.randint(1, 10)
                        type_modifier = self.SHIP_COMBAT_MODIFIERS.get(
                            (defender_ship.type, attacker_ship.type), 1.0
                        )
                        # Symmetric attack-drones bonus on the defender's
                        # return fire, using the defender's in-loop drone count
                        # (combat-resolver.md:83, +5% per 10 drones).
                        defender_drone_mult = 1 + 0.05 * (defender_drones // 10)
                        damage = (
                            base_damage * defender_damage_mult * type_modifier
                            * def_weapon["base_damage"] * defender_drone_mult
                        )
                        hit = self._apply_weapon_damage(damage, def_weapon, attacker_combat)
                        defender_damage_dealt += hit["shield_damage"] + hit["hull_damage"]

                        if hit["destroyed"]:
                            attacker_ship_destroyed = True
                            combat_details.append({
                                "round": round_number,
                                "actor": "defender",
                                "action": "ship_destroyed",
                                "message": f"{defender_name}'s {def_weapon_name} critically damaged {attacker.username}'s ship, forcing ejection",
                                "weapon_type": def_weapon_name,
                                "shield_damage": hit["shield_damage"],
                                "hull_damage": hit["hull_damage"],
                                "critical_hit": hit["critical"]
                            })
                        else:
                            modifier_note = f" (x{type_modifier:.1f} type advantage)" if type_modifier != 1.0 else ""
                            crit_note = " [CRITICAL HIT]" if hit["critical"] else ""
                            combat_details.append({
                                "round": round_number,
                                "actor": "defender",
                                "action": "ship_attack",
                                "message": (
                                    f"{defender_name}'s {def_weapon_name} hit {attacker.username}'s ship for "
                                    f"{hit['shield_damage']} shield / {hit['hull_damage']} hull damage"
                                    f"{crit_note}{modifier_note}"
                                ),
                                "weapon_type": def_weapon_name,
                                "shield_damage": hit["shield_damage"],
                                "hull_damage": hit["hull_damage"],
                                "critical_hit": hit["critical"],
                                "attacker_shields": hit["shields_remaining"],
                                "attacker_hull": hit["hull_remaining"]
                            })
                else:
                    # Miss
                    combat_details.append({
                        "round": round_number,
                        "actor": "defender",
                        "action": "miss",
                        "message": f"{defender_name}'s attack missed {attacker.username}'s ship"
                    })

            # --- Escape check after both sides have dealt damage this round ---
            # A combatant whose hull is exposed (all drones destroyed) attempts
            # to flee. This represents being "below 25% effective hull" since
            # the drone shield layer is gone and the ship is taking direct hits.
            if not attacker_ship_destroyed and not defender_ship_destroyed:
                # Defender tries to escape if they have no drones left (hull
                # exposed). NPC defenders (defender is None) never roll:
                # v1 static pirates stand and fight; NPC flee behavior is
                # Design-only (npc-scheduler.md). Without this skip, the
                # zero-drone NPC would roll escape from round 1 and
                # DEFENDER_FLED would dominate while the static ship never
                # actually leaves the sector.
                if defender is not None and defender_drones <= 0:
                    escape_pct = self._calculate_escape_chance(defender_ship, attacker_ship)
                    if random.randint(1, 100) <= escape_pct:
                        fled_result = CombatResult.DEFENDER_FLED
                        combat_details.append({
                            "round": round_number,
                            "actor": "defender",
                            "action": "escape",
                            "message": (
                                f"{defender_name}'s ship engaged emergency thrusters "
                                f"and escaped! (escape chance: {escape_pct}%)"
                            ),
                            "escape_chance": escape_pct
                        })

                # Attacker tries to escape if they have no drones left (hull exposed)
                if fled_result is None and attacker_drones <= 0:
                    escape_pct = self._calculate_escape_chance(attacker_ship, defender_ship)
                    if random.randint(1, 100) <= escape_pct:
                        fled_result = CombatResult.ATTACKER_FLED
                        combat_details.append({
                            "round": round_number,
                            "actor": "attacker",
                            "action": "escape",
                            "message": (
                                f"{attacker.username}'s ship engaged emergency thrusters "
                                f"and escaped! (escape chance: {escape_pct}%)"
                            ),
                            "escape_chance": escape_pct
                        })

            if fled_result is not None:
                break

            # Check if combat ends due to round limit
            if round_number >= 10:
                combat_details.append({
                    "round": round_number,
                    "action": "stalemate",
                    "message": "Combat ends in a draw after 10 rounds"
                })
                break

        # Persist post-battle attrition on BOTH hulls. Shields and hull stay
        # damaged after the fight even when nobody dies — there is no
        # automatic between-battle shield regeneration here (the seeded
        # combat JSONB carries shield_recharge_rate for a future regen
        # slice; restoring shields/hull is ShipService.repair_ship's job at
        # repair facilities). The dicts were mutated in place, so
        # flag_modified is required for the JSONB write; the caller's single
        # post-battle commit (attack_player / attack_npc_ship) lands both.
        flag_modified(attacker_ship, "combat")
        flag_modified(defender_ship, "combat")

        # Determine result
        if fled_result is not None:
            result = fled_result
            if fled_result == CombatResult.ATTACKER_FLED:
                message = f"{attacker.username} fled from combat with {defender_name}"
            else:
                message = f"{defender_name} escaped from {attacker.username}'s attack"
        elif attacker_ship_destroyed and defender_ship_destroyed:
            result = CombatResult.MUTUAL_DESTRUCTION
            message = "Combat ended in mutual destruction"
        elif attacker_ship_destroyed:
            result = CombatResult.DEFENDER_VICTORY
            message = f"{defender_name} defeated {attacker.username} in combat"
        elif defender_ship_destroyed:
            result = CombatResult.ATTACKER_VICTORY
            message = f"{attacker.username} defeated {defender_name} in combat"
        else:
            result = CombatResult.DRAW
            message = "Combat ended in a draw"
        
        # Determine cargo theft if attacker victorious.
        # Ship.cargo JSONB shape is {"capacity": n, "used": n, "contents": {commodity: qty}}
        # (see ShipService.create_ship) — only the contents dict holds commodities.
        cargo_stolen = {}
        defender_cargo_contents = ((defender_ship.cargo or {}).get("contents") or {})
        if result == CombatResult.ATTACKER_VICTORY and defender_cargo_contents:
            # Take a random portion of cargo
            for resource, amount in defender_cargo_contents.items():
                if not isinstance(amount, (int, float)) or amount <= 0:
                    continue
                if random.random() < 0.7:  # 70% chance to steal each resource
                    steal_amount = int(amount * random.uniform(0.3, 0.8))  # Steal 30-80%
                    if steal_amount > 0:
                        cargo_stolen[resource] = steal_amount
            
            if cargo_stolen:
                cargo_list = ", ".join([f"{amount} {resource}" for resource, amount in cargo_stolen.items()])
                combat_details.append({
                    "round": round_number,
                    "actor": "attacker",
                    "action": "cargo_theft",
                    "message": f"{attacker.username} salvaged cargo from {defender_name}'s ship: {cargo_list}"
                })
        
        # Finalize results
        combat_details.append({
            "round": round_number,
            "action": "combat_end",
            "result": result.name,
            "message": message
        })
        
        return {
            "result": result,
            "message": message,
            "rounds": round_number,
            "attacker_drones_lost": attacker_drones_lost,
            "defender_drones_lost": defender_drones_lost,
            # CombatLog columns are Integers — round the float tallies once here
            "attacker_damage_dealt": int(round(attacker_damage_dealt)),
            "defender_damage_dealt": int(round(defender_damage_dealt)),
            "attacker_ship_destroyed": attacker_ship_destroyed,
            "defender_ship_destroyed": defender_ship_destroyed,
            "cargo_stolen": cargo_stolen,
            "combat_details": combat_details,
            "attacker_ship_state": {
                "shields": attacker_combat.get("shields"),
                "max_shields": attacker_combat.get("max_shields"),
                "hull": attacker_combat.get("hull"),
                "max_hull": attacker_combat.get("max_hull")
            },
            "defender_ship_state": {
                "shields": defender_combat.get("shields"),
                "max_shields": defender_combat.get("max_shields"),
                "hull": defender_combat.get("hull"),
                "max_hull": defender_combat.get("max_hull")
            }
        }
    
    def _resolve_drone_combat(self, attacker: Player, sector: Sector, deployments: List[DroneDeployment]) -> Dict[str, Any]:
        """Resolve combat between a ship and sector drones."""
        # Get attacker ship and equipment
        attacker_ship = attacker.current_ship
        attacker_drones = attacker.defense_drones
        
        # Combine all defender drones
        total_defender_drones = sum(d.drone_count for d in deployments)
        defender_drones = total_defender_drones
        
        # Combat parameters
        attacker_attack = self._calculate_attack_power(attacker_ship, attacker_drones)
        defender_attack = total_defender_drones * 0.5  # Each drone contributes to attack power
        
        # Track combat details
        round_number = 0
        attacker_drones_lost = 0
        defender_drones_lost = 0
        attacker_ship_destroyed = False
        combat_details = []
        
        # Track deployments affected
        deployment_updates = []
        for deployment in deployments:
            deployment_updates.append({
                "deployment_id": str(deployment.id),
                "player_id": str(deployment.player_id),
                "starting_drones": deployment.drone_count,
                "drones_lost": 0
            })
        
        # Combat continues until one side is defeated or retreats
        while (not attacker_ship_destroyed and defender_drones > 0):
            round_number += 1
            
            # Add round header
            combat_details.append({
                "round": round_number,
                "message": f"Combat Round {round_number}"
            })
            
            # Attacker's turn
            if not attacker_ship_destroyed:
                # Calculate damage to drones
                drones_destroyed = random.randint(1, min(5, defender_drones))
                defender_drones -= drones_destroyed
                defender_drones_lost += drones_destroyed
                
                # Distribute drone losses across deployments
                # This is simplified - a more sophisticated implementation would be needed
                # for a real game to properly attribute drone losses
                remaining_to_distribute = drones_destroyed
                for deployment_update in deployment_updates:
                    if remaining_to_distribute <= 0:
                        break
                    
                    deployment = next((d for d in deployments if str(d.id) == deployment_update["deployment_id"]), None)
                    if deployment and deployment.drone_count > deployment_update["drones_lost"]:
                        available = deployment.drone_count - deployment_update["drones_lost"]
                        lost = min(remaining_to_distribute, available)
                        deployment_update["drones_lost"] += lost
                        remaining_to_distribute -= lost
                
                combat_details.append({
                    "round": round_number,
                    "actor": "attacker",
                    "action": "drone_attack",
                    "message": f"{attacker.username}'s ship destroyed {drones_destroyed} sector defense drones",
                    "drones_destroyed": drones_destroyed
                })
            
            # Check if combat is over
            if defender_drones <= 0:
                break
            
            # Defender's turn (drones)
            # Calculate chance to hit
            hit_chance = min(0.7, defender_attack / (attacker_attack * 2) * 0.5)
            
            # Random element
            if random.random() < hit_chance:
                # Successful hit
                # Determine if attacking drones or ship
                if attacker_drones > 0:
                    # Attack attacker's drones first
                    drones_destroyed = random.randint(1, min(3, attacker_drones))
                    attacker_drones -= drones_destroyed
                    attacker_drones_lost += drones_destroyed
                    combat_details.append({
                        "round": round_number,
                        "actor": "defender",
                        "action": "drone_attack",
                        "message": f"Sector defense drones destroyed {drones_destroyed} of {attacker.username}'s drones",
                        "drones_destroyed": drones_destroyed
                    })
                else:
                    # Attack ship - calculate ship damage
                    damage = random.randint(1, 8)
                    
                    # Check if attacker ship destroyed
                    ship_destruction_chance = damage / 60  # Lower chance than player vs player
                    if random.random() < ship_destruction_chance:
                        attacker_ship_destroyed = True
                        combat_details.append({
                            "round": round_number,
                            "actor": "defender",
                            "action": "ship_destroyed",
                            "message": f"Sector defense drones critically damaged {attacker.username}'s ship, forcing ejection"
                        })
                    else:
                        combat_details.append({
                            "round": round_number,
                            "actor": "defender",
                            "action": "ship_attack",
                            "message": f"Sector defense drones hit {attacker.username}'s ship for {damage} damage"
                        })
            else:
                # Miss
                combat_details.append({
                    "round": round_number,
                    "actor": "defender",
                    "action": "miss",
                    "message": f"Sector defense drones' attack missed {attacker.username}'s ship"
                })
            
            # Check if combat ends due to round limit
            if round_number >= 8:
                combat_details.append({
                    "round": round_number,
                    "action": "stalemate",
                    "message": "Combat ends as attacker withdraws after 8 rounds"
                })
                break
        
        # Determine result
        if attacker_ship_destroyed:
            result = CombatResult.DEFENDER_VICTORY
            message = f"Sector defense drones defeated {attacker.username}"
        elif defender_drones <= 0:
            result = CombatResult.ATTACKER_VICTORY
            message = f"{attacker.username} destroyed all sector defense drones"
        else:
            result = CombatResult.DRAW
            message = "Combat ended in a stalemate"
        
        # Finalize results
        combat_details.append({
            "round": round_number,
            "action": "combat_end",
            "result": result.name,
            "message": message
        })
        
        return {
            "result": result,
            "message": message,
            "rounds": round_number,
            "attacker_drones_lost": attacker_drones_lost,
            "defender_drones_lost": defender_drones_lost,
            "attacker_ship_destroyed": attacker_ship_destroyed,
            "deployment_updates": deployment_updates,
            "combat_details": combat_details
        }
    
    def _resolve_planet_combat(self, attacker: Player, planet: Planet,
                              planet_owner: Optional[Player]) -> Dict[str, Any]:
        """Resolve combat between a ship and a planet."""
        # Get attacker ship and equipment
        attacker_ship = attacker.current_ship
        attacker_drones = attacker.defense_drones

        # Planet defenses
        planet_defense_level = planet.defense_level or 0
        planet_shields = planet.shields or 0
        planet_weapons = planet.weapon_batteries or 0

        # Calculate planetary defense reduction (shields, defense level, generators)
        planetary_def = self._calculate_planetary_defense_reduction(planet)
        damage_reduction = planetary_def["damage_reduction"]
        remaining_shield_hp = planetary_def["shield_hp"]

        # Combat parameters
        attacker_attack = self._calculate_attack_power(attacker_ship, attacker_drones)
        planet_attack = planet_weapons * 2 + planet_defense_level * 3
        planet_defense = planet_shields * 3 + planet_defense_level * 5

        # Track combat details
        round_number = 0
        attacker_drones_lost = 0
        planet_damage = 0
        attacker_ship_destroyed = False
        planet_captured = False
        combat_details = []

        # Log planetary defense status at start of combat
        if damage_reduction > 0 or remaining_shield_hp > 0:
            combat_details.append({
                "round": 0,
                "action": "planetary_defense_status",
                "message": f"Planetary defenses active: {planetary_def['description']}",
                "damage_reduction": damage_reduction,
                "shield_hp": remaining_shield_hp
            })

        # Combat continues until one side is defeated or retreats
        while (not attacker_ship_destroyed and not planet_captured):
            round_number += 1

            # Add round header
            combat_details.append({
                "round": round_number,
                "message": f"Combat Round {round_number}"
            })

            # Attacker's turn
            if not attacker_ship_destroyed:
                # Calculate chance to hit
                hit_chance = min(0.8, attacker_attack / (planet_defense * 1.2) * 0.6)

                # Random element
                if random.random() < hit_chance:
                    # Successful hit - damage planet defenses
                    raw_damage = random.randint(1, 5)

                    # Apply planetary defense reduction to attacker's damage
                    reduced_damage = max(1, int(raw_damage * (1.0 - damage_reduction)))

                    # If shield HP remains, absorb damage there first
                    if remaining_shield_hp > 0:
                        shield_absorbed = min(reduced_damage, remaining_shield_hp)
                        remaining_shield_hp -= shield_absorbed
                        hull_damage = reduced_damage - shield_absorbed
                        if shield_absorbed > 0:
                            combat_details.append({
                                "round": round_number,
                                "actor": "attacker",
                                "action": "shield_hit",
                                "message": f"Planetary shields absorbed {shield_absorbed} damage ({remaining_shield_hp} shield HP remaining)",
                                "shield_absorbed": shield_absorbed,
                                "shield_hp_remaining": remaining_shield_hp
                            })
                        damage = hull_damage
                    else:
                        damage = reduced_damage

                    planet_damage += damage

                    # Update planet defense parameters for subsequent rounds
                    effective_defense_left = max(0, planet_defense_level - planet_damage)
                    planet_defense = effective_defense_left * 5 + planet_shields * 3

                    if damage > 0:
                        reduction_note = f" (reduced from {raw_damage} by {damage_reduction:.0%} defenses)" if damage_reduction > 0 else ""
                        combat_details.append({
                            "round": round_number,
                            "actor": "attacker",
                            "action": "planet_attack",
                            "message": f"{attacker.username}'s ship damaged planet defenses for {damage} points{reduction_note}",
                            "damage": damage,
                            "raw_damage": raw_damage,
                            "damage_reduction": damage_reduction
                        })
                    
                    # Check if planet captured
                    if planet_damage >= planet_defense_level:
                        planet_captured = True
                        combat_details.append({
                            "round": round_number,
                            "actor": "attacker",
                            "action": "planet_captured",
                            "message": f"{attacker.username} has overcome planetary defenses and captured the planet"
                        })
                else:
                    # Miss
                    combat_details.append({
                        "round": round_number,
                        "actor": "attacker",
                        "action": "miss",
                        "message": f"{attacker.username}'s attack missed planetary defenses"
                    })
            
            # Check if combat is over
            if planet_captured:
                break
            
            # Planet's turn
            # Calculate chance to hit
            planet_hit_chance = min(0.7, planet_attack / (attacker_attack * 1.5) * 0.5)
            
            # Random element
            if random.random() < planet_hit_chance:
                # Successful hit
                # Determine if attacking drones or ship
                if attacker_drones > 0:
                    # Attack attacker's drones first
                    drones_destroyed = random.randint(1, min(3, attacker_drones))
                    attacker_drones -= drones_destroyed
                    attacker_drones_lost += drones_destroyed
                    combat_details.append({
                        "round": round_number,
                        "actor": "defender",
                        "action": "drone_attack",
                        "message": f"Planetary defenses destroyed {drones_destroyed} of {attacker.username}'s drones",
                        "drones_destroyed": drones_destroyed
                    })
                else:
                    # Attack ship - calculate ship damage
                    damage = random.randint(1, 7)
                    
                    # Check if attacker ship destroyed
                    ship_destruction_chance = damage / 50
                    if random.random() < ship_destruction_chance:
                        attacker_ship_destroyed = True
                        combat_details.append({
                            "round": round_number,
                            "actor": "defender",
                            "action": "ship_destroyed",
                            "message": f"Planetary defenses critically damaged {attacker.username}'s ship, forcing ejection"
                        })
                    else:
                        combat_details.append({
                            "round": round_number,
                            "actor": "defender",
                            "action": "ship_attack",
                            "message": f"Planetary defenses hit {attacker.username}'s ship for {damage} damage"
                        })
            else:
                # Miss
                combat_details.append({
                    "round": round_number,
                    "actor": "defender",
                    "action": "miss",
                    "message": f"Planetary defense systems' attack missed {attacker.username}'s ship"
                })
            
            # Check if combat ends due to round limit
            if round_number >= 10:
                combat_details.append({
                    "round": round_number,
                    "action": "stalemate",
                    "message": "Combat ends as attacker withdraws after 10 rounds"
                })
                break
        
        # Determine result
        if attacker_ship_destroyed:
            result = CombatResult.DEFENDER_VICTORY
            message = f"Planetary defenses defeated {attacker.username}"
        elif planet_captured:
            result = CombatResult.ATTACKER_VICTORY
            message = f"{attacker.username} captured planet {planet.name}"
        else:
            result = CombatResult.DRAW
            message = "Combat ended in a stalemate"
        
        # Finalize results
        combat_details.append({
            "round": round_number,
            "action": "combat_end",
            "result": result.name,
            "message": message
        })
        
        return {
            "result": result,
            "message": message,
            "rounds": round_number,
            "attacker_drones_lost": attacker_drones_lost,
            "defender_drones_lost": 0,  # Planets don't have drones
            "attacker_ship_destroyed": attacker_ship_destroyed,
            "planet_damage": planet_damage,
            "planet_captured": planet_captured,
            "combat_details": combat_details
        }
    
    def _resolve_port_combat(self, attacker: Player, port: Station, 
                            port_owner: Optional[Player]) -> Dict[str, Any]:
        """Resolve combat between a ship and a station."""
        # Similar to planet combat but with port-specific parameters
        # Get attacker ship and equipment
        attacker_ship = attacker.current_ship
        attacker_drones = attacker.defense_drones
        
        # Station defenses
        port_defense_level = port.defense_level or 0
        port_shields = port.shields or 0
        port_weapons = port.defense_weapons or 0
        
        # Combat parameters
        attacker_attack = self._calculate_attack_power(attacker_ship, attacker_drones)
        port_attack = port_weapons * 2 + port_defense_level * 2
        port_defense = port_shields * 2 + port_defense_level * 4
        
        # Track combat details
        round_number = 0
        attacker_drones_lost = 0
        port_damage = 0
        attacker_ship_destroyed = False
        port_captured = False
        combat_details = []
        
        # Combat continues until one side is defeated or retreats
        while (not attacker_ship_destroyed and not port_captured):
            round_number += 1
            
            # Add round header
            combat_details.append({
                "round": round_number,
                "message": f"Combat Round {round_number}"
            })
            
            # Attacker's turn
            if not attacker_ship_destroyed:
                # Calculate chance to hit
                hit_chance = min(0.8, attacker_attack / (port_defense * 1.1) * 0.6)
                
                # Random element
                if random.random() < hit_chance:
                    # Successful hit - damage port defenses
                    damage = random.randint(1, 5)
                    port_damage += damage
                    
                    # Update port defense parameters for subsequent rounds
                    effective_defense_left = max(0, port_defense_level - port_damage)
                    port_defense = effective_defense_left * 4 + port_shields * 2
                    
                    combat_details.append({
                        "round": round_number,
                        "actor": "attacker",
                        "action": "port_attack",
                        "message": f"{attacker.username}'s ship damaged port defenses for {damage} points",
                        "damage": damage
                    })
                    
                    # Check if port captured
                    if port_damage >= port_defense_level:
                        port_captured = True
                        combat_details.append({
                            "round": round_number,
                            "actor": "attacker",
                            "action": "port_captured",
                            "message": f"{attacker.username} has overcome port defenses and captured the port"
                        })
                else:
                    # Miss
                    combat_details.append({
                        "round": round_number,
                        "actor": "attacker",
                        "action": "miss",
                        "message": f"{attacker.username}'s attack missed port defenses"
                    })
            
            # Check if combat is over
            if port_captured:
                break
            
            # Station's turn
            # Calculate chance to hit
            port_hit_chance = min(0.7, port_attack / (attacker_attack * 1.3) * 0.5)
            
            # Random element
            if random.random() < port_hit_chance:
                # Successful hit
                # Determine if attacking drones or ship
                if attacker_drones > 0:
                    # Attack attacker's drones first
                    drones_destroyed = random.randint(1, min(3, attacker_drones))
                    attacker_drones -= drones_destroyed
                    attacker_drones_lost += drones_destroyed
                    combat_details.append({
                        "round": round_number,
                        "actor": "defender",
                        "action": "drone_attack",
                        "message": f"Station defenses destroyed {drones_destroyed} of {attacker.username}'s drones",
                        "drones_destroyed": drones_destroyed
                    })
                else:
                    # Attack ship - calculate ship damage
                    damage = random.randint(1, 6)
                    
                    # Check if attacker ship destroyed
                    ship_destruction_chance = damage / 50
                    if random.random() < ship_destruction_chance:
                        attacker_ship_destroyed = True
                        combat_details.append({
                            "round": round_number,
                            "actor": "defender",
                            "action": "ship_destroyed",
                            "message": f"Station defenses critically damaged {attacker.username}'s ship, forcing ejection"
                        })
                    else:
                        combat_details.append({
                            "round": round_number,
                            "actor": "defender",
                            "action": "ship_attack",
                            "message": f"Station defenses hit {attacker.username}'s ship for {damage} damage"
                        })
            else:
                # Miss
                combat_details.append({
                    "round": round_number,
                    "actor": "defender",
                    "action": "miss",
                    "message": f"Station defense systems' attack missed {attacker.username}'s ship"
                })
            
            # Check if combat ends due to round limit
            if round_number >= 8:
                combat_details.append({
                    "round": round_number,
                    "action": "stalemate",
                    "message": "Combat ends as attacker withdraws after 8 rounds"
                })
                break
        
        # Determine result
        if attacker_ship_destroyed:
            result = CombatResult.DEFENDER_VICTORY
            message = f"Station defenses defeated {attacker.username}"
        elif port_captured:
            result = CombatResult.ATTACKER_VICTORY
            message = f"{attacker.username} captured port {port.name}"
        else:
            result = CombatResult.DRAW
            message = "Combat ended in a stalemate"
        
        # Finalize results
        combat_details.append({
            "round": round_number,
            "action": "combat_end",
            "result": result.name,
            "message": message
        })
        
        return {
            "result": result,
            "message": message,
            "rounds": round_number,
            "attacker_drones_lost": attacker_drones_lost,
            "defender_drones_lost": 0,  # Ports don't have drones like players
            "attacker_ship_destroyed": attacker_ship_destroyed,
            "port_damage": port_damage,
            "port_captured": port_captured,
            "combat_details": combat_details
        }
    
    def _calculate_escape_chance(self, fleeing_ship: Ship, pursuing_ship: Ship) -> int:
        """Calculate the percentage chance of a ship escaping combat.

        Escape chance is based on relative speed of both ships, with a bonus
        for nimble ship types (FAST_COURIER, SCOUT_SHIP). Result is clamped
        to the range 10-90%.

        Args:
            fleeing_ship: The ship attempting to escape.
            pursuing_ship: The ship trying to prevent escape.

        Returns:
            Escape chance as an integer percentage (10-90).
        """
        fleeing_speed = fleeing_ship.current_speed if fleeing_ship else 1.0
        pursuing_speed = pursuing_ship.current_speed if pursuing_ship else 1.0

        chance = 30 + int(fleeing_speed * 10) - int(pursuing_speed * 5)

        # Hull-ratio valve (combat-resolver.md:128-133): the more damaged the
        # fleeing ship's hull, the more desperate/likely the escape — up to a
        # +30 bonus at near-zero hull: int((1 - hull/max_hull) * 30). Reads the
        # live combat JSONB; guards missing keys and a zero/absent max_hull.
        if fleeing_ship:
            combat = getattr(fleeing_ship, "combat", None) or {}
            max_hull = combat.get("max_hull") or 0
            hull = combat.get("hull") or 0
            if max_hull > 0:
                hull_ratio = max(0.0, min(1.0, hull / max_hull))
                chance += int((1 - hull_ratio) * 30)

        # Fast/agile ships get a flat +20% bonus
        if fleeing_ship and fleeing_ship.type in self.FAST_ESCAPE_SHIP_TYPES:
            chance += 20

        # Clamp to 10-90%
        return max(10, min(90, chance))

    def _calculate_planetary_defense_reduction(self, planet: Planet) -> Dict[str, Any]:
        """Calculate how much planetary defenses reduce incoming attack damage.

        Reads the planet's defense_level (0-10), shields, and defense_shields
        (shield generator level) to produce a damage reduction factor and a
        shield HP pool that must be depleted before hull damage is dealt.

        Returns:
            Dict with:
                damage_reduction: float 0.0-0.9 — multiplicative reduction on
                    incoming damage (e.g. 0.35 means 35% less damage).
                shield_hp: int — flat shield hit-points from shield generators
                    that must be burned through before planet hull takes damage.
                description: str — human-readable summary.
        """
        defense_level = getattr(planet, "defense_level", 0) or 0
        shield_gen_level = getattr(planet, "defense_shields", 0) or 0
        shields = getattr(planet, "shields", 0) or 0

        # Each defense_level reduces damage by 5%, capped at 50% (level 10)
        level_reduction = min(defense_level * 0.05, 0.50)

        # Shield generators add a flat shield HP pool (500 HP per generator level,
        # plus any existing shield value on the planet).
        shield_hp = (shield_gen_level * 500) + (shields * 100)

        # Total damage_reduction also includes a small bonus from shield generators
        # (each gen level adds 4% reduction, up to 40% at level 10)
        gen_reduction = min(shield_gen_level * 0.04, 0.40)

        # Combined reduction capped at 0.9 so planets are never invincible
        damage_reduction = min(level_reduction + gen_reduction, 0.90)

        parts = []
        if defense_level > 0:
            parts.append(f"Level {defense_level} defenses ({level_reduction:.0%} reduction)")
        if shield_gen_level > 0:
            parts.append(f"Level {shield_gen_level} shield generators ({gen_reduction:.0%} reduction, {shield_hp} shield HP)")
        description = " + ".join(parts) if parts else "No planetary defenses"

        return {
            "damage_reduction": round(damage_reduction, 2),
            "shield_hp": shield_hp,
            "description": description
        }

    def _calculate_attack_power(self, ship: Ship, drones: int) -> float:
        """Calculate the attack power of a ship and its drones."""
        if not ship:
            return 0
        
        # Base attack power depends on ship type
        ship_type_attack = {
            ShipType.LIGHT_FREIGHTER: 10,
            ShipType.CARGO_HAULER: 15,
            ShipType.FAST_COURIER: 20,
            ShipType.SCOUT_SHIP: 25,
            ShipType.COLONY_SHIP: 15,
            ShipType.DEFENDER: 40,
            ShipType.CARRIER: 30,
            ShipType.WARP_JUMPER: 20
        }
        
        base_attack = ship_type_attack.get(ship.type, 10)
        
        # Parse combat JSON for additional attack power
        combat_data = ship.combat if hasattr(ship, "combat") and ship.combat else {}
        attack_bonus = combat_data.get("attack_bonus", 0)
        
        # Each drone contributes to attack power
        drone_attack = drones * 2

        # Maintenance condition scales overall combat effectiveness
        # (ships.md performance bands: Worn -5%, Degraded -20%, Critical -75%).
        from src.services.maintenance_service import combat_multiplier
        return (base_attack + attack_bonus + drone_attack) * combat_multiplier(ship)

    def _calculate_defense_power(self, ship: Ship, drones: int) -> float:
        """Calculate the defense power of a ship and its drones."""
        if not ship:
            return 0
        
        # Base defense power depends on ship type
        ship_type_defense = {
            ShipType.LIGHT_FREIGHTER: 10,
            ShipType.CARGO_HAULER: 20,
            ShipType.FAST_COURIER: 15,
            ShipType.SCOUT_SHIP: 10,
            ShipType.COLONY_SHIP: 20,
            ShipType.DEFENDER: 50,
            ShipType.CARRIER: 40,
            ShipType.WARP_JUMPER: 15
        }
        
        base_defense = ship_type_defense.get(ship.type, 10)
        
        # Parse combat JSON for additional defense
        combat_data = ship.combat if hasattr(ship, "combat") and ship.combat else {}
        shield_bonus = combat_data.get("shield_bonus", 0)
        hull_bonus = combat_data.get("hull_bonus", 0)
        evasion = combat_data.get("evasion", 0)
        
        # Each drone contributes to defense
        drone_defense = drones * 1.5

        # Maintenance condition scales overall combat effectiveness (ships.md bands).
        from src.services.maintenance_service import combat_multiplier
        return (base_defense + shield_bonus + hull_bonus + evasion + drone_defense) * combat_multiplier(ship)
    
    def _handle_ship_destruction(self, player: Player, destroyer: Optional[Player], cause: str) -> None:
        """Handle a player's ship being destroyed."""
        if not player.current_ship:
            return
        
        # Check if ship is indestructible (like Escape Pod)
        if self.ship_service.is_ship_indestructible(player.current_ship):
            logger.info(f"Ship {player.current_ship.name} is indestructible, cannot be destroyed")
            return
        
        # Use ship service to handle destruction and escape pod ejection
        escape_pod = self.ship_service.destroy_ship(
            ship=player.current_ship,
            destroyer=destroyer,
            cause=cause
        )
        
        logger.info(f"Player {player.id} ship destroyed, ejected to {escape_pod.name}")
    
    def _transfer_cargo(self, source_ship: Ship, target_ship: Ship, cargo_to_transfer: Dict[str, int]) -> None:
        """Transfer cargo from one ship to another.

        Operates on the real cargo JSONB shape
        {"capacity": n, "used": n, "contents": {commodity: qty}} and clamps
        transfers to the target ship's remaining capacity so combat salvage
        cannot overflow the victor's hold.
        """
        if not source_ship or not target_ship:
            return

        source_cargo = source_ship.cargo or {}
        target_cargo = target_ship.cargo or {}
        source_contents: Dict[str, int] = source_cargo.get("contents") or {}
        target_contents: Dict[str, int] = target_cargo.get("contents") or {}

        target_capacity = target_cargo.get("capacity", 0) or 0
        target_used = sum(q for q in target_contents.values() if isinstance(q, (int, float)))
        remaining_capacity = max(0, int(target_capacity) - int(target_used))

        # Transfer each resource, bounded by what the source actually holds
        # and what the target can still carry
        for resource, amount in cargo_to_transfer.items():
            if remaining_capacity <= 0:
                break
            available = source_contents.get(resource, 0)
            if not isinstance(available, (int, float)) or available <= 0:
                continue
            moved = min(int(amount), int(available), remaining_capacity)
            if moved <= 0:
                continue

            # Remove from source
            source_contents[resource] = int(available) - moved
            if source_contents[resource] <= 0:
                del source_contents[resource]

            # Add to target
            target_contents[resource] = int(target_contents.get(resource, 0)) + moved
            remaining_capacity -= moved

        # Write back with recalculated usage; flag_modified is required for
        # SQLAlchemy to detect in-place JSONB mutation
        source_cargo["contents"] = source_contents
        source_cargo["used"] = sum(int(q) for q in source_contents.values())
        target_cargo["contents"] = target_contents
        target_cargo["used"] = sum(int(q) for q in target_contents.values())
        source_ship.cargo = source_cargo
        target_ship.cargo = target_cargo
        flag_modified(source_ship, "cargo")
        flag_modified(target_ship, "cargo")
    
    def _transfer_planet_ownership(self, planet: Planet, new_owner: Player) -> None:
        """Transfer ownership of a planet to a new player via many-to-many."""
        from src.models.station import player_stations
        # Clear existing owners from the join table
        player_planets = self.db.execute(
            self.db.query(Planet).filter(Planet.id == planet.id).statement
        )
        # Use direct SQL to clear the many-to-many
        from sqlalchemy import text
        self.db.execute(
            text("DELETE FROM player_planets WHERE planet_id = :pid"),
            {"pid": str(planet.id)}
        )
        # Add new owner
        self.db.execute(
            text("INSERT INTO player_planets (player_id, planet_id) VALUES (:player_id, :planet_id)"),
            {"player_id": str(new_owner.id), "planet_id": str(planet.id)}
        )
        logger.info("Planet %s ownership transferred to player %s", planet.id, new_owner.id)

    def _transfer_port_ownership(self, port: Station, new_owner: Player) -> None:
        """Transfer ownership of a port to a new player."""
        port.owner_id = new_owner.id