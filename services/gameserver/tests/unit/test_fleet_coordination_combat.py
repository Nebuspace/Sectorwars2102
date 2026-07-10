"""Unit tests for WO-P4-fleet-coord-combat-wire.

fleet_service.py's own organized FleetBattle round simulator already applied
Fleet.coordination_bonus (fleet_service.py:958-965, canon fleet-tactics.md:100
"Live... Proven on dev 2026-06-18") -- that path is untouched here. The real
gap this WO closes: combat_service.py's `_resolve_ship_combat` (the resolver
behind a player's personal attack_player / attack_npc_ship / npc_attack_player
actions, NOT an organized fleet battle) never read it, despite canon's own
damage-stack formula (combat-resolver.md:79-89) listing
`* (1 + fleet.coordination_bonus)` as a required step alongside the rank/medal
term that WAS already implemented.

Two lanes:
1. `FleetService.get_coordination_bonus` (fleet_service.py) in isolation --
   ship-keyed lookup, unenrolled-ship default, defensive clamp.
2. `_resolve_ship_combat` (combat_service.py) wiring -- fleet-enrolled
   attacker/defender damage scales by (1 + coordination_bonus); a
   non-enrolled ship's damage is byte-identical to the pre-wire baseline
   (10.0, the same value test_drone_scalar_canon.py's sibling tests already
   pin for this exact setup); an NPC-initiated attacker (no Player row) never
   raises and resolves to no bonus.

Reuses the established combat-resolver test idioms (see
tests/unit/test_drone_scalar_canon.py / test_siege_vulnerability_combat.py):
a real (unpersisted) Ship instance (the resolver flag_modified()s its combat
JSONB), a SimpleNamespace Player stand-in, scripted `random.random` /
`random.randint` for determinism, and a hull-threshold trick to bound the
round count without walking the escape-chance machinery. Damage magnitude is
only ever asserted on a SURVIVING target -- a destroyed/overkilled target's
reported damage floors at its own original hull value (see
_apply_weapon_damage's `max(0.0, hull - hull_hit - critical)`), losing the
exact multiplier signal this WO needs to prove.
"""
import types
import uuid
from unittest.mock import MagicMock

import pytest

import src.services.combat_service as combat_service_module
from src.services.combat_service import CombatService
from src.services.fleet_service import FleetService
from src.models.fleet import Fleet, FleetMember
from src.models.ship import Ship as ShipModel, ShipType


# --- Shared fixtures -------------------------------------------------------

def _combat_ship(*, ship_type=ShipType.LIGHT_FREIGHTER, hull=1000.0, max_hull=1000.0):
    ship = ShipModel()
    ship.type = ship_type
    ship.combat = {"shields": 0, "max_shields": 0, "hull": hull, "max_hull": max_hull}
    ship.maintenance = None
    ship.is_destroyed = False
    ship.current_sector_id = None
    return ship


def _combat_player(*, ship, username="pilot"):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        username=username,
        military_rank="__no_such_rank__",  # forces the zero rank-bonus fallback
        attack_drones=0,
        defense_drones=0,
        current_ship=ship,
    )


def _scripted_random(values):
    it = iter(values)
    return lambda: next(it)


def _fixed_randint(a, b):
    """base_damage rolls (1, 10) always max out at 10; anything else the
    ceiling -- deterministic regardless of call site (mirrors
    test_drone_scalar_canon.py's _fixed_randint)."""
    return 10 if (a, b) == (1, 10) else b


def _fleet_db(fleet_lookup_results):
    """A MagicMock db whose FleetMember query is scripted via a shared
    iterator (consumed in the exact order get_coordination_bonus is called
    -- attacker's ship first, then defender's ship, per the wiring order in
    _resolve_ship_combat). Every other model (ShipSpecification, the medals
    lane, etc.) degrades harmlessly through MagicMock's default chain --
    the same reachability already relied on by CombatService(MagicMock())
    in test_siege_vulnerability_combat.py / test_drone_scalar_canon.py."""
    db = MagicMock()
    it = iter(fleet_lookup_results)

    def _query(model, *a, **k):
        q = MagicMock()
        if model is FleetMember:
            q.filter.return_value.first.side_effect = lambda: next(it)
        return q

    db.query.side_effect = _query
    return db


def _enrolled(bonus):
    """A real (unpersisted) FleetMember, with a real (unpersisted) Fleet at
    coordination_bonus=`bonus`, wired via the actual relationship attribute
    -- not a SimpleNamespace/MagicMock stand-in. get_coordination_bonus
    isinstance-checks both (a permissive stand-in can otherwise satisfy
    `if not member or not member.fleet` without ever being real ORM data --
    see fleet_service.py:132's isinstance rationale), so a stand-in here
    would silently exercise a code path production never takes."""
    fm = FleetMember()
    fm.fleet = Fleet(coordination_bonus=bonus)
    return fm


# --- Lane 1: FleetService.get_coordination_bonus, in isolation -------------

def test_get_coordination_bonus_reads_the_cached_fleet_value():
    ship_id = uuid.uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _enrolled(0.075)

    bonus = FleetService(db).get_coordination_bonus(ship_id)

    assert bonus == pytest.approx(0.075)


def test_get_coordination_bonus_is_zero_for_an_unenrolled_ship():
    ship_id = uuid.uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    assert FleetService(db).get_coordination_bonus(ship_id) == 0.0


def test_get_coordination_bonus_clamps_a_corrupted_negative_value():
    """Defensive clamp mirrors fleet_service's own combat-path read
    (`max(0.0, fleet.coordination_bonus or 0.0)`, fleet_service.py:964) --
    a NULL/negative cached value must never reduce damage below the
    solo-combatant baseline."""
    ship_id = uuid.uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _enrolled(-0.5)

    assert FleetService(db).get_coordination_bonus(ship_id) == 0.0


# --- Lane 2: wired into _resolve_ship_combat --------------------------------

def test_solo_attacker_damage_is_byte_identical_to_the_pre_wire_baseline():
    """Neither ship fleet-enrolled: attacker_damage_mult stays 1.0, so a
    base-10 hit lands for exactly 10.0 -- the same value
    test_drone_scalar_canon.py's test_defense_drones_no_longer_grant_offense
    already pins for this identical setup, proving the wire-in is a true
    no-op for the solo-combatant path."""
    db = _fleet_db([None, None])  # attacker unenrolled, defender unenrolled
    cs = CombatService(db)
    attacker = _combat_player(ship=_combat_ship(hull=1.0, max_hull=1.0))
    defender = _combat_player(ship=_combat_ship(hull=10000.0, max_hull=10000.0))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(combat_service_module.random, "random", _scripted_random([0.0, 0.99, 0.0, 0.99]))
        mp.setattr(combat_service_module.random, "randint", _fixed_randint)
        result = cs._resolve_ship_combat(attacker, defender, sector=None)

    assert result["attacker_damage_dealt"] == pytest.approx(10.0)


def test_fleet_enrolled_attacker_damage_scales_by_coordination_bonus():
    """Same setup as the baseline above, except the attacker's ship is
    enrolled in a fleet with a 0.075 coordination_bonus (a 5-ship fleet:
    min(0.20, (5-2)*0.025) = 0.075). attacker_damage_mult becomes 1.075,
    scaling the identical base-10 hit to 10.75 -- the resolver's return dict
    rounds the accumulated total to the nearest INTEGER, not 1 decimal
    (combat_service.py:3194 `int(round(attacker_damage_dealt))`), so the
    expected value here replicates that exact rounding rather than
    hand-deriving a decimal. The tiny attacker hull (1.0) means the
    defender's un-screened, unscripted-magnitude return hit ends the fight
    in the same round -- the attacker's own hit lands first and is tallied
    before that happens, so its magnitude is exact and un-floored (target
    hull is 10000, comfortably survives)."""
    coordination_bonus = 0.075
    db = _fleet_db([_enrolled(coordination_bonus), None])  # attacker enrolled, defender not
    cs = CombatService(db)
    attacker = _combat_player(ship=_combat_ship(hull=1.0, max_hull=1.0))
    defender = _combat_player(ship=_combat_ship(hull=10000.0, max_hull=10000.0))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(combat_service_module.random, "random", _scripted_random([0.0, 0.99, 0.0, 0.99]))
        mp.setattr(combat_service_module.random, "randint", _fixed_randint)
        result = cs._resolve_ship_combat(attacker, defender, sector=None)

    expected = int(round(10.0 * (1.0 + coordination_bonus)))
    assert result["attacker_damage_dealt"] == expected
    assert result["attacker_damage_dealt"] != 10


def test_fleet_enrolled_defender_return_fire_scales_by_coordination_bonus():
    """Symmetric defender-side proof. Round 1: the attacker is scripted to
    MISS outright (0.99 clears no hit_chance, which caps at 0.8) -- zero
    damage dealt, so its magnitude can't contaminate the reading and no crit
    draw is needed (a miss never reaches _apply_weapon_damage). The defender
    then lands an exact, un-floored hit on the attacker's LARGE (10000) hull
    -- this is the value under test. Neither ship died in round 1, so a
    round 2 follows: the attacker (now unmissed) destroys the defender's
    small (5.0) hull with an overkill hit whose magnitude this test does not
    assert on (only the round-1 defender return-fire magnitude matters
    here), ending the loop."""
    coordination_bonus = 0.075
    db = _fleet_db([None, _enrolled(coordination_bonus)])  # attacker not, defender enrolled
    cs = CombatService(db)
    attacker = _combat_player(ship=_combat_ship(hull=10000.0, max_hull=10000.0))
    defender = _combat_player(ship=_combat_ship(hull=5.0, max_hull=5.0))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            combat_service_module.random, "random",
            _scripted_random([0.99, 0.0, 0.99, 0.0, 0.99]),
            # round1: attacker miss | defender hit, no-crit
            # round2: attacker hit, no-crit
        )
        mp.setattr(combat_service_module.random, "randint", _fixed_randint)
        result = cs._resolve_ship_combat(attacker, defender, sector=None)

    expected = int(round(10.0 * (1.0 + coordination_bonus)))
    assert result["defender_damage_dealt"] == expected
    assert result["defender_damage_dealt"] != 10
    assert result["defender_ship_destroyed"] is True


def test_npc_initiated_attacker_resolves_to_no_bonus_without_raising():
    """WO-CMB-NPC-INITIATED-1 (2026-07-10): attacker=None, attacker_ship set
    directly -- there is no Player row to look up a fleet through, and an
    NPC-controlled ship can never be a FleetMember (fleets are Team-owned
    player structures) regardless. The ship-keyed lookup must not special-
    case this: it queries by attacker_ship.id like any other ship and
    naturally gets back None/no-match -- proven here by NOT seeding the
    FleetMember dict at all, and asserting the resolver both completes
    without raising and produces the identical byte-for-byte baseline
    damage (10.0) as the solo-Player-attacker case above."""
    db = _fleet_db([None, None])
    cs = CombatService(db)
    npc_attacker_ship = _combat_ship(hull=1.0, max_hull=1.0)
    defender = _combat_player(ship=_combat_ship(hull=10000.0, max_hull=10000.0))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(combat_service_module.random, "random", _scripted_random([0.0, 0.99, 0.0, 0.99]))
        mp.setattr(combat_service_module.random, "randint", _fixed_randint)
        result = cs._resolve_ship_combat(
            attacker=None, defender=defender, sector=None, attacker_ship=npc_attacker_ship
        )

    assert result["attacker_damage_dealt"] == pytest.approx(10.0)
