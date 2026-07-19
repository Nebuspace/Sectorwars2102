"""DB-free regression pins for the combat core loop (WO-QTI-CORELOOP-PINS
Lane 2): the per-hit damage stack order, ship-to-ship round-resolution
termination conditions, and the planetary 3-source defense-reduction math.

Dedupe: test_shield_regen_sr1.py pins CombatService._apply_shield_regen
(the OUT-OF-COMBAT shield-credit-on-read helper) -- a different method,
untouched here. test_combat_escape.py pins _calculate_escape_chance.
test_siege_vulnerability_combat.py pins the siege-vulnerability defense
MULTIPLIER gate inside _resolve_planet_combat, not the underlying
_calculate_planetary_defense_reduction composition this file pins, and its
own scenarios hold defense_buildings at zero throughout (no turret/orbital
math exercised there). test_drone_scalar_canon.py / test_patrol_encounters.py
/ test_movement_drone_encounters.py cover drone-encounter and patrol legs,
disjoint from ship-vs-ship and ship-vs-planet resolution. WO-DRN drone-
attrition scalars (test_drone_cap_enforcement.py, test_drone_combat_record.py)
are this WO's explicit keep-out -- this lane's round-resolution scenarios are
built with defender_drones=0/attacker_drones=0 throughout so the drone-screen
branch never fires and no drone-attrition number is ever asserted here.

Real (unpersisted) Ship ORM instances + SimpleNamespace Player/Sector stand-
ins + scripted ``random`` module functions for determinism -- this is the
established pattern (test_siege_vulnerability_combat.py, test_drone_scalar_
canon.py): _resolve_ship_combat/_resolve_planet_combat flag_modified() the
ship's combat JSONB, which requires a real mapped instance; Player/Sector are
never flag_modified()'d so a SimpleNamespace is sufficient and avoids DB
column bookkeeping. CombatService(db=None) is safe throughout because every
db-touching helper on these paths is either skipped by pre-seeding
ship.combat with all four required keys (so _ensure_combat_state's
ShipSpecification lookup branch never fires) or is independently defensive
(_medal_combat_damage_bonus degrades to 0.0 on any db failure -- confirmed
by CombatService(None) here).
"""
from __future__ import annotations

import types
import uuid

import pytest

import src.services.combat_service as combat_service_module
from src.models.combat import CombatResult
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipType
from src.services.combat_service import CombatService


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _scripted_random(values):
    it = iter(values)
    return lambda: next(it)


def _scripted_randint(values):
    it = iter(values)
    return lambda a, b: next(it)


def _combat_ship(*, ship_type=ShipType.LIGHT_FREIGHTER, shields=100, max_shields=100,
                  hull=100, max_hull=100, shield_resistance=0.0, armor_rating=0.0,
                  is_destroyed=False):
    """A fresh, real (never-flushed) Ship instance with a fully pre-seeded
    combat dict -- all four required keys present so _ensure_combat_state's
    ShipSpecification db lookup branch is never entered (db=None safe)."""
    ship = ShipModel()
    ship.type = ship_type
    ship.combat = {"shields": shields, "max_shields": max_shields, "hull": hull, "max_hull": max_hull}
    ship.maintenance = None  # combat_multiplier(ship) treats a missing/None dict as neutral 1.0
    ship.equipment_slots = {}
    ship.tow_state = None
    ship.shield_resistance = shield_resistance
    ship.armor_rating = armor_rating
    ship.is_destroyed = is_destroyed
    ship.cargo = {"capacity": 50, "used": 0, "contents": {}}
    return ship


def _player(*, ship, username="tester", attack_drones=0, defense_drones=0,
            military_rank="Recruit"):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        username=username,
        current_ship=ship,
        attack_drones=attack_drones,
        defense_drones=defense_drones,
        military_rank=military_rank,
    )


def _cs():
    return CombatService(None)


# ---------------------------------------------------------------------------
# Damage stack order (_apply_weapon_damage, static / pure)
# ---------------------------------------------------------------------------


LASER = CombatService.WEAPON_TYPES["laser"]  # base=1.0, shield_eff=0.8, hull_eff=1.0


class TestWeaponDamageStackOrder:
    """combat-resolver.md "Damage stack": shields absorb first (scaled by
    weapon.shield_effectiveness and shield_resistance), the RESIDUAL bleeds
    into hull (scaled by weapon.hull_effectiveness and armor_rating), and a
    5% crit adds half the (armor-reduced) hull hit again."""

    def test_damage_fully_absorbed_by_shields_leaves_hull_untouched(self):
        target = {"shields": 100.0, "hull": 100.0}
        hit = CombatService._apply_weapon_damage(10.0, LASER, target)
        # shield_hit = min(10,100) * 0.8 * (1-0) = 8.0; residual = 0 -> hull_hit = 0.
        assert hit["shield_damage"] == 8.0
        assert hit["hull_damage"] == 0.0
        assert target["shields"] == pytest.approx(92.0)
        assert target["hull"] == 100.0
        assert hit["destroyed"] is False

    def test_residual_bleeds_into_hull_after_shields_deplete(self):
        target = {"shields": 5.0, "hull": 100.0}
        # hull_hit > 0 here, so _apply_weapon_damage's internal 5% crit roll
        # calls random.random() -- script it above threshold (no crit) so
        # the exact-hull-damage assertion below isn't a 1-in-20 flake.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(combat_service_module.random, "random", _scripted_random([1.0]))
            hit = CombatService._apply_weapon_damage(10.0, LASER, target)
        # absorbed = min(10,5) = 5 -> shield_hit = 5*0.8 = 4.0.
        # residual = 10-5 = 5 -> hull_hit = 5*1.0*(1-0) = 5.0.
        assert hit["shield_damage"] == 4.0
        assert hit["hull_damage"] == 5.0
        assert target["shields"] == 1.0
        assert target["hull"] == 95.0

    def test_shield_resistance_reduces_only_the_shield_component(self):
        target = {"shields": 100.0, "hull": 100.0}
        hit = CombatService._apply_weapon_damage(10.0, LASER, target, shield_resistance=0.5)
        # shield_hit = 10*0.8*(1-0.5) = 4.0; hull untouched (all damage absorbed by shields).
        assert hit["shield_damage"] == 4.0
        assert hit["hull_damage"] == 0.0

    def test_armor_rating_reduces_only_the_hull_component(self):
        target = {"shields": 0.0, "hull": 100.0}
        # hull_hit > 0 here too -- same crit-roll determinism note as above.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(combat_service_module.random, "random", _scripted_random([1.0]))
            hit = CombatService._apply_weapon_damage(10.0, LASER, target, armor_rating=0.5)
        # absorbed=0 (no shields) -> residual=10 -> hull_hit = 10*1.0*(1-0.5) = 5.0.
        assert hit["shield_damage"] == 0.0
        assert hit["hull_damage"] == 5.0
        assert target["hull"] == 95.0

    def test_critical_hit_adds_half_the_hull_hit_when_rng_below_threshold(self):
        target = {"shields": 0.0, "hull": 100.0}
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(combat_service_module.random, "random", _scripted_random([0.0]))
            hit = CombatService._apply_weapon_damage(10.0, LASER, target)
        # hull_hit = 10.0; critical = 10.0*0.5 = 5.0 -> total hull damage 15.0.
        assert hit["critical"] is True
        assert hit["hull_damage"] == 15.0
        assert target["hull"] == 85.0

    def test_no_critical_when_rng_at_or_above_threshold(self):
        target = {"shields": 0.0, "hull": 100.0}
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(combat_service_module.random, "random", _scripted_random([0.05]))
            hit = CombatService._apply_weapon_damage(10.0, LASER, target)
        assert hit["critical"] is False
        assert hit["hull_damage"] == 10.0

    def test_hull_and_shields_floor_at_zero_never_negative(self):
        # EMP (shield_effectiveness=2.0, hull_effectiveness=0.3): its >1.0
        # shield_effectiveness is what actually drives shield_hit PAST the
        # remaining shield pool (laser's 0.8 effectiveness never would --
        # shield_hit = absorbed*eff <= shields*eff < shields whenever
        # eff < 1.0, so the floor only bites with an amplifying weapon).
        emp = CombatService.WEAPON_TYPES["emp"]
        target = {"shields": 2.0, "hull": 1.0}
        hit = CombatService._apply_weapon_damage(50.0, emp, target)
        assert target["shields"] == 0.0
        assert target["hull"] == 0.0
        assert hit["destroyed"] is True

    def test_resistance_fraction_clamps_ceiling_at_0_9(self):
        assert CombatService._resistance_fraction(5.0) == pytest.approx(0.9)

    def test_resistance_fraction_negative_or_none_floors_at_0(self):
        assert CombatService._resistance_fraction(-1.0) == 0.0
        assert CombatService._resistance_fraction(None) == 0.0


# ---------------------------------------------------------------------------
# Round-resolution termination (_resolve_ship_combat)
# ---------------------------------------------------------------------------


class TestShipCombatRoundTermination:
    """No drones on either side throughout (attack_drones=defense_drones=0)
    -- the drone-screen branch is WO-DRN's property, kept out of these pins
    per the WO's explicit instruction."""

    def test_combat_ends_when_defender_hull_reaches_zero(self):
        attacker_ship = _combat_ship(shields=0, hull=100)
        defender_ship = _combat_ship(shields=0, hull=1)
        attacker = _player(ship=attacker_ship)
        defender = _player(ship=defender_ship, username="victim")
        cs = _cs()

        with pytest.MonkeyPatch.context() as mp:
            # attacker hit-roll succeeds, base_damage rolls max (10), no crit.
            mp.setattr(combat_service_module.random, "random", _scripted_random([0.0, 1.0]))
            mp.setattr(combat_service_module.random, "randint", _scripted_randint([10]))
            result = cs._resolve_ship_combat(attacker, defender, sector=None)

        assert result["result"] == CombatResult.ATTACKER_VICTORY
        assert result["rounds"] == 1
        assert result["defender_ship_destroyed"] is True
        assert result["attacker_ship_destroyed"] is False

    def test_combat_ends_when_attacker_hull_reaches_zero(self):
        attacker_ship = _combat_ship(shields=0, hull=1)
        defender_ship = _combat_ship(shields=0, hull=100)
        attacker = _player(ship=attacker_ship)
        defender = _player(ship=defender_ship, username="victim")
        cs = _cs()

        with pytest.MonkeyPatch.context() as mp:
            # attacker's own swing misses (roll 1.0 >= hit_chance), defender then hits for max.
            mp.setattr(combat_service_module.random, "random", _scripted_random([1.0, 0.0, 1.0]))
            mp.setattr(combat_service_module.random, "randint", _scripted_randint([10]))
            result = cs._resolve_ship_combat(attacker, defender, sector=None)

        assert result["result"] == CombatResult.DEFENDER_VICTORY
        assert result["rounds"] == 1
        assert result["attacker_ship_destroyed"] is True
        assert result["defender_ship_destroyed"] is False

    def test_combat_stalemates_as_draw_after_exactly_10_rounds(self):
        """Every attacker AND defender roll misses (random.random() always
        1.0, never below any hit_chance) -- combat runs the full round cap
        and ends in a draw, never a destruction. Both sides carry a live
        (non-zero, unasserted) drone count solely so the post-round escape
        check's `drones <= 0` hull-exposed gate never opens -- an all-miss
        fight with zero drones would otherwise flee on round 1's escape
        roll instead of reaching the round cap, which is what this test
        pins. The drone COUNT itself is never asserted (WO-DRN keep-out)."""
        attacker_ship = _combat_ship(shields=50, hull=50)
        defender_ship = _combat_ship(shields=50, hull=50)
        attacker = _player(ship=attacker_ship, attack_drones=5)
        defender = _player(ship=defender_ship, username="victim", defense_drones=5)
        cs = _cs()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(combat_service_module.random, "random", _scripted_random([1.0] * 40))
            mp.setattr(combat_service_module.random, "randint", _scripted_randint([10] * 40))
            result = cs._resolve_ship_combat(attacker, defender, sector=None)

        assert result["rounds"] == 10
        assert result["result"] == CombatResult.DRAW
        assert result["attacker_ship_destroyed"] is False
        assert result["defender_ship_destroyed"] is False
        # Neither hull moved -- every roll missed.
        assert attacker_ship.combat["hull"] == 50
        assert defender_ship.combat["hull"] == 50

    def test_mutual_destruction_when_both_hulls_hit_zero_same_round(self):
        attacker_ship = _combat_ship(shields=0, hull=1)
        defender_ship = _combat_ship(shields=0, hull=1)
        attacker = _player(ship=attacker_ship)
        defender = _player(ship=defender_ship, username="victim")
        cs = _cs()

        with pytest.MonkeyPatch.context() as mp:
            # Both swings hit for max damage in round 1 -- attacker fires
            # first and destroys the defender, but the while-loop condition
            # (`not attacker_ship_destroyed and not defender_ship_destroyed`)
            # was already true when this round started, so the defender's
            # own already-in-flight return fire (guarded only by the
            # `if defender_ship_destroyed: break` check AFTER the attacker's
            # swing) never runs once the defender is destroyed -- mirroring
            # production's round structure, this scenario is therefore
            # actually ATTACKER_VICTORY, not mutual destruction (the
            # defender never gets to swing back once destroyed). This pins
            # that exact structural fact.
            # [hit-chance check (hits), critical-hit roll inside _apply_weapon_damage (no crit)]
            mp.setattr(combat_service_module.random, "random", _scripted_random([0.0, 1.0]))
            mp.setattr(combat_service_module.random, "randint", _scripted_randint([10]))
            result = cs._resolve_ship_combat(attacker, defender, sector=None)

        assert result["result"] == CombatResult.ATTACKER_VICTORY
        assert result["defender_ship_destroyed"] is True
        assert result["attacker_ship_destroyed"] is False


# ---------------------------------------------------------------------------
# Planetary 3-source defense-reduction math (_calculate_planetary_defense_reduction)
# ---------------------------------------------------------------------------


def _planet(*, defense_level=0, defense_shields=0, shields=0, specialization=None,
            active_events=None):
    return types.SimpleNamespace(
        defense_level=defense_level,
        defense_shields=defense_shields,
        shields=shields,
        specialization=specialization,
        active_events=active_events or {},
    )


class TestPlanetaryDefenseReduction:
    """defense.md / WO-CT1: three independently-capped damage-reduction
    sources (defense_level, shield generators, citadel turret/orbital
    buildings) sum then get capped at 0.9; shield generators + orbital
    platforms separately contribute a flat shield-HP pool."""

    def test_defense_level_reduction_is_5pct_per_level(self):
        cs = _cs()
        result = cs._calculate_planetary_defense_reduction(_planet(defense_level=4))
        assert result["damage_reduction"] == pytest.approx(0.20)

    def test_defense_level_reduction_caps_at_50pct(self):
        cs = _cs()
        result = cs._calculate_planetary_defense_reduction(_planet(defense_level=20))
        assert result["damage_reduction"] == pytest.approx(0.50)

    def test_shield_generator_adds_500hp_per_level_plus_existing_shields_x100(self):
        cs = _cs()
        result = cs._calculate_planetary_defense_reduction(
            _planet(defense_shields=2, shields=3)
        )
        # (2*500) + (3*100) = 1300
        assert result["shield_hp"] == 1300

    def test_shield_generator_reduction_is_4pct_per_level_capped_40pct(self):
        cs = _cs()
        result = cs._calculate_planetary_defense_reduction(_planet(defense_shields=3))
        assert result["damage_reduction"] == pytest.approx(0.12)
        capped = cs._calculate_planetary_defense_reduction(_planet(defense_shields=20))
        assert capped["damage_reduction"] == pytest.approx(0.40)

    def test_turret_network_reduction_3pct_each_capped_18pct(self):
        cs = _cs()
        planet = _planet(active_events={"defense_buildings": {"turret_network": 2}})
        result = cs._calculate_planetary_defense_reduction(planet)
        assert result["damage_reduction"] == pytest.approx(0.06)
        assert result["anti_drone_kills_per_round"] == 6  # min(2*3, 18)

    def test_orbital_platform_reduction_6pct_each_plus_250hp_armor(self):
        cs = _cs()
        planet = _planet(active_events={"defense_buildings": {"orbital_platform": 2}})
        result = cs._calculate_planetary_defense_reduction(planet)
        assert result["damage_reduction"] == pytest.approx(0.12)
        assert result["shield_hp"] == 500  # 2 * 250, no shield_gen/shields contribution

    def test_specialization_multiplier_scales_the_combined_reduction(self):
        """Military specialization (ADR-0087) is x1.5 on the combined
        pre-cap reduction. defense_level=4 alone gives 0.20; Military scales
        it to 0.30."""
        from src.services.planetary_service import SPECIALIZATION_BONUSES
        assert SPECIALIZATION_BONUSES["military"]["defense"] == pytest.approx(1.5)
        cs = _cs()
        result = cs._calculate_planetary_defense_reduction(
            _planet(defense_level=4, specialization="military")
        )
        assert result["damage_reduction"] == pytest.approx(0.30)

    def test_total_damage_reduction_never_exceeds_0_9_cap(self):
        cs = _cs()
        planet = _planet(
            defense_level=20, defense_shields=20,
            active_events={"defense_buildings": {"turret_network": 10, "orbital_platform": 10}},
            specialization="military",
        )
        result = cs._calculate_planetary_defense_reduction(planet)
        assert result["damage_reduction"] == pytest.approx(0.90)

    def test_defense_grid_drone_damage_bonus_tiers(self):
        cs = _cs()
        none_grid = cs._calculate_planetary_defense_reduction(_planet())
        l1_grid = cs._calculate_planetary_defense_reduction(
            _planet(active_events={"defense_buildings": {"planetary_defense_grid": 1}})
        )
        l2_grid = cs._calculate_planetary_defense_reduction(
            _planet(active_events={"defense_buildings": {"planetary_defense_grid": 2}})
        )
        assert none_grid["drone_damage_bonus"] == 0.0
        assert l1_grid["drone_damage_bonus"] == pytest.approx(0.15)
        assert l2_grid["drone_damage_bonus"] == pytest.approx(0.25)
