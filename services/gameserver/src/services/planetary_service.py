"""
Planetary management service for handling planet operations.

This service manages planetary colonization, resource allocation,
building construction, defenses, and sieges.
"""

from typing import Dict, Any, Optional, List
from uuid import UUID, uuid4
from datetime import datetime, timedelta, UTC
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func, text
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.exc import OperationalError
import logging

from src.core.game_time import canonical_hours_since
from src.services.structures import _via_settle_guard
from src.models.player import Player
from src.models.planet import Planet, PlanetType, player_planets
from src.models.sector import Sector
from src.models.ship import Ship
from src.models.genesis_device import GenesisDevice, GenesisType, GenesisStatus, PlanetFormation
from src.models.team import Team

logger = logging.getLogger(__name__)

# Siege configuration constants
SIEGE_TURNS_THRESHOLD = 3       # Consecutive turns enemies must be present to trigger siege
SIEGE_MORALE_LOSS_PER_TURN = 5  # Morale % lost per turn under siege
SIEGE_PRODUCTION_PENALTY = 0.25 # 25% production reduction during siege

# Low-habitability resource-cost penalty (WO-F5; canon anchor
# FEATURES/planets/colonization.md "Low-habitability resource cost penalty",
# lines 211-213). A marginally habitable world spends extra on life support and
# environmental control, so its NET output is lower than an identical hospitable
# world. There is no separate consumption/upkeep ledger in this service — the
# production-rate formula yields the per-day NET the colony banks — so the canon
# "+20% resource costs" is realized here by netting the three commodity output
# rates DOWN by the same fraction (the extra cost is paid out of output), applied
# exactly like SIEGE_PRODUCTION_PENALTY's siege_multiplier just below it.
#
# NO-CANON: the doc marks this 📐 Design-only and the threshold/percentage are
# given as a TARGET ("< 30", "+20%"), not a settled rule. LOW_HABITABILITY_
# THRESHOLD = 30 and LOW_HABITABILITY_PRODUCTION_PENALTY = 0.20 are the WO/doc
# target values — FLAGGED for DECISIONS, not invented. Below the threshold the
# colony already DECLINES in population (HABITABILITY_GROWTH_THRESHOLD = 20);
# this penalty additionally taxes commodity output across the marginal band
# (hab < 30), so a hab-20 world both shrinks AND nets ~20% less than a hab-50
# world with identical allocations/buildings.
LOW_HABITABILITY_THRESHOLD = 30           # NO-CANON: target threshold (doc line 213)
LOW_HABITABILITY_PRODUCTION_PENALTY = 0.20  # NO-CANON: target "+20% resource costs"
# ADR-0076 retired the flat DEFENSE_UPGRADE_COST=1000/level path (upgrade_defense);
# citadel level unlocks tiers and defense_unit_price (per added unit) prices them.
DEFENSE_MAX_LEVEL = 10          # Maximum defense level
# Per-unit credit cost to ADD planetary defense units (ADR-0076 "Scaled defense
# pricing", Accepted). The price is no longer flat: it scales with citadel level
# and planet type so that fortified, hostile-terrain worlds cost meaningfully
# more to garrison. The server MUST charge the scaled price or the UI's "you can
# afford this" gate is a lie and defenses are an economic faucet. Reducing units
# is free (no refund); Genesis pre-installed defenses stay free (they are seeded
# directly, never routed through update_defenses).
#
#   per-unit price = round_to_nearest_10(
#       BASE[unit] x CITADEL_MULT[citadel_level] x PLANET_MOD[planet_type]
#   )
#
# Worked examples (ADR-0076):
#   L1 turret  Terran     = round(500 x 1.0 x 0.75) = 380   (375 -> 380, half-up)
#   L5 turret  Gas Giant  =       500 x 3.0 x 1.5   = 2250
#   L5 fighter Gas Giant  =      2000 x 3.0 x 1.5   = 9000
DEFENSE_UNIT_BASE_COST = {"turrets": 500, "shields": 1000, "fighters": 2000}

# Citadel-level price multiplier (ADR-0076). citadel_level <= 1 or null -> 1.0;
# anything above 5 clamps to the L5 multiplier.
DEFENSE_CITADEL_MULT = {1: 1.0, 2: 1.25, 3: 1.6, 4: 2.2, 5: 3.0}

# Planet-type price multiplier (ADR-0076), keyed by PlanetType enum.
# Terran/Oceanic 0.75 (hospitable) · Mountainous/Arctic 1.0 · Desert/Volcanic
# 1.25 · Gas Giant/Barren 1.5 (most hostile). Any PlanetType NOT listed here
# falls back to 1.0 — this default is NO-CANON (ADR-0076 names only the eight
# types above), so it is FLAGGED: ICE, JUNGLE, TROPICAL, ARTIFICIAL currently
# resolve to the 1.0 tier by this fallback rather than by an explicit canon rule.
DEFENSE_PLANET_MOD = {
    PlanetType.TERRAN: 0.75,
    PlanetType.OCEANIC: 0.75,
    PlanetType.MOUNTAINOUS: 1.0,
    PlanetType.ARCTIC: 1.0,
    PlanetType.DESERT: 1.25,
    PlanetType.VOLCANIC: 1.25,
    PlanetType.GAS_GIANT: 1.5,
    PlanetType.BARREN: 1.5,
}
# NO-CANON fallback for any PlanetType not in DEFENSE_PLANET_MOD (see note above).
DEFENSE_PLANET_MOD_DEFAULT = 1.0


def defense_unit_price(unit_type: str, citadel_level: Optional[int], planet_type) -> int:
    """ADR-0076 scaled per-unit price for adding one planetary defense unit.

        price = round_to_nearest_10(BASE x CITADEL_MULT x PLANET_MOD)

    citadel_level <= 1 or None -> the L1 (1.0) multiplier; > 5 clamps to L5 (3.0).
    planet_type accepts a PlanetType enum member (resolved directly) or its
    string value/name (resolved leniently); unrecognized types use the NO-CANON
    1.0 default. Rounding is HALF-UP to the nearest 10 (e.g. 375 -> 380).
    """
    base = DEFENSE_UNIT_BASE_COST[unit_type]

    # Citadel multiplier: clamp the level into the 1..5 table.
    level = citadel_level or 0
    if level <= 1:
        citadel_mult = DEFENSE_CITADEL_MULT[1]
    elif level >= 5:
        citadel_mult = DEFENSE_CITADEL_MULT[5]
    else:
        citadel_mult = DEFENSE_CITADEL_MULT[level]

    # Planet-type multiplier: accept the enum directly, else resolve a string.
    planet_mod = None
    if isinstance(planet_type, PlanetType):
        planet_mod = DEFENSE_PLANET_MOD.get(planet_type)
    elif planet_type is not None:
        key = str(planet_type).upper().replace(" ", "_").replace("-", "_")
        for member in PlanetType:
            if member.value == key or member.name == key:
                planet_mod = DEFENSE_PLANET_MOD.get(member)
                break
    if planet_mod is None:
        planet_mod = DEFENSE_PLANET_MOD_DEFAULT

    raw = base * citadel_mult * planet_mod
    # Round HALF-UP to the nearest 10 using integer arithmetic (avoids the
    # banker's-rounding surprise the stdlib round() would give on a *.5 boundary).
    return int((raw + 5) // 10) * 10
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

# Siege stockpile plunder (FEATURES/planets/defense.md "Siege" → "Resource theft":
# "a fraction of generated commodities should transfer to the besieger" — was
# 📐 Design-only, "penalty is applied but no transfer". This is that transfer.
# Each APPLIED siege turn skims a small fraction of the besieged planet's
# stockpiles (fuel_ore / organics / equipment) into the besieger's hold. It runs
# inside _apply_siege_turn, which fires EXACTLY ONCE per applied siege turn (the
# advance_siege anchor — siege_turns — guarantees a turn is applied at most once),
# so the skim is idempotent across Loop-A re-reads / scheduler sweeps: no
# double-skim, no skim per Loop-A pass.
#
# NO-CANON: defense.md says "a fraction" but gives no number. SIEGE_STOCKPILE_
# SKIM_FRACTION = 0.05 (5% of each stockpile commodity per applied siege turn) is
# a deliberately CONSERVATIVE choice — at 5%/day a stockpile decays geometrically
# (≈ half drained after ~14 siege turns), so a sustained siege meaningfully bleeds
# the colony without instantly emptying it on the first applied turn. FLAGGED for
# DECISIONS; easier to raise than to claw back an over-tuned plunder faucet.
SIEGE_STOCKPILE_SKIM_FRACTION = 0.05  # NO-CANON: stockpile fraction skimmed per applied siege turn
# The three plunderable planetary stockpile columns and the cargo-contents key
# each maps to (matching combat_service._transfer_cargo's commodity contents).
SIEGE_STOCKPILE_COMMODITIES = (
    ("fuel_ore", "ore"),
    ("organics", "organics"),
    ("equipment", "equipment"),
)

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
    6: {"name": "Fortress Shield", "strength": 20000, "regen_per_hour": 2500, "cost": 750000},
    7: {"name": "Citadel Shield", "strength": 30000, "regen_per_hour": 3500, "cost": 1000000},
    8: {"name": "Planetary Shield", "strength": 40000, "regen_per_hour": 5000, "cost": 1500000},
    9: {"name": "Quantum Shield", "strength": 50000, "regen_per_hour": 6500, "cost": 2000000},
    10: {"name": "Impervious Shield", "strength": 75000, "regen_per_hour": 7500, "cost": 3000000},
}

# Shield-generator upgrades are time-based (ADR-0086): upgrading to level N takes
# N x 6 hours (L1 = 6h ... L10 = 60h). Stored as a JSONB anchor on
# planet.active_events['shield_upgrade'] = {from, to, started_at, complete_at}
# (no migration) and settled lazily on read, mirroring the defense-building queue.
SHIELD_GENERATOR_BUILD_HOURS_PER_LEVEL = 6

# Colony specialization multipliers (ADR-0087). Single source of truth, shared
# with combat_service for the defense multiplier. "production" scales commodity
# output (applied in _calculate_production_rates); "defense" scales the planet's
# damage reduction + shield HP in combat (combat_service._calculate_planetary_
# defense_reduction); "research" scales the research-point yield below. Balanced
# is a +10% all-round generalist (no longer a no-op).
SPECIALIZATION_BONUSES = {
    "agricultural": {
        "production": {"fuel": 0.8, "organics": 1.5, "equipment": 0.8, "colonists": 1.2},
        "defense": 0.9, "research": 0.8,
    },
    "industrial": {
        "production": {"fuel": 0.9, "organics": 0.8, "equipment": 1.5, "colonists": 0.9},
        "defense": 1.0, "research": 0.9,
    },
    "military": {
        "production": {"fuel": 0.9, "organics": 0.9, "equipment": 1.1, "colonists": 0.8},
        "defense": 1.5, "research": 0.8,
    },
    "research": {
        "production": {"fuel": 0.8, "organics": 0.8, "equipment": 0.9, "colonists": 0.9},
        "defense": 0.8, "research": 1.5,
    },
    "balanced": {
        "production": {"fuel": 1.1, "organics": 1.1, "equipment": 1.1, "colonists": 1.1},
        "defense": 1.1, "research": 1.1,
    },
}

# Research-point yield (ADR-0087): a research planet accrues research points per
# day from its Research Lab level, scaled by the specialization research
# multiplier (+ citadel bonus, − siege). Accrued lazily into
# active_events['research_points'] (JSONB, no migration), mirroring commodity
# production. NOTE: the SINK for research points (what they unlock) is an open
# design decision — see DECISIONS colony-research-points-sink.
RESEARCH_POINTS_PER_LAB_LEVEL_PER_DAY = 25

# Canon daily colonist growth (FEATURES/planets/colonization.md "Population
# growth"): colonist_rate = colonists × 0.01 × (habitability_score / 100),
# i.e. base growth = 1% per day, scaled linearly by habitability.
DAILY_GROWTH_BASE = 0.01
SECONDS_PER_DAY = 86400.0

# Surplus-pioneer faucet (FEATURES/economy/lifecycle.md §1.4 "Colonist sales"):
# an owned, colonized planet produces SURPLUS PIONEERS over time (births,
# retirees seeking transit, voluntary outbound migrants). These accrue into
# planet.active_events['surplus_pioneers'] (a running integer counter; JSONB, no
# migration) on the production tick, with sub-unit progress banked in
# production_carry['surplus_pioneers']. The accrued surplus is sold at a Class-0
# Pioneer Office (pioneer_service.sell_planet_surplus) at 30-80 cr/pioneer.
#
# Canon TARGET (lifecycle.md §1.4): "a fully-developed planet should yield
# 1,000-5,000 cr/day in pioneer-export contracts." The accrual is proportional
# to the colony's working population so a fresh 100-colonist outpost yields a
# trickle while a developed L4/L5 colony (50,000-200,000 colonists) hits the
# band. At a representative colonist-sale price of ~55 cr (midpoint of the 30-80
# range), 1,000-5,000 cr/day == ~18-91 pioneers/day. The rate below puts a
# 50,000-colonist developed world at 50,000 × 0.0005 = 25 pioneers/day ≈ ~1,375
# cr/day — deliberately near the LOW edge of the canon band (easier to raise
# than to claw back an over-minted faucet).
#
# NO-CANON: the exact per-colonist surplus rate is not specified — canon gives
# only the cr/day TARGET band, not a pioneers/colonist coefficient. This value
# is FLAGGED for DECISIONS; it is bounded by, and consistent with, the canon
# target band above. See WO-PL3-v2 report.
SURPLUS_PIONEER_RATE_PER_DAY = 0.0005  # NO-CANON: pioneers/colonist/day (faucet)

# Habitability ZERO-CROSSING for natural population growth (WO-AH, Max-ruled:
# "growth is a function of habitability — ABOVE a threshold → GROW, BELOW it →
# DECLINE"). CANON anchor: FEATURES/planets/colonization.md line 95 — "BARREN and
# ICE planets have negative natural growth … the colony shrinks" — and the same
# doc's design note (line 186): "the production tick needs an explicit decline
# branch when habitability_score < threshold (e.g., < 20)."
#
# HABITABILITY_GROWTH_THRESHOLD is that crossing point. AT or ABOVE it the
# colony grows on the unchanged canon formula (so genesis worlds, hab 40–90, and
# every other habitable world grow EXACTLY as before — no behavioral change for
# them). BELOW it the colony declines. THRESHOLD = 20 is the value the canon
# design note literally suggests; it makes the harsh worlds shrink (nexus
# generation: BARREN 10–40, VOLCANIC 10–30, low-end ICE) while keeping genesis
# worlds and every DESERT-or-better world firmly positive.
#
# DAILY_DECLINE_BASE is the per-day decline slope: the rate scales with how far
# BELOW the threshold a world sits, so a near-uninhabitable hab-0 BARREN shrinks
# faster than a hab-19 marginal world, and a freshly-terraformed world hovering
# just under the threshold barely loses anyone before crossing into growth.
# Daily decline = -colonists × DAILY_DECLINE_BASE × (THRESHOLD − habitability)/100.
# Worst case (hab 0, BARREN): -colonists × 0.01 × 20/100 = -0.2%/day — exactly the
# canon-table magnitude for ICE (line 89) and twice the BARREN figure (line 88);
# the slope keeps decline gentle and recoverable, never a cliff. NO-CANON: the
# exact THRESHOLD (20) and DECLINE_BASE (0.01) values are flagged for DECISIONS;
# they are bounded by, and consistent with, the canon decline note above.
HABITABILITY_GROWTH_THRESHOLD = 20
DAILY_DECLINE_BASE = 0.01

# Per-tick elapsed cap. CANON: SYSTEMS/planetary-production-tick.md "Failure
# modes" — "Tick scheduler runs late (huge elapsed) | Cap elapsed at 24 hours
# per tick to prevent runaway growth." The lazy advance-on-read realization of
# the tick must honor the same cap: a planet idle for days credits at most 24h
# of production/growth on the next tick. Combined with invariant 4
# (`last_production` monotonically non-decreasing) the canon semantics are:
# advance the durable anchor by ONLY the consumed elapsed (min(elapsed, cap)),
# NOT to now — so the backlog naturally drains 24h per subsequent tick rather
# than being silently forfeited. Applies to both production accrual and
# population growth so the two anchors behave identically.
MAX_TICK_ELAPSED_SECONDS = 86400.0  # 24 hours

# Per-PlanetType production-efficiency multipliers. CANON: the planet-type
# efficiency table in FEATURES/planets/production.md ("Planet-type efficiency
# table") and the matching column block in FEATURES/planets/colonization.md
# ("Planet types"). The doc names the welcome-world type "TERRA"; the enum
# (models/planet.py:PlanetType) spells it "TERRAN" — mapped to the documented
# TERRA row (0/0/0: a Capital welcome world doesn't produce). The table folds
# directly into _calculate_production_rates as a per-resource multiplier on top
# of building / specialization / citadel / siege modifiers.
#
# CANON GAP (flagged, NOT invented): PlanetType also defines GAS_GIANT, JUNGLE,
# ARCTIC and TROPICAL, which the canon efficiency table does not list. Per the
# WO-O instruction ("If a specific type's number isn't in canon, FLAG it and use
# a neutral 1.0"), each of those four gets a neutral {1.0, 1.0, 1.0} and is
# recorded in NEUTRAL_TYPE_EFFICIENCY_TYPES so the gap is auditable rather than
# silently guessed. See DECISIONS write-back note in the WO-O report.
TYPE_EFFICIENCY = {
    # type:                 fuel,  organics, equipment
    PlanetType.TERRAN:      {"fuel": 0.0, "organics": 0.0, "equipment": 0.0},
    PlanetType.MOUNTAINOUS: {"fuel": 0.6, "organics": 0.4, "equipment": 1.5},
    PlanetType.OCEANIC:     {"fuel": 1.5, "organics": 0.4, "equipment": 0.6},
    PlanetType.DESERT:      {"fuel": 0.4, "organics": 1.5, "equipment": 0.6},
    PlanetType.VOLCANIC:    {"fuel": 1.0, "organics": 0.0, "equipment": 2.0},
    PlanetType.BARREN:      {"fuel": 0.0, "organics": 0.0, "equipment": 1.5},
    PlanetType.ICE:         {"fuel": 0.8, "organics": 1.2, "equipment": 0.5},
}
# Neutral 1.0 fallback for the four enum types absent from the canon table.
NEUTRAL_TYPE_EFFICIENCY = {"fuel": 1.0, "organics": 1.0, "equipment": 1.0}
NEUTRAL_TYPE_EFFICIENCY_TYPES = (
    PlanetType.GAS_GIANT,
    PlanetType.JUNGLE,
    PlanetType.ARCTIC,
    PlanetType.TROPICAL,
)


def type_efficiency_for(planet_type) -> Dict[str, float]:
    """Per-resource production multiplier for a PlanetType (canon table above).

    Returns a neutral {1.0, 1.0, 1.0} for any type not in the canon table
    (the four flagged GAP types, or a NULL/unknown type from legacy data) so an
    un-canonized planet type never zeroes or inflates production by accident.
    """
    return TYPE_EFFICIENCY.get(planet_type, NEUTRAL_TYPE_EFFICIENCY)


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

        # This is a hot read endpoint that now WRITES (lazy growth + production
        # accrual commit on every read). Fail fast under row-lock contention so a
        # leaked FOR UPDATE lock elsewhere can never hang all planet reads into a
        # 504 cascade — on timeout we serve the data without persisting accrual
        # (the un-advanced anchor makes the next read catch up).
        try:
            self.db.execute(text("SET LOCAL lock_timeout = '3s'"))
        except OperationalError:
            self.db.rollback()

        # Lazily apply colonist growth (population growth is NOT one of the spine's three clocks,
        # §9 — it stays a pre-settle lazy call), then settle the planetary clocks.
        changed = self.apply_population_growth(planet)

        # Siege LIFECYCLE stays BEFORE settle() (§5.2): _detect_siege may LIFT a stale siege
        # (enemies gone OR owner present) — canon (defense.md) requires owner ABSENCE, so the owner
        # standing on the planet (the common case for this owner-facing read) must LIFT, not decay.
        # settle()'s siege substep only ADVANCES morale on a siege that STILL holds; it never
        # starts/lifts. _detect_siege commits its own state change.
        if planet.under_siege:
            self._detect_siege(planet, planet.owner_id or player_id)

        # CRT WO-K1a cutover: ONE planetary tick replaces the direct apply_resource_production +
        # advance_siege calls — production + (held) siege morale + terraforming + research faucet,
        # each on its own inner anchor in its own clock domain (no `now` threaded in).
        from src.services.structures import settle
        changed = settle(planet, db=self.db).changed or changed
        if changed:
            try:
                self.db.commit()
            except OperationalError:
                # Row-lock timeout (a leaked/long lock held the planet row):
                # abandon this read's accrual rather than 504. Re-fetch the
                # planet so formatting reflects committed DB state.
                self.db.rollback()
                planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
                if not planet:
                    raise ValueError("Planet not found or not owned by player")
                logger.warning(
                    f"get_planet_details: lock timeout on planet {planet_id}; "
                    f"served without persisting lazy accrual"
                )

        return self._format_planet_data(planet)

    def apply_population_growth(self, planet: Planet) -> bool:
        """Lazily apply canon colonist growth/decline since planet.last_growth_at.

        Canon daily formula (FEATURES/planets/colonization.md "Population
        growth"): colonist_rate = colonists × 0.01 × (habitability_score/100),
        pro-rated here by elapsed wall-clock time.

        Habitability ZERO-CROSSING (WO-AH, Max-ruled): growth is a function of
        habitability with a crossing point at HABITABILITY_GROWTH_THRESHOLD.
        At/above the threshold the colony GROWS on the unchanged canon formula
        (habitable worlds behave exactly as before). Below it the colony
        DECLINES — colonists are lost at -colonists × DAILY_DECLINE_BASE ×
        (THRESHOLD − habitability)/100 per day, realizing the canon promise
        (colonization.md line 95) that BARREN/ICE worlds shrink without
        immigration or terraforming. Terraforming a harsh world past the
        threshold flips the same colony from decline back into growth.

        Decline floors `colonists` at 0 — a colony can shrink toward
        abandonment but never goes negative, and the planet row is never
        deleted here (a future abandonment/claimable pass owns that).

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

        # 24h per-tick elapsed cap (CANON: planetary-production-tick.md "Failure
        # modes"). A planet idle for days credits at most 24h of growth per tick;
        # the growth branch below advances the anchor by only the consumed
        # (capped) window so any > 24h backlog drains 24h per subsequent read
        # rather than producing a runaway jump. The short-circuit branches
        # (siege / nothing-to-grow / at-ceiling) advance straight to `now`
        # because they produce nothing — there is no backlog worth draining.
        capped_elapsed = min(elapsed_seconds, MAX_TICK_ELAPSED_SECONDS)

        # Siege halts population growth (colonization.md "Other growth
        # modifiers"); besieged time yields nothing, so advance the anchor.
        if planet.under_siege:
            planet.last_growth_at = now
            return True

        colonists = planet.colonists or 0
        habitability = max(planet.habitability_score or 0, 0)

        if colonists <= 0:
            # No colonists to grow or lose; keep the anchor current so future
            # colonists don't grow (or decline) retroactively.
            planet.last_growth_at = now
            return True

        # ── Habitability zero-crossing (WO-AH) ─────────────────────────────
        # Below the threshold the colony DECLINES; the harsh world cannot
        # sustain its population until terraforming raises habitability past
        # the crossing point. This branch fully owns the below-threshold case
        # (its own anchor banking + floor-at-0) and returns; the growth path
        # below runs only for at/above-threshold worlds, unchanged.
        if habitability < HABITABILITY_GROWTH_THRESHOLD:
            decline_per_day = (
                colonists
                * DAILY_DECLINE_BASE
                * ((HABITABILITY_GROWTH_THRESHOLD - habitability) / 100.0)
            )
            decline_per_second = decline_per_day / SECONDS_PER_DAY
            # capped_elapsed already honors the 24h per-tick cap so a colony
            # idle for days loses at most one capped window per read, draining
            # any backlog 24h per subsequent read (mirrors the growth path).
            lost = int(decline_per_second * capped_elapsed)
            if lost <= 0:
                # Not enough elapsed time to lose a whole colonist yet — leave
                # the anchor untouched so the remainder keeps accruing.
                return False

            # Floor at 0: a colony shrinks toward abandonment but never goes
            # negative, and the planet row is never deleted here.
            if lost >= colonists:
                lost = colonists
                planet.last_growth_at = now
            else:
                # Consume only the whole-colonist time; bank the remainder so a
                # slow decline is never robbed of sub-colonist progress.
                seconds_consumed = lost / decline_per_second
                planet.last_growth_at = anchor + timedelta(seconds=seconds_consumed)

            planet.colonists = colonists - lost
            # Mirror the loss in the demographic total so a declining colony
            # actually shrinks on screen; keep population >= colonists (the
            # working-age subset never exceeds the total).
            planet.population = max(planet.colonists, (planet.population or 0) - lost)
            logger.debug(
                f"Lazy decline on planet {planet.id}: -{lost} colonists "
                f"(now {planet.colonists}, habitability {habitability} "
                f"< threshold {HABITABILITY_GROWTH_THRESHOLD})"
            )
            return True
        # ───────────────────────────────────────────────────────────────────

        rate_per_day = colonists * DAILY_GROWTH_BASE * (habitability / 100.0)
        if rate_per_day <= 0:
            # Nothing can grow (zero habitability at/above threshold is
            # impossible, but guard anyway); keep the anchor current.
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
        gained = int(rate_per_second * capped_elapsed)
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

    def apply_resource_production(self, planet: Planet, *, _via_settle: bool = False) -> bool:
        """Lazily accrue commodity production since planet.last_production.

        Mirrors apply_population_growth: production rates from
        _calculate_production_rates (per-day, already including building,
        specialization, citadel and siege modifiers) are pro-rated by elapsed
        wall-clock time and added to the planet's fuel_ore/organics/equipment
        stockpiles. This is the lazy advance-on-read realization of the
        documented production tick (SYSTEMS/planetary-production-tick.md) — no
        scheduler.

        Sub-unit progress is banked exactly in a per-resource fractional carry
        stored in planet.active_events['production_carry'] (JSONB, no migration),
        so a fast resource never robs a slow one of its accruing fraction when
        the anchor advances. Stockpiles are uncapped for now (the storage-cap
        formula is design-only); the citadel safe provides the protected,
        capacity-limited store.

        Returns True if any state changed (whole units accrued, or the anchor
        was initialized/advanced) so callers know to commit.

        ``_via_settle`` (CRT spine, WO-K1a): the structures.settle() spine passes True; any other
        caller (direct/legacy, legit while DORMANT pre-cutover) leaves it False. Post-cutover a
        False call is a stray surviving clock-writer — the guard makes it loud under tests (I5).
        ``now`` is NOT a spine value here: this body reads its OWN wall-clock anchor as shipped.
        """
        _via_settle_guard("apply_resource_production", _via_settle)
        now = datetime.now(UTC)

        if planet.last_production is None:
            # First read since the column landed: anchor now, accrue later.
            planet.last_production = now
            return True

        anchor = planet.last_production
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)

        elapsed_seconds = (now - anchor).total_seconds()
        if elapsed_seconds <= 0:
            return False

        # 24h per-tick elapsed cap (CANON: planetary-production-tick.md "Failure
        # modes" — "Cap elapsed at 24 hours per tick to prevent runaway growth").
        # A planet idle for days accrues at most 24h of production on this tick;
        # crucially the anchor advances by ONLY the consumed window (anchor +
        # capped_elapsed), NOT to now — so a 72h-idle planet yields 24h now and
        # the remaining backlog drains 24h per subsequent tick (invariant 4:
        # last_production monotonically non-decreasing). Advancing to now would
        # silently forfeit the excess. The production_carry fractional bank below
        # is preserved unchanged and reflects exactly this 24h window.
        capped_elapsed = min(elapsed_seconds, MAX_TICK_ELAPSED_SECONDS)

        rates = self._calculate_production_rates(planet)
        # Map production-rate keys to stockpile columns.
        resource_cols = (("fuel", "fuel_ore"), ("organics", "organics"), ("equipment", "equipment"))
        research_rate = rates.get("research", 0) or 0  # ADR-0087 research-point yield

        # Surplus-pioneer faucet (lifecycle.md §1.4): an OWNED, colonized colony
        # produces surplus pioneers proportional to its working population. Gated
        # on ownership (owner_id set), live colonists, and NOT under siege (a
        # besieged colony exports no one — mirrors colonist_rate=0 under siege).
        # Shares this method's idempotent anchor/carry so it can never
        # double-accrue across a settle()+read on the same window.
        surplus_rate = 0.0
        if (
            planet.owner_id is not None
            and (planet.colonists or 0) > 0
            and not planet.under_siege
        ):
            surplus_rate = (planet.colonists or 0) * SURPLUS_PIONEER_RATE_PER_DAY

        if (
            all((rates.get(key, 0) or 0) <= 0 for key, _ in resource_cols)
            and research_rate <= 0
            and surplus_rate <= 0
        ):
            # Nothing producing (no commodity allocation AND no research lab AND
            # no surplus-pioneer faucet); keep the anchor current so a later
            # allocation doesn't accrue retroactively. No backlog to drain when
            # nothing is produced, so advancing fully to now is correct here (no
            # runaway risk).
            planet.last_production = now
            return True

        events = planet.active_events if isinstance(planet.active_events, dict) else {}
        carry = dict(events.get("production_carry", {})) if isinstance(events.get("production_carry"), dict) else {}

        gains = {}
        for key, col in resource_cols:
            rate_per_day = rates.get(key, 0) or 0
            produced = carry.get(col, 0.0) + rate_per_day * (capped_elapsed / SECONDS_PER_DAY)
            gained = int(produced)
            gains[col] = (gained, produced - gained)

        # Research points accrue into a running active_events counter (no column);
        # the fractional remainder shares the production_carry dict under 'research'.
        research_produced = carry.get("research", 0.0) + research_rate * (capped_elapsed / SECONDS_PER_DAY)
        research_gained = int(research_produced)

        # Surplus pioneers accrue into a running active_events counter (no column),
        # exactly like research points; the fractional remainder shares the
        # production_carry dict under 'surplus_pioneers'. (lifecycle.md §1.4)
        surplus_produced = carry.get("surplus_pioneers", 0.0) + surplus_rate * (capped_elapsed / SECONDS_PER_DAY)
        surplus_gained = int(surplus_produced)

        if sum(g for g, _ in gains.values()) + research_gained + surplus_gained <= 0:
            # Not enough elapsed time for a whole unit of anything yet; leave the
            # anchor (and stored carry) untouched so fractions keep banking
            # against the growing elapsed window.
            return False

        for col, (gained, remainder) in gains.items():
            if gained > 0:
                setattr(planet, col, (getattr(planet, col) or 0) + gained)
            carry[col] = remainder

        carry["research"] = research_produced - research_gained
        # Bank the surplus-pioneer fractional remainder only when the faucet is
        # active or already carrying — don't pollute every researching planet's
        # carry dict with a 0.0 surplus key.
        if surplus_rate > 0 or "surplus_pioneers" in carry:
            carry["surplus_pioneers"] = surplus_produced - surplus_gained

        new_events = dict(events)
        new_events["production_carry"] = carry
        # Only stamp research_points on planets that actually research (or already
        # carry the key) — don't pollute every producing planet's JSONB with a 0.
        if research_gained > 0 or "research_points" in events:
            new_events["research_points"] = int(events.get("research_points", 0) or 0) + research_gained
        # Likewise stamp surplus_pioneers only when whole pioneers accrued or the
        # counter already exists, so an empty/un-owned planet's JSONB stays clean.
        if surplus_gained > 0 or "surplus_pioneers" in events:
            new_events["surplus_pioneers"] = int(events.get("surplus_pioneers", 0) or 0) + surplus_gained
        planet.active_events = new_events
        # Advance the anchor by ONLY the consumed (capped) window, not to now, so
        # a backlog > 24h drains 24h per subsequent tick rather than being
        # silently forfeited (CANON: ≤24h production per tick; invariant 4:
        # last_production monotonically non-decreasing). When elapsed ≤ 24h this
        # is exactly `now` (anchor + elapsed_seconds), preserving the prior
        # behavior for the common case.
        planet.last_production = anchor + timedelta(seconds=capped_elapsed)

        logger.debug(
            f"Lazy production on planet {planet.id}: "
            + ", ".join(f"+{g} {c}" for c, (g, _) in gains.items() if g > 0)
            + (f", +{research_gained} research" if research_gained > 0 else "")
            + (f", +{surplus_gained} surplus_pioneers" if surplus_gained > 0 else "")
        )
        return True

    def realize_production(self, planet: Planet, *, _via_settle: bool = False) -> bool:
        """Force-advance one planet's commodity production to the canonical now.

        Scheduler/admin-facing alias for the lazy advance-on-read accrual
        (apply_resource_production). Extracted so the production sweep and the
        admin /tick endpoint can drive a planet's production forward WITHOUT a
        player read of get_planet_details, exactly as terraforming/siege use
        _advance_terraforming / advance_siege off the read path.

        Idempotency is inherited unchanged from apply_resource_production: the
        durable per-planet anchor is planet.last_production, with sub-unit
        progress banked in active_events['production_carry']. Only the elapsed
        time that produced whole units is consumed from the anchor; running it
        twice in quick succession (e.g. scheduler sweep + admin tick + a player
        read) accrues exactly elapsed × rate once and is a no-op thereafter —
        never double-counting. Mutates the planet; the CALLER commits (mirrors
        the terraforming/siege advance methods the sweep already drives).

        Returns True if any state changed (units accrued, or the anchor was
        initialized/advanced) so the caller knows to commit.
        """
        # Thin force-alias: pass the spine token straight through to the engine (so a
        # settle()-driven force-advance does not trip its own guard).
        return self.apply_resource_production(planet, _via_settle=_via_settle)

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
        # no refund). ADR-0076 scaled pricing — each added unit is charged the
        # citadel- and planet-type-scaled per-unit price (defense_unit_price),
        # not a flat rate. Mirrors the client DefenseConfiguration cost so the
        # UI's affordability gate is honest. Without this, defenses are free.
        new_turrets = max(0, turrets) if turrets is not None else planet.defense_turrets
        new_shields = max(0, shields) if shields is not None else planet.defense_shields
        new_fighters = max(0, fighters) if fighters is not None else planet.defense_fighters
        cost = (
            defense_unit_price("turrets", planet.citadel_level, planet.type)
            * max(0, new_turrets - (planet.defense_turrets or 0))
            + defense_unit_price("shields", planet.citadel_level, planet.type)
            * max(0, new_shields - (planet.defense_shields or 0))
            + defense_unit_price("fighters", planet.citadel_level, planet.type)
            * max(0, new_fighters - (planet.defense_fighters or 0))
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

        # Calculate total defense power. The citadel contributes a passive
        # defensive garrison on top of the deployed units (WO-G6): the full
        # value of the current citadel level, plus — WHILE an upgrade is in
        # progress — 50% of the next level's passive-defense delta (the other
        # 50% lands on completion when citadel_level increments). Idle citadels
        # add only their current-level value; no citadel adds nothing.
        from src.services.citadel_service import citadel_passive_defense_rating
        defense_power = (
            planet.defense_turrets * 10 +
            planet.defense_shields * 5 +
            planet.defense_fighters * 2 +
            citadel_passive_defense_rating(planet)
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
        # if the siege is about to lift this read, the turns that already elapsed under it must
        # still be applied — detecting first would clear siege_started_at and silently forgive that
        # decay. CRT WO-K1a: settle() advances the held siege (+ other clocks, each idempotent).
        from src.services.structures import settle
        _settle_res = settle(planet, db=self.db)

        # Run siege detection to get current state (may lift the siege)
        siege_info = self._detect_siege(planet, player_id)

        if _settle_res.changed:
            # settle() mutated clocks (morale/siege_turns/production/terraform); _detect_siege
            # already committed its own changes, so persist the settle too.
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

        # Settle accrued morale decay BEFORE re-evaluating siege validity (S1), so a siege that
        # lifts this turn still applies the elapsed decay rather than forgiving it when
        # _detect_siege clears siege_started_at. CRT WO-K1a: settle() advances the held siege.
        from src.services.structures import settle
        _settle_res = settle(planet, db=self.db)

        siege_info = self._detect_siege(planet, owner_id)

        if _settle_res.changed:
            # _detect_siege committed its own changes; persist the settle too.
            self.db.commit()

        return {
            "underSiege": planet.under_siege,
            "changed": siege_info.get("state_changed", False) or ("siege" in _settle_res.steps_changed),
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

        # 5. Resource theft (defense.md "Siege" → "Resource theft"): skim a small
        # fraction of the besieged planet's stockpiles to the besieger this turn.
        # This is the ONE per-applied-siege-turn path, so the skim happens exactly
        # once per turn (idempotent across Loop-A re-reads — no double-skim).
        plundered = self._skim_siege_stockpiles(planet)
        if plundered:
            effects_applied["stockpilePlunder"] = plundered

        # Increment siege turn counter
        planet.siege_turns = (planet.siege_turns or 0) + 1
        effects_applied["siegeTurns"] = planet.siege_turns

        return effects_applied

    def _skim_siege_stockpiles(self, planet: Planet) -> Dict[str, int]:
        """Transfer a conservative fraction of the besieged planet's stockpiles to
        the besieger (defense.md "Siege" → "Resource theft").

        Called from _apply_siege_turn — i.e. exactly ONCE per APPLIED siege turn
        (siege_turns is the applied-turn marker; advance_siege only applies the
        pending = elapsed - applied delta), so this is idempotent: no double-skim
        across Loop-A re-reads or scheduler sweeps.

        Skims SIEGE_STOCKPILE_SKIM_FRACTION of each plunderable stockpile column
        (fuel_ore / organics / equipment) off the planet and deposits the looted
        commodities into the besieger's current ship hold, clamped to the ship's
        remaining cargo capacity (mirroring combat_service._transfer_cargo so the
        plunder cannot overflow the besieger's hold). The planet row is already
        held by the siege path (the caller's settle()/read lock); the besieger row
        + ship row are locked here before any mutation. Nothing leaves the planet
        unless it is accepted by the besieger's hold — credits/units are conserved.

        Returns the per-commodity amounts actually moved (empty if nothing moved).
        """
        attacker_id = planet.siege_attacker_id
        if not attacker_id:
            return {}

        # Compute the would-be skim per stockpile column FIRST; if the planet has
        # nothing worth taking, do no work (and acquire no locks).
        wanted: Dict[str, int] = {}  # cargo-contents key -> qty wanted off the planet
        col_for_key: Dict[str, str] = {}  # cargo-contents key -> planet column name
        for column, cargo_key in SIEGE_STOCKPILE_COMMODITIES:
            stock = int(getattr(planet, column, 0) or 0)
            if stock <= 0:
                continue
            take = int(stock * SIEGE_STOCKPILE_SKIM_FRACTION)
            if take <= 0:
                continue
            wanted[cargo_key] = take
            col_for_key[cargo_key] = column
        if not wanted:
            return {}

        # Lock the besieger (player row) THEN the ship row before mutating — same
        # lock order combat uses (planet row already held by the caller).
        besieger = (
            self.db.query(Player)
            .filter(Player.id == attacker_id)
            .with_for_update()
            .first()
        )
        if not besieger or not besieger.current_ship_id:
            # Besieger gone or in no ship to receive cargo — skim nothing this turn
            # (defense.md says "transfer to the besieger"; with no hold, no transfer).
            return {}

        ship = (
            self.db.query(Ship)
            .filter(Ship.id == besieger.current_ship_id)
            .with_for_update()
            .first()
        )
        if not ship:
            return {}

        ship_cargo = ship.cargo or {}
        contents: Dict[str, int] = dict(ship_cargo.get("contents") or {})
        capacity = int(ship_cargo.get("capacity", 0) or 0)
        used = sum(int(q) for q in contents.values() if isinstance(q, (int, float)))
        remaining = max(0, capacity - used)
        if remaining <= 0:
            return {}

        moved: Dict[str, int] = {}
        for cargo_key, take in wanted.items():
            if remaining <= 0:
                break
            move = min(take, remaining)
            if move <= 0:
                continue
            column = col_for_key[cargo_key]
            # Remove from the planet stockpile (never below zero) ...
            current_stock = int(getattr(planet, column, 0) or 0)
            move = min(move, current_stock)
            if move <= 0:
                continue
            setattr(planet, column, current_stock - move)
            # ... and add to the besieger's hold.
            contents[cargo_key] = int(contents.get(cargo_key, 0)) + move
            remaining -= move
            moved[cargo_key] = move

        if not moved:
            return {}

        ship_cargo["contents"] = contents
        ship_cargo["used"] = sum(int(q) for q in contents.values())
        ship.cargo = ship_cargo
        flag_modified(ship, "cargo")

        logger.info(
            "Siege plunder: planet %s skimmed %s to besieger %s (ship %s)",
            planet.id, moved, besieger.id, ship.id,
        )
        return moved

    def advance_siege(self, planet: Planet, *, _via_settle: bool = False) -> bool:
        """
        Advance siege progression: apply every siege turn accrued since the
        siege began (same lazy advance-on-read pattern as colonist growth and
        terraforming). Driven both on read AND on a fixed cadence by the
        npc_scheduler planetary-advance sweep, so a besieged planet keeps
        losing morale even when its owner never re-opens the planet screen.
        Time-accurate and idempotent, so the two paths reconcile cleanly
        regardless of which runs first.

        Anchor arithmetic: `siege_turns` doubles as the applied-turn marker.
        At siege onset _detect_siege leaves it exactly at
        SIEGE_TURNS_THRESHOLD (the escalation counter that triggered the
        siege), so turns applied since onset = siege_turns - threshold.
        Elapsed turns derive from siege_started_at via canonical hours
        (GAME_TIME_SCALE-aware) at SIEGE_TURN_HOURS per turn.

        Mutates the planet (morale, siege_turns); caller commits.
        Returns True if any turns were applied.

        ``_via_settle`` (CRT spine): True from structures.settle()'s siege substep; reads its OWN
        canonical anchor (siege_started_at) as shipped — no spine ``now`` threaded in.
        """
        _via_settle_guard("advance_siege", _via_settle)
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

    def _get_shield_upgrade(self, planet: Planet) -> Optional[Dict[str, Any]]:
        """Return the in-progress shield-upgrade anchor from active_events, or None."""
        events = planet.active_events
        if isinstance(events, dict):
            su = events.get("shield_upgrade")
            return dict(su) if isinstance(su, dict) else None
        return None

    def _set_shield_upgrade(self, planet: Planet, data: Optional[Dict[str, Any]]) -> None:
        """Persist (data) or clear (None) the shield-upgrade anchor in active_events JSONB."""
        events = planet.active_events
        if not isinstance(events, dict):
            events = {"legacy_events": events} if events else {}
        events = dict(events)
        if data is None:
            events.pop("shield_upgrade", None)
        else:
            events["shield_upgrade"] = data
        planet.active_events = events

    def _settle_shield_upgrade(self, planet: Planet, now: datetime) -> bool:
        """Apply a shield upgrade whose build timer has elapsed (lazy advance-on-read).

        Mirrors the citadel/defense-building lazy-settle: when the timer is done the
        generator level + shield strength advance to the target and the anchor clears.
        Returns True if anything changed so the caller can persist.
        """
        su = self._get_shield_upgrade(planet)
        if not su:
            return False
        complete_at = su.get("complete_at")
        done = True
        if complete_at:
            try:
                done = now >= datetime.fromisoformat(complete_at)
            except (ValueError, TypeError):
                done = True  # malformed timestamp: settle rather than strand the upgrade
        if not done:
            return False
        to_level = max(0, min(SHIELD_GENERATOR_MAX_LEVEL, int(su.get("to", planet.defense_shields or 0))))
        info = SHIELD_GENERATOR_LEVELS.get(to_level, SHIELD_GENERATOR_LEVELS[0])
        planet.defense_shields = to_level
        planet.shields = info["strength"]
        self._set_shield_upgrade(planet, None)
        self.db.flush()
        logger.info(
            "Shield generator upgrade completed to level %s (%s) on planet %s (id=%s)",
            to_level, info["name"], planet.name, planet.id,
        )
        return True

    def upgrade_shield_generator(
        self,
        planet_id: UUID,
        player_id: UUID
    ) -> Dict[str, Any]:
        """
        Begin a time-based shield-generator upgrade (ADR-0086).

        Shield generators provide planetary shields that absorb damage during
        attacks and sieges. Each level increases shield strength, regeneration
        rate, and cost. Uses planet.defense_shields to track the generator level
        and planet.shields for the current shield strength value.

        Credits are charged up front; the level + strength advance only when the
        build timer (target level x 6 hours) elapses, settled lazily on read.
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

        # Settle any already-finished upgrade FIRST so a completed-but-unsettled
        # build doesn't block the next one.
        now = datetime.now(UTC)
        self._settle_shield_upgrade(planet, now)

        if self._get_shield_upgrade(planet):
            su = self._get_shield_upgrade(planet)
            raise ValueError(
                f"Shield generator upgrade already in progress (to level {su.get('to')})"
            )

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

        # Charge credits now; the level/strength advance on completion (settle).
        player.credits -= upgrade_cost
        build_hours = next_level * SHIELD_GENERATOR_BUILD_HOURS_PER_LEVEL
        complete_at = now + timedelta(hours=build_hours)
        self._set_shield_upgrade(planet, {
            "from": current_level,
            "to": next_level,
            "started_at": now.isoformat(),
            "complete_at": complete_at.isoformat(),
        })

        self.db.commit()
        self.db.refresh(player)

        logger.info(
            "Shield generator upgrade started: level %s -> %s (%s), %sh, on planet %s (id=%s)",
            current_level, next_level, next_level_info["name"], build_hours, planet.name, planet.id,
        )

        return {
            "success": True,
            "upgrading": True,
            "shieldGenerator": {
                "fromLevel": current_level,
                "toLevel": next_level,
                "maxLevel": SHIELD_GENERATOR_MAX_LEVEL,
                "name": next_level_info["name"],
                "strength": next_level_info["strength"],
                "regenPerHour": next_level_info["regen_per_hour"],
            },
            "buildHours": build_hours,
            "startedAt": now.isoformat(),
            "completeAt": complete_at.isoformat(),
            "remainingSeconds": build_hours * 3600,
            "creditsCost": upgrade_cost,
            "creditsRemaining": player.credits,
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

        # Lazy advance-on-read: apply a shield upgrade whose timer has elapsed.
        now = datetime.now(UTC)
        if self._settle_shield_upgrade(planet, now):
            self.db.commit()

        # Shield generator info
        shield_level = planet.defense_shields or 0
        shield_info = SHIELD_GENERATOR_LEVELS.get(shield_level, SHIELD_GENERATOR_LEVELS[0])

        # In-progress timed upgrade (ADR-0086), if any
        upgrade_block = None
        su = self._get_shield_upgrade(planet)
        if su:
            complete_at = su.get("complete_at")
            remaining_seconds = 0
            if complete_at:
                try:
                    remaining_seconds = max(
                        0, int((datetime.fromisoformat(complete_at) - now).total_seconds())
                    )
                except (ValueError, TypeError):
                    remaining_seconds = 0
            to_level = int(su.get("to", shield_level + 1))
            to_info = SHIELD_GENERATOR_LEVELS.get(to_level, {})
            upgrade_block = {
                "fromLevel": int(su.get("from", shield_level)),
                "toLevel": to_level,
                "toName": to_info.get("name"),
                "startedAt": su.get("started_at"),
                "completeAt": complete_at,
                "remainingSeconds": remaining_seconds,
            }

        # Next level upgrade cost (only meaningful when not already upgrading)
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
                    "buildHours": (shield_level + 1) * SHIELD_GENERATOR_BUILD_HOURS_PER_LEVEL,
                } if next_level_info else None,
                "isUpgrading": upgrade_block is not None,
                "upgrade": upgrade_block,
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

        # Settle pending morale decay BEFORE clearing siege state (S1): the turns that elapsed
        # while the siege stood are earned and must be applied; clearing siege_started_at first
        # would discard them. CRT WO-K1a: settle() credits the held siege's final turns.
        from src.services.structures import settle
        settle(planet, db=self.db)

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
            # Current commodity stockpiles + the accrual anchor so the client can
            # project a live per-second count between polls (keys match productionRates).
            "stockpiles": {
                "fuel": planet.fuel_ore or 0,
                "organics": planet.organics or 0,
                "equipment": planet.equipment or 0,
                # ADR-0087 research-point yield (accrued in active_events; no column).
                "research": int((planet.active_events or {}).get("research_points", 0))
                if isinstance(planet.active_events, dict) else 0,
            },
            "lastProductionAt": planet.last_production.isoformat() if planet.last_production else None,
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
            # Genesis formation state so the Colonial Registry can show a live
            # "forming — Nh remaining" readout (genesis-devices.md formation UI).
            "formationStatus": getattr(planet, "formation_status", None),
            "formationStartedAt": planet.formation_started_at.isoformat() if getattr(planet, "formation_started_at", None) else None,
            "formationCompleteAt": planet.formation_complete_at.isoformat() if getattr(planet, "formation_complete_at", None) else None,
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

        # Planet-type efficiency (CANON: FEATURES/planets/production.md formula
        # `resource += ... × planet_type_efficiency × ...`; per-type table in
        # production.md / colonization.md). A VOLCANIC world produces 0 organics
        # and 2× equipment; a TERRA(N) welcome world produces nothing. Applied
        # to the three commodity rates only — colonist growth is governed by the
        # habitability formula below, and the per-type growth bias is captured
        # there via habitability_score (the per-type negative-growth branch is
        # design-only per colonization.md, so not enforced here).
        type_eff = type_efficiency_for(planet.type)
        fuel_rate *= type_eff["fuel"]
        organics_rate *= type_eff["organics"]
        equipment_rate *= type_eff["equipment"]

        # Colonist growth rate (1% per day base), scaled by habitability
        habitability = max(planet.habitability_score or 0, 1)
        habitability_multiplier = habitability / 100.0
        colonist_rate = planet.colonists * 0.01 * habitability_multiplier

        # Research-point yield (ADR-0087): driven by the Research Lab level.
        research_rate = (planet.research_level or 0) * RESEARCH_POINTS_PER_LAB_LEVEL_PER_DAY

        # Apply specialization bonuses
        research_mult = 1.0
        if planet.specialization:
            bonuses = self._calculate_specialization_bonuses(planet.specialization)
            production_bonus = bonuses["production"]

            fuel_rate *= production_bonus.get("fuel", 1.0)
            organics_rate *= production_bonus.get("organics", 1.0)
            equipment_rate *= production_bonus.get("equipment", 1.0)
            colonist_rate *= production_bonus.get("colonists", 1.0)
            research_mult = bonuses.get("research", 1.0)
            research_rate *= research_mult

        # Citadel passive production bonus: +5% per citadel level (citadels.md
        # "Per-level passive bonuses"). Applies to commodity output, not growth.
        citadel_level = planet.citadel_level or 0
        if citadel_level > 0:
            citadel_multiplier = 1 + 0.05 * citadel_level
            fuel_rate *= citadel_multiplier
            organics_rate *= citadel_multiplier
            equipment_rate *= citadel_multiplier
            research_rate *= citadel_multiplier

        # Colony cap-taper (CANON colonization.md:190-194, WO-CT2): population growth halts at the
        # demographic ceiling and tapers linearly across the top 10% band. Keys on population vs
        # max_population (the demographic ceiling), NOT the workforce colonist cap. No shrink below
        # the cap — overcrowding stops growth, it does not decay the colony.
        max_pop = planet.max_population or 0
        pop = planet.population or 0
        if max_pop > 0 and colonist_rate > 0:
            if pop >= max_pop:
                colonist_rate = 0.0
            elif pop > 0.9 * max_pop:
                # linear 100% → 0% across [0.9·max_pop, max_pop]
                taper = (max_pop - pop) / (0.1 * max_pop)
                colonist_rate *= max(0.0, min(1.0, taper))

        # Low-habitability resource-cost penalty (WO-F5; colonization.md:211-213).
        # A world below LOW_HABITABILITY_THRESHOLD pays extra for life support /
        # environmental control, netting its commodity output DOWN by
        # LOW_HABITABILITY_PRODUCTION_PENALTY. Mirrors the siege_multiplier shape
        # just below — a single sub-1.0 multiplier on the three commodity rates.
        # Wrapped defensively so a malformed habitability_score on a hot read
        # path can never raise; on any hiccup the colony simply pays no penalty.
        try:
            low_hab_score = planet.habitability_score
            if low_hab_score is not None and low_hab_score < LOW_HABITABILITY_THRESHOLD:
                low_hab_multiplier = 1.0 - LOW_HABITABILITY_PRODUCTION_PENALTY
                fuel_rate *= low_hab_multiplier
                organics_rate *= low_hab_multiplier
                equipment_rate *= low_hab_multiplier
                research_rate *= low_hab_multiplier
        except (TypeError, ValueError):
            logger.warning(
                f"Low-habitability penalty skipped on planet "
                f"{getattr(planet, 'id', '?')}: bad habitability_score "
                f"{getattr(planet, 'habitability_score', None)!r}"
            )

        # Apply siege effects
        if planet.under_siege:
            # Production output reduced by 25%
            siege_multiplier = 1.0 - SIEGE_PRODUCTION_PENALTY
            fuel_rate *= siege_multiplier
            organics_rate *= siege_multiplier
            equipment_rate *= siege_multiplier
            research_rate *= siege_multiplier
            # Population growth halted during siege
            colonist_rate = 0.0

        return {
            "fuel": round(fuel_rate, 2),
            "organics": round(organics_rate, 2),
            "equipment": round(equipment_rate, 2),
            "colonists": round(colonist_rate, 2),
            "research": round(research_rate, 2),
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
        """Return the multiplier set for a planet specialization (ADR-0087).

        Single source of truth is the module-level SPECIALIZATION_BONUSES (also
        read by combat_service for the defense multiplier).
        """
        return SPECIALIZATION_BONUSES.get(specialization, SPECIALIZATION_BONUSES["balanced"])