"""Unit tests for WO-DRN-COMBAT-RECORD.

Two techniques, matched to what each site needs (pattern:
tests/unit/test_drone_scalar_canon.py):

1. **Behavioral, resolver-level** (fake-DB via a bare ``MagicMock()``
   ``CombatService.db`` — the resolver itself never touches ``self.db``):
   ``_resolve_sector_drone_combat`` is exercised directly against real
   (unpersisted) ``Drone`` instances — needed for their genuine
   ``take_damage`` method — plus a ``SimpleNamespace`` attacker/sector and a
   real ``Ship`` for the same ``flag_modified``-needs-instance-state reason
   as the sibling WO's tests. Covers battles_fought / damage_dealt /
   SECTOR_DEFENSE bonus / kills semantics — none of which depend on
   ``self.db``.

2. **Behavioral, route-level** (fake synchronous ``Session`` whose
   ``.query(Model)`` dispatches by model class, pattern:
   tests/unit/test_drone_scalar_canon.py's ``_armory_db``):
   ``attack_sector_drones`` is exercised end-to-end to prove the
   ``DroneCombat`` row-per-participant write and the String(2000)
   ``combat_log`` truncation — both live only in the route wrapper, not the
   resolver.

3. **Static/AST**: a real ``DroneCombat(...)`` constructor call site exists
   in combat_service.py (today the only hits are the class def, ``__repr__``,
   and the import) — AST (not grep/text) so an explanatory comment can never
   produce a false positive.

All scenarios are engineered so the *outcome* (DRAW / ATTACKER_VICTORY /
DEFENDER_VICTORY) is deterministic from plain arithmetic (huge ship hull to
force survival across all 8 rounds, or a 1-hp ship hull to force destruction
on the first return-fire hit) — only ``random.randint`` (the per-drone
return-fire roll) is ever monkeypatched, never ``random.random`` (the
_apply_weapon_damage critical chance, which affects ship hull/shields only,
never the drone-side counters this WO adds).
"""
import ast
import json
import types
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import src.services.combat_service as combat_service_module
from src.services.combat_service import CombatService
from src.models.combat import CombatType
from src.models.drone import Drone as DroneModel, DroneCombat as DroneCombatModel, DroneStatus
from src.models.player import Player as PlayerModel
from src.models.sector import Sector as SectorModel
from src.models.ship import Ship as ShipModel, ShipType


# --- Shared fixtures --------------------------------------------------------

def _combat_ship(*, hull=100_000.0, max_hull=100_000.0):
    ship = ShipModel()
    ship.type = ShipType.LIGHT_FREIGHTER
    ship.combat = {"shields": 0, "max_shields": 0, "hull": hull, "max_hull": max_hull}
    ship.maintenance = None
    ship.is_destroyed = False
    ship.current_sector_id = None
    return ship


def _attacker(*, ship, attack_drones=0):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        username="pilot",
        military_rank="__no_such_rank__",  # forces the zero-bonus fallback
        attack_drones=attack_drones,
        current_ship=ship,
        current_ship_id=uuid.uuid4(),
    )


def _hostile_drone(*, attack_power=10, defense_power=0, health=1000, max_health=1000):
    drone = DroneModel()
    drone.id = uuid.uuid4()
    drone.drone_type = "attack"
    drone.attack_power = attack_power
    drone.defense_power = defense_power
    drone.health = health
    drone.max_health = max_health
    drone.status = DroneStatus.DEPLOYED.value
    drone.battles_fought = 0
    drone.damage_dealt = 0
    drone.damage_taken = 0
    drone.kills = 0
    return drone


def _sector(sector_id=42):
    return types.SimpleNamespace(id=uuid.uuid4(), sector_id=sector_id)


# --- Resolver-level: battles_fought / damage_dealt / damage_taken ----------

def test_battles_fought_and_damage_dealt_recorded_damage_taken_untouched_by_new_code():
    """3 drones, huge health/hull -> none destroyed across all 8 rounds
    (DRAW). Each drone counts the engagement exactly once. All 3 return fire
    every round (none are ever removed from live_drones), so damage_dealt is
    positive for all 3. The attacker always focuses live_drones[0] (the same
    object every round, since nothing here ever gets destroyed) — proving
    damage_taken stays exactly where take_damage (pre-existing, untouched by
    this WO) leaves it: >0 for the focused drone, 0 for the other two."""
    cs = CombatService(MagicMock())
    drones = [_hostile_drone() for _ in range(3)]
    attacker = _attacker(ship=_combat_ship())
    sector = _sector()

    result = cs._resolve_sector_drone_combat(attacker, sector, drones)

    assert result["result"].name == "DRAW"
    for drone in drones:
        assert drone.battles_fought == 1
        assert drone.damage_dealt > 0
    assert drones[0].damage_taken > 0
    assert drones[1].damage_taken == 0
    assert drones[2].damage_taken == 0


# --- Resolver-level: SECTOR_DEFENSE +5% return-fire bonus -------------------

def test_sector_defense_bonus_multiplies_return_fire_by_exactly_1_05():
    """Deterministic randint (always returns the ceiling, 20) makes the
    pre-bonus hit exactly 20; SECTOR_DEFENSE_BONUS_MULT (1.05) must land the
    drone's recorded damage_dealt at exactly 21 — falsifying a resolver that
    grants no bonus (which would leave it at 20). A 1-hp ship hull ends the
    fight after exactly this one return-fire hit, so there is only a single
    round's damage to assert on (not an 8-round accumulation)."""
    cs = CombatService(MagicMock())
    drone = _hostile_drone(attack_power=20)
    attacker = _attacker(ship=_combat_ship(hull=1.0, max_hull=1.0))
    sector = _sector()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(combat_service_module.random, "randint", lambda a, b: b)
        result = cs._resolve_sector_drone_combat(attacker, sector, [drone])

    assert drone.damage_dealt == 21
    assert result["defender_damage_dealt"] == 21
    tagged = [e for e in result["combat_details"] if e.get("tag") == CombatType.SECTOR_DEFENSE.value]
    assert len(tagged) >= 1


# --- Resolver-level: kills semantics + winner_drone_id ----------------------

def test_kills_credited_to_survivors_only_when_attacker_ship_is_destroyed():
    """2 drones with high health (never destroyed) vs. a 1-hp ship: round 1's
    return fire is guaranteed lethal (any positive hit clears a 1.0 hull with
    0 shields), so both surviving drones get kills+=1 and winner_drone_id is
    the first survivor."""
    cs = CombatService(MagicMock())
    drones = [_hostile_drone(attack_power=50) for _ in range(2)]
    attacker = _attacker(ship=_combat_ship(hull=1.0, max_hull=1.0))
    sector = _sector()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(combat_service_module.random, "randint", lambda a, b: b)
        result = cs._resolve_sector_drone_combat(attacker, sector, drones)

    assert result["attacker_ship_destroyed"] is True
    assert result["result"].name == "DEFENDER_VICTORY"
    assert drones[0].kills == 1
    assert drones[1].kills == 1
    assert result["winner_drone_id"] == drones[0].id


def test_kills_stay_zero_and_no_winner_when_the_ship_survives():
    """2 low-health drones vs. a huge-hull ship: the attacker clears the
    sector (ATTACKER_VICTORY) before either drone ever returns fire — no
    drone kills a ship here, so kills stay 0 and winner_drone_id is None."""
    cs = CombatService(MagicMock())
    drones = [_hostile_drone(health=5, max_health=5) for _ in range(2)]
    attacker = _attacker(ship=_combat_ship())
    sector = _sector()

    result = cs._resolve_sector_drone_combat(attacker, sector, drones)

    assert result["result"].name == "ATTACKER_VICTORY"
    assert result["attacker_ship_destroyed"] is False
    assert drones[0].kills == 0
    assert drones[1].kills == 0
    assert result["winner_drone_id"] is None


# --- Route-level: DroneCombat row-per-participant + combat_log truncation --

class _SectorDronesDb:
    """Drives CombatService.attack_sector_drones's db.query(...) call order
    for a DRAW outcome (no drones destroyed -> the DroneDeployment-recall
    branch and the destroy_pirate_drones reputation hook are both skipped,
    so only Player / Sector / Drone queries are ever dispatched)."""

    def __init__(self, *, attacker, sector, target_drones):
        self._attacker = attacker
        self._sector = sector
        self._target_drones = target_drones
        self.add = MagicMock()
        self.commit = MagicMock()

    def query(self, model):
        q = MagicMock()
        if model is PlayerModel:
            # WO-MONEY-REREAD-SERVICES: combat_service now chains
            # .populate_existing() ahead of .with_for_update() -- route the
            # mock chain through it (returns the same filtered-query mock)
            # so .first() still resolves to the real attacker fixture
            # instead of an unconfigured auto-vivified MagicMock.
            filtered = q.filter.return_value
            filtered.populate_existing.return_value = filtered
            filtered.with_for_update.return_value.first.return_value = self._attacker
        elif model is SectorModel:
            q.filter.return_value.first.return_value = self._sector
        elif model is DroneModel:
            q.filter.return_value.with_for_update.return_value.all.return_value = self._target_drones
        else:
            raise AssertionError(f"unexpected db.query({model!r}) in a no-destruction DRAW scenario")
        return q


def _dronecombat_rows(db):
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], DroneCombatModel)]


def test_attack_sector_drones_writes_one_dronecombat_row_per_participant():
    sector = _sector(sector_id=42)
    drones = [_hostile_drone() for _ in range(3)]
    attacker = _attacker(ship=_combat_ship())
    attacker.current_sector_id = 42
    attacker.is_docked = False
    attacker.is_landed = False
    attacker.turns = 10
    attacker.lifetime_turns_spent = 0
    db = _SectorDronesDb(attacker=attacker, sector=sector, target_drones=drones)

    result = CombatService(db).attack_sector_drones(attacker_id=attacker.id, sector_id=42)

    assert result["success"] is True
    rows = _dronecombat_rows(db)
    assert len(rows) == 3
    assert {r.defender_drone_id for r in rows} == {d.id for d in drones}
    assert all(r.attacker_drone_id is None for r in rows)
    assert all(r.sector_id == sector.id for r in rows)
    assert all(r.rounds > 0 for r in rows)


def test_attack_sector_drones_long_engagement_combat_log_fits_column():
    """12 drones, huge ship hull -> the full 8-round cap runs (nothing dies
    on either side). The genuine combat_details JSON for that engagement
    exceeds DroneCombat.combat_log's String(2000) bound (proving this
    scenario actually exercises truncation, not trivially fits already);
    every written row's combat_log must still be <= 2000 chars — no
    DataError on insert."""
    sector = _sector(sector_id=7)
    drones = [_hostile_drone() for _ in range(12)]
    attacker = _attacker(ship=_combat_ship())
    attacker.current_sector_id = 7
    attacker.is_docked = False
    attacker.is_landed = False
    attacker.turns = 10
    attacker.lifetime_turns_spent = 0
    db = _SectorDronesDb(attacker=attacker, sector=sector, target_drones=drones)

    result = CombatService(db).attack_sector_drones(attacker_id=attacker.id, sector_id=7)

    assert result["success"] is True
    assert len(json.dumps(result["combat_details"])) > 2000
    rows = _dronecombat_rows(db)
    assert len(rows) == 12
    assert all(len(r.combat_log) <= 2000 for r in rows)


# --- Static/AST: a real constructor call site exists ------------------------

def test_dronecombat_constructor_call_site_exists_in_combat_service():
    tree = ast.parse(Path(combat_service_module.__file__).read_text())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "DroneCombat"
    ]
    assert len(calls) >= 1
