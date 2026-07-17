"""Unit tests for WO-P5-planets-railgun-resolver: rail gun batteries firing in
_resolve_planet_combat (defense.md §"Fixed rail gun batteries").

Canon: "This makes rail guns a specialized anti-capital weapon. Small/fast
ships shrug them off; large ships take heavy damage." Canon's literal raw
numbers (1,000-3,000 burst x up to 200% ship-class multiplier) would instakill
on this resolver's randint(1,7)/round ship-damage scale, so the injected
magnitude is NO-CANON (RAIL_GUN_BASE_DAMAGE_PER_BATTERY /
RAIL_GUN_MAX_BONUS_DAMAGE, combat_service.py) while the ship-class RATIOS are
canon-pinned, read straight off citadel_service.DEFENSE_BUILDINGS["rail_gun"]
["effects"]["ship_size_multiplier_pct"].

House pattern mirrors test_siege_vulnerability_combat.py: a real
(unpersisted) Ship instance + SimpleNamespace Player + SimpleNamespace Planet
+ scripted ``random`` for determinism.
"""
import types
import uuid
from unittest.mock import MagicMock

import src.services.combat_service as combat_service_module
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipType
from src.services.combat_service import CombatService
from src.services.citadel_service import DEFENSE_BUILDINGS


def _combat_ship(*, ship_type=ShipType.LIGHT_FREIGHTER):
    ship = ShipModel()
    ship.type = ship_type
    ship.combat = {}
    ship.maintenance = None
    return ship


def _combat_player(*, ship, username="assailant"):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        username=username,
        attack_drones=0,
        current_ship=ship,
    )


def _planet(*, defense_level=6, rail_gun_count=0, name="Fortress Prime"):
    active_events = (
        {"defense_buildings": {"rail_gun": rail_gun_count}}
        if rail_gun_count
        else {}
    )
    return types.SimpleNamespace(
        name=name,
        defense_level=defense_level,
        shields=0,
        weapon_batteries=0,
        defense_shields=0,
        active_events=active_events,
        specialization=None,
        under_siege=False,
        morale=100,
        siege_attacker_id=None,
    )


def _scripted_random(values):
    it = iter(values)
    return lambda: next(it)


def _scripted_randint(values):
    it = iter(values)
    return lambda a, b: next(it)


# --------------------------------------------------------------------------- #
# (1) The canon ship-class ratio table pins _calculate_rail_gun_bonus_damage
#     directly (isolated from the round loop -- no RNG involved, deterministic).
# --------------------------------------------------------------------------- #

def test_zero_rail_guns_is_zero_bonus_with_no_rng_call():
    cs = CombatService(MagicMock())
    planet = _planet(rail_gun_count=0)
    ship = _combat_ship(ship_type=ShipType.CARRIER)

    # No random-module patch at all -- if the function drew any random call
    # this would be a MagicMock-vs-int TypeError, not a silent pass.
    bonus = cs._calculate_rail_gun_bonus_damage(planet, ship)
    assert bonus == 0


def test_no_attacker_ship_is_zero_bonus():
    cs = CombatService(MagicMock())
    planet = _planet(rail_gun_count=4)
    assert cs._calculate_rail_gun_bonus_damage(planet, None) == 0


def test_capital_class_takes_far_more_bonus_damage_than_a_scout():
    """Canon's anti-capital table: CARRIER 200% vs SCOUT_SHIP 10% -- a
    20x ratio. Same battery count, only ship class differs."""
    cs = CombatService(MagicMock())
    planet = _planet(rail_gun_count=4)

    carrier_bonus = cs._calculate_rail_gun_bonus_damage(planet, _combat_ship(ship_type=ShipType.CARRIER))
    scout_bonus = cs._calculate_rail_gun_bonus_damage(planet, _combat_ship(ship_type=ShipType.SCOUT_SHIP))

    # 4 batteries * 0.5 base * 2.0 (200%) = 4.0 -> 4
    assert carrier_bonus == 4
    # 4 batteries * 0.5 base * 0.10 (10%) = 0.2 -> rounds to 0: "small/fast
    # ships shrug them off" per canon, exactly.
    assert scout_bonus == 0
    assert carrier_bonus > scout_bonus


def test_bonus_scales_with_battery_count():
    cs = CombatService(MagicMock())
    ship = _combat_ship(ship_type=ShipType.DEFENDER)  # 120% per canon table

    one_battery = cs._calculate_rail_gun_bonus_damage(_planet(rail_gun_count=1), ship)
    four_batteries = cs._calculate_rail_gun_bonus_damage(_planet(rail_gun_count=4), ship)

    # 1 * 0.5 * 1.2 = 0.6 -> 1 ; 4 * 0.5 * 1.2 = 2.4 -> 2
    assert one_battery == 1
    assert four_batteries == 2
    assert four_batteries > one_battery


def test_hard_ceiling_caps_a_malformed_oversized_battery_count():
    """The defensive ceiling (RAIL_GUN_MAX_BONUS_DAMAGE) only matters for a
    corrupted/out-of-range count -- legitimate max build-out (10 @ L5) lands
    exactly at the cap for a Carrier, never past it. Proves the ceiling
    actually fires for the abuse case."""
    cs = CombatService(MagicMock())
    ship = _combat_ship(ship_type=ShipType.CARRIER)
    planet = _planet(rail_gun_count=999)

    bonus = cs._calculate_rail_gun_bonus_damage(planet, ship)
    assert bonus == cs.RAIL_GUN_MAX_BONUS_DAMAGE == 10


def test_legit_max_buildout_lands_exactly_at_the_cap_never_past_it():
    """10 rail guns (the L5 max per defense.md capacity ladder) vs a Carrier
    (200%, the highest table entry) is the strongest legitimate combination
    the build system can produce -- confirms it never exceeds the ceiling."""
    cs = CombatService(MagicMock())
    ship = _combat_ship(ship_type=ShipType.CARRIER)
    planet = _planet(rail_gun_count=10)

    bonus = cs._calculate_rail_gun_bonus_damage(planet, ship)
    assert bonus == 10 == cs.RAIL_GUN_MAX_BONUS_DAMAGE


def test_untabled_ship_type_defaults_to_neutral_100_percent():
    """ESCAPE_POD has no entry in citadel_service's ship_size_multiplier_pct
    table -- must not crash, defaults to a neutral 100%."""
    cs = CombatService(MagicMock())
    assert ShipType.ESCAPE_POD.name not in DEFENSE_BUILDINGS["rail_gun"]["effects"]["ship_size_multiplier_pct"]
    planet = _planet(rail_gun_count=4)
    ship = _combat_ship(ship_type=ShipType.ESCAPE_POD)

    bonus = cs._calculate_rail_gun_bonus_damage(planet, ship)
    # 4 * 0.5 * 1.0 (default) = 2.0 -> 2
    assert bonus == 2


# --------------------------------------------------------------------------- #
# (2) Full-resolver regression: identical assault, 0 rail guns must be
#     byte-identical to pre-WO combat (the load-bearing regression assert).
# --------------------------------------------------------------------------- #

def test_zero_rail_guns_end_to_end_is_byte_identical_to_pre_wo_combat():
    """Same controlled scenario test_siege_vulnerability_combat.py's
    unbesieged control uses (defense_level=6, LIGHT_FREIGHTER attacker,
    scripted hit/miss/damage rolls) -- with active_events={} (no rail guns),
    the resolver must produce the EXACT pre-WO numbers and NO rail-gun note
    anywhere in the combat log."""
    cs = CombatService(MagicMock())
    ship = _combat_ship(ship_type=ShipType.LIGHT_FREIGHTER)
    attacker = _combat_player(ship=ship)
    planet = _planet(defense_level=6, rail_gun_count=0)

    import pytest
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            combat_service_module.random, "random",
            _scripted_random([0.0, 0.0, 0.0]),  # attacker hit, defender hit, ship-destroyed
        )
        mp.setattr(combat_service_module.random, "randint", _scripted_randint([5, 7]))
        result = cs._resolve_planet_combat(attacker, planet, planet_owner=None)

    assert result["planet_damage"] == 3
    assert result["attacker_ship_destroyed"] is True
    assert result["rounds"] == 1
    for entry in result["combat_details"]:
        assert "rail-gun" not in entry.get("message", "")
    # Exact pre-WO message text (7 damage, no note appended).
    ship_hit_entries = [d for d in result["combat_details"] if d.get("action") == "ship_destroyed"]
    assert len(ship_hit_entries) == 1
    assert ship_hit_entries[0]["message"] == (
        f"Planetary defenses critically damaged {attacker.username}'s ship, forcing ejection"
    )


def test_rail_gun_bonus_flows_through_the_full_resolver_message():
    """A Carrier attacker vs a planet with 4 rail guns: the ship-attack
    message must carry the base randint(1,7)=7 roll PLUS the +4 rail-gun
    bonus (7+4=11), with the anti-capital note attached."""
    cs = CombatService(MagicMock())
    ship = _combat_ship(ship_type=ShipType.CARRIER)
    attacker = _combat_player(ship=ship)
    planet = _planet(defense_level=6, rail_gun_count=4)

    import pytest
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            combat_service_module.random, "random",
            _scripted_random([0.0, 0.0, 0.0]),  # attacker hit, defender hit, ship-destroyed
        )
        mp.setattr(combat_service_module.random, "randint", _scripted_randint([5, 7]))
        result = cs._resolve_planet_combat(attacker, planet, planet_owner=None)

    ship_hit_entries = [d for d in result["combat_details"] if d.get("action") == "ship_destroyed"]
    assert len(ship_hit_entries) == 1
    assert ship_hit_entries[0]["message"] == (
        f"Planetary defenses critically damaged {attacker.username}'s ship, "
        f"forcing ejection (+4 rail-gun anti-capital fire)"
    )
