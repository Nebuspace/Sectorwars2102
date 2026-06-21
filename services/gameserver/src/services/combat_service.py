import json
import logging
import uuid
import random
import math
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import and_, or_

from src.models.player import Player
from src.models.ship import Ship, ShipStatus, ShipType, ShipSpecification
from src.models.sector import Sector
from src.models.combat import CombatType, CombatResult
from src.models.combat_log import CombatLog, CombatOutcome
from src.models.drone import Drone, DroneDeployment, DroneStatus
from src.models.planet import Planet
from src.models.region import RegionType
from src.models.station import Station
from src.services.ship_service import ShipService
from src.services.ranking_service import RankingService
from src.services.ship_upgrade_service import ShipUpgradeService
from src.services.turn_service import spend_turns
from src.core.game_time import canonical_hours_since

logger = logging.getLogger(__name__)

# ── NPC-kill loot faucet (WO-DBB-EC2 / economy lifecycle §1.2) ──────────────
# Canon (FEATURES/economy/lifecycle.md:29-31): destroying an NPC ship mints
# NEW credits into the economy — a TRUE FAUCET, ≈ 5–15% of the NPC ship's
# current_value, "capped per encounter to discourage farm loops". PvP kills
# stay strictly ZERO-SUM (they redistribute existing credits, never mint), so
# this faucet lives ONLY in attack_npc_ship and is gated on a genuine NPC-hull
# destruction.
#
# ⚠️ NO-CANON NUMBERS — FLAGGED FOR MAX (lifecycle.md marks loot tables /
# per-region scaling "📐 Design-only" with no committed magnitudes):
#   * the 5–15% band endpoints, and
#   * the per-encounter credit ceiling,
# are conservative placeholders chosen to seed the documented faucet without
# enabling a farm loop. The band matches the canon "≈ 5–15%" wording; the cap
# is deliberately low (one weak-NPC kill yields little, and even a fat-hull
# kill cannot exceed the ceiling), so repeatedly grinding weak NPCs hits a
# hard credit ceiling per encounter. Tune once Max sets canon.
NPC_KILL_LOOT_MINT_MIN_PCT = 0.05  # NO-CANON, flagged — lifecycle.md "≈ 5%"
NPC_KILL_LOOT_MINT_MAX_PCT = 0.15  # NO-CANON, flagged — lifecycle.md "≈ 15%"
NPC_KILL_LOOT_MINT_CAP = 5000      # NO-CANON, flagged — per-encounter ceiling


def _regen_turns(db: Session, player: Player) -> None:
    """Bring a player's turn balance current (lazy ADR-0004 regen) before an
    affordability check / spend, via the turns-lane frozen hook
    ``turn_service.regenerate_turns(db, player)``.

    Defensive on every axis: the hook is built by the turns lane and may be
    absent at runtime in some deployments, so it is resolved by ``getattr``
    rather than imported at module load (a missing hook must never break the
    combat import or a fight). A regen hiccup is logged and swallowed — at
    worst the player attacks with a slightly stale (lower) balance, never a
    crash. py_compile-safe: nothing here references a symbol that does not
    yet exist at parse time."""
    try:
        import src.services.turn_service as _turn_service
        hook = getattr(_turn_service, "regenerate_turns", None)
        if callable(hook):
            hook(db, player)
    except Exception as e:  # never let regen break combat
        logger.error("Turn regen hook failed (continuing with current balance): %s", e)


def _medal_combat_damage_bonus(db: Session, player: Player) -> float:
    """WO-CG — the summed, capped medal ``combat_damage`` bonus (percent) for a
    player, folded into the combat damage multiplier alongside the rank term.

    Defensive on every axis (mirrors ``_dispatch_combat_medals``): the medals
    lane read path is resolved by ``getattr`` (may be absent in a deployment
    where the medals lane hasn't landed), and any failure returns 0.0 — a medal
    hiccup must NEVER alter or break combat resolution. The result is already
    clamped to the blessed +3% cap by ``get_active_medal_bonuses``. Returns a
    PERCENT (e.g. 2.0 = +2%); the caller divides by 100.0."""
    try:
        if player is None or getattr(player, "id", None) is None:
            return 0.0
        import src.services.medal_service as _medal_service
        hook = getattr(_medal_service, "get_active_medal_bonuses", None)
        if not callable(hook):
            return 0.0
        bonuses = hook(db, player.id) or {}
        return float(bonuses.get("combat_damage", 0.0) or 0.0)
    except Exception as e:  # never let a medal read break combat
        logger.error("Medal combat-damage bonus read failed (continuing without): %s", e)
        return 0.0


def _dispatch_combat_medals(db: Session, killer: Player, context: Dict[str, Any]) -> None:
    """Fire the medals-lane frozen hook
    ``medal_service.check_and_award_combat_medals(db, killer_player, context)``
    after a resolved kill (ADR-0028 medal storage).

    ``context`` carries at least ``{victim_id, combat_log_id, kind}``. The
    hook is idempotent on the medals-lane side; this dispatcher is defensive:
    resolved by ``getattr`` (the hook may be absent in a deployment where the
    medals lane hasn't landed), and any failure is logged and swallowed — a
    medal hiccup must NEVER break combat resolution. py_compile-safe: no
    parse-time reference to a not-yet-existing symbol.

    The frozen signature is ``check_and_award_combat_medals(db, killer,
    context)`` (medals lane). It is dispatched as a module-level function on
    ``medal_service`` if present, otherwise as a ``MedalService`` instance
    method with the same ``(db, killer, context)`` argument shape."""
    try:
        import src.services.medal_service as _medal_module
        module_hook = getattr(_medal_module, "check_and_award_combat_medals", None)
        if callable(module_hook):
            module_hook(db, killer, context)
            return
        MedalService = getattr(_medal_module, "MedalService", None)
        method_hook = getattr(MedalService, "check_and_award_combat_medals", None)
        if MedalService is not None and callable(method_hook):
            MedalService(db).check_and_award_combat_medals(db, killer, context)
    except Exception as e:  # never let a medal hiccup break combat
        logger.error("Combat medal dispatch hook failed: %s", e)


def _dispatch_bounty_medals(db: Session, collector_id) -> None:
    """Fire the medals-lane bounty hook
    ``medal_service.check_and_award_bounty_medals(db, collector_id)`` after a
    paying bounty collection (combat.bounty_hunter / bounties_collected).

    Same defensive contract as ``_dispatch_combat_medals``: resolved by
    ``getattr`` (the hook may be absent in a deployment where the medals lane
    hasn't landed), idempotent on the medals-lane side, and any failure is
    logged and swallowed — a medal hiccup must NEVER break combat resolution."""
    try:
        import src.services.medal_service as _medal_module
        hook = getattr(_medal_module, "check_and_award_bounty_medals", None)
        if callable(hook):
            hook(db, collector_id)
    except Exception as e:  # never let a medal hiccup break combat
        logger.error("Bounty medal dispatch hook failed: %s", e)


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

    # --- Between-battle shield regeneration (WO-SR1) ---------------------
    # ShipSpecification.shield_recharge_rate seeds Ship.combat but nothing
    # consumed it. Shields recover OUT OF COMBAT only, via an advance-on-read
    # tick run when a ship's combat state is read at the START of a battle
    # (i.e. for the idle gap since its last battle) — never mid-fight, since
    # the resolver reads combat state exactly once per engagement. Hull is
    # NEVER regenerated (hull = repair-only canon, combat.md:97).
    #
    # NO-CANON: combat.md:97 marks between-battle shield regen as deferred
    # (📐, "no scheduler") and gives NO cadence, rate unit, or first-credit
    # bound. Conservative interpretations, flagged for Max:
    #   * RATE UNIT — shield_recharge_rate is read as shield-points PER
    #     CANONICAL HOUR, mirroring the market stock-regen convention
    #     (production_rate = units per canonical hour, trading_service).
    #   * PER-CREDIT-WINDOW CAP — EVERY regen window (not just the first) is
    #     capped at this many canonical hours, so a ship returning after weeks
    #     doesn't full-regen in one jump and a legacy/absent anchor can't credit
    #     unbounded; bounded above by max_shields regardless. NB: at high
    #     GAME_TIME_SCALE this caps a long-idle ship's regen window (24 canonical
    #     hrs ≈ 10 wall-min at scale 144) — NO-CANON tuning, flag for Max if
    #     between-battle regen feels too slow on dev.
    SHIELD_REGEN_ANCHOR_KEY = "shields_last_regen"  # NO-CANON (JSONB anchor name)
    SHIELD_REGEN_MAX_CREDIT_HOURS = 24.0             # NO-CANON (per-credit-window cap)

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

        # Carrier ship-hangar inertness (WO-AE; ships.md:341): a ship docked
        # inside a Carrier hangar cannot attack and cannot be individually
        # targeted (damage to the Carrier never reaches it). Reject before any
        # turn charge. Lazy import avoids a service import cycle.
        from src.services.hangar_service import HangarService
        _hangar = HangarService(self.db)
        if _hangar.is_ship_hangared(attacker.current_ship_id):
            return {"success": False, "message": "Your ship is docked inside a Carrier and cannot attack — undock first"}
        if _hangar.is_ship_hangared(defender.current_ship_id):
            return {"success": False, "message": "That ship is docked inside a Carrier and cannot be targeted"}

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
        # Bring the attacker's turn balance current before the affordability
        # check so lazy ADR-0004 regen isn't lost by the upcoming spend.
        _regen_turns(self.db, attacker)
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
                # (Medal awards are handled by the single frozen dispatcher
                # _dispatch_combat_medals below — the legacy inline
                # check_combat_medals call was removed to fire the medal hook
                # exactly once per kill.)
        except Exception as e:
            logger.error("Failed ARIA hooks after combat: %s", e)

        # Medal dispatch hook (ADR-0028 / medals lane). Fires on a resolved
        # PvP KILL — attacker victory in which the defender's ship was
        # destroyed. Best-effort via the defensive dispatcher (idempotent on
        # the medals-lane side; a medal hiccup never breaks combat).
        if (combat_result["result"] == CombatResult.ATTACKER_VICTORY
                and combat_result["defender_ship_destroyed"]):
            _dispatch_combat_medals(
                self.db,
                attacker,
                {
                    "victim_id": defender.id,
                    "combat_log_id": combat_log.id,
                    "kind": "pvp",
                },
            )

        # Grey-flag PvP status (WO-BL). Two interacting facts, both evaluated
        # against PRE-resolution standing so a kill that drops the defender's rep
        # via destruction side-effects can't change the verdict:
        #   - was the DEFENDER a good-standing player? → only then does attacking
        #     them mark the ATTACKER grey (1h, "player_attack").
        #   - is the DEFENDER currently grey, and does the ATTACKER qualify for the
        #     penalty-free exemption? → then the attack_innocent rep penalty AND the
        #     attack_innocent police engagement are both SKIPPED.
        # Snapshot the defender's good-standing BEFORE any rep mutation below.
        from src.services.grey_flag_service import (
            GreyFlagService,
            GREY_KIND_PLAYER_ATTACK,
            is_good_standing,
            attack_is_penalty_free,
        )
        defender_was_good_standing = is_good_standing(defender)
        attack_was_penalty_free = attack_is_penalty_free(attacker, defender)

        # Personal reputation + bounty hooks
        attacked_innocent = False
        killed_escape_pod = False
        try:
            from src.services.personal_reputation_service import PersonalReputationService
            from src.services.bounty_service import BountyService
            rep_service = PersonalReputationService(self.db)

            if combat_result["result"] == CombatResult.ATTACKER_VICTORY:
                # Attacker won — check if defender had bounties
                bounty_service = BountyService(self.db)
                bounty_result = bounty_service.collect_bounty(attacker.id, defender.id)
                if bounty_result.get("total_collected", 0) > 0:
                    # Bounty paid out — heroic bounty hunting.
                    rep_service.adjust_reputation(attacker.id, 100, "defeat_bounty_target")
                    # Medal: combat.bounty_hunter (bounties_collected). Fires only
                    # on a genuine paying collection, inside this combat unit of
                    # work; idempotent on the medals side. Defensive dispatch —
                    # never breaks combat (the hook swallows its own errors).
                    _dispatch_bounty_medals(self.db, attacker.id)
                elif not bounty_result.get("had_bounty"):
                    # Target carried NO bounty at all — attacked a genuine
                    # innocent. Reputation penalty + police "attack_innocent"
                    # engagement trigger (attacked_innocent gates that below) —
                    # UNLESS the target is grey and this attacker qualifies for the
                    # penalty-free exemption (WO-BL): bringing a flagged aggressor
                    # to justice is lawful, so neither the rep penalty nor the
                    # police "attack_innocent" routing fires.
                    if attack_was_penalty_free:
                        logger.info(
                            "Grey-flag exemption: player %s killed grey target %s "
                            "penalty-free (kind=%s) — attack_innocent rep + police "
                            "skipped (WO-BL)",
                            attacker.id, defender.id, defender.grey_kind,
                        )
                    else:
                        rep_service.adjust_reputation(attacker.id, -100, "attack_innocent")
                        attacked_innocent = True
                        # Aggressing on a GOOD-STANDING player marks the ATTACKER
                        # grey for 1h: good-standing players may now hunt them
                        # penalty-free. Only good-standing victims trigger this —
                        # gunning down an already-grey/outlaw player is its own
                        # (separate) consequence, not a fresh open-season mark. MAX
                        # rule applied inside set_grey (never shortens a longer grey).
                        if defender_was_good_standing:
                            GreyFlagService(self.db).set_grey(
                                attacker, GREY_KIND_PLAYER_ATTACK
                            )
                # else: had_bounty True but total_collected == 0 — the target
                # was a known criminal whose head this hunter had ALREADY turned
                # in (system bounty deduped by the claims ledger). Killing a
                # criminal you've already claimed is neither heroic nor innocent-
                # slaughter: apply NEITHER +100 nor -100, and do NOT set
                # attacked_innocent (no police "attack_innocent" routing).
                # Check if the DESTROYED defender ship was an escape pod —
                # evaluated against the pre-destruction snapshot, not
                # defender.current_ship (which is now the post-kill pod).
                if defender_pre_destruction_type == ShipType.ESCAPE_POD:
                    rep_service.adjust_reputation(attacker.id, -500, "kill_escape_pod")
                    killed_escape_pod = True
            elif combat_result["result"] == CombatResult.DEFENDER_VICTORY:
                # Defender successfully defended — reputation boost
                rep_service.adjust_reputation(defender.id, 50, "defend_against_attacker")
        except Exception as e:
            logger.error("Failed reputation/bounty hooks after combat: %s", e)

        # Suspect / Wanted lifecycle (police-forces.md + ranking.md). The
        # ranking lane only DISPLAYS these flags; combat SETS them, keyed off
        # the same personal-reputation signals fired just above:
        #   - attack_innocent (attacker victory, no bounty on a lawful target)
        #     → Suspect: an "attack on an innocent" is the canon Federation
        #       Suspect trigger (police-forces.md:36).
        #   - kill_escape_pod (egregious — gunning down a defenseless pod), OR
        #     the attacker's reputation now sits at/below the canon Wanted
        #     threshold (personal_reputation < −500, ranking.md / police-
        #     forces.md:35) → Wanted.
        # DEFERRED (canon conflict — see DECISIONS.md "combat-suspect-wanted-
        # triggers"). Canon (ranking.md:177-211, ADR-0007) defines Suspect
        # Status ONLY from early Cargo-Wreck salvage and Wanted Status ONLY
        # from piloting a reported-stolen ship — NOT from attack-innocent /
        # escape-pod / rep-threshold (those are police *engagement-spawn*
        # triggers, a distinct concept). Setting the flags off combat signals
        # would canonize an invented rule, so the SET is withheld pending Max's
        # ruling + a column-name reconciliation (is_suspect/is_wanted vs canon
        # suspect_status/suspect_until/wanted_status + auto-clear timer). The
        # columns + the ranking-lane DISPLAY wiring remain in place so the rail
        # is ready the moment canon is settled. Police engagement routing below
        # is unaffected (it keys off the rep signals, which ARE canon).
        _ = (attacked_innocent, killed_escape_pod)  # retained for the deferred trigger

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

        # Carrier ship-hangar inertness (WO-AE): a docked passenger cannot
        # attack. (An NPC ship is never hangared, so only the attacker is
        # guarded here.)
        from src.services.hangar_service import HangarService
        if HangarService(self.db).is_ship_hangared(attacker.current_ship_id):
            return {"success": False, "message": "Your ship is docked inside a Carrier and cannot attack — undock first"}

        if attacker.current_sector_id != npc_ship.sector_id:
            return {"success": False, "message": "Target is not in your sector"}

        # Attack turn cost comes from the defender ship's specification,
        # exactly as in player-vs-player combat
        defender_spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == npc_ship.type
        ).first()
        turn_cost = getattr(defender_spec, 'attack_turn_cost', None) or 2
        # Lazy ADR-0004 regen before the affordability check / spend.
        _regen_turns(self.db, attacker)
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
        minted_loot = 0
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

            # ── NPC-kill loot faucet (WO-DBB-EC2 / lifecycle.md §1.2) ──────
            # On a genuine NPC-SHIP DESTRUCTION, mint NEW credits = a band of
            # the hull's current_value, ON TOP of the wallet-seed loot above.
            # This is the documented true faucet (lifecycle.md:29). It is
            # deliberately scoped to attack_npc_ship ONLY, so PvP combat stays
            # zero-sum (attack_player never reaches this code).
            #
            # Triple-gated so nothing else can trip the faucet:
            #   1. ATTACKER_VICTORY (the enclosing branch),
            #   2. defender_ship_destroyed — for an NPC hull, ATTACKER_VICTORY
            #      already implies destruction (no escape pods; the resolver
            #      sets result == ATTACKER_VICTORY iff defender_ship_destroyed),
            #      but we assert it explicitly so the mint is provably ONE-TIME
            #      and never fires on a fled/survived NPC or a per-round tick,
            #   3. npc_ship.is_npc — defence-in-depth that this really is an
            #      NPC-piloted hull (owner_id NULL / is_npc True), so a drone
            #      kill (a different method entirely) or any non-NPC hull can
            #      never mint.
            # No double-mint on multi-round resolution: this block runs exactly
            # once per resolved attack_npc_ship call (combat is fully resolved
            # by _resolve_ship_combat before we get here), and the attacker /
            # npc_ship / looted_npc rows are already locked above (with_for_update),
            # in the canonical NPC-row-then-player-row order — no new lock taken.
            if (combat_result["defender_ship_destroyed"]
                    and getattr(npc_ship, "is_npc", False)):
                # Hull-value basis: NPC ships spawn with current_value=0 (npc_spawn
                # sets only Reputation.current_value), so fall back to the ship
                # SPEC's catalog base_cost (defender_spec already queried above for
                # turn_cost). Without this fallback the faucet is inert. The
                # per-encounter cap below still binds on fat hulls.
                hull_value = (int(npc_ship.current_value or 0)
                              or int(getattr(defender_spec, "base_cost", 0) or 0))
                if hull_value > 0:
                    loot_pct = random.uniform(
                        NPC_KILL_LOOT_MINT_MIN_PCT, NPC_KILL_LOOT_MINT_MAX_PCT
                    )
                    # Per-encounter cap is the anti-farm ceiling: grinding weak
                    # NPCs hits a hard credit limit, and even a high-value hull
                    # cannot mint above NPC_KILL_LOOT_MINT_CAP per kill.
                    minted_loot = min(
                        int(hull_value * loot_pct), NPC_KILL_LOOT_MINT_CAP
                    )
                    if minted_loot > 0:
                        attacker.credits = (attacker.credits or 0) + minted_loot
                        logger.info(
                            "NPC-kill loot faucet: minted %d cr (%.1f%% of hull "
                            "value %d, capped at %d) to player %s for destroying "
                            "NPC ship %s (NO-CANON band/cap, lifecycle.md §1.2; "
                            "flagged for Max)",
                            minted_loot, loot_pct * 100, hull_value,
                            NPC_KILL_LOOT_MINT_CAP, attacker.id, npc_ship.id,
                        )

            # Notoriety consequence: gunning down a REPUTABLE merchant is a
            # crime — the canon attack_innocent penalty (−100, mirroring PvP).
            # An UNSCRUPULOUS / NOTORIOUS trader (notoriety ≥ threshold) is a
            # lawful target — no penalty. Raiders are always fair game;
            # marshals carry their own faction penalty further below.
            # Gate BOTH the penalty and the notorious-trader reward on the
            # actual destruction of the NPC ship. For NPC ships ATTACKER_VICTORY
            # already implies destruction (no escape pods — see the resolver at
            # _resolve_*; result == ATTACKER_VICTORY iff defender_ship_destroyed),
            # but we assert the flag explicitly so the consequence is provably
            # ONE-TIME and non-farmable: it fires only when the trader is gone,
            # never on a survived/fled NPC or a per-round tick.
            if looted_npc is not None and combat_result["defender_ship_destroyed"]:
                from src.models.npc_character import NPCArchetype as _Arch
                from src.services.npc_spawn_service import LAWFUL_TARGET_THRESHOLD
                if (looted_npc.archetype == _Arch.TRADER
                        and (looted_npc.notoriety or 0) < LAWFUL_TARGET_THRESHOLD):
                    # Reputable / standard merchant — gunning them down is a
                    # crime (canon attack_innocent −100 personal rep, ADR-0042).
                    try:
                        from src.services.personal_reputation_service import (
                            PersonalReputationService,
                        )
                        PersonalReputationService(self.db).adjust_reputation(
                            attacker.id, -100, "attack_innocent"
                        )
                    except Exception as e:
                        logger.error("Failed innocent-trader reputation hook: %s", e)
                    # DEFERRED (canon conflict) — see the PvP-path note above and
                    # DECISIONS.md "combat-suspect-wanted-triggers". The Suspect/
                    # Wanted SET off combat signals is withheld pending Max's
                    # ruling; the rep penalty above is canon and stays.
                elif looted_npc.archetype == _Arch.TRADER:
                    # Notorious / unscrupulous trader (notoriety ≥ threshold) —
                    # a LAWFUL target (ADR-0074 §10). Killing one is not merely
                    # penalty-free: canon says it "yields a positive incentive
                    # (bounty / faction approval)". We grant a modest positive
                    # personal-reputation reward, mirroring the penalty form so
                    # it folds into THIS method's single locked commit (the
                    # adjust_reputation helper only flush()es — it never commits
                    # mid-transaction, so no second lock and no second commit).
                    #
                    # Non-farmable by construction: the NPC ship is destroyed in
                    # the same block below (and the destruction flag is asserted
                    # above), so a notorious trader can be killed — and thus
                    # rewarded — exactly once.
                    #
                    # ⚠️ NO-CANON NUMBER — FLAG FOR MAX / DECISIONS: ADR-0074 §10
                    # specifies the positive incentive but gives NO magnitude.
                    # +25 is a deliberately modest placeholder — well under the
                    # −100 attack_innocent penalty so bounty-hunting notorious
                    # traders is a net-positive nudge, not a reputation-farming
                    # treadmill. Tune once Max sets canon.
                    NOTORIOUS_TRADER_KILL_REWARD = 25  # NO-CANON, flagged
                    try:
                        from src.services.personal_reputation_service import (
                            PersonalReputationService,
                        )
                        PersonalReputationService(self.db).adjust_reputation(
                            attacker.id,
                            NOTORIOUS_TRADER_KILL_REWARD,
                            "killed_notorious_trader",
                        )
                        logger.info(
                            "Notorious-trader kill by player %s (%s, notoriety=%s) "
                            "— personal rep %+d applied (NO-CANON magnitude, "
                            "ADR-0074 §10; flagged for Max)",
                            attacker.id, looted_npc.name,
                            looted_npc.notoriety, NOTORIOUS_TRADER_KILL_REWARD,
                        )
                    except Exception as e:
                        logger.error("Failed notorious-trader reward hook: %s", e)

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
                        from src.models.faction import FactionType
                        from src.services.faction_service import apply_faction_rep_delta
                        # Sentinel kills crash Galactic Concord standing
                        # (police-forces.md). The CONCORD FactionType enum
                        # value now exists, so the hook is wired here.
                        #
                        # ⚠️ NO-CANON NUMBER — FLAG FOR MAX: canon states the
                        # standing loss but gives NO numeric magnitude. We
                        # mirror the Federation marshal-kill −250 scale as a
                        # defensible placeholder. The Sentinels are the Nexus
                        # hub-invariant enforcers (a stronger body than the
                        # Federation Police), so the true value may warrant a
                        # HARSHER penalty than −250 once Max sets canon.
                        SENTINEL_KILL_CONCORD_PENALTY = -250  # NO-CANON, flagged
                        apply_faction_rep_delta(
                            self.db,
                            attacker.id,
                            FactionType.CONCORD,
                            SENTINEL_KILL_CONCORD_PENALTY,
                            reason=f"Sentinel kill ({dead_npc.display_name})",
                        )
                        logger.info(
                            "Sentinel kill by player %s (%s) — Galactic Concord "
                            "standing %+d applied (NO-CANON magnitude, mirrors "
                            "Marshal −250; flagged for Max)",
                            attacker.id, dead_npc.display_name,
                            SENTINEL_KILL_CONCORD_PENALTY,
                        )
                elif dead_npc.faction_code == "pirates":
                    # Emergent faction-rep (ADR-0032): "Kill a Pirate or Cabal
                    # NPC | +5 Terran Federation". Routed through the ADR-0032
                    # dispatcher — the single canon entry point for emergent
                    # faction reputation — NOT a raw apply_faction_rep_delta
                    # call, so the trigger table stays the one tuning surface.
                    #
                    # DOUBLE-FIRE SAFE: this is a genuinely NEW hook. Before
                    # this WO no faction-rep was awarded for a pirate kill (the
                    # only faction-rep at this site was the LAW_ENFORCEMENT
                    # penalty branch above — a different action, faction, and
                    # sign). The personal-rep hooks elsewhere in this method
                    # (attack_innocent / defeat_bounty_target / etc.) are the
                    # DISJOINT personal-reputation signal (ADR-0056 N-D1) and
                    # are untouched. The dispatcher is flush-only (it delegates
                    # to apply_faction_rep_delta) and folds into this method's
                    # single commit, exactly like the police-kill hook above.
                    try:
                        from src.services.emergent_reputation_service import (
                            apply_emergent_action,
                        )
                        apply_emergent_action(
                            self.db,
                            attacker,
                            "KILL_PIRATE_NPC",
                            {"sector_id": sector.sector_id},
                        )
                    except Exception as e:
                        logger.error(
                            "Failed KILL_PIRATE_NPC emergent-rep hook: %s", e
                        )

            # Medal dispatch hook (ADR-0028 / medals lane) for a resolved NPC
            # kill. defender_id is NULL on NPC combat logs (no Player behind
            # the ship), so kind="npc" lets the idempotent medals-lane hook
            # decide whether NPC kills count — combat does not pre-judge.
            _dispatch_combat_medals(
                self.db,
                attacker,
                {
                    "victim_id": None,
                    "combat_log_id": combat_log.id,
                    "kind": "npc",
                },
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
            "credits_minted": minted_loot,
            "cargo_looted": combat_result["cargo_stolen"] or {},
        }
        if police_response:
            result_dict["police_response"] = police_response
        return result_dict

    def attack_sector_drones(self, attacker_id: uuid.UUID, sector_id: int) -> Dict[str, Any]:
        """Attack the hostile drones deployed in the player's current sector.

        A 2-turn PvE engagement: the player's ship fights the live Drone rows
        deployed in the sector (Drone.sector_id == sector UUID, status DEPLOYED
        or DAMAGED) that the attacker does NOT own. Each drone is resolved
        against its real per-drone stats (health / attack_power / defense_power)
        via Drone.take_damage. Clearing all hostile drones awards the canon
        destroy_pirate_drones reputation bonus (+10).

        The legacy aggregate-count drone path (sector.drones_present /
        DroneDeployment.drone_count) referenced columns that do not exist on the
        live schema, so this works directly against the real per-drone model.
        """
        # Lock the attacker row to prevent concurrent turn-deduction races
        # (mirrors attack_player / attack_planet).
        attacker = self.db.query(Player).filter(
            Player.id == attacker_id
        ).with_for_update().first()
        if not attacker:
            return {"success": False, "message": "Player not found"}

        # Check if attacker has an active ship
        if not attacker.current_ship:
            return {"success": False, "message": "No active ship selected"}

        # Check if player is in the target sector
        if attacker.current_sector_id != sector_id:
            return {"success": False, "message": "You must be in the sector to attack its drones"}

        # Check if player is docked or landed
        if attacker.is_docked or attacker.is_landed:
            return {"success": False, "message": "Cannot attack while docked at a port or landed on a planet"}

        # Get sector (sector_id is the human-readable number; Drone.sector_id is
        # the sector's UUID, so we resolve through the Sector row)
        sector = self.db.query(Sector).filter(Sector.sector_id == sector_id).first()
        if not sector:
            return {"success": False, "message": "Sector not found"}

        # Find the live hostile drones deployed in this sector. Exclude the
        # attacker's own drones (you cannot attack your own deployment) and any
        # already-destroyed/returning drones. Lock the rows so a concurrent
        # attack or recall cannot double-resolve the same drones.
        target_drones = self.db.query(Drone).filter(
            Drone.sector_id == sector.id,
            Drone.player_id != attacker_id,
            Drone.status.in_([DroneStatus.DEPLOYED.value, DroneStatus.DAMAGED.value]),
            Drone.health > 0,
        ).with_for_update().all()

        if not target_drones:
            return {"success": False, "message": "No hostile drones present in this sector"}

        # Check if attacker has enough turns (canon 2-turn drone engagement)
        turn_cost = 2
        # Lazy ADR-0004 regen before the affordability check / spend.
        _regen_turns(self.db, attacker)
        if attacker.turns < turn_cost:
            return {
                "success": False,
                "message": f"Not enough turns to attack sector drones (need {turn_cost})",
            }

        # Snapshot starting drone total before combat mutates the rows
        starting_drone_count = len(target_drones)

        # Resolve combat against the real drone rows
        combat_result = self._resolve_sector_drone_combat(attacker, sector, target_drones)

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
            defender_id=None,  # No single owning player for sector drones
            rounds=combat_result["rounds"],
            attacker_drones=attacker.defense_drones,
            defender_drones=starting_drone_count,
            attacker_drones_lost=combat_result["attacker_drones_lost"],
            defender_drones_lost=combat_result["defender_drones_lost"],
            attacker_damage_dealt=combat_result["attacker_damage_dealt"],
            defender_damage_dealt=combat_result["defender_damage_dealt"],
            combat_log=json.dumps(combat_result["combat_details"]),
            ended_at=datetime.now()
        )

        self.db.add(combat_log)

        # Apply combat effects to the attacker's ship
        if combat_result["attacker_ship_destroyed"]:
            self._handle_ship_destruction(attacker, None, "drone_combat")

        # Update attacker's carried drone count if any were lost
        if combat_result["attacker_drones_lost"] > 0:
            attacker.defense_drones = max(
                0, attacker.defense_drones - combat_result["attacker_drones_lost"]
            )

        # Deactivate the matching deployment records for fully-destroyed drones
        # so the sector control bookkeeping stays consistent.
        destroyed_drone_ids = combat_result["destroyed_drone_ids"]
        drones_remaining = starting_drone_count - len(destroyed_drone_ids)
        if destroyed_drone_ids:
            self.db.query(DroneDeployment).filter(
                DroneDeployment.drone_id.in_(destroyed_drone_ids),
                DroneDeployment.is_active == True
            ).update(
                {
                    DroneDeployment.is_active: False,
                    DroneDeployment.recalled_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )

        # Award the canon destroy_pirate_drones reputation bonus when the sector
        # is cleared of hostile drones. Defensive: a reputation hiccup must
        # never fail the combat resolution (mirrors the other rep hooks).
        if combat_result["result"] == CombatResult.ATTACKER_VICTORY and destroyed_drone_ids:
            try:
                from src.services.personal_reputation_service import PersonalReputationService
                PersonalReputationService(self.db).adjust_reputation(
                    attacker.id, 10, "destroy_pirate_drones"
                )
            except Exception as e:
                logger.error("Failed destroy_pirate_drones reputation hook: %s", e)

        # Update last_combat timestamp for sector
        sector.last_combat = datetime.now()

        # Commit changes
        self.db.commit()

        return {
            "success": True,
            "message": combat_result["message"],
            "combat_result": combat_result["result"].name,
            "combat_details": combat_result["combat_details"],
            "drones_destroyed": combat_result["defender_drones_lost"],
            "drones_remaining": drones_remaining,
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
        # Lazy ADR-0004 regen before the affordability check / spend.
        _regen_turns(self.db, attacker)
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
        
        # If planet was captured, transfer ownership and deliver capture rewards.
        if combat_result["planet_captured"]:
            # CRT WO-K1a §1.2: settle to the CURRENT (pre-flip) owner BEFORE the ownership swap —
            # realizes pending production onto the planet's stockpiles (the captor inherits them
            # with ownership) and drains the research faucet to the owner who EARNED it. Then flip.
            from src.services.structures import settle
            settle(planet, db=self.db)
            self._transfer_planet_ownership(planet, attacker)
            # Capture rewards (DECISIONS planet-assault-reward-model, Max
            # 2026-06-20): resources-to-captor (primary), ARIA memory (always),
            # faction neg-rep (faction-owned only), find-planet bounty (if any).
            # Fires EXACTLY ONCE per capture: attack_planet resolves combat in a
            # single _resolve_planet_combat call (not an external per-round loop)
            # and reaches this branch once, after the ownership swap is staged and
            # before the single commit below. Best-effort — never breaks combat.
            self._award_planet_capture_rewards(
                attacker, planet, planet_owner, sector, combat_result
            )
        
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
        # Lazy ADR-0004 regen before the affordability check / spend.
        _regen_turns(self.db, attacker)
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

        # Grey-flag PvP status (WO-BL): assaulting a STATION marks the attacker
        # grey for 1 DAY ("station_attack") — while grey, ANY player may attack
        # them penalty-free. Charged for the act of assaulting infrastructure,
        # regardless of capture/outcome (this line is only reached after every
        # guard has passed and the attack has definitively proceeded). MAX rule
        # applied inside set_grey. Best-effort: a grey-flag hiccup never breaks
        # combat resolution. (attack_port is not yet route-wired — port assault
        # returns 501 — but the rail is ready for when it lands.)
        try:
            from src.services.grey_flag_service import (
                GreyFlagService,
                GREY_KIND_STATION_ATTACK,
            )
            GreyFlagService(self.db).set_grey(attacker, GREY_KIND_STATION_ATTACK)
        except Exception as e:
            logger.error("Failed grey-flag hook after port assault: %s", e)

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

        # WO-SR1: out-of-combat shield regen for the idle gap since the last
        # battle. Runs here because combat state is read exactly ONCE per
        # engagement (at the start), so this credits time the ship spent
        # NOT fighting and never fires mid-fight. Hull is untouched.
        self._apply_shield_regen(combat)
        return combat

    def _apply_shield_regen(self, combat: Dict[str, Any]) -> float:
        """Regenerate a ship's shields toward its spec-max for the canonical
        time elapsed since the last regen, advancing the anchor to "now".

        Between-battle (out-of-combat) only — the single caller,
        _ensure_combat_state, runs at the start of each engagement, so the
        credited interval is the gap since the prior battle. Returns the
        number of shield points credited (>= 0); 0 when already at cap, no
        rate, or no positive elapsed time. NEVER touches hull (hull =
        repair-only canon, combat.md:97) and never exceeds max_shields.

        The anchor (SHIELD_REGEN_ANCHOR_KEY) is stored inside the combat
        JSONB as an ISO-8601 UTC timestamp — no new column / migration. On a
        ship that has never had the anchor (legacy / freshly seeded), the
        baseline is set to "now" and NOTHING is credited this read; regen
        accrues from the next read forward. flag_modified for the JSONB write
        is the battle caller's responsibility (the same post-battle commit
        that persists shield/hull attrition); when no battle damage follows,
        the advanced anchor still rides along on that commit. A pure read
        that never commits simply re-derives the same elapsed gap next time —
        idempotent and safe.
        """
        now = datetime.now(timezone.utc)
        anchor_raw = combat.get(self.SHIELD_REGEN_ANCHOR_KEY)

        # First touch: establish the baseline, credit nothing. This prevents a
        # legacy ship (no anchor) from retroactively regenerating from epoch.
        if not anchor_raw:
            combat[self.SHIELD_REGEN_ANCHOR_KEY] = now.isoformat()
            return 0.0

        try:
            anchor = datetime.fromisoformat(anchor_raw)
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            # Corrupt anchor: reset baseline, credit nothing.
            combat[self.SHIELD_REGEN_ANCHOR_KEY] = now.isoformat()
            return 0.0

        rate = combat.get("shield_recharge_rate")
        max_shields = combat.get("max_shields") or 0
        shields = max(0.0, float(combat.get("shields") or 0))

        # Always advance the anchor so a no-op read doesn't bank elapsed time
        # for a later credit when the rate/cap conditions later change.
        combat[self.SHIELD_REGEN_ANCHOR_KEY] = now.isoformat()

        if not rate or rate <= 0 or max_shields <= 0 or shields >= max_shields:
            return 0.0

        elapsed_hours = canonical_hours_since(anchor, now)
        if elapsed_hours <= 0:
            return 0.0

        # First-credit cap: bound a large idle gap (anchor very old) so a
        # long-dormant ship doesn't snap to full as if it had never moved.
        elapsed_hours = min(elapsed_hours, self.SHIELD_REGEN_MAX_CREDIT_HOURS)

        regen = float(rate) * elapsed_hours
        new_shields = min(float(max_shields), shields + regen)
        credited = round(new_shields - shields, 1)
        if credited <= 0:
            return 0.0

        combat["shields"] = round(new_shields, 1)
        return credited

    @staticmethod
    def _resistance_fraction(rating: Any) -> float:
        """Clamp a raw shield_resistance / armor_rating column value to a
        safe damage-reduction fraction in [0.0, 0.9].

        The Ship.shield_resistance / armor_rating columns store a *fraction*
        of incoming damage absorbed before it lands (see ship.py:132-135).
        Defensive bounds:
          - None / non-numeric / negative  -> 0.0 (no reduction; never a
            negative rating that would AMPLIFY damage).
          - Capped at 0.9 so a misconfigured rating can never make a ship
            fully invulnerable (no 100% absorb, no zero-divide downstream).
        """
        try:
            frac = float(rating)
        except (TypeError, ValueError):
            return 0.0
        if frac != frac:  # NaN guard
            return 0.0
        if frac <= 0.0:
            return 0.0
        return min(frac, 0.9)

    @staticmethod
    def _apply_weapon_damage(
        damage: float,
        weapon: Dict[str, Any],
        target_combat: Dict[str, Any],
        shield_resistance: float = 0.0,
        armor_rating: float = 0.0,
    ) -> Dict[str, Any]:
        """Apply one weapon hit per the canon damage stack
        (combat-resolver.md "Damage stack — order of operations"):

            shield_hit = min(damage, shields) * weapon.shield_effectiveness
                                              * (1 - shield_resistance)
            residual   = damage - min(damage, shields)
            hull_hit   = residual * weapon.hull_effectiveness
                                  * (1 - armor_rating)
            critical   = (RNG < 0.05) ? hull_hit * 0.5 : 0

        Shields absorb first, the residual bleeds into hull, and a 5%
        critical adds half the hull hit again. The defender ship's
        ``shield_resistance`` reduces the shield component of the hit and
        ``armor_rating`` reduces the hull component — both are *fractions*
        of damage absorbed (Ship.shield_resistance / armor_rating columns,
        ship.py:132-135). The critical bonus is computed from the
        armor-reduced hull hit so armor protects against crits too.

        Both ratings are passed through ``_resistance_fraction`` by the
        caller (clamped to [0.0, 0.9]) so a missing / negative / oversized
        rating can never amplify damage, fully nullify a hit, or divide by
        zero. With both ratings 0.0 (the seeded default), this reduces
        exactly to the prior shields-first stack — existing behavior is
        preserved.

        The canon defense-drones passive (-5% per 10 drones) is
        structurally moot in this resolver: drones are a discrete screen
        layer that must be fully destroyed before any ship hit lands, so
        the defender's drone count is always 0 when this runs.

        Floors: shields and hull are never written below 0. Mutates
        target_combat in place and returns the hit summary.
        """
        shields = max(0.0, float(target_combat.get("shields") or 0))
        hull = max(0.0, float(target_combat.get("hull") or 0))

        # Defensive re-clamp (callers already clamp, but never trust a raw
        # column value to be in range here either).
        shield_resistance = CombatService._resistance_fraction(shield_resistance)
        armor_rating = CombatService._resistance_fraction(armor_rating)

        absorbed = min(damage, shields)
        shield_hit = absorbed * weapon["shield_effectiveness"] * (1.0 - shield_resistance)
        residual = damage - absorbed
        hull_hit = residual * weapon["hull_effectiveness"] * (1.0 - armor_rating)

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

        # WO-BC tractor escape-suppression (single-shot MVP). CANON combat.md:162
        # — a tractor-locked target "cannot succeed at flee actions while the lock
        # holds"; tractor does NO damage (combat.md:167). If the ATTACKER's ship
        # carries the tractor_beam equipment in weapon_mode "tractor", the DEFENDER's
        # escape chance is forced to 0 for this single combat resolution. The full
        # multi-round 3-round lock / speed-debuff stacking / counterplay is DEFERRED
        # (DECISIONS.md tractor-weapon-mode-scope; orchestrator option (a)). Read via
        # the canonical equipment-effects merge; defensive (a None ship or a missing
        # equipment_slots JSONB simply yields no tractor, never a crash — combat must
        # never break on a missing accessory). Damage/outcome are UNCHANGED — the only
        # effect is zeroing the defender's flee roll.
        attacker_has_tractor = False
        try:
            if attacker_ship is not None:
                attacker_effects = ShipUpgradeService.get_equipment_effects(attacker_ship)
                attacker_has_tractor = attacker_effects.get("weapon_mode") == "tractor"
                # WO-AF tow mutual-exclusion (ships.md:365): the Tractor Beam
                # slot is mutually exclusive with weapon-mode firing while a tow
                # is active — a hauler cannot tow AND tractor-attack at once. If
                # the attacker is actively towing a ship, the tractor weapon mode
                # is unavailable: the escape-suppression lock does NOT apply.
                if attacker_has_tractor and getattr(attacker_ship, "tow_state", None):
                    from src.services.tow_service import TowService
                    if TowService(self.db).is_actively_towing(attacker_ship):
                        attacker_has_tractor = False
        except Exception as e:  # never let an equipment-read break combat
            logger.error("Tractor equipment read failed (continuing without lock): %s", e)
            attacker_has_tractor = False

        if defender is not None:
            defender_ship = defender.current_ship
            defender_name = defender.username
            defender_drones = defender.defense_drones
            defender_bonuses = RankingService.get_rank_bonuses(defender.military_rank)
            # WO-CG: fold the defender's summed, capped medal combat_damage bonus
            # into their return-fire damage multiplier alongside the rank term.
            defender_medal_pct = _medal_combat_damage_bonus(self.db, defender)
            defender_damage_mult = 1.0 + (
                (defender_bonuses["combat_damage_bonus_percent"] + defender_medal_pct) / 100.0
            )
        else:
            if defender_ship is None:
                raise ValueError("NPC combat requires a defender_ship")
            defender_name = defender_ship.name
            defender_drones = 0
            defender_damage_mult = 1.0

        # Get rank combat bonus for the attacker
        attacker_bonuses = RankingService.get_rank_bonuses(attacker.military_rank)
        # WO-CG: fold the attacker's summed, capped medal combat_damage bonus into
        # the damage multiplier alongside the rank term (≤ +3% from all medals).
        attacker_medal_pct = _medal_combat_damage_bonus(self.db, attacker)
        attacker_damage_mult = 1.0 + (
            (attacker_bonuses["combat_damage_bonus_percent"] + attacker_medal_pct) / 100.0
        )

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
                        hit = self._apply_weapon_damage(
                            damage, atk_weapon, defender_combat,
                            shield_resistance=self._resistance_fraction(
                                getattr(defender_ship, "shield_resistance", 0.0)
                            ),
                            armor_rating=self._resistance_fraction(
                                getattr(defender_ship, "armor_rating", 0.0)
                            ),
                        )
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
                        hit = self._apply_weapon_damage(
                            damage, def_weapon, attacker_combat,
                            shield_resistance=self._resistance_fraction(
                                getattr(attacker_ship, "shield_resistance", 0.0)
                            ),
                            armor_rating=self._resistance_fraction(
                                getattr(attacker_ship, "armor_rating", 0.0)
                            ),
                        )
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
                    # WO-BC tractor lock: a tractor-equipped attacker denies the
                    # defender's escape (canon combat.md:162). Force the chance to 0
                    # so the flee roll can never succeed this resolution. Applies to
                    # BOTH attack_player and attack_npc_ship — the defender here is the
                    # non-attacker side regardless of player/NPC type (NPC defenders
                    # already skip the roll via the `defender is not None` guard, so in
                    # practice this bites the PvP defender; harmless and correct either
                    # way). Damage/outcome otherwise unchanged.
                    if attacker_has_tractor:
                        escape_pct = 0
                    if escape_pct > 0 and random.randint(1, 100) <= escape_pct:
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
        # damaged after the fight even when nobody dies. Shields recover only
        # OUT OF COMBAT via the advance-on-read regen in _ensure_combat_state
        # (WO-SR1), which credits the idle gap before the NEXT battle and also
        # advanced each side's regen anchor to "now" when this battle opened —
        # that anchor advance rides along on this same JSONB write. Hull
        # restores only via ShipService.repair_ship at repair facilities. The
        # dicts were mutated in place, so flag_modified is required for the
        # JSONB write; the caller's single post-battle commit (attack_player /
        # attack_npc_ship) lands both.
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

    def _resolve_sector_drone_combat(
        self, attacker: Player, sector: Sector, target_drones: List[Drone]
    ) -> Dict[str, Any]:
        """Resolve combat between a player's ship and the live hostile Drone
        rows deployed in a sector.

        Operates on the real per-drone model: each Drone carries its own
        health / attack_power / defense_power, and Drone.take_damage tracks
        damage and flips status to DESTROYED at 0 HP. The attacker's ship
        attack power comes from the shared _calculate_attack_power helper; the
        ship's hull is read/mutated through the same combat JSONB shape the
        ship-vs-ship resolver uses.
        """
        attacker_ship = attacker.current_ship
        attacker_carried_drones = attacker.defense_drones

        # Attacker firepower per round (shared helper — ship type + bonuses +
        # carried drones + maintenance multiplier)
        attacker_attack = self._calculate_attack_power(attacker_ship, attacker_carried_drones)

        # Seed/read the attacker ship's canonical combat state (shields,
        # max_shields, hull, max_hull from its ShipSpecification) — the same
        # path the ship-vs-ship resolver uses, so a ship without persisted
        # hull/shields gets its real spec values rather than a flat default.
        # _apply_weapon_damage mutates this dict in place; we flag_modified
        # the Ship.combat JSONB after the battle.
        attacker_combat = self._ensure_combat_state(attacker_ship)

        # Drone return-fire is routed through the canonical damage stack
        # (shields absorb first, residual bleeds to hull). Drones are not ships
        # and have no SHIP_DEFAULT_WEAPONS entry, so they fire a neutral kinetic
        # profile (laser: shields 0.8 / hull 1.0), mirroring the default ship
        # weapon rather than inventing drone-specific weapon constants.
        drone_weapon = self.WEAPON_TYPES["laser"]

        round_number = 0
        attacker_drones_lost = 0
        defender_drones_lost = 0
        attacker_damage_dealt = 0
        defender_damage_dealt = 0
        attacker_ship_destroyed = False
        destroyed_drone_ids: List[uuid.UUID] = []
        combat_details = []

        # Live working set of drones still fighting
        live_drones = list(target_drones)

        combat_details.append({
            "round": 0,
            "action": "engagement_start",
            "message": (
                f"{attacker.username} engages {len(live_drones)} hostile drone(s) "
                f"in Sector {sector.sector_id}"
            ),
        })

        # Combat continues until one side is defeated or the round cap is hit
        while not attacker_ship_destroyed and live_drones and round_number < 8:
            round_number += 1

            combat_details.append({
                "round": round_number,
                "message": f"Combat Round {round_number}",
            })

            # --- Attacker's turn: damage spread across one target drone ---
            # The attacker focuses fire on the first live drone each round,
            # applying ship attack power reduced by that drone's defense.
            target = live_drones[0]
            drone_defense = max(0, target.defense_power or 0)
            damage = max(1, int(attacker_attack) - drone_defense)
            attacker_damage_dealt += damage
            destroyed = target.take_damage(damage)
            if destroyed:
                defender_drones_lost += 1
                destroyed_drone_ids.append(target.id)
                live_drones.remove(target)
                combat_details.append({
                    "round": round_number,
                    "actor": "attacker",
                    "action": "drone_destroyed",
                    "message": (
                        f"{attacker.username}'s ship destroyed a hostile drone "
                        f"({target.drone_type}) for {damage} damage"
                    ),
                })
            else:
                combat_details.append({
                    "round": round_number,
                    "actor": "attacker",
                    "action": "drone_attack",
                    "message": (
                        f"{attacker.username}'s ship hit a hostile drone "
                        f"({target.drone_type}) for {damage} damage "
                        f"({target.health}/{target.max_health} HP remaining)"
                    ),
                })

            # Combat over if all drones cleared this round
            if not live_drones:
                break

            # --- Defenders' turn: surviving drones return fire on the ship ---
            # Aggregate raw drone damage this round (each drone's own
            # attack_power, lightly randomised — no invented base numbers),
            # then route it through the canonical shields-first damage stack so
            # a shielded ship is protected exactly as in ship-vs-ship combat.
            round_drone_damage = 0
            for drone in live_drones:
                base = max(1, drone.attack_power or 0)
                hit = random.randint(max(1, base // 2), base)
                round_drone_damage += hit

            defender_damage_dealt += round_drone_damage
            hit_result = self._apply_weapon_damage(
                round_drone_damage, drone_weapon, attacker_combat,
                shield_resistance=self._resistance_fraction(
                    getattr(attacker_ship, "shield_resistance", 0.0)
                ),
                armor_rating=self._resistance_fraction(
                    getattr(attacker_ship, "armor_rating", 0.0)
                ),
            )

            if hit_result["destroyed"]:
                attacker_ship_destroyed = True
                combat_details.append({
                    "round": round_number,
                    "actor": "defender",
                    "action": "ship_destroyed",
                    "message": (
                        f"Hostile drones dealt {round_drone_damage} damage "
                        f"({hit_result['shield_damage']} to shields, "
                        f"{hit_result['hull_damage']} to hull) and destroyed "
                        f"{attacker.username}'s ship, forcing ejection"
                    ),
                })
            else:
                combat_details.append({
                    "round": round_number,
                    "actor": "defender",
                    "action": "ship_attack",
                    "message": (
                        f"Hostile drones hit {attacker.username}'s ship for "
                        f"{round_drone_damage} damage "
                        f"({hit_result['shields_remaining']} shields / "
                        f"{hit_result['hull_remaining']} hull remaining)"
                    ),
                })

        # Persist the shield/hull depletion back to the ship combat JSONB so
        # damage carries between engagements (only when the ship survived —
        # destruction is handled by the caller via _handle_ship_destruction).
        # _apply_weapon_damage mutated attacker_combat (== attacker_ship.combat)
        # in place; flag_modified ensures the JSONB write is persisted.
        if attacker_ship and not attacker_ship_destroyed:
            flag_modified(attacker_ship, "combat")

        # Determine result
        if attacker_ship_destroyed:
            result = CombatResult.DEFENDER_VICTORY
            message = f"Sector drones defeated {attacker.username}"
        elif not live_drones:
            result = CombatResult.ATTACKER_VICTORY
            message = f"{attacker.username} destroyed all hostile drones in the sector"
        else:
            result = CombatResult.DRAW
            message = "Combat ended in a stalemate — surviving drones remain"

        combat_details.append({
            "round": round_number,
            "action": "combat_end",
            "result": result.name,
            "message": message,
        })

        return {
            "result": result,
            "message": message,
            "rounds": round_number,
            "attacker_drones_lost": attacker_drones_lost,
            "defender_drones_lost": defender_drones_lost,
            "attacker_damage_dealt": attacker_damage_dealt,
            "defender_damage_dealt": defender_damage_dealt,
            "attacker_ship_destroyed": attacker_ship_destroyed,
            "destroyed_drone_ids": destroyed_drone_ids,
            "combat_details": combat_details,
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

        # Calculate planetary defense reduction (shields, defense level, generators,
        # and the citadel-built turret_network / orbital_platform contributions — WO-CT1).
        planetary_def = self._calculate_planetary_defense_reduction(planet)
        damage_reduction = planetary_def["damage_reduction"]
        remaining_shield_hp = planetary_def["shield_hp"]
        # Turret networks are automated point-defense: they shred attacking drones
        # every round (CANON: each turret destroys 1-3 drones/round). 0 for legacy
        # planets / planets without turret networks (no behaviour change).
        anti_drone_kills_per_round = planetary_def.get("anti_drone_kills_per_round", 0)

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

            # Automated turret-network point-defense (WO-CT1): turret networks fire
            # every round independent of the planet's main-weapon hit roll, shredding
            # the attacker's drone swarm. CANON (defense.md): each turret destroys
            # 1-3 drones/round; the kill band scales with the turret_network count.
            # No turret networks -> ceiling is 0 -> this block is a no-op (legacy safe).
            if anti_drone_kills_per_round > 0 and attacker_drones > 0:
                turret_kills = random.randint(1, min(anti_drone_kills_per_round, attacker_drones))
                attacker_drones -= turret_kills
                attacker_drones_lost += turret_kills
                combat_details.append({
                    "round": round_number,
                    "actor": "defender",
                    "action": "turret_defense",
                    "message": f"Automated turret network destroyed {turret_kills} of {attacker.username}'s drones",
                    "drones_destroyed": turret_kills
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
        """Resolve combat between an attacking ship+drone-swarm and a station.

        WO-BP-a — STATION-DEFENSE KERNEL (gated). Max: "stations are really
        really powerful" = DEFENSE + DETERRENCE, NOT capture. A station is a
        FORMIDABLE fixed installation: a huge regenerating shield_pool over a
        deep hull_armor, plus STRONG defensive fire and dedicated point-defense
        that SHRED the attacker's drone swarm every round. A drone count that
        would wreck a ship gets ground to nothing here; the station repels the
        assault decisively and survives.

        Defense stats are read from the EXISTING ``defenses`` JSONB (no new
        columns / no migration); ``.get`` defaults match the additive JSONB
        default in models/station.py so legacy rows are equally formidable.

        GATING: ``port_captured`` is still *computed* (so a future, Max-blessed
        takeover design can build on a true value rather than a hard-coded
        lie), but capture requires grinding ``hull_armor`` (default 5000) to
        zero, and a per-round damage CEILING (150) combined with the 8-round
        limit caps total reachable hull damage at ~1200 << 5000 — so capture is
        mathematically unreachable here regardless of drone count. The ONLY
        caller, attack_port, is DISABLED/unwired; this resolver does not
        transfer ownership and is unreachable from any route. Making the
        defense formidable + fixing the AttributeError crash must NOT make a
        station capturable or the path live — and it does not.
        """
        # --- Attacker ---
        attacker_ship = attacker.current_ship
        attacker_drones = attacker.defense_drones or 0
        attacker_attack = self._calculate_attack_power(attacker_ship, attacker_drones)

        # --- Station defenses (from the existing `defenses` JSONB) ---
        # Magnitudes are deliberately FORMIDABLE (NO-CANON, orchestrator-bless
        # pending). The .get defaults match the additive JSONB default in
        # models/station.py so legacy rows (no new keys) are equally powerful.
        defenses = port.defenses or {}
        hull_armor = int(defenses.get("hull_armor", 5000) or 0)
        shield_pool = int(defenses.get("shield_pool", 4000) or 0)
        shield_max = shield_pool
        shield_regen = int(defenses.get("shield_regen", 200) or 0)
        defensive_fire = int(defenses.get("defensive_fire", 120) or 0)
        point_defense = int(defenses.get("point_defense_rating", 30) or 0)
        # Legacy fields still flavour the station's offensive output.
        station_drones = int(defenses.get("defense_drones", 0) or 0)
        patrol_ships = int(defenses.get("patrol_ships", 0) or 0)

        # Per-round damage CEILING the attacker can ever deliver to the station
        # (after shields). This is the deterrent linchpin: even an absurd drone
        # swarm cannot grind a 5000-hull station to zero within the 8-round
        # limit, because a single round can chip at most this much hull. With
        # the ceiling at 150 and an 8-round cap, max theoretical hull damage is
        # ~1200 << 5000 — capture is mathematically unreachable here. Computed,
        # never tripped; the only caller (attack_port) is disabled regardless.
        per_round_damage_ceiling = 150

        # Station's combined anti-swarm output per round. Strong enough to gut a
        # large swarm in a couple of rounds.
        station_fire_power = defensive_fire + station_drones * 3 + patrol_ships * 8

        # Track combat details (contract preserved for the dormant caller).
        round_number = 0
        attacker_drones_lost = 0
        port_damage = 0           # cumulative damage that has reached hull_armor
        attacker_ship_destroyed = False
        port_captured = False
        combat_details = []

        # Combat continues until the attacker is repelled/destroyed, the
        # (effectively unreachable) capture threshold is crossed, or the round
        # limit forces the attacker to withdraw.
        while not attacker_ship_destroyed and not port_captured:
            round_number += 1

            combat_details.append({
                "round": round_number,
                "message": f"Combat Round {round_number}"
            })

            # --- Station fires FIRST: a fixed fortress engages on contact, so
            # the swarm is attrited BEFORE it can land its volley. SHRED the
            # drone swarm; once the screen is gone, maul the exposed ship. ---
            if attacker_drones > 0:
                # Defensive fire converts to drones killed; point-defense adds a
                # flat anti-swarm bonus. A station out-guns any realistic swarm.
                drones_destroyed = min(
                    attacker_drones,
                    max(1, station_fire_power // 4) + point_defense
                )
                attacker_drones -= drones_destroyed
                attacker_drones_lost += drones_destroyed
                combat_details.append({
                    "round": round_number,
                    "actor": "defender",
                    "action": "drone_attack",
                    "message": (
                        f"Station defensive fire SHREDDED {drones_destroyed} of "
                        f"{attacker.username}'s drones ({attacker_drones} remain)"
                    ),
                    "drones_destroyed": drones_destroyed
                })
            else:
                # Swarm gone — defensive fire turns on the hull of the ship.
                # With no drone screen the attacker is critically exposed.
                damage = defensive_fire // 2 + station_drones + patrol_ships * 3
                ship_destruction_chance = min(0.95, damage / 60)
                if random.random() < ship_destruction_chance:
                    attacker_ship_destroyed = True
                    combat_details.append({
                        "round": round_number,
                        "actor": "defender",
                        "action": "ship_destroyed",
                        "message": (
                            f"With its drone screen gone, station fire critically "
                            f"crippled {attacker.username}'s ship, forcing ejection"
                        )
                    })
                else:
                    combat_details.append({
                        "round": round_number,
                        "actor": "defender",
                        "action": "ship_attack",
                        "message": f"Station fire raked {attacker.username}'s unscreened ship for {damage} damage"
                    })

            if attacker_ship_destroyed:
                break

            # --- Attacker's turn: the SURVIVING swarm chips at shields, then
            # hull. Per-round damage is hard-capped (per_round_damage_ceiling)
            # AND scales with the swarm the station hasn't shredded yet, so the
            # attacker's output decays fast as drones are lost. ---
            drone_factor = attacker_drones / max(1, (attacker.defense_drones or 1))
            raw = int(attacker_attack * (0.3 + 0.7 * drone_factor))
            incoming = max(0, min(per_round_damage_ceiling, raw))

            absorbed = min(shield_pool, incoming)
            shield_pool -= absorbed
            hull_hit = incoming - absorbed
            if hull_hit > 0:
                hull_armor = max(0, hull_armor - hull_hit)
                port_damage += hull_hit

            if incoming > 0:
                combat_details.append({
                    "round": round_number,
                    "actor": "attacker",
                    "action": "port_attack",
                    "message": (
                        f"{attacker.username}'s assault hit the station for {incoming} "
                        f"(shields absorbed {absorbed}, hull took {hull_hit}); "
                        f"shields {shield_pool}/{shield_max}, hull {hull_armor}"
                    ),
                    "damage": incoming
                })
            else:
                combat_details.append({
                    "round": round_number,
                    "actor": "attacker",
                    "action": "miss",
                    "message": f"{attacker.username}'s drone swarm is too depleted to scratch the station"
                })

            # Capture requires the hull ground fully to zero — mathematically
            # unreachable within the round limit given the per-round ceiling.
            # Computed (so a future Max-blessed takeover can build on a true
            # value), never tripped here; the caller (attack_port) is disabled.
            if hull_armor <= 0:
                port_captured = True
                combat_details.append({
                    "round": round_number,
                    "actor": "attacker",
                    "action": "port_captured",
                    "message": f"{attacker.username} has overcome the station's structure"
                })
                break

            # Shields regenerate — a sustained siege barely dents the station.
            if shield_pool < shield_max:
                shield_pool = min(shield_max, shield_pool + shield_regen)

            # Round limit: the attacker is forced to withdraw (station repels).
            if round_number >= 8:
                combat_details.append({
                    "round": round_number,
                    "action": "stalemate",
                    "message": "The station's defenses hold; the attacker withdraws after 8 rounds"
                })
                break

        # --- Determine result ---
        # Any realistic assault ends here: the swarm is shredded and the
        # attacker is either destroyed or driven off — the station is repelled
        # decisively and is NOT captured.
        if port_captured:
            result = CombatResult.ATTACKER_VICTORY
            message = f"{attacker.username} captured station {port.name}"
        elif attacker_ship_destroyed:
            result = CombatResult.DEFENDER_VICTORY
            message = f"Station {port.name} destroyed {attacker.username}'s ship and repelled the assault"
        else:
            # Withdrawal after the round limit with the station intact is a
            # defender win — the station held and the attacker fled.
            result = CombatResult.DEFENDER_VICTORY
            message = f"Station {port.name} repelled {attacker.username}'s assault"

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
            "defender_drones_lost": 0,  # the station's drones are a fire stat, not a depletable pool here
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

    def _read_defense_buildings(self, planet: Planet) -> Dict[str, int]:
        """Read the citadel-built defense buildings off the planet's active_events JSONB.

        Mirrors citadel_service._get_defense_buildings: the operational building
        counts live under ``active_events["defense_buildings"]`` as a
        ``{building_type: count}`` mapping. This combat-side reader is fully
        defensive so a malformed or missing JSONB never crashes a fight:

          * active_events absent / None / not a dict (legacy list form) -> {}
          * defense_buildings absent / not a dict                        -> {}
          * individual counts that are non-int or negative                -> coerced to 0

        Legacy planets that pre-date the defense_buildings sub-dict therefore
        contribute exactly zero from this path (treated as 0 — no behaviour change).
        """
        events = getattr(planet, "active_events", None)
        if not isinstance(events, dict):
            return {}
        raw = events.get("defense_buildings", {})
        if not isinstance(raw, dict):
            return {}
        clean: Dict[str, int] = {}
        for btype, count in raw.items():
            try:
                n = int(count)
            except (TypeError, ValueError):
                n = 0
            clean[str(btype)] = max(0, n)
        return clean

    def _calculate_planetary_defense_reduction(self, planet: Planet) -> Dict[str, Any]:
        """Calculate how much planetary defenses reduce incoming attack damage.

        Reads the planet's defense_level (0-10), shields, defense_shields (shield
        generator level), AND the citadel-built defense_buildings stored in the
        active_events JSONB (turret_network + orbital_platform counts) to produce
        a damage-reduction factor, a shield HP pool that must be depleted before
        hull damage is dealt, and a per-round anti-drone kill contribution.

        WO-CT1: until this, defense_buildings were dead wiring — players could
        build turret networks and orbital platforms and they had ZERO combat
        effect. This folds them in alongside the existing defense_level / shield
        terms so built defenses actually defend.

        Returns:
            Dict with:
                damage_reduction: float 0.0-0.9 — multiplicative reduction on
                    incoming damage (e.g. 0.35 means 35% less damage).
                shield_hp: int — flat shield hit-points (shield generators +
                    orbital-platform armour) that must be burned through before
                    planet hull takes damage.
                anti_drone_kills_per_round: int — max attacking drones the turret
                    networks shred each round (turret_network count -> kill band).
                description: str — human-readable summary.
        """
        defense_level = getattr(planet, "defense_level", 0) or 0
        shield_gen_level = getattr(planet, "defense_shields", 0) or 0
        shields = getattr(planet, "shields", 0) or 0

        # Citadel-built defense buildings (JSONB, defensive read — never crashes).
        buildings = self._read_defense_buildings(planet)
        turret_networks = buildings.get("turret_network", 0)
        orbital_platforms = buildings.get("orbital_platform", 0)

        # Colony-specialization defense multiplier (ADR-0087): Military planets get
        # +50% effective defense (×1.5); Research/Agricultural are softer (×0.8/0.9);
        # Balanced ×1.1. Scales the damage-reduction, the shield HP pool, and the
        # building contributions — the planet's whole defensive contribution.
        from src.services.planetary_service import SPECIALIZATION_BONUSES
        spec = getattr(planet, "specialization", None)
        defense_mult = SPECIALIZATION_BONUSES.get(spec, {}).get("defense", 1.0) if spec else 1.0

        # Each defense_level reduces damage by 5%, capped at 50% (level 10)
        level_reduction = min(defense_level * 0.05, 0.50)

        # Shield generators add a flat shield HP pool (500 HP per generator level,
        # plus any existing shield value on the planet), scaled by specialization.
        shield_hp_base = (shield_gen_level * 500) + (shields * 100)

        # Total damage_reduction also includes a small bonus from shield generators
        # (each gen level adds 4% reduction, up to 40% at level 10)
        gen_reduction = min(shield_gen_level * 0.04, 0.40)

        # --- WO-CT1: defense-building contributions ----------------------------
        # NO-CANON per-round magnitudes (orchestrator to bless): chosen CONSERVATIVE
        # and on the resolver's existing toy scale. defense.md gives orbital base
        # damage 500/burst and turrets 1-3 drone-kills/round, but the resolver runs
        # on a raw-damage scale of 1-5/round with capture at planet_defense_level
        # (max 10) — injecting raw 500 damage would end every fight in round 1, so
        # orbital platforms contribute MITIGATION + ARMOUR (shield HP) here rather
        # than raw burst damage. The full off-scale orbital combat model (500-1500
        # burst, ship-class multipliers, 2-sector range phase, platform health,
        # EMP suppression) is a larger, separately-scoped redesign left for Max.
        #
        #   turret_network    -> +3% damage reduction each (cap +18% over the band)
        #   orbital_platform   -> +6% damage reduction each (cap +18%)
        #                         + 250 shield HP each (armoured installation)
        turret_reduction = min(turret_networks * 0.03, 0.18)
        orbital_reduction = min(orbital_platforms * 0.06, 0.18)
        building_reduction = turret_reduction + orbital_reduction
        orbital_shield_hp = orbital_platforms * 250

        # turret anti-drone kills: CANON (defense.md "each turret destroys 1-3
        # drones/round"). The per-round CEILING the resolver may apply, scaled by
        # the network count (the actual count rolled is randint(1, ceiling) in the
        # counterattack so a single turret still respects the 1-3 band).
        anti_drone_kills_per_round = min(turret_networks * 3, 18)
        # ----------------------------------------------------------------------

        shield_hp = int((shield_hp_base + orbital_shield_hp) * defense_mult)

        # Apply the specialization multiplier to the combined reduction, then cap
        # at 0.9 so planets are never invincible.
        damage_reduction = min(
            (level_reduction + gen_reduction + building_reduction) * defense_mult,
            0.90,
        )

        parts = []
        if defense_level > 0:
            parts.append(f"Level {defense_level} defenses ({level_reduction:.0%} reduction)")
        if shield_gen_level > 0:
            parts.append(f"Level {shield_gen_level} shield generators ({gen_reduction:.0%} reduction, {shield_hp} shield HP)")
        if turret_networks > 0:
            parts.append(f"{turret_networks} turret network(s) ({turret_reduction:.0%} reduction, {anti_drone_kills_per_round} drone-kills/round)")
        if orbital_platforms > 0:
            parts.append(f"{orbital_platforms} orbital platform(s) ({orbital_reduction:.0%} reduction, {orbital_shield_hp} armour HP)")
        description = " + ".join(parts) if parts else "No planetary defenses"

        return {
            "damage_reduction": round(damage_reduction, 2),
            "shield_hp": shield_hp,
            "anti_drone_kills_per_round": anti_drone_kills_per_round,
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

    # NOTE (WO-BL): the dead combat-driven _set_suspect / _set_wanted static
    # helpers were REMOVED here. They were the never-wired (zero call sites)
    # combat auto-setters for is_suspect / is_wanted — wrong triggers (attack-
    # innocent / escape-pod / rep-threshold are police *engagement* triggers, not
    # the canon Suspect/Wanted lifecycle), no expiry, and a canon conflict (see
    # the deferred note in attack_player + DECISIONS.md "combat-suspect-wanted-
    # triggers"). The GREY-FLAG system (grey_flag_service) now carries the
    # combat-aggression consequence with the correct expiring open-season
    # semantics. The is_suspect / is_wanted COLUMNS remain in place (untouched)
    # for the canon-correct cargo-wreck / stolen-ship triggers when they land.

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

    def _resolve_planet_owning_faction(self, planet: Planet):
        """Resolve the FACTION that owns ``planet``, or None.

        DECISIONS planet-assault-reward-model conditional (c): the capture
        neg-rep penalty fires ONLY against a faction-owning planet. The Planet
        model currently has NO faction-owner field — ownership is expressed only
        via the ``player_planets`` join table (human players) or "unowned"
        (``planet.owner`` empty). There is therefore no faction-owner signal to
        read, so this returns None today and the (c) penalty branch never fires.

        Isolated here so that the day a planet faction-owner field lands, the
        faction is resolved in ONE place and the whole conditional activates
        without touching the reward orchestration. Returns a ``FactionType`` or
        None. Never raises.
        """
        # No faction-owner field exists on Planet yet (see docstring). When one
        # lands (e.g. planet.faction_code / planet.npc_owner_id), map it to a
        # FactionType here and return it.
        return None

    def _award_planet_capture_rewards(
        self,
        attacker: Player,
        planet: Planet,
        previous_owner: Optional[Player],
        sector: Optional[Sector],
        combat_result: Dict[str, Any],
    ) -> None:
        """Deliver the rewards for a successful planet capture (fires ONCE).

        Per the Max ruling (DECISIONS planet-assault-reward-model, 2026-06-20):
          (a) RESOURCES (PRIMARY): the planet's stored + producing resources
              transfer to the captor (NOT razed). Stored stockpiles already
              transfer implicitly with ownership (they are columns on the planet
              the captor now owns); here we additionally REALIZE pending
              production up to now so the captor receives the full, current
              stockpile rather than a stale snapshot. Idempotent (the production
              accrual is anchored on planet.last_production).
          (b) ARIA MEMORY (ALWAYS): record a combat/event memory — ARIA learns
              from everything the player does.
          (c) FACTION NEG-REP (CONDITIONAL): only if the planet is faction-owned,
              apply a negative rep delta vs the owning faction. No faction-owner
              field exists on Planet today, so this never fires yet (the branch
              is wired and correct for the moment the schema lands).
          (d) FIND-PLANET BOUNTY (CONDITIONAL): only if such a bounty exists, pay
              it. No find-planet bounty schema exists (bounties are player-kill
              only), so this is a no-op today.

        Called exactly once per capture, after _transfer_planet_ownership and
        before attack_planet's single commit. Each leg is independently
        best-effort: a failure in one never aborts the others or the combat
        commit. The CALLER owns the commit (this method only stages mutations).
        """
        # --- (a) RESOURCES (PRIMARY) — already realized by the pre-transfer settle() (CRT WO-K1a
        # §1.2): production was accrued onto the planet's stockpiles (planet columns) BEFORE the
        # ownership swap, so the captor inherits the up-to-date totals; the research faucet drained
        # to the pre-flip owner who earned it. Nothing to realize here.

        # --- (b) ARIA MEMORY (ALWAYS) — record the capture as a combat memory.
        # combat_service is sync; use the sync ARIA recorder (flush-free, dedup
        # by content hash — which also guards against a double memory if this
        # method were ever reached twice for the same capture).
        try:
            from src.services.aria_personal_intelligence_service import (
                get_aria_intelligence_service,
            )
            attacker_ship = attacker.current_ship
            planet_label = getattr(planet, "custom_name", None) or \
                getattr(planet, "auto_name", None) or planet.name
            get_aria_intelligence_service().record_combat_memory_sync(
                str(attacker.id),
                {
                    "event": "planet_captured",
                    "outcome": "victory",
                    "opponent_name": planet_label,
                    "sector_id": sector.sector_id if sector else None,
                    "attacker_ship": attacker_ship.type.value if attacker_ship else None,
                    "defender_ship": None,
                    "planet_id": str(planet.id),
                    "previous_owner_id": str(previous_owner.id) if previous_owner else None,
                },
                self.db,
            )
        except Exception as e:
            logger.error(
                "Planet-capture ARIA memory hook failed for player %s: %s",
                attacker.id, e,
            )

        # --- (c) FACTION NEG-REP (CONDITIONAL) — only when faction-owned.
        # Resolves to None today (no planet faction-owner field), so this is a
        # no-op until the schema lands. Routed through the emergent_reputation
        # module (the single tuning surface; magnitude is NO-CANON −50).
        try:
            owning_faction = self._resolve_planet_owning_faction(planet)
            if owning_faction is not None:
                from src.services.emergent_reputation_service import (
                    apply_planet_capture_faction_penalty,
                )
                apply_planet_capture_faction_penalty(
                    self.db,
                    attacker,
                    owning_faction,
                    {"sector_id": sector.sector_id if sector else None},
                )
        except Exception as e:
            logger.error(
                "Planet-capture faction neg-rep hook failed for player %s: %s",
                attacker.id, e,
            )

        # --- (d) FIND-PLANET BOUNTY (CONDITIONAL) — pay only if one exists.
        # The bounty system is player-kill only (no planet-indexed bounty schema:
        # no planet bounty table, no collect_planet_bounty). There is no
        # find-planet bounty to collect, so this is a documented no-op. When a
        # planet-bounty schema lands, the payout call belongs here.

    def _transfer_port_ownership(self, port: Station, new_owner: Player) -> None:
        """Transfer ownership of a port to a new player."""
        port.owner_id = new_owner.id