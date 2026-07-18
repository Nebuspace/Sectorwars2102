"""Unit tests for WO-P5-planets-minefield-wiring: planet_minefield reachable
via the build endpoint + its one-time proximity-strike combat impact in
_resolve_planet_combat (defense.md §"Mine fields").

Canon: "Mines target incoming hostiles only... deal 500-1,500 hull damage per
mine impact, ignore shields." Unlike turrets/rail guns (recurring, every-round
weapons), a minefield fires ONCE, on assault-round entry (round 1). Canon's
literal per-mine-impact magnitude would instakill on this resolver's
randint(1,7)/round scale, so the injected magnitude is NO-CANON
(MINEFIELD_BASE_DAMAGE_PER_FIELD / MINEFIELD_MAX_BONUS_DAMAGE,
combat_service.py) -- same WO-CT1/rail_gun shape, this time with no
ship-class table (canon gives none for mines).

House pattern mirrors test_rail_gun_resolver.py / test_siege_vulnerability_
combat.py: a real (unpersisted) Ship instance + SimpleNamespace Player +
SimpleNamespace Planet + scripted ``random`` for determinism.
"""
import types
import uuid
from unittest.mock import MagicMock

import pytest

import src.services.combat_service as combat_service_module
from src.models.combat import CombatResult
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipType
from src.services.combat_service import CombatService


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


def _planet(*, defense_level=6, minefield_count=0, name="Fortress Prime"):
    active_events = (
        {"defense_buildings": {"planet_minefield": minefield_count}}
        if minefield_count
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
# (1) _calculate_minefield_bonus_damage pinned directly -- deterministic, no
#     RNG, no ship-class dependence (canon gives no ship-class table for mines).
# --------------------------------------------------------------------------- #

def test_zero_minefields_is_zero_bonus_with_no_rng_call():
    cs = CombatService(MagicMock())
    planet = _planet(minefield_count=0)
    # No random-module patch -- if the function drew any random call this
    # would be a MagicMock-vs-int TypeError, not a silent pass.
    assert cs._calculate_minefield_bonus_damage(planet) == 0


def test_bonus_scales_with_minefield_count():
    cs = CombatService(MagicMock())
    one_field = cs._calculate_minefield_bonus_damage(_planet(minefield_count=1))
    two_fields = cs._calculate_minefield_bonus_damage(_planet(minefield_count=2))

    assert one_field == 3      # 1 * MINEFIELD_BASE_DAMAGE_PER_FIELD (3)
    assert two_fields == 6     # 2 * 3
    assert two_fields > one_field


def test_legit_max_buildout_lands_exactly_at_the_cap_never_past_it():
    """3 minefields (the L5 max per defense.md capacity ladder: 1@L3/2@L4/3@L5)
    is the strongest legitimate build -- confirms it never exceeds the ceiling."""
    cs = CombatService(MagicMock())
    planet = _planet(minefield_count=3)
    bonus = cs._calculate_minefield_bonus_damage(planet)
    assert bonus == 9 == cs.MINEFIELD_MAX_BONUS_DAMAGE


def test_hard_ceiling_caps_a_malformed_oversized_minefield_count():
    cs = CombatService(MagicMock())
    planet = _planet(minefield_count=999)
    bonus = cs._calculate_minefield_bonus_damage(planet)
    assert bonus == cs.MINEFIELD_MAX_BONUS_DAMAGE == 9


def test_minefield_bonus_does_not_vary_by_ship_class():
    """Canon gives rail guns an anti-capital table but NO such table for
    mines ('target incoming hostiles only') -- _calculate_minefield_bonus_damage
    doesn't even take an attacker_ship argument, confirming this by signature."""
    import inspect
    params = inspect.signature(CombatService._calculate_minefield_bonus_damage).parameters
    assert list(params.keys()) == ["self", "planet"]


# --------------------------------------------------------------------------- #
# (2) Full-resolver regression: 0 minefields must be byte-identical to pre-WO
#     combat (the load-bearing regression assert).
# --------------------------------------------------------------------------- #

def test_zero_minefields_end_to_end_is_byte_identical_to_pre_wo_combat():
    cs = CombatService(MagicMock())
    ship = _combat_ship(ship_type=ShipType.LIGHT_FREIGHTER)
    attacker = _combat_player(ship=ship)
    planet = _planet(defense_level=6, minefield_count=0)

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
        assert "minefield" not in entry.get("message", "").lower()
        assert entry.get("action") != "minefield_strike"
    ship_hit_entries = [d for d in result["combat_details"] if d.get("action") == "ship_destroyed"]
    assert len(ship_hit_entries) == 1
    assert ship_hit_entries[0]["message"] == (
        f"Planetary defenses critically damaged {attacker.username}'s ship, forcing ejection"
    )


# --------------------------------------------------------------------------- #
# (3) Minefield present: proximity strike on round-1 entry, both branches.
# --------------------------------------------------------------------------- #

def test_minefield_destroys_attacker_outright_on_entry_before_any_other_turn_fires():
    """1 minefield -> mine_bonus=3, destruction_chance=3/50=0.06. A scripted
    random.random()=0.0 (< 0.06) destroys the ship immediately on round-1
    entry, BEFORE the turret/attacker/defender turns ever run -- proven by
    scripting zero randint values (StopIteration if anything drew one)."""
    cs = CombatService(MagicMock())
    ship = _combat_ship(ship_type=ShipType.CARRIER)  # ship class must NOT matter for mines
    attacker = _combat_player(ship=ship)
    planet = _planet(defense_level=6, minefield_count=1)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(combat_service_module.random, "random", _scripted_random([0.0]))
        mp.setattr(combat_service_module.random, "randint", _scripted_randint([]))
        result = cs._resolve_planet_combat(attacker, planet, planet_owner=None)

    assert result["attacker_ship_destroyed"] is True
    assert result["rounds"] == 1
    assert result["result"] == CombatResult.DEFENDER_VICTORY
    strike_entries = [d for d in result["combat_details"] if d.get("action") == "minefield_strike"]
    assert len(strike_entries) == 1
    assert strike_entries[0]["damage"] == 3
    assert strike_entries[0]["message"] == (
        f"Planetary minefield mines caught {attacker.username}'s ship on approach for 3 damage, forcing ejection"
    )
    # No ship_destroyed / ship_attack / turret_defense entries -- the round
    # never reached the pre-existing turn structure.
    assert not any(d.get("action") in ("ship_destroyed", "ship_attack", "turret_defense") for d in result["combat_details"])


def test_minefield_survivor_continues_into_the_same_round_normally():
    """1 minefield -> mine_bonus=3, destruction_chance=0.06. A scripted
    random.random()=0.5 (>= 0.06) survives the mine strike, and round 1
    continues into the pre-existing turret/attacker/defender turn sequence
    unaffected (here scripted to end in a normal ship-destroyed round-1
    outcome, matching the byte-identical-at-zero scenario's numbers)."""
    cs = CombatService(MagicMock())
    ship = _combat_ship(ship_type=ShipType.LIGHT_FREIGHTER)
    attacker = _combat_player(ship=ship)
    planet = _planet(defense_level=6, minefield_count=1)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            combat_service_module.random, "random",
            _scripted_random([0.5, 0.0, 0.0, 0.0]),  # mine-survive, attacker hit, defender hit, ship-destroyed
        )
        mp.setattr(combat_service_module.random, "randint", _scripted_randint([5, 7]))
        result = cs._resolve_planet_combat(attacker, planet, planet_owner=None)

    strike_entries = [d for d in result["combat_details"] if d.get("action") == "minefield_strike"]
    assert len(strike_entries) == 1
    assert strike_entries[0]["damage"] == 3
    assert strike_entries[0]["message"] == (
        f"Planetary minefield mines struck {attacker.username}'s ship for 3 damage on approach"
    )
    # Combat continues in the SAME round (1) to the normal ship-destroyed path.
    assert result["rounds"] == 1
    assert result["attacker_ship_destroyed"] is True
    ship_destroyed_entries = [d for d in result["combat_details"] if d.get("action") == "ship_destroyed"]
    assert len(ship_destroyed_entries) == 1
