"""
Ship Upgrade Service
Handles ship upgrades (engine, cargo, shields, etc.) and equipment installation.
"""

import logging
import random
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.ship import Ship, ShipType, ShipSpecification, UpgradeType

logger = logging.getLogger(__name__)


# SHIP-MODS WO-SM-3 STEP 4 — the four EQUIPMENT-FAMILY module classes whose
# legacy effects are consumed out of the equipment_slots JSONB (by their EXISTING
# consumers), NOT from a scalar Ship column. Their effects ARE baked/tracked in
# Ship.modules["_baked"] by _apply_module_effects, but persisting them into the
# equipment_slots key each consumer reads is DEFERRED to a follow-up WO (see the
# rationale in install_module). Until then a module of one of these classes is
# fitted-and-baked but runtime-INERT — install_module/remove_module surface
# ``consumer_inert: True`` so the UI can warn the player.
#
# WHY DEFERRED (not wired here) — flagged for the orchestrator/Max:
#   * harvester (passive_income): get_passive_income() keys off the literal
#     EQUIPMENT_DEFINITIONS slug ("quantum_harvester") and reads the CATALOG
#     effect, not a stored slot value. A synthetic module key is unrecognized
#     (silently 0); writing the literal "quantum_harvester" key COLLIDES with
#     install_equipment's own slot, the npc_scheduler daily-credit anchor logic,
#     and the legacy "is the equipment installed?" detection. Wiring it correctly
#     needs a consumer change (a new module-aware passive-income source), which is
#     out of SM-3's no-new-consumer scope.
#   * lander/mining/tractor (landing_bonus / mining_efficiency / tow_capable /
#     weapon_mode): get_equipment_effects() MERGES numeric effects ADDITIVELY
#     across all equipment_slots entries, but each consumer reads landing_bonus /
#     mining_efficiency as a SINGLE multiplicative factor and tow_capable /
#     weapon_mode as a presence flag. A synthetic module slot would (a) double-
#     count against a same-family legacy equipment a player also owns (1.25+1.25),
#     and (b) deliver the UNTIERED/un-supercharged catalog value, not the baked
#     tier value. Writing that key would make the module WORK WRONG — STEP 4 says
#     do not write a wrong key. Correct wiring needs a module-aware effect source
#     the consumers read (a follow-up WO).
_EQUIPMENT_FAMILY_DEFERRED = frozenset({"harvester", "lander", "mining", "tractor"})

# ============================================================================
# GALACTIC-CITIZEN tier (WO-GC-B). The Citizen tier is DATA + an eligibility
# predicate, never new power. Two pieces live here:
#   1. requires_satisfied() — the shared eligibility resolver the kernel's
#      `requires` seam dispatches on (None=open · "citizen"=membership ·
#      {faction:tier}=reputation, deferred to GC-C/Exalted).
#   2. CITIZEN_COSMETICS — the L1 zero-slot cosmetic catalog. EVERY entry is
#      `effects: {}` (the P2W firewall: paid buys SHAPE/EXPRESSION, never power
#      or income). Applied to Ship.modules.cosmetics (outside `installed`, so a
#      skin never eats a finite slot). The income-fence CI test asserts this.
# Canon: design-briefs/galactic-citizen-unified/03-spec.md §3.3/§4.1/§4.3/§4.4.
# ============================================================================
_CITIZEN_SUBSCRIPTION_TIER = "galactic_citizen"

# Income effect-keys a Citizen surface may NEVER carry (the firewall, named at
# the key level because income leaks through effects, not just classes).
GC_INCOME_EFFECT_KEYS = frozenset({
    "passive_income", "mining_efficiency", "cargo_bonus_percent",
    "credit_bonus", "income_bonus", "trade_profit_bonus",
})
# Classes a Citizen-gated slot/module may never be (combat + income axes).
GC_FORBIDDEN_CLASSES = frozenset({
    "harvester", "mining", "weapon", "weapon_damage", "combat",
})

# L1 cosmetic catalog — zero-stat overlays keyed by cosmetic slot. Each carries
# requires:"citizen" and effects:{} (firewall). Applying writes the chosen value
# into Ship.modules.cosmetics[slot]; null clears it.
CITIZEN_COSMETICS = {
    "frame": {
        "label": "Citizen Hull Frame",
        "description": "A visible plating skin / silhouette accent on any owned ship.",
        "requires": "citizen",
        "effects": {},
        "values": ["citizen_aurora", "citizen_obsidian"],
    },
    "slot_glow": {
        "label": "Aurora Slot-Glow",
        "description": "Installed-module slots render a Citizen hue (cosmetic glow).",
        "requires": "citizen",
        "effects": {},
        "values": ["citizen_hue"],
    },
    "crest": {
        "label": "Citizen Crest",
        "description": "The SpaceDock build-card carries a Citizen sigil.",
        "requires": "citizen",
        "effects": {},
        "values": ["citizen_sigil"],
    },
}


def is_galactic_citizen(db: Session, player: Player) -> bool:
    """The lapse-safe Citizen check — the EXACT double-check used by the weekly
    perk (economy_faucet_service._apply_citizen_perks): the canonical
    Player.is_galactic_citizen flag AND a live re-read of the User row's
    subscription_tier (so a lapsed membership stops conferring at once)."""
    from src.models.user import User
    if not getattr(player, "is_galactic_citizen", False):
        return False
    user = db.query(User).filter(User.id == player.user_id).first()
    return user is not None and user.subscription_tier == _CITIZEN_SUBSCRIPTION_TIER


def requires_satisfied(db: Session, player: Player, requires) -> bool:
    """Resolve a `requires` eligibility predicate (kernel seam, §4.1).

    None       → open (any player)
    "citizen"  → an active Galactic Citizen (lapse-safe double-check)
    {fac:tier} → reputation gate (Exalted ships / faction_requirements) — NOT
                 wired in GC-B; fails closed until GC-C/Exalted lands it.
    """
    if requires is None:
        return True
    if requires == "citizen":
        return is_galactic_citizen(db, player)
    # dict / faction-reputation predicate: deferred (fail closed, never bypass).
    return False


class ShipUpgradeService:
    """Service for managing ship upgrades and equipment installations"""

    UPGRADE_DEFINITIONS = {
        UpgradeType.ENGINE: {
            "base_cost": 5000,
            "cost_multiplier": 2.0,
            "effect_per_level": {"speed_bonus": 0.5},
            "description": "Improves ship speed by +0.5 per level"
        },
        UpgradeType.CARGO_HOLD: {
            "base_cost": 3000,
            "cost_multiplier": 1.8,
            "effect_per_level": {"cargo_bonus_percent": 30},
            "description": "Increases cargo capacity by +30% per level"
        },
        UpgradeType.SHIELD: {
            "base_cost": 8000,
            "cost_multiplier": 2.2,
            "effect_per_level": {"shield_bonus": 200},
            "description": "Increases max shields by +200 per level"
        },
        UpgradeType.HULL: {
            "base_cost": 7000,
            "cost_multiplier": 2.0,
            "effect_per_level": {"hull_bonus": 300},
            "description": "Increases hull points by +300 per level"
        },
        UpgradeType.SENSOR: {
            "base_cost": 6000,
            "cost_multiplier": 2.5,
            # Canon (sw2102-docs ship-systems.md §2.5): "Each Sensor level adds
            # +15% evasion. Sensors also affect scan range." The evasion number
            # is canon; the scan-range increment is NO-CANON (the doc marks the
            # scan-range effect 📐 Design-only with no per-level figure). Kernel:
            # +1 scanner-range sector per Sensor level — flagged for a
            # DECISIONS.md Pending ruling. The effective scanner range
            # (spec base + this bonus) is computed by effective_scanner_range();
            # there is no per-instance scanner_range column to mutate, so the
            # bonus is applied as a derived value the scan path consults.
            "effect_per_level": {"evasion_bonus_percent": 15, "scanner_range_bonus": 1},
            "description": "Increases evasion by +15% per level and scan range by +1 sector per level"
        },
        UpgradeType.DRONE_BAY: {
            "base_cost": 10000,
            "cost_multiplier": 2.0,
            "effect_per_level": {"drone_capacity_bonus": 2},
            "description": "Increases drone capacity by +2 per level"
        },
        UpgradeType.GENESIS_CONTAINMENT: {
            "base_cost": 15000,
            "cost_multiplier": 3.0,
            "effect_per_level": {"genesis_capacity_bonus": 2},
            "description": "Increases genesis device capacity by +2 per level"
        },
        # NO-CANON kernel (sw2102-docs ship-systems.md §2.9 marks cost/effect 📐 Design-only).
        # Cost scaling mirrors the Hull/Sensor utility tier (base 6,000, x2.0). Effect:
        # each level reduces the ship's mechanical failure rate by 0.15 (15% relative)
        # of the spec's base maintenance_rate, applied via _apply_upgrade_effects into the
        # maintenance JSONB. Numbers flagged for a DECISIONS Pending entry.
        UpgradeType.MAINTENANCE_SYSTEM: {
            "base_cost": 6000,
            "cost_multiplier": 2.0,
            "effect_per_level": {"failure_rate_reduction": 0.15},
            "description": "Reduces mechanical failure rate by 15% per level"
        },
    }

    EQUIPMENT_DEFINITIONS = {
        "quantum_harvester": {
            "name": "Quantum Harvester",
            "description": "Harvests quantum particles from space, providing passive income",
            "cost": 25000,
            "compatible_ships": [ShipType.SCOUT_SHIP, ShipType.FAST_COURIER, ShipType.DEFENDER, ShipType.WARP_JUMPER],
            "effects": {"passive_income": 100}
        },
        "mining_laser": {
            "name": "Mining Laser",
            "description": "Allows direct mining of asteroid fields for resources",
            "cost": 35000,
            "compatible_ships": [ShipType.CARGO_HAULER, ShipType.COLONY_SHIP, ShipType.DEFENDER],
            "effects": {"mining_efficiency": 1.5}
        },
        "planetary_lander": {
            "name": "Planetary Lander",
            "description": "Advanced landing module for improved planet interaction",
            "cost": 20000,
            "compatible_ships": [ShipType.COLONY_SHIP, ShipType.LIGHT_FREIGHTER, ShipType.CARGO_HAULER],
            "effects": {"landing_bonus": 1.25}
        },
        # CANON (sw2102-docs ship-systems.md:127, ships.md tractor-beam-tow-operations,
        # combat.md:154-167): dual-use Tractor Beam. Cost 40,000; installable on
        # Cargo Hauler / Defender / Carrier / Warp Jumper. Two effects:
        #   - tow_capable: the AF ship-tow flag (movement consults it + Ship.tow_state).
        #     INCLUDED here per canon so the equipment is canon-complete, but the tow
        #     BEHAVIOR is NOT built here — that is WO-AF.
        #   - weapon_mode "tractor": the combat-side face. In combat the tractor does
        #     NO damage (combat.md:167) and denies the target's escape (combat.md:162).
        #     WO-BC builds only the single-shot escape-suppression kernel of this; the
        #     full multi-round 3-round lock / additive speed-debuff stacking / counterplay
        #     described in combat.md:161-165 is DEFERRED to a multi-round combat-engagement
        #     model (DECISIONS.md tractor-weapon-mode-scope, ⏳ Pending — orchestrator
        #     selected option (a), ship the single-shot MVP now).
        "tractor_beam": {
            "name": "Tractor Beam",
            "description": "Dual-use tractor projector: in combat it locks the target and denies escape (no damage); as a tow rig it enables ship-towing through warp tunnels",
            "cost": 40000,
            "compatible_ships": [ShipType.CARGO_HAULER, ShipType.DEFENDER, ShipType.CARRIER, ShipType.WARP_JUMPER],
            "effects": {"tow_capable": True, "weapon_mode": "tractor"}
        },
    }

    # ========================================================================
    # WO-MINING — Mining Laser upgrade ladder (the "mining_laser_level" entry).
    # ------------------------------------------------------------------------
    # CANON (sw2102-docs FEATURES/economy/mining.md § Mining Laser upgrade ladder):
    # the base 35,000 cr Mining Laser ships at level 0; three purchasable levels
    # raise its yield multiplier. The per-level COST + cumulative + yield
    # MULTIPLIER are copied VERBATIM from the canon table:
    #   L1: 50,000 cr  (cum 85,000)   1.25×
    #   L2: 100,000 cr (cum 185,000)  1.5×   (gate for quantum_shards trace drops)
    #   L3: 200,000 cr (cum 385,000)  2.0×   (lifts precious_metals to its 11% cap)
    #
    # The level is NOT a ship.upgrades JSONB stat (which is keyed by UpgradeType
    # and stores hull-stat upgrades) — it is a `level: int` key INSIDE the
    # equipment_slots["mining_laser"] dict, which is where mining_service.py reads
    # the laser level (frozen contract (F)). This entry is therefore a STRING-keyed
    # ladder kept OUT of the UpgradeType-keyed UPGRADE_DEFINITIONS dict (whose
    # consumers iterate it as UpgradeType members and call `.value` — a string key
    # there would crash get_upgrade_info / purchase_upgrade / degrade_random_system).
    # purchase_mining_laser_upgrade REUSES the existing purchase ritual
    # (_get_ship_and_player lock → credit check → flush) rather than forking it.
    # ========================================================================
    MINING_LASER_LADDER = {
        "mining_laser_level": {
            "name": "Mining Laser Upgrade",
            "description": "Upgrades the equipped Mining Laser, raising its yield multiplier",
            "max_level": 3,
            # 1-based per-level cost / yield multiplier (canon ladder table).
            "levels": {
                1: {"cost": 50000, "yield_multiplier": 1.25},
                2: {"cost": 100000, "yield_multiplier": 1.5},
                3: {"cost": 200000, "yield_multiplier": 2.0},
            },
        }
    }

    # ========================================================================
    # SHIP-MODS (WO-SM-2): the unified module catalog + bake-on-install effects.
    # ------------------------------------------------------------------------
    # SHIP-MODS-MASTER.md §5: the 8 legacy upgrade tracks + the 4 player
    # equipment plug-ins fold INTO one catalog as 12 module CLASSES, each tiered
    # Mk I / II / III. Tier-1 effect magnitudes are the existing ✅-Decided
    # per-level magnitudes ported 1:1 from UPGRADE_DEFINITIONS + EQUIPMENT_
    # DEFINITIONS above (zero new balance surface for the ported numbers).
    #
    # This is the Phase-A KERNEL: the catalog (this dict) + _apply_module_effects
    # (bake-on-install). install/remove/routes + the Phase-2 destructive cutover
    # are SEPARATE WOs (SM-3 / Max-gated). During coexistence both legacy upgrades
    # and modules write the SAME baked stat columns — see _apply_module_effects for
    # the zero-double-count contract.
    # ========================================================================

    # §4.1 supercharge multiplier (flat) — a module installed in a supercharged
    # slot has its effects multiplied by this. Snapshotted as `super_at_install`
    # on the slot record so a later slot-layout re-tune never silently re-buffs a
    # fielded ship. [NO-CANON — Max-blessed launch value.]
    SUPERCHARGE_MULT = 1.6

    # §4.2 stacking cap — FLAT best-3 per effect: of all same-effect contributions
    # only the 3 LARGEST count (summed); the rest contribute 0. The dumb cap that
    # prevents the god-ship; the smooth geometric DR curve is the Phase-B swap-in.
    # [NO-CANON — Max-blessed launch value.]
    MODULE_STACK_BEST_N = 3

    # §5.3 tier curve (NO-CANON, co-tuned + Max-blessed): a module's effect scales
    # SUB-LINEARLY with tier (so breadth-by-count survives as a real alternative to
    # depth-in-a-super-slot) while cost scales faster. tier is 1-based (Mk I = 1).
    #   tier_effect = base_effect × MODULE_TIER_EFFECT_MULT ** (tier - 1)
    #   tier_cost   = base_cost   × MODULE_TIER_COST_MULT   ** (tier - 1)
    MODULE_TIER_EFFECT_MULT = 1.6   # Mk III effect = 2.56× base
    MODULE_TIER_COST_MULT = 2.2     # Mk III cost  = 4.84× base
    MODULE_MAX_TIER = 3             # Mk I / II / III

    # §6.x SALVAGE — removing an installed module refunds this FRACTION of its
    # (tier-scaled) catalog cost; the rest is the salvage haircut (you don't get
    # the full price back for pulling a module). int-truncated on credit-back.
    # [NO-CANON — Max-blessed launch value; flagged for a DECISIONS.md ruling on
    # the exact refund fraction.]
    SALVAGE_FRACTION = 0.25

    # Genesis hulls — the module-class hull gate for the `genesis` family, mirroring
    # how the legacy GENESIS_CONTAINMENT track is only buyable on genesis-capable
    # specs (ShipSpecification.genesis_compatible=True in the seeder). These are the
    # five hulls seeded genesis_compatible.
    _GENESIS_HULLS = [
        ShipType.CARGO_HAULER,
        ShipType.COLONY_SHIP,
        ShipType.DEFENDER,
        ShipType.CARRIER,
        ShipType.WARP_JUMPER,
    ]

    # §5.2 — the 12 module families' tier-1 base spec (the ✅ per-level / equipment
    # magnitude ported 1:1). Each entry:
    #   class           : the module-class label (== UpgradeType-family / equipment lineage)
    #   base_cost       : Mk I credit price (tier cost scales from this)
    #   base_effects    : tier-1 effect dict — the SAME effect keys _apply_module_effects
    #                     bakes into the stat columns (matched to _apply_upgrade_effects)
    #   compatible_ships: hull gate (None = open; genesis = genesis hulls only) —
    #                     mirrors EQUIPMENT_DEFINITIONS["...compatible_ships"]
    #   requires        : the Citizen/faction eligibility predicate seam (None = open,
    #                     default) — built day one so the Citizen tier is DATA not code
    #   slot_class      : which class-locked slot accepts this module (None = any slot)
    #   inert           : True for the ported-but-not-consumed families (cargo, drone) —
    #                     the effect is still BAKED into its column, but no new consumer
    #                     is wired in the kernel (fix D); a separate balance-flagged WO
    #                     wires the consumer.
    #
    # §5.4 HARD CANON: there is NO `weapon_damage` family — attack_rating is fixed at
    # hull purchase; modules add tactical/defensive/utility modifiers only.
    _MODULE_FAMILIES = {
        # --- ported from UPGRADE_DEFINITIONS (✅ live per-level magnitudes) ---
        "engine": {
            "base_cost": 5000,
            "base_effects": {"speed_bonus": 0.5},
            "compatible_ships": None,
            "requires": None,
            "slot_class": None,
            "name": "Engine Module",
            "description": "Improves ship speed (and shortens the Warp Jumper's post-jump cooldown).",
        },
        "shield": {
            "base_cost": 8000,
            "base_effects": {"shield_bonus": 200},
            "compatible_ships": None,
            "requires": None,
            "slot_class": None,
            "name": "Shield Module",
            "description": "Increases max shields.",
        },
        "hull": {
            "base_cost": 7000,
            "base_effects": {"hull_bonus": 300},
            "compatible_ships": None,
            "requires": None,
            "slot_class": None,
            "name": "Hull Module",
            "description": "Increases hull points.",
        },
        "sensor": {
            "base_cost": 6000,
            "base_effects": {"evasion_bonus_percent": 15, "scanner_range_bonus": 1},
            "compatible_ships": None,
            "requires": None,
            "slot_class": None,
            "name": "Sensor Module",
            "description": "Increases evasion and scanner range.",
        },
        "maintenance": {
            "base_cost": 6000,
            "base_effects": {"failure_rate_reduction": 0.15},
            "compatible_ships": None,
            "requires": None,
            # slot_class "maintenance": fits OPEN slots (unchanged — open slots
            # skip the class check) AND the Citizen Clipper's maintenance-locked
            # super slot (WO-GC-C). Without this the maintenance-fenced slot would
            # accept no module (a dead slot — caught live). The Clipper is the only
            # maintenance-locked slot, so no other hull is affected.
            "slot_class": "maintenance",
            "name": "Maintenance Module",
            "description": "Reduces mechanical failure rate (clamped to a full 1.0 reduction).",
        },
        "genesis": {
            "base_cost": 15000,
            "base_effects": {"genesis_capacity_bonus": 2},
            "compatible_ships": _GENESIS_HULLS,
            "requires": None,
            "slot_class": None,
            "name": "Genesis Containment Module",
            "description": "Increases genesis-device capacity (genesis-capable hulls only).",
        },
        # cargo: now CONSUMED. The cargo_bonus_percent bake into
        # cargo._capacity_bonus_percent (see _apply_module_effects) is read by
        # models/ship.py effective_cargo_capacity(), which WO-SM made the single
        # cargo-capacity reader (trade / mining / salvage / planetary / warp-gate).
        # The `inert` marker is therefore removed — the module's effect is live.
        "cargo": {
            "base_cost": 3000,
            "base_effects": {"cargo_bonus_percent": 30},
            "compatible_ships": None,
            "requires": None,
            "slot_class": None,
            "name": "Cargo Module",
            "description": "Increases cargo capacity (consumed via effective_cargo_capacity).",
        },
        # drone: STILL INERT. The drone_capacity_bonus bake has no scalar Ship
        # column, and the live consumer (drone_service._drone_bay_bonus) reads the
        # Drone Bay level out of Ship.upgrades[DRONE_BAY] — the legacy UpgradeType
        # JSONB — NOT the module bake. So a drone MODULE is fitted-and-baked but its
        # capacity bonus is never read. Making it live needs a module-aware
        # drone-capacity source (a separate WO); kept `inert` until then.
        "drone": {
            "base_cost": 10000,
            "base_effects": {"drone_capacity_bonus": 2},
            "compatible_ships": None,
            "requires": None,
            "slot_class": None,
            "inert": True,  # [NO-CANON] module bake unread — consumer reads Ship.upgrades[DRONE_BAY], not the bake
            "name": "Drone Bay Module",
            "description": "Increases drone capacity (module bake NOT yet consumed — consumer reads the legacy upgrade level; wiring is a separate WO).",
        },
        # --- ported from EQUIPMENT_DEFINITIONS (the 4 player equipment plug-ins) ---
        "harvester": {
            "base_cost": 25000,
            "base_effects": {"passive_income": 100},
            # Mirrors quantum_harvester compatible_ships.
            "compatible_ships": [
                ShipType.SCOUT_SHIP, ShipType.FAST_COURIER,
                ShipType.DEFENDER, ShipType.WARP_JUMPER,
            ],
            "requires": None,
            "slot_class": None,
            "name": "Quantum Harvester Module",
            "description": "Harvests quantum particles for passive income.",
        },
        "lander": {
            "base_cost": 20000,
            "base_effects": {"landing_bonus": 1.25},
            # Mirrors planetary_lander compatible_ships.
            "compatible_ships": [
                ShipType.COLONY_SHIP, ShipType.LIGHT_FREIGHTER, ShipType.CARGO_HAULER,
            ],
            "requires": None,
            "slot_class": None,
            "name": "Planetary Lander Module",
            "description": "Improves planet-landing interaction.",
        },
        "mining": {
            "base_cost": 35000,
            "base_effects": {"mining_efficiency": 1.5},
            # Mirrors mining_laser compatible_ships.
            "compatible_ships": [
                ShipType.CARGO_HAULER, ShipType.COLONY_SHIP, ShipType.DEFENDER,
            ],
            "requires": None,
            "slot_class": None,
            "name": "Mining Laser Module",
            "description": "Enables direct asteroid mining.",
        },
        "tractor": {
            "base_cost": 40000,
            # Non-numeric, non-tiering effects: a tractor is a tractor at any tier
            # (the tow flag + the combat-side tractor weapon_mode). These do NOT
            # scale by tier or supercharge — only numeric effects do (see
            # _apply_module_effects). slot_class "combat": the tractor's combat face
            # makes it eligible for the class-locked "combat" slot (Defender) — a
            # TACTICAL module (no damage, §5.4), not firepower.
            "base_effects": {"tow_capable": True, "weapon_mode": "tractor"},
            # Mirrors tractor_beam compatible_ships.
            "compatible_ships": [
                ShipType.CARGO_HAULER, ShipType.DEFENDER,
                ShipType.CARRIER, ShipType.WARP_JUMPER,
            ],
            "requires": None,
            "slot_class": "combat",
            "name": "Tractor Beam Module",
            "description": "Dual-use tractor: combat escape-denial (no damage) + ship-tow rig.",
        },
    }

    @staticmethod
    def _scale_effects(base_effects: Dict[str, Any], tier: int) -> Dict[str, Any]:
        """Scale a family's tier-1 base_effects to `tier` (1-based) per §5.3.

        Only NUMERIC effects scale (``base × 1.6^(tier-1)``); boolean/string
        effects (the tractor's ``tow_capable`` / ``weapon_mode``) are tier-invariant
        and passed through unchanged. Rounded sensibly: ints stay int, floats keep
        2 decimals so the catalog reads cleanly.
        """
        factor = ShipUpgradeService.MODULE_TIER_EFFECT_MULT ** (tier - 1)
        scaled: Dict[str, Any] = {}
        for k, v in base_effects.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                scaled[k] = v  # tier-invariant (tow_capable / weapon_mode)
                continue
            val = v * factor
            scaled[k] = int(round(val)) if isinstance(v, int) else round(val, 2)
        return scaled

    @staticmethod
    def _build_module_definitions() -> Dict[tuple, Dict[str, Any]]:
        """Expand _MODULE_FAMILIES into the tiered catalog keyed by (class, tier).

        §5.1 entry shape per (class, tier): {name, cost, effects, compatible_ships,
        requires, slot_class[, inert]}. Mk I/II/III via the §5.3 curve:
        effect ×1.6^(tier-1), cost ×2.2^(tier-1).
        """
        catalog: Dict[tuple, Dict[str, Any]] = {}
        for cls, fam in ShipUpgradeService._MODULE_FAMILIES.items():
            for tier in range(1, ShipUpgradeService.MODULE_MAX_TIER + 1):
                tier_label = {1: "Mk I", 2: "Mk II", 3: "Mk III"}[tier]
                cost = int(round(fam["base_cost"] * (ShipUpgradeService.MODULE_TIER_COST_MULT ** (tier - 1))))
                entry = {
                    "name": f"{fam['name']} {tier_label}",
                    "description": fam["description"],
                    "cost": cost,
                    "effects": ShipUpgradeService._scale_effects(fam["base_effects"], tier),
                    "compatible_ships": fam["compatible_ships"],
                    "requires": fam["requires"],
                    "slot_class": fam["slot_class"],
                    "class": cls,
                    "tier": tier,
                }
                if fam.get("inert"):
                    entry["inert"] = True
                catalog[(cls, tier)] = entry
        return catalog

    # §5.1 the catalog — MODULE_DEFINITIONS[(class, tier)] -> entry. Built once at
    # class-definition time from _MODULE_FAMILIES (the §5.2 magnitudes) via the §5.3
    # tier curve. The (class, tier) tuple key matches the §7 bake loop
    # (MODULE_DEFINITIONS[(m["class"], m["tier"])]).
    # NOTE: assigned after the class body (see below) because it references the
    # @staticmethod builders.

    # NO-CANON kernel (ship-systems.md §2.5 marks the Sensor scan-range effect
    # 📐 Design-only): each Sensor upgrade level adds +1 sector of scanner range
    # on top of the hull spec's base scanner_range. Flagged for a DECISIONS.md
    # Pending ruling on the exact per-level figure.
    SCANNER_RANGE_BONUS_PER_SENSOR_LEVEL = 1

    # CANON (ship-systems.md §2.5 line 90, marked ✅ Shipped): "Each Sensor level
    # adds +15% evasion." Applied as a derived percentage of the hull spec's base
    # evasion, scaled by installed Sensor level — exactly the baked-bonus shape of
    # SCANNER_RANGE_BONUS_PER_SENSOR_LEVEL above. The +15% figure IS canon (not
    # NO-CANON); only the linear-per-level composition (15% × level vs compounding)
    # is an implementation choice — chosen linear to match the doc's stated cap
    # ("Sensor L1–L3 (max evasion: +45%)", line 181 = 15% × 3).
    EVASION_BONUS_PCT_PER_SENSOR_LEVEL = 0.15

    # NO-CANON kernel (ship-systems.md §6.6 line 242 marks the Engine
    # jump-cooldown-reduction effect "📐 Design-only" with no per-level figure):
    # each Engine upgrade level shortens the Warp Jumper's post-jump quantum
    # cooldown by 10% (multiplicative), floored so the cumulative reduction can
    # never drive the cooldown below half its base. Engine's existing speed_bonus
    # (Ship.current_speed) effect is UNCHANGED — this is the second, previously
    # unbuilt half of the upgrade. Flagged for a DECISIONS.md Pending ruling on
    # the exact per-level magnitude and floor.
    ENGINE_JUMP_COOLDOWN_REDUCTION_PER_LEVEL = 0.10
    ENGINE_JUMP_COOLDOWN_FACTOR_FLOOR = 0.5

    @staticmethod
    def get_engine_level(ship) -> int:
        """Read the ship's current Engine upgrade level from its upgrades JSONB."""
        upgrades = getattr(ship, "upgrades", None)
        if not isinstance(upgrades, dict):
            return 0
        try:
            return int(upgrades.get(UpgradeType.ENGINE.value, 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def engine_jump_cooldown_factor(ship) -> float:
        """Multiplier in [FLOOR, 1.0] applied to the base quantum-jump cooldown
        duration, scaled by the ship's installed Engine level.

        Returns 1.0 at Engine L0 (no reduction) and decreases ~10% per level
        (``(1 - 0.10) ** level``), clamped at ``ENGINE_JUMP_COOLDOWN_FACTOR_FLOOR``
        so no amount of upgrading can collapse the cooldown to zero. e.g. L0=1.0,
        L1=0.90, L2=0.81, L3=0.729 — an Engine-L3 Warp Jumper's post-jump cooldown
        is ~27% shorter than an un-upgraded hull's. NO-CANON magnitude (see the
        class-level constants); the canonical effect is documented but the
        per-level figure is 📐 Design-only.
        """
        level = ShipUpgradeService.get_engine_level(ship)
        if level <= 0:
            return 1.0
        factor = (1.0 - ShipUpgradeService.ENGINE_JUMP_COOLDOWN_REDUCTION_PER_LEVEL) ** level
        return max(ShipUpgradeService.ENGINE_JUMP_COOLDOWN_FACTOR_FLOOR, factor)

    @staticmethod
    def get_sensor_level(ship) -> int:
        """Read the ship's current Sensor upgrade level from its upgrades JSONB."""
        upgrades = getattr(ship, "upgrades", None)
        if not isinstance(upgrades, dict):
            return 0
        try:
            return int(upgrades.get(UpgradeType.SENSOR.value, 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def effective_scanner_range(ship, base_scanner_range: int) -> int:
        """Effective scanner range = the hull spec's base scanner_range plus the
        Sensor-upgrade scan-range bonus (+1 sector per Sensor level, NO-CANON
        kernel — see SCANNER_RANGE_BONUS_PER_SENSOR_LEVEL).

        `Ship` has no per-instance scanner_range column (the value lives on
        `ShipSpecification.scanner_range`); callers pass that spec base in and
        the scan path consults the returned effective value.
        """
        sensor_level = ShipUpgradeService.get_sensor_level(ship)
        bonus = sensor_level * ShipUpgradeService.SCANNER_RANGE_BONUS_PER_SENSOR_LEVEL
        return int(base_scanner_range) + bonus

    @staticmethod
    def effective_evasion(ship, base_evasion: int) -> int:
        """Effective combat evasion = the hull spec's base evasion scaled up by
        the Sensor-upgrade bonus (+15% per installed Sensor level — CANON
        ship-systems.md §2.5 line 90; see EVASION_BONUS_PCT_PER_SENSOR_LEVEL).

        Mirrors `effective_scanner_range`: `Ship` has no separate per-instance
        evasion column (the static value lives on `ShipSpecification.evasion`),
        so the combat seed path passes that spec base in and consults the
        returned effective value. The bonus is linear in Sensor level
        (`base × (1 + 0.15 × level)`), so an un-upgraded ship (Sensor L0) gets
        `base × 1.0` — its base evasion is returned UNCHANGED. Rounded to an int
        to match the integer `ShipSpecification.evasion` column and the integer
        defense-component arithmetic in combat_service.
        """
        sensor_level = ShipUpgradeService.get_sensor_level(ship)
        multiplier = 1.0 + (sensor_level * ShipUpgradeService.EVASION_BONUS_PCT_PER_SENSOR_LEVEL)
        return int(round(int(base_evasion) * multiplier))

    @staticmethod
    def get_passive_income(ship) -> int:
        """Total per-period passive_income a ship's installed equipment grants.

        Read-only. Authoritative source is EQUIPMENT_DEFINITIONS keyed by the
        equipment actually installed in the ship's equipment_slots JSONB — NOT
        the effects snapshot stored on the slot at install time — so a future
        re-tuning of the canonical passive_income figure (e.g. a DECISIONS.md
        ruling) takes effect for already-equipped ships without a backfill. If a
        ship carries the effect via MULTIPLE equipment sources, their
        passive_income values are SUMMED. Returns 0 when the ship carries no
        passive_income equipment (the common case), so the idle-income sweep
        skips it cleanly.

        Used by npc_scheduler_service's daily idle-income credit-grant sweep
        (ship-systems.md §passive_income: "applied per-tick by an idle-income
        job"). Magnitude/cadence are NO-CANON (the doc marks the effect
        📐 Design-only) — flagged for the orchestrator.
        """
        equipment_slots = getattr(ship, "equipment_slots", None) or {}
        total = 0
        for eq_key in equipment_slots.keys():
            eq_def = ShipUpgradeService.EQUIPMENT_DEFINITIONS.get(eq_key)
            if not eq_def:
                continue
            value = eq_def.get("effects", {}).get("passive_income")
            if isinstance(value, (int, float)):
                total += int(value)
        return total

    @staticmethod
    def get_equipment_effects(ship) -> Dict[str, Any]:
        """Read equipment_slots JSONB and return a merged dict of all active effects.

        Example return: {"passive_income": 100, "mining_efficiency": 1.5}
        Services can call this to apply bonuses from installed equipment.
        """
        equipment_slots = getattr(ship, 'equipment_slots', None) or {}
        merged: Dict[str, Any] = {}
        for eq_key, eq_data in equipment_slots.items():
            effects = eq_data.get("effects", {}) if isinstance(eq_data, dict) else {}
            for effect_name, effect_value in effects.items():
                if effect_name in merged:
                    # Additive stacking for numeric effects
                    if isinstance(effect_value, (int, float)) and isinstance(merged[effect_name], (int, float)):
                        merged[effect_name] += effect_value
                    else:
                        merged[effect_name] = effect_value
                else:
                    merged[effect_name] = effect_value
        return merged

    def __init__(self, db: Session):
        self.db = db

    def _get_ship_and_player(self, ship_id: uuid.UUID, player_id: uuid.UUID) -> tuple:
        """Fetch and validate ship ownership. Returns (ship, player, error_dict).
        Locks the player row to prevent concurrent purchase race conditions."""
        player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
        if not player:
            return None, None, {"success": False, "message": "Player not found"}

        ship = self.db.query(Ship).filter(Ship.id == ship_id).with_for_update().first()
        if not ship:
            return None, None, {"success": False, "message": "Ship not found"}

        if ship.owner_id != player_id:
            return None, None, {"success": False, "message": "You do not own this ship"}

        if ship.is_destroyed:
            return None, None, {"success": False, "message": "Cannot modify a destroyed ship"}

        return ship, player, None

    def _get_current_upgrade_level(self, ship: Ship, upgrade_type: UpgradeType) -> int:
        """Get the current upgrade level for a given type from the ship's upgrades JSONB."""
        upgrades = ship.upgrades
        if not upgrades or not isinstance(upgrades, dict):
            return 0
        return upgrades.get(upgrade_type.value, 0)

    def _get_max_upgrade_level(self, ship: Ship, upgrade_type: UpgradeType) -> int:
        """Get the max upgrade level for a given type from the ship's specification."""
        spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ship.type
        ).first()
        if not spec or not spec.max_upgrade_levels:
            return 0
        return spec.max_upgrade_levels.get(upgrade_type.value, 0)

    def _calculate_upgrade_cost(self, upgrade_type: UpgradeType, current_level: int) -> int:
        """Calculate the cost for the next upgrade level."""
        definition = self.UPGRADE_DEFINITIONS[upgrade_type]
        return int(definition["base_cost"] * (definition["cost_multiplier"] ** current_level))

    def get_upgrade_info(self, ship_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        """
        Returns current upgrade levels, max levels, and costs for next upgrade
        for each category, plus equipped equipment slots.
        """
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ship.type
        ).first()

        upgrade_info = {}
        for upgrade_type, definition in self.UPGRADE_DEFINITIONS.items():
            current_level = self._get_current_upgrade_level(ship, upgrade_type)
            max_level = spec.max_upgrade_levels.get(upgrade_type.value, 0) if spec and spec.max_upgrade_levels else 0
            at_max = current_level >= max_level

            upgrade_info[upgrade_type.value] = {
                "current_level": current_level,
                "max_level": max_level,
                "at_max": at_max,
                "next_cost": self._calculate_upgrade_cost(upgrade_type, current_level) if not at_max else None,
                "effect_per_level": definition["effect_per_level"],
                "description": definition["description"],
            }

        # Equipment slots
        equipment_slots = ship.equipment_slots if hasattr(ship, 'equipment_slots') and ship.equipment_slots else {}

        # Available equipment for this ship type
        available_equipment = {}
        for eq_key, eq_def in self.EQUIPMENT_DEFINITIONS.items():
            compatible = ship.type in eq_def["compatible_ships"]
            installed = eq_key in equipment_slots
            available_equipment[eq_key] = {
                "name": eq_def["name"],
                "description": eq_def["description"],
                "cost": eq_def["cost"],
                "compatible": compatible,
                "installed": installed,
                "effects": eq_def["effects"],
            }

        return {
            "success": True,
            "ship_id": str(ship.id),
            "ship_name": ship.name,
            "ship_type": ship.type.value,
            "upgrades": upgrade_info,
            "equipment": available_equipment,
            "equipped": equipment_slots,
            "player_credits": player.credits,
        }

    def purchase_upgrade(self, ship_id: uuid.UUID, player_id: uuid.UUID, upgrade_type: UpgradeType) -> Dict[str, Any]:
        """
        Purchase an upgrade for a ship. Validates ownership, level limits, and credits.
        Applies stat changes to the ship.
        """
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        if upgrade_type not in self.UPGRADE_DEFINITIONS:
            return {"success": False, "message": f"Unknown upgrade type: {upgrade_type}"}

        current_level = self._get_current_upgrade_level(ship, upgrade_type)
        max_level = self._get_max_upgrade_level(ship, upgrade_type)

        if max_level == 0:
            return {
                "success": False,
                "message": f"This ship type cannot be upgraded with {upgrade_type.value}"
            }

        if current_level >= max_level:
            return {
                "success": False,
                "message": f"{upgrade_type.value} is already at maximum level ({max_level})"
            }

        cost = self._calculate_upgrade_cost(upgrade_type, current_level)

        if player.credits < cost:
            return {
                "success": False,
                "message": f"Insufficient credits. Need {cost:,}, have {player.credits:,}",
                "cost": cost,
                "player_credits": player.credits,
            }

        # Deduct credits
        player.credits -= cost

        # Increment upgrade level in ship's upgrades JSONB
        if not ship.upgrades or not isinstance(ship.upgrades, dict):
            ship.upgrades = {}
        ship.upgrades[upgrade_type.value] = current_level + 1
        flag_modified(ship, 'upgrades')

        new_level = current_level + 1
        definition = self.UPGRADE_DEFINITIONS[upgrade_type]
        effects = definition["effect_per_level"]

        # Apply stat changes based on upgrade type
        updated_stats = self._apply_upgrade_effects(ship, upgrade_type, effects)

        self.db.flush()

        logger.info(
            f"Player {player_id} upgraded {upgrade_type.value} to level {new_level} "
            f"on ship {ship.name} for {cost:,} credits"
        )

        return {
            "success": True,
            "message": f"{upgrade_type.value} upgraded to level {new_level}",
            "upgrade_type": upgrade_type.value,
            "new_level": new_level,
            "max_level": max_level,
            "cost_paid": cost,
            "remaining_credits": player.credits,
            "updated_stats": updated_stats,
        }

    def _apply_upgrade_effects(self, ship: Ship, upgrade_type: UpgradeType, effects: Dict[str, Any]) -> Dict[str, Any]:
        """Apply the stat changes for an upgrade to the ship and return a summary of changes."""
        updated = {}

        if upgrade_type == UpgradeType.ENGINE:
            speed_bonus = effects["speed_bonus"]
            ship.current_speed += speed_bonus
            updated["current_speed"] = ship.current_speed

        elif upgrade_type == UpgradeType.CARGO_HOLD:
            # Cargo capacity is stored in the cargo JSONB or derived from spec;
            # we store a cargo_capacity_bonus in cargo JSONB for the service layer to use.
            if not ship.cargo or not isinstance(ship.cargo, dict):
                ship.cargo = {}
            current_bonus = ship.cargo.get("_capacity_bonus_percent", 0)
            ship.cargo["_capacity_bonus_percent"] = current_bonus + effects["cargo_bonus_percent"]
            flag_modified(ship, 'cargo')
            updated["cargo_capacity_bonus_percent"] = ship.cargo["_capacity_bonus_percent"]

        elif upgrade_type == UpgradeType.SHIELD:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            shield_bonus = effects["shield_bonus"]
            combat["max_shields"] = combat.get("max_shields", 0) + shield_bonus
            combat["shields"] = combat.get("shields", 0) + shield_bonus
            ship.combat = combat
            flag_modified(ship, 'combat')
            updated["max_shields"] = combat["max_shields"]
            updated["shields"] = combat["shields"]

        elif upgrade_type == UpgradeType.HULL:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            hull_bonus = effects["hull_bonus"]
            combat["max_hull"] = combat.get("max_hull", 0) + hull_bonus
            combat["hull"] = combat.get("hull", 0) + hull_bonus
            ship.combat = combat
            flag_modified(ship, 'combat')
            updated["max_hull"] = combat["max_hull"]
            updated["hull"] = combat["hull"]

        elif upgrade_type == UpgradeType.SENSOR:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            evasion_bonus = effects["evasion_bonus_percent"]
            base_evasion = combat.get("evasion", 0)
            combat["evasion"] = base_evasion + evasion_bonus
            ship.combat = combat
            flag_modified(ship, 'combat')
            updated["evasion"] = combat["evasion"]
            # Scan-range half of the Sensor upgrade (canon ship-systems.md §2.5;
            # NO-CANON per-level figure). `Ship` has no scanner_range column, so
            # the effective value is derived from the hull spec's base
            # scanner_range plus the (now incremented) Sensor level. Reported so
            # the upgrade UI / scan path can surface the wider reach.
            spec = self.db.query(ShipSpecification).filter(
                ShipSpecification.type == ship.type
            ).first()
            base_scanner_range = spec.scanner_range if spec and spec.scanner_range is not None else 0
            updated["scanner_range"] = self.effective_scanner_range(ship, base_scanner_range)

        elif upgrade_type == UpgradeType.DRONE_BAY:
            drone_bonus = effects["drone_capacity_bonus"]
            # Drone capacity is not a direct column on Ship; store in upgrades JSONB
            # which is already handled. The service layer reads max from spec + upgrades.
            updated["drone_capacity_bonus"] = drone_bonus

        elif upgrade_type == UpgradeType.GENESIS_CONTAINMENT:
            genesis_bonus = effects["genesis_capacity_bonus"]
            ship.max_genesis_devices += genesis_bonus
            updated["max_genesis_devices"] = ship.max_genesis_devices

        elif upgrade_type == UpgradeType.MAINTENANCE_SYSTEM:
            # Accumulate a cumulative failure-rate reduction into the maintenance JSONB.
            # Stored as a fraction (0.0–1.0); the failure-roll logic multiplies the spec's
            # base maintenance_rate by (1 - failure_rate_reduction). Clamp at 1.0 so the
            # cumulative reduction can never invert the rate.
            maintenance = ship.maintenance if isinstance(ship.maintenance, dict) else {}
            reduction = effects["failure_rate_reduction"]
            current_reduction = maintenance.get("failure_rate_reduction", 0)
            maintenance["failure_rate_reduction"] = min(1.0, current_reduction + reduction)
            ship.maintenance = maintenance
            flag_modified(ship, 'maintenance')
            updated["failure_rate_reduction"] = maintenance["failure_rate_reduction"]

        return updated

    def _apply_module_effects(self, ship: Ship) -> Dict[str, Any]:
        """SHIP-MODS §7 — bake-on-install: recompute the MODULE subsystem's total
        contribution from ``Ship.modules`` and re-derive each baked stat column so
        the column holds ``spec_base + legacy_upgrade_contribution + module_total``.

        Called by install_module / remove_module (WO-SM-3) AFTER the ``Ship.modules``
        JSONB has been mutated. This is recompute-from-installed *inside the module
        subsystem* — there is NO per-module symmetric inverse to maintain; removal
        just drops the slot entry and re-bakes (the win the spec prized).

        ───────────────────────────────────────────────────────────────────────────
        ZERO-DOUBLE-COUNT contract (the load-bearing detail):
        The baked columns already hold ``spec_base + legacy_upgrade_contribution``,
        written INCREMENTALLY (``+=`` / ``+ bonus``) by _apply_upgrade_effects — there
        is no source from which to re-derive the legacy contribution cheaply (cargo /
        maintenance bonuses are cumulative-in-JSONB, not back-computable from a level).
        So a naive ``column = spec_base + legacy + module`` is unwritable. Instead this
        method is a REPLACE: it stores the LAST-baked module total in
        ``Ship.modules["_baked"]`` and, on every re-bake, removes the previous module
        contribution and adds the freshly-computed one:

            column_new = column_current - previous_module_total + new_module_total

        Because the legacy contribution rides inside ``column_current`` untouched, the
        legacy bonus and the module bonus are each added EXACTLY ONCE. This makes the
        bake idempotent (re-baking identical modules is a no-op) and install→remove
        exactly reversible: install a `shield` module over a legacy SHIELD upgrade →
        ``max_shields == spec_base + upgrade + module``; remove it → restored exactly
        to ``spec_base + upgrade`` (§7.1 bake-correctness test). The caller mutates
        ``Ship.modules`` first, then calls this; it does NOT commit or flush.

        Defensive: null / {} / None modules → drains any previously-baked module
        contribution to zero (all modules removed). Missing catalog defs and stray
        slot records are skipped, never crash.
        """
        updated: Dict[str, Any] = {}

        modules = getattr(ship, "modules", None)
        if not isinstance(modules, dict):
            # No modules JSONB at all (hull predates feature / first-ever bake with
            # nothing installed): nothing to add and nothing prior to drain.
            return updated

        installed = modules.get("installed")
        if not isinstance(installed, dict):
            installed = {}

        # --- 0. WO-GC-C leg 4 — Citizen lapse-neutralization FIREWALL. -----------
        # Resolve the spec's per-slot `requires` predicates ONCE. For non-citizen
        # hulls (no gated slot) this is an empty map → zero per-bake overhead and the
        # owner is never resolved. For the Citizen Clipper, slot 3 carries "citizen":
        # if the ship's OWNER is not an active Galactic Citizen, that slot's module is
        # SKIPPED below (contributes 0). Because the bake is a REPLACE (column =
        # current − prev_baked + new_total), skipping the slot DROPS its contribution
        # from the baked column while lapsed and a later re-bake (nightly sweep / on
        # re-subscribe) RESTORES it — idempotent, and the install→remove _baked-delta
        # contract (§7.1) is preserved (it just sees a smaller new_total this bake).
        slot_requires: Dict[str, Any] = {}
        spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ship.type
        ).first()
        if spec is not None:
            slot_requires = {
                str(s["i"]): s.get("requires")
                for s in (spec.module_slots or {}).get("slots", []) or []
                if isinstance(s, dict) and "i" in s and s.get("requires")
            }
        # Lazy-resolve owner ONLY when a gated slot exists (the common path skips it).
        owner = None
        if slot_requires:
            owner = self.db.query(Player).filter(Player.id == ship.owner_id).first()

        # --- 1. Accumulate per-effect contributions from every installed module. ---
        # ordering §4.4:  per-module base  →  ×adjacency (Phase B; 1.0 here)  →  ×supercharge
        by_effect: Dict[str, List[float]] = {}
        for slot_key, m in installed.items():  # slot_key is a STR (JSON keys) — never assume int
            if not isinstance(m, dict):
                continue  # stray / malformed slot record
            # WO-GC-C leg 4 firewall: a citizen-conditional slot contributes 0 while
            # its owner's membership is lapsed (skip → dropped from the REPLACE bake).
            req = slot_requires.get(slot_key)
            if req and (owner is None or not requires_satisfied(self.db, owner, req)):
                continue
            cls = m.get("class")
            tier = m.get("tier")
            entry = self.MODULE_DEFINITIONS.get((cls, tier))
            if not entry:
                continue  # unknown (class, tier) — skip, don't crash
            base = entry.get("effects", {})

            adj = self._adjacency_factor(slot_key, cls, ship)   # Phase-B stub == 1.0
            sc = self.SUPERCHARGE_MULT if m.get("super_at_install") else 1.0

            for k, v in base.items():
                # Only NUMERIC effects accumulate/scale. Boolean/string effects
                # (tractor tow_capable / weapon_mode) are presence-flags handled by
                # their own consumers, NOT summed into a baked numeric column.
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                by_effect.setdefault(k, []).append(v * adj * sc)

        # --- 2. §4.2 FLAT best-3 cap per effect: keep only the 3 largest, sum them. ---
        module_totals: Dict[str, float] = {
            k: self._best_n_flat(vs, self.MODULE_STACK_BEST_N) for k, vs in by_effect.items()
        }

        # --- 3. The previously-baked module total (zero on first bake). ---
        prev = modules.get("_baked")
        if not isinstance(prev, dict):
            prev = {}

        # --- 4. Re-derive each baked column = current - prev_module + new_module. ---
        # Mirrors EXACTLY the columns _apply_upgrade_effects writes, so legacy
        # upgrades and modules share one accumulator (no consumer change).

        def _delta(effect_key: str) -> float:
            """Signed change to apply to a column for one effect this bake."""
            return module_totals.get(effect_key, 0) - prev.get(effect_key, 0)

        # engine.speed_bonus -> current_speed (additive; floor at base_speed).
        d = _delta("speed_bonus")
        if d:
            base_speed = getattr(ship, "base_speed", 0) or 0
            ship.current_speed = max(base_speed, (ship.current_speed or 0) + d)
            updated["current_speed"] = ship.current_speed

        # cargo.cargo_bonus_percent -> cargo._capacity_bonus_percent (INERT: baked,
        # no kernel consumer change — fix D).
        d = _delta("cargo_bonus_percent")
        if d:
            cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
            cargo["_capacity_bonus_percent"] = max(0, cargo.get("_capacity_bonus_percent", 0) + d)
            ship.cargo = cargo
            flag_modified(ship, "cargo")
            updated["cargo_capacity_bonus_percent"] = cargo["_capacity_bonus_percent"]

        # shield.shield_bonus -> combat.max_shields / shields.
        d = _delta("shield_bonus")
        if d:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            combat["max_shields"] = max(0, combat.get("max_shields", 0) + d)
            # Current shields rise with new capacity but never exceed the (re-derived)
            # max nor go negative.
            combat["shields"] = max(0, min(combat.get("shields", 0) + d, combat["max_shields"]))
            ship.combat = combat
            flag_modified(ship, "combat")
            updated["max_shields"] = combat["max_shields"]
            updated["shields"] = combat["shields"]

        # hull.hull_bonus -> combat.max_hull / hull.
        d = _delta("hull_bonus")
        if d:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            combat["max_hull"] = max(0, combat.get("max_hull", 0) + d)
            new_hull = min(combat.get("hull", 0) + d, combat["max_hull"])
            # Floor at 1 while the hull can hold any (never let a re-bake destroy the ship).
            combat["hull"] = max(1, new_hull) if combat["max_hull"] >= 1 else max(0, new_hull)
            ship.combat = combat
            flag_modified(ship, "combat")
            updated["max_hull"] = combat["max_hull"]
            updated["hull"] = combat["hull"]

        # sensor.evasion_bonus_percent -> combat.evasion. (scanner_range is DERIVED,
        # no per-instance column — handled by the scan path; not baked here, matching
        # _apply_upgrade_effects.)
        d = _delta("evasion_bonus_percent")
        if d:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            combat["evasion"] = max(0, combat.get("evasion", 0) + d)
            ship.combat = combat
            flag_modified(ship, "combat")
            updated["evasion"] = combat["evasion"]

        # maintenance.failure_rate_reduction -> maintenance.failure_rate_reduction
        # (clamp [0.0, 1.0]).
        d = _delta("failure_rate_reduction")
        if d:
            maintenance = ship.maintenance if isinstance(ship.maintenance, dict) else {}
            new_red = (maintenance.get("failure_rate_reduction", 0) or 0) + d
            maintenance["failure_rate_reduction"] = min(1.0, max(0.0, new_red))
            ship.maintenance = maintenance
            flag_modified(ship, "maintenance")
            updated["failure_rate_reduction"] = maintenance["failure_rate_reduction"]

        # genesis.genesis_capacity_bonus -> max_genesis_devices.
        d = _delta("genesis_capacity_bonus")
        if d:
            ship.max_genesis_devices = max(0, (ship.max_genesis_devices or 0) + d)
            updated["max_genesis_devices"] = ship.max_genesis_devices

        # EQUIPMENT-FAMILY effects (harvester.passive_income / lander.landing_bonus
        # / mining.mining_efficiency) + drone.drone_capacity_bonus are KERNEL-INERT
        # here (reviewer SM-2 HIGH gate-fix). Unlike engine/shield/hull/sensor/
        # maintenance/genesis (which write real scalar/JSONB stat columns above),
        # these four have NO scalar column on the Ship model — their legacy
        # consumers read them out of the equipment_slots JSONB that install_equipment
        # writes (quantum_service passive income, the landing/mining/tow paths). The
        # earlier draft wrote ship.passive_income/landing_bonus/mining_efficiency,
        # which DO NOT EXIST on Ship → silent hasattr no-ops (the HIGH). Rather than
        # ship a misleading dead write, the kernel tracks these totals in _baked
        # (below) — a correct, re-tune-safe snapshot — and DEFERS their consumer
        # persistence to WO-SM-3, which wires install_module + writes each
        # equipment-family effect into the equipment_slots key its existing consumer
        # already reads (no new consumer, no double-count vs install_equipment).
        # Surfaced in `updated` for observability so SM-3's bake-correctness test can
        # assert the tracked totals.
        for _inert_key in (
            "passive_income", "landing_bonus", "mining_efficiency", "drone_capacity_bonus",
        ):
            if _inert_key in module_totals or _inert_key in prev:
                updated[_inert_key] = module_totals.get(_inert_key, 0)

        # --- 5. Snapshot the new module total so the NEXT bake replaces it cleanly. ---
        # Boolean/string effects (tractor flags) are NOT in module_totals (they were
        # skipped) — they are presence flags read directly from Ship.modules by their
        # own consumers, not summed into a column, so they never enter the _baked
        # delta math.
        modules["_baked"] = module_totals
        ship.modules = modules
        flag_modified(ship, "modules")

        return updated

    @staticmethod
    def _best_n_flat(values: List[float], n: int) -> float:
        """§4.2 FLAT best-N cap: sum only the N LARGEST contributions; the rest
        contribute 0. The dumb anti-god-ship cap shipped in the kernel (the smooth
        geometric DR curve is the Phase-B swap-in). Empty -> 0.
        """
        if not values:
            return 0
        return sum(sorted(values, reverse=True)[:n])

    @staticmethod
    def _adjacency_factor(slot_key: str, module_class, ship: Ship) -> float:
        """§4.4 adjacency factor — Phase-B STUB. Same-class adjacency clustering
        multiplies a module's effect; in the Phase-A kernel adjacency is the
        identity (1.0) so the live multiplier chain is ``base → ×supercharge →
        best-3 cap`` only. The (x,y) lattice is authored in ShipSpecification.
        module_slots NOW so adjacency becomes a behaviour toggle here, never a
        re-migration.
        """
        return 1.0

    def _reverse_upgrade_effects(self, ship: Ship, upgrade_type: UpgradeType, effects: Dict[str, Any]) -> Dict[str, Any]:
        """Reverse the stat changes ONE level of an upgrade contributed — the exact
        inverse of ``_apply_upgrade_effects`` for a single level.

        Used by ``degrade_random_system`` when a mechanical failure drops an
        installed upgrade by one level (WO-AB). Because every effect in
        ``_apply_upgrade_effects`` is applied ADDITIVELY per level (``+=`` on a
        column, or ``+ bonus`` into a JSONB key), reversing exactly one level is
        the symmetric subtraction of that level's ``effect_per_level`` figure.
        Returns a summary dict of the new (post-reversal) stat values, mirroring
        the apply path's return contract.

        Defensive clamps: current shields/hull never exceed their (now lowered)
        max, and no derived stat is driven below 0 — a degrade must leave ship
        stats consistent, never negative.
        """
        updated: Dict[str, Any] = {}

        if upgrade_type == UpgradeType.ENGINE:
            speed_bonus = effects["speed_bonus"]
            # Floor at base_speed so a degrade can't push speed below the hull's
            # native floor (the move-cost path penalises current_speed < base_speed).
            ship.current_speed = max(ship.base_speed, ship.current_speed - speed_bonus)
            updated["current_speed"] = ship.current_speed

        elif upgrade_type == UpgradeType.CARGO_HOLD:
            if not ship.cargo or not isinstance(ship.cargo, dict):
                ship.cargo = {}
            current_bonus = ship.cargo.get("_capacity_bonus_percent", 0)
            ship.cargo["_capacity_bonus_percent"] = max(0, current_bonus - effects["cargo_bonus_percent"])
            flag_modified(ship, 'cargo')
            updated["cargo_capacity_bonus_percent"] = ship.cargo["_capacity_bonus_percent"]

        elif upgrade_type == UpgradeType.SHIELD:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            shield_bonus = effects["shield_bonus"]
            combat["max_shields"] = max(0, combat.get("max_shields", 0) - shield_bonus)
            # Current shields can't exceed the lowered max, and never go negative.
            combat["shields"] = max(0, min(combat.get("shields", 0) - shield_bonus, combat["max_shields"]))
            ship.combat = combat
            flag_modified(ship, 'combat')
            updated["max_shields"] = combat["max_shields"]
            updated["shields"] = combat["shields"]

        elif upgrade_type == UpgradeType.HULL:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            hull_bonus = effects["hull_bonus"]
            combat["max_hull"] = max(0, combat.get("max_hull", 0) - hull_bonus)
            # Current hull can't exceed the lowered max; floor at 1 so a degrade
            # never destroys the ship (consistent with the mine-detonation floor).
            new_hull = min(combat.get("hull", 0) - hull_bonus, combat["max_hull"])
            combat["hull"] = max(1, new_hull) if combat["max_hull"] >= 1 else max(0, new_hull)
            ship.combat = combat
            flag_modified(ship, 'combat')
            updated["max_hull"] = combat["max_hull"]
            updated["hull"] = combat["hull"]

        elif upgrade_type == UpgradeType.SENSOR:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            evasion_bonus = effects["evasion_bonus_percent"]
            combat["evasion"] = max(0, combat.get("evasion", 0) - evasion_bonus)
            ship.combat = combat
            flag_modified(ship, 'combat')
            updated["evasion"] = combat["evasion"]
            # Scanner range is DERIVED from the (already-decremented) Sensor level
            # via effective_scanner_range(), so no per-instance column to reverse —
            # report the recomputed effective value for parity with the apply path.
            spec = self.db.query(ShipSpecification).filter(
                ShipSpecification.type == ship.type
            ).first()
            base_scanner_range = spec.scanner_range if spec and spec.scanner_range is not None else 0
            updated["scanner_range"] = self.effective_scanner_range(ship, base_scanner_range)

        elif upgrade_type == UpgradeType.DRONE_BAY:
            # Drone capacity is derived from spec + the upgrades JSONB level
            # (no stored column), so decrementing the level — done by the caller —
            # IS the reversal. Report the per-level figure for symmetry.
            updated["drone_capacity_bonus"] = -effects["drone_capacity_bonus"]

        elif upgrade_type == UpgradeType.GENESIS_CONTAINMENT:
            genesis_bonus = effects["genesis_capacity_bonus"]
            ship.max_genesis_devices = max(0, ship.max_genesis_devices - genesis_bonus)
            updated["max_genesis_devices"] = ship.max_genesis_devices

        elif upgrade_type == UpgradeType.MAINTENANCE_SYSTEM:
            maintenance = ship.maintenance if isinstance(ship.maintenance, dict) else {}
            reduction = effects["failure_rate_reduction"]
            current_reduction = maintenance.get("failure_rate_reduction", 0)
            maintenance["failure_rate_reduction"] = max(0.0, current_reduction - reduction)
            ship.maintenance = maintenance
            flag_modified(ship, 'maintenance')
            updated["failure_rate_reduction"] = maintenance["failure_rate_reduction"]

        return updated

    def degrade_random_system(self, ship: Ship) -> Dict[str, Any]:
        """Mechanical-failure consequence (WO-AB): pick ONE random installed
        upgrade (level >= 1) on ``ship`` and drop it by one level, REVERSING that
        level's stat contribution so ship stats stay consistent.

        Failure-roll model (blessed, base_rate 2%/jump — DECISIONS Pending):
        ``movement_service`` fires this on a roll after a SUCCESSFUL jump. The
        player re-buys the lost level through the existing ``purchase_upgrade``
        flow — there is no repair endpoint.

        No-op contract: if the ship has no installed upgrades (``ship.upgrades``
        empty or every entry at level 0), nothing is degraded and
        ``{"degraded": False}`` is returned — a failed roll on an un-upgraded ship
        is a harmless miss. The caller is responsible for the surrounding
        transaction; this helper mutates the ship and flags JSONB but does NOT
        commit or flush.

        Returns ``{"degraded": True, "upgrade_type", "old_level", "new_level",
        "updated_stats"}`` on a real degrade, else ``{"degraded": False}``.
        """
        upgrades = ship.upgrades if isinstance(ship.upgrades, dict) else {}

        # Candidate upgrade-type strings with an installed level >= 1 that map to
        # a known UpgradeType (defensive against stray JSONB keys).
        candidates: List[UpgradeType] = []
        for type_value, level in upgrades.items():
            try:
                lvl = int(level)
            except (TypeError, ValueError):
                continue
            if lvl < 1:
                continue
            try:
                upgrade_type = UpgradeType(type_value)
            except ValueError:
                continue  # unknown key in the JSONB — skip, don't crash
            if upgrade_type not in self.UPGRADE_DEFINITIONS:
                continue
            candidates.append(upgrade_type)

        if not candidates:
            return {"degraded": False}

        chosen = random.choice(candidates)
        old_level = int(upgrades[chosen.value])
        new_level = old_level - 1

        # Reverse exactly the degraded level's stat contribution BEFORE/at the
        # same time as decrementing the counter — never leave the bonus applied.
        definition = self.UPGRADE_DEFINITIONS[chosen]
        updated_stats = self._reverse_upgrade_effects(ship, chosen, definition["effect_per_level"])

        # Decrement (or remove) the level in the upgrades JSONB.
        if new_level <= 0:
            del ship.upgrades[chosen.value]
        else:
            ship.upgrades[chosen.value] = new_level
        flag_modified(ship, 'upgrades')

        logger.info(
            "Mechanical failure on ship %s: %s degraded %d -> %d",
            getattr(ship, "id", "?"), chosen.value, old_level, new_level,
        )

        return {
            "degraded": True,
            "upgrade_type": chosen.value,
            "old_level": old_level,
            "new_level": new_level,
            "updated_stats": updated_stats,
        }

    def install_equipment(self, ship_id: uuid.UUID, player_id: uuid.UUID, equipment_key: str) -> Dict[str, Any]:
        """
        Install a piece of equipment on a ship. Validates ownership, compatibility,
        slot availability, and credits.
        """
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        if equipment_key not in self.EQUIPMENT_DEFINITIONS:
            return {"success": False, "message": f"Unknown equipment: {equipment_key}"}

        eq_def = self.EQUIPMENT_DEFINITIONS[equipment_key]

        # Check ship type compatibility
        if ship.type not in eq_def["compatible_ships"]:
            compatible_names = [st.value for st in eq_def["compatible_ships"]]
            return {
                "success": False,
                "message": (
                    f"{eq_def['name']} is not compatible with {ship.type.value}. "
                    f"Compatible ships: {', '.join(compatible_names)}"
                ),
            }

        # Check if already installed
        equipment_slots = ship.equipment_slots if hasattr(ship, 'equipment_slots') and ship.equipment_slots else {}
        if equipment_key in equipment_slots:
            return {
                "success": False,
                "message": f"{eq_def['name']} is already installed on this ship"
            }

        # Check credits
        cost = eq_def["cost"]
        if player.credits < cost:
            return {
                "success": False,
                "message": f"Insufficient credits. Need {cost:,}, have {player.credits:,}",
                "cost": cost,
                "player_credits": player.credits,
            }

        # Deduct credits
        player.credits -= cost

        # Add to equipment_slots JSONB
        if not hasattr(ship, 'equipment_slots') or not ship.equipment_slots:
            ship.equipment_slots = {}
        ship.equipment_slots[equipment_key] = {
            "installed_at": datetime.utcnow().isoformat(),
            "effects": eq_def["effects"],
        }
        flag_modified(ship, 'equipment_slots')

        # WO-DBB-QR1: the Quantum Harvester flips the dedicated slot flag (prereq for QR2).
        if equipment_key == "quantum_harvester":
            ship.quantum_harvester_slot = True

        self.db.flush()

        logger.info(
            f"Player {player_id} installed {eq_def['name']} on ship {ship.name} "
            f"for {cost:,} credits"
        )

        return {
            "success": True,
            "message": f"{eq_def['name']} installed successfully",
            "equipment": equipment_key,
            "cost_paid": cost,
            "remaining_credits": player.credits,
            "effects": eq_def["effects"],
        }

    def uninstall_equipment(self, ship_id: uuid.UUID, player_id: uuid.UUID, equipment_key: str) -> Dict[str, Any]:
        """
        Uninstall a piece of equipment from a ship. No credit refund.
        """
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        # Check if equipment is installed
        equipment_slots = ship.equipment_slots if hasattr(ship, 'equipment_slots') and ship.equipment_slots else {}
        if equipment_key not in equipment_slots:
            eq_name = self.EQUIPMENT_DEFINITIONS.get(equipment_key, {}).get("name", equipment_key)
            return {
                "success": False,
                "message": f"{eq_name} is not installed on this ship"
            }

        eq_def = self.EQUIPMENT_DEFINITIONS.get(equipment_key, {})
        eq_name = eq_def.get("name", equipment_key)

        # Remove from equipment_slots JSONB
        del ship.equipment_slots[equipment_key]
        flag_modified(ship, 'equipment_slots')

        # WO-DBB-QR1: removing the Quantum Harvester clears the slot flag.
        if equipment_key == "quantum_harvester":
            ship.quantum_harvester_slot = False

        self.db.flush()

        logger.info(
            f"Player {player_id} uninstalled {eq_name} from ship {ship.name} (no refund)"
        )

        return {
            "success": True,
            "message": f"{eq_name} uninstalled (no credit refund)",
            "equipment": equipment_key,
        }

    def purchase_mining_laser_upgrade(
        self, ship_id: uuid.UUID, player_id: uuid.UUID
    ) -> Dict[str, Any]:
        """Buy the next Mining Laser upgrade level (WO-MINING ladder).

        REUSES the existing purchase ritual (the same ``_get_ship_and_player``
        row-lock, credit check, and ``self.db.flush()`` used by
        ``purchase_upgrade`` / ``install_equipment``) — it does NOT fork that
        flow. The difference is the STORE: the laser level is a ``level: int``
        key inside ``equipment_slots["mining_laser"]`` (the slot mining_service.py
        reads), not the UpgradeType-keyed ``ship.upgrades`` JSONB. Per-level cost
        and the resulting yield multiplier come VERBATIM from the canon ladder
        (MINING_LASER_LADDER). Requires a Mining Laser already installed.
        """
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        ladder = self.MINING_LASER_LADDER["mining_laser_level"]
        max_level = ladder["max_level"]

        # The laser must be installed before it can be upgraded.
        equipment_slots = (
            ship.equipment_slots
            if hasattr(ship, "equipment_slots") and ship.equipment_slots
            else {}
        )
        slot = equipment_slots.get("mining_laser")
        if not isinstance(slot, dict):
            return {
                "success": False,
                "message": "No Mining Laser is installed on this ship",
            }

        try:
            current_level = int(slot.get("level", 0))
        except (TypeError, ValueError):
            current_level = 0

        if current_level >= max_level:
            return {
                "success": False,
                "message": f"Mining Laser is already at maximum level ({max_level})",
            }

        next_level = current_level + 1
        level_def = ladder["levels"][next_level]
        cost = level_def["cost"]

        if player.credits < cost:
            return {
                "success": False,
                "message": f"Insufficient credits. Need {cost:,}, have {player.credits:,}",
                "cost": cost,
                "player_credits": player.credits,
            }

        # Deduct credits and write the level INTO the equipment slot dict.
        player.credits -= cost
        slot["level"] = next_level
        ship.equipment_slots["mining_laser"] = slot
        flag_modified(ship, "equipment_slots")

        self.db.flush()

        logger.info(
            "Player %s upgraded Mining Laser to level %d on ship %s for %s credits",
            player_id, next_level, ship.name, f"{cost:,}",
        )

        return {
            "success": True,
            "message": f"Mining Laser upgraded to level {next_level}",
            "new_level": next_level,
            "max_level": max_level,
            "yield_multiplier": level_def["yield_multiplier"],
            "cost_paid": cost,
            "remaining_credits": player.credits,
        }

    # ========================================================================
    # SHIP-MODS (WO-SM-3): the install / remove ritual that DRIVES the SM-2 bake.
    # ------------------------------------------------------------------------
    # CRITICAL CONTRACT (proven in SM-2): _apply_module_effects is a _baked-delta
    # REPLACE. So install_module / remove_module MUST mutate
    # ``ship.modules["installed"]`` IN PLACE (read the dict → set/del the slot key
    # → reassign the SAME dict, PRESERVING the "_baked" key) and THEN call
    # _apply_module_effects + flag_modified. NEVER replace the whole modules dict —
    # that wipes ``_baked`` and the re-bake double-adds the module delta.
    # The None → {"v":1, "installed":{}} transition on first install mirrors how
    # install_equipment seeds equipment_slots on first use.
    # ========================================================================

    def _resolve_docked_shipyard_station(self, player: Player):
        """Return (station, error_dict). The player must be docked at a station
        that offers shipyard services to fit/strip modules. Mirrors the shipyard
        gate purchase_ship uses (_station_offers_shipyard).

        Returns (None, error) if not docked or the docked station is not a
        shipyard; (station, None) on success.
        """
        # Imported here to avoid a module-level import cycle (routes import this
        # service; the gate helper lives beside the routes).
        from src.models.station import Station
        from src.api.routes.ship_upgrades import _station_offers_shipyard

        if not getattr(player, "is_docked", False) or not player.current_port_id:
            return None, {"success": False, "message": "You must be docked at a shipyard to fit modules"}
        station = self.db.query(Station).filter(Station.id == player.current_port_id).first()
        if not station:
            return None, {"success": False, "message": "Docked station not found"}
        if not _station_offers_shipyard(station):
            return None, {"success": False, "message": "This station does not offer shipyard services"}
        return station, None

    @staticmethod
    def _spec_slot(spec: ShipSpecification, slot_index: int) -> Optional[Dict[str, Any]]:
        """Find the slot record with ``i == slot_index`` in a spec's module_slots
        lattice, or None. module_slots shape:
            {"v":1,"cols":int,"rows":int,"slots":[{"i","x","y","super","class","requires"}]}
        """
        ms = getattr(spec, "module_slots", None)
        if not isinstance(ms, dict):
            return None
        for slot in ms.get("slots", []) or []:
            if isinstance(slot, dict) and slot.get("i") == slot_index:
                return slot
        return None

    def install_module(
        self,
        ship_id: uuid.UUID,
        player_id: uuid.UUID,
        slot_index: int,
        module_class: str,
        tier: int,
    ) -> Dict[str, Any]:
        """Fit a module of ``(module_class, tier)`` into ``slot_index`` on a ship,
        then RE-BAKE the stat columns via _apply_module_effects.

        Guards (reusing install_equipment's): ownership / not-destroyed / credits
        via _get_ship_and_player; ADDED — shipyard gate (must be docked at a
        shipyard), the slot must EXIST in the spec lattice and be EMPTY, the
        module's slot_class must match the slot's class (a class-locked slot only
        accepts its own class; an unlocked ``class:null`` slot accepts any), hull
        compatibility (compatible_ships), and the (NO-CANON, currently open)
        ``requires`` eligibility predicate.

        The slot record is written IN PLACE into ``ship.modules["installed"]``
        (None → {"v":1,"installed":{}} on first install, PRESERVING any "_baked"
        key), then _apply_module_effects re-derives the baked columns. Returns the
        baked deltas in ``updated_stats``.
        """
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        # --- catalog lookup ---
        entry = self.MODULE_DEFINITIONS.get((module_class, tier))
        if not entry:
            return {"success": False, "message": f"Unknown module: {module_class} Mk{tier}"}

        # --- deferred equipment-family guard (reviewer LOW#2 fix) ---
        # harvester/lander/mining/tractor bake into Ship.modules["_baked"] but their
        # effect is NOT yet wired to its equipment_slots consumer (the deferred MED) —
        # so installing one would CHARGE 20-40k cr for a runtime-INERT module (a
        # pay-for-nothing trap). Block install until the consumer-wiring follow-up
        # lands; the family stays catalog-LISTED (get_ship_modules / the UI can show
        # it as "coming soon") so it surfaces for when it's unblocked.
        if module_class in _EQUIPMENT_FAMILY_DEFERRED:
            return {
                "success": False,
                "message": (
                    f"{entry['name']} is not yet installable — its runtime effect is "
                    f"pending consumer wiring (it would be inert if fitted). Coming soon."
                ),
                "consumer_inert": True,
            }

        # --- shipyard gate ---
        station, gate_error = self._resolve_docked_shipyard_station(player)
        if gate_error:
            return gate_error

        # --- hull compatibility (None == open to all hulls) ---
        compatible = entry.get("compatible_ships")
        if compatible is not None and ship.type not in compatible:
            compatible_names = [st.value for st in compatible]
            return {
                "success": False,
                "message": (
                    f"{entry['name']} is not compatible with {ship.type.value}. "
                    f"Compatible hulls: {', '.join(compatible_names)}"
                ),
            }

        # --- requires eligibility predicate (None == open; Citizen tier is DATA,
        # not code — the seam is built, the predicate stays open until ruled). ---
        requires = entry.get("requires")
        if requires is not None and not requires_satisfied(self.db, player, requires):
            # Eligibility not met — fail closed (never a silent bypass). The
            # resolver handles "citizen" (membership) live; faction predicates
            # fail closed until GC-C/Exalted wires them (WO-GC-B §4.1).
            return {
                "success": False,
                "message": f"{entry['name']} requires membership you don't currently hold ({requires})",
            }

        # --- resolve the spec slot lattice ---
        spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ship.type
        ).first()
        slot = self._spec_slot(spec, slot_index) if spec else None
        if slot is None:
            return {"success": False, "message": f"This hull has no module slot {slot_index}"}

        # --- slot_class match: a class-locked slot only accepts its own class;
        # an unlocked (class:null) slot accepts any module. ---
        slot_class = slot.get("class")
        module_slot_class = entry.get("slot_class")
        if slot_class is not None and module_slot_class != slot_class:
            return {
                "success": False,
                "message": (
                    f"Slot {slot_index} is a '{slot_class}' slot; "
                    f"{entry['name']} does not fit it"
                ),
            }

        # --- slot must be EMPTY ---
        modules = getattr(ship, "modules", None)
        if not isinstance(modules, dict):
            # None → first-ever install: seed the grid (preserve nothing — there is
            # no prior _baked). Mirrors install_equipment's None → {} transition.
            modules = {"v": 1, "installed": {}}
        installed = modules.get("installed")
        if not isinstance(installed, dict):
            installed = {}
            modules["installed"] = installed
        slot_key = str(slot_index)
        if slot_key in installed:
            return {"success": False, "message": f"Module slot {slot_index} is already occupied"}

        # --- credits ---
        cost = entry["cost"]
        if player.credits < cost:
            return {
                "success": False,
                "message": f"Insufficient credits. Need {cost:,}, have {player.credits:,}",
                "cost": cost,
                "player_credits": player.credits,
            }
        player.credits -= cost

        # --- write the slot record IN PLACE (preserve "_baked"); snapshot the
        # slot's supercharge flag at install so a later lattice re-tune can't
        # silently re-buff a fielded ship (§4.1). ---
        installed[slot_key] = {
            "class": module_class,
            "tier": tier,
            "super_at_install": bool(slot.get("super")),
            "installed_at": datetime.utcnow().isoformat(),
        }
        # Reassign the SAME modules dict (carrying its "_baked" key untouched) so
        # the bake's delta math stays correct.
        ship.modules = modules

        # --- re-bake: re-derive every baked stat column from the new installed set ---
        updated_stats = self._apply_module_effects(ship)
        flag_modified(ship, "modules")

        self.db.flush()

        logger.info(
            "Player %s installed %s in slot %s on ship %s for %s credits",
            player_id, entry["name"], slot_index, ship.name, f"{cost:,}",
        )

        result = {
            "success": True,
            "message": f"{entry['name']} installed in slot {slot_index}",
            "module": {"class": module_class, "tier": tier, "slot_index": slot_index},
            "supercharged": bool(slot.get("super")),
            "cost_paid": cost,
            "remaining_credits": player.credits,
            "updated_stats": updated_stats,
        }
        # EQUIPMENT-FAMILY families (harvester/lander/mining/tractor) are baked but
        # NOT yet wired to their equipment_slots consumers — see the class-level
        # note + _apply_module_effects. Surface the deferral so the caller/UI can
        # warn the player the module is install-but-inert.
        if module_class in _EQUIPMENT_FAMILY_DEFERRED:
            result["consumer_inert"] = True
            result["consumer_note"] = (
                f"The {module_class} module's runtime effect is not yet wired to its "
                f"consumer (deferred follow-up); it is fitted and baked but inert."
            )
        return result

    def remove_module(
        self,
        ship_id: uuid.UUID,
        player_id: uuid.UUID,
        slot_index: int,
    ) -> Dict[str, Any]:
        """Strip the module out of ``slot_index`` and RE-BAKE — the re-bake recomputes
        the baked columns from the now-smaller installed set, restoring them exactly
        to ``spec_base + legacy_upgrade_contribution`` (§7.1 reversibility).

        Shipyard-gated. Refunds ``int(catalog_cost × SALVAGE_FRACTION)`` credits.
        The slot is DROPPED IN PLACE from ``ship.modules["installed"]`` (preserving
        "_baked"); _apply_module_effects then re-derives the columns. Returns the
        refund + restored deltas.
        """
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        # --- shipyard gate ---
        station, gate_error = self._resolve_docked_shipyard_station(player)
        if gate_error:
            return gate_error

        modules = getattr(ship, "modules", None)
        if not isinstance(modules, dict):
            return {"success": False, "message": f"Module slot {slot_index} is empty"}
        installed = modules.get("installed")
        if not isinstance(installed, dict):
            installed = {}
            modules["installed"] = installed
        slot_key = str(slot_index)
        record = installed.get(slot_key)
        if not isinstance(record, dict):
            return {"success": False, "message": f"Module slot {slot_index} is empty"}

        module_class = record.get("class")
        tier = record.get("tier")
        entry = self.MODULE_DEFINITIONS.get((module_class, tier))
        # Refund the salvage fraction of the catalog cost (0 if the def vanished —
        # never gift credits from a stray slot record).
        cost = entry["cost"] if entry else 0
        refund = int(cost * self.SALVAGE_FRACTION)

        # --- drop the slot IN PLACE (preserve "_baked"), then re-bake ---
        del installed[slot_key]
        ship.modules = modules  # same dict, "_baked" intact
        updated_stats = self._apply_module_effects(ship)
        flag_modified(ship, "modules")

        if refund > 0:
            player.credits += refund

        self.db.flush()

        logger.info(
            "Player %s removed module from slot %s on ship %s (refund %s credits)",
            player_id, slot_index, ship.name, f"{refund:,}",
        )

        result = {
            "success": True,
            "message": f"Module removed from slot {slot_index} (salvage refund {refund:,} cr)",
            "module": {"class": module_class, "tier": tier, "slot_index": slot_index},
            "refund": refund,
            "remaining_credits": player.credits,
            "updated_stats": updated_stats,
        }
        if module_class in _EQUIPMENT_FAMILY_DEFERRED:
            result["consumer_inert"] = True
        return result

    # --- Galactic-Citizen L1 cosmetics (WO-GC-B) ----------------------------
    def get_cosmetics(self, ship_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        """Return the cosmetic catalog + the ship's applied overlay + the
        player's live Citizen status (so the UI can render the skin + the
        "Galactic Citizen" label, greying it when membership has lapsed).
        Owner-only read; no mutation."""
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {"success": False, "message": "Player not found"}
        ship = self.db.query(Ship).filter(Ship.id == ship_id).first()
        if not ship:
            return {"success": False, "message": "Ship not found"}
        if ship.owner_id != player_id:
            return {"success": False, "message": "You do not own this ship"}
        modules = ship.modules if isinstance(ship.modules, dict) else {}
        applied = modules.get("cosmetics") if isinstance(modules.get("cosmetics"), dict) else {}
        return {
            "success": True,
            "ship_id": str(ship.id),
            "catalog": CITIZEN_COSMETICS,
            "applied": applied,
            "is_galactic_citizen": is_galactic_citizen(self.db, player),
        }

    def set_cosmetic(
        self, ship_id: uuid.UUID, player_id: uuid.UUID, slot: str, value: Optional[str]
    ) -> Dict[str, Any]:
        """Apply (or clear, value=None) a Citizen cosmetic overlay on a ship.
        Owner-only + Citizen-gated (the resolver). Cosmetics live in
        Ship.modules.cosmetics, OUTSIDE `installed`, so a skin never eats a
        finite slot. Zero stat effect (firewall)."""
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        entry = CITIZEN_COSMETICS.get(slot)
        if entry is None:
            return {"success": False, "message": f"Unknown cosmetic slot '{slot}'"}
        if value is not None and value not in entry["values"]:
            return {"success": False, "message": f"'{value}' is not a valid {slot} cosmetic"}

        if not requires_satisfied(self.db, player, entry.get("requires")):
            return {
                "success": False,
                "message": "Citizen cosmetics require an active Galactic Citizen membership.",
                "requires_citizen": True,
            }

        # JSONB mutation discipline: copy → set/clear → reassign + flag_modified.
        modules = dict(ship.modules) if isinstance(ship.modules, dict) else {"v": 1, "installed": {}}
        cosmetics = dict(modules.get("cosmetics") or {})
        if value is None:
            cosmetics.pop(slot, None)
        else:
            cosmetics[slot] = value
        modules["cosmetics"] = cosmetics
        ship.modules = modules
        flag_modified(ship, "modules")
        self.db.flush()  # route owns the commit (matches install/remove_module)

        return {
            "success": True,
            "message": (f"Cleared {slot} cosmetic" if value is None
                        else f"Applied {entry['label']}: {value}"),
            "ship_id": str(ship.id),
            "cosmetics": cosmetics,
        }


# SHIP-MODS §5.1 — the tiered module catalog, keyed by (class, tier). Assigned
# after the class body because it is built from the @staticmethod expanders
# (_build_module_definitions → _scale_effects) which reference the class-level
# §5.2/§5.3 constants. The §7 bake loop reads MODULE_DEFINITIONS[(class, tier)].
ShipUpgradeService.MODULE_DEFINITIONS = ShipUpgradeService._build_module_definitions()
