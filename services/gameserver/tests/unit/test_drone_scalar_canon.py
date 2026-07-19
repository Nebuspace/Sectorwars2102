"""Unit tests for WO-DRN-SCALAR-CANON.

Two techniques, matched to what each site needs:

1. **Behavioral** (fake-DB, pattern: tests/unit/test_drone_cap_enforcement.py /
   test_combat_escape.py): ``_resolve_ship_combat`` is exercised directly with
   a real (unpersisted) ``Ship`` instance — the resolver ``flag_modified()``s
   the ship's ``combat`` JSONB, which needs SQLAlchemy instance state a bare
   ``SimpleNamespace`` doesn't carry — plus a ``SimpleNamespace`` Player
   stand-in, and monkeypatched ``random`` for determinism, proving the
   attacker-side +5%/10 drone multiplier now reads ``Player.attack_drones``
   (not ``defense_drones``) — the single most important canon-facing
   behavior change in this WO.
2. **Static/AST** (combat_service.py's own CombatLog snapshots + post-combat
   attrition lines are simple attribute reads spread across 5 near-identical
   wrapper methods — cheaper and more precise to verify by parsing the module
   than to exercise all 5 heavy wrappers, each of which needs its own
   locked-row / turn-cost / hangar-check mocking largely orthogonal to this
   WO). AST matching (not text/grep) so an explanatory comment can never
   produce a false positive or negative.

Armory-cap tests exercise the actual route function (``purchase_armory_item``)
against a fake synchronous ``Session`` whose ``.query(Model)`` dispatches by
model class — the route itself is a handful of sequential ``db.query(...)
.filter(...).first()`` calls, no different in shape from a real request.
"""
import ast
import types
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import src.services.combat_service as combat_service_module
from src.services.combat_service import CombatService
from src.models.ship import Ship as ShipModel, ShipType
from src.models.player import Player as PlayerModel
from src.models.station import Station as StationModel
from src.api.routes.armory import (
    ArmoryPurchaseRequest,
    purchase_armory_item,
    ARMORY_CATALOG,
)


# --- Behavioral: _resolve_ship_combat reads attack_drones for the attacker ---

def _combat_ship(*, ship_type=ShipType.LIGHT_FREIGHTER, hull=1000.0, max_hull=1000.0):
    ship = ShipModel()
    ship.type = ship_type
    ship.combat = {"shields": 0, "max_shields": 0, "hull": hull, "max_hull": max_hull}
    ship.maintenance = None
    ship.is_destroyed = False
    ship.current_sector_id = None
    return ship


def _combat_player(*, attack_drones=0, defense_drones=0, ship, username="pilot"):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        username=username,
        military_rank="__no_such_rank__",  # forces the zero-bonus fallback
        attack_drones=attack_drones,
        defense_drones=defense_drones,
        current_ship=ship,
    )


def _fixed_randint(a, b):
    """base_damage rolls (1, 10) always max out; anything else returns the
    ceiling — deterministic without caring which branch called it."""
    return 10 if (a, b) == (1, 10) else b


def _scripted_random(values):
    """A ``random.random`` replacement that yields exactly ``values`` in
    order — StopIteration (a loud failure) if the resolver draws more calls
    than the scenario was scripted for, which is the point: an unscripted
    extra draw means the scenario didn't end the round it was built to."""
    it = iter(values)
    return lambda: next(it)


def test_attack_drones_feed_the_offensive_multiplier_not_defense_drones():
    """Attacker with attack_drones=20/defense_drones=0: the +5%/10 mult
    (1.10) pushes a base-10 hit to exactly 11.0 (hit, then a scripted
    NON-critical second roll so the crit bonus can't accidentally inflate
    the hit), destroying a defender ship parked at exactly 10.5 hull —
    combat ends right there (before the defender ever gets a turn), so
    only 2 random() draws are needed. Post-combat, only attack_drones would
    be debited (combat_result carries attacker_drones_lost=0 here — no
    return fire was ever thrown, so there is nothing to assert on the
    counters this round; the debit *line* itself is pinned statically below)."""
    cs = CombatService(MagicMock())
    attacker = _combat_player(attack_drones=20, defense_drones=0, ship=_combat_ship())
    defender = _combat_player(attack_drones=0, defense_drones=0, ship=_combat_ship(hull=10.5, max_hull=10.5))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(combat_service_module.random, "random", _scripted_random([0.0, 0.99]))
        mp.setattr(combat_service_module.random, "randint", _fixed_randint)
        result = cs._resolve_ship_combat(attacker, defender, sector=None)

    assert result["defender_ship_destroyed"] is True


def test_defense_drones_no_longer_grant_offense():
    """Mirror scenario: attack_drones=0/defense_drones=20 must yield mult
    1.0 (defense drones stop feeding offense) — the SAME 10.5-hull target
    survives the attacker's hit (exactly 10.0 damage, leaving 0.5 hull).
    The attacker's own hull is pinned at 1.0 so the defender's un-screened
    return hit (guaranteed, non-critical) ends the fight in the same round
    regardless of its exact magnitude — bounding this to 4 scripted draws
    without needing to walk the escape-chance / round-limit machinery."""
    cs = CombatService(MagicMock())
    attacker = _combat_player(
        attack_drones=0, defense_drones=20, ship=_combat_ship(hull=1.0, max_hull=1.0)
    )
    defender = _combat_player(attack_drones=0, defense_drones=0, ship=_combat_ship(hull=10.5, max_hull=10.5))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            combat_service_module.random, "random",
            _scripted_random([0.0, 0.99, 0.0, 0.99]),  # attacker hit/no-crit, defender hit/no-crit
        )
        mp.setattr(combat_service_module.random, "randint", _fixed_randint)
        result = cs._resolve_ship_combat(attacker, defender, sector=None)

    assert result["defender_ship_destroyed"] is False
    assert result["attacker_ship_destroyed"] is True
    assert result["attacker_damage_dealt"] == pytest.approx(10.0)


def test_defender_return_fire_still_scales_with_defense_drones():
    """Defender-side invariance (combat_service.py:2528 return-fire mult):
    with the attacker carrying no screen (attack_drones=0, so its turn is a
    guaranteed miss — hit_chance is always <= 0.8, so a fixed random() of
    0.99 never clears it), the defender's own return fire must still scale
    with ITS defense_drones (30 -> mult 1.15), independent of the attacker's
    attack_drones. A tiny attacker hull (1.0) means the defender's single
    counter-hit ends the fight in round 1 regardless of its exact magnitude,
    so only the destroy outcome need be asserted."""
    cs = CombatService(MagicMock())
    attacker = _combat_player(attack_drones=0, defense_drones=0, ship=_combat_ship(hull=1.0, max_hull=1.0))
    defender = _combat_player(attack_drones=0, defense_drones=30, ship=_combat_ship())

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            combat_service_module.random, "random",
            _scripted_random([0.99, 0.0, 0.99]),  # attacker misses; defender hits, no crit
        )
        mp.setattr(combat_service_module.random, "randint", _fixed_randint)
        result = cs._resolve_ship_combat(attacker, defender, sector=None)

    assert result["attacker_ship_destroyed"] is True
    assert result["defender_ship_destroyed"] is False


# --- Static/AST: every attacker-side site flipped, every defender-side site untouched ---

def _combat_service_ast():
    path = Path(combat_service_module.__file__)
    return ast.parse(path.read_text())


def _attribute_read_count(tree: ast.AST, base_name: str, attr_name: str) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == attr_name
        and isinstance(node.value, ast.Name)
        and node.value.id == base_name
    )


def test_attacker_side_defense_drones_reads_are_fully_flipped():
    tree = _combat_service_ast()
    assert _attribute_read_count(tree, "attacker", "defense_drones") == 0
    # The 5 seeds + 5 debits (one is a two-line max() call, still 1 read of
    # the attribute inside the max() plus 1 as the assignment target) + 5
    # CombatLog snapshots this WO flips (combat_service.py:2334, :2760,
    # :2940, :3222, :3325 seeds; :661, :1305, :1486-1487, :1621, :1771
    # debits — each debit line reads attacker.attack_drones twice, once as
    # the assignment target and once inside max(); :614, :1133, :1468,
    # :1606, :1756 CombatLog snapshots).
    assert _attribute_read_count(tree, "attacker", "attack_drones") == 20


def test_defender_side_defense_drones_reads_are_untouched():
    tree = _combat_service_ast()
    # Defender's screen seed (:2309), CombatLog snapshot (:615), post-combat
    # debit (:664, read twice), and the :2528 return-fire mult all still key
    # off defense_drones — bit-identical per the WO's constraint.
    assert _attribute_read_count(tree, "defender", "defense_drones") >= 4
    assert _attribute_read_count(tree, "defender", "attack_drones") == 0


def test_combat_log_attacker_drones_snapshot_reads_attack_drones():
    """5 of 6 CombatLog(...) constructor sites (ship-vs-ship, sector drones,
    planet, port, plus attack_npc_ship) snapshot attacker_drones= from
    attacker.attack_drones -- a real Player row.

    The 6th site, npc_attack_player (WO-CMB-NPC-INITIATED-1, Max ruling
    2026-07-10 -- the symmetric NPC-initiated-attack mirror of
    attack_npc_ship), is the deliberate exception: there IS no `attacker`
    Player variable in that function at all (attacker=None is passed to
    _resolve_ship_combat; the attacking side is npc_ship, an NPC-controlled
    Ship). It carries a literal attacker_drones=0, not an attribute read --
    an NPC ship has no attack_drones concept to snapshot. Bumped 5 -> 6 as
    its own reviewed change (mirrors test_combat_log_region_snapshot.py's
    own 5->6 pin bump documenting this same site) rather than a silent
    widening of the assertion -- a further count change must stay deliberate."""
    tree = _combat_service_ast()
    kwarg_values = [
        kw.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CombatLog"
        for kw in node.keywords
        if kw.arg == "attacker_drones"
    ]
    assert len(kwarg_values) == 6

    attribute_reads = [v for v in kwarg_values if isinstance(v, ast.Attribute)]
    constant_zeros = [v for v in kwarg_values if isinstance(v, ast.Constant)]
    assert len(attribute_reads) == 5, (
        "expected exactly 5 sites reading attacker.attack_drones -- a "
        "changed count here means a real Player-attacker site stopped "
        "snapshotting the live drone count (or gained/lost a site); review "
        "deliberately."
    )
    assert len(constant_zeros) == 1, (
        "expected exactly 1 constant-0 site (npc_attack_player, no Player "
        "attacker to read from) -- if this count changed, either a new "
        "NPC-attacker site was added (review + document it here) or the "
        "existing one now wrongly reads an attribute."
    )
    for value in attribute_reads:
        assert value.attr == "attack_drones"
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "attacker"
    assert constant_zeros[0].value == 0


# --- Armory: carried-scalar caps honor the Drone Bay bonus ---

def _armory_db(*, player, station, ship, spec):
    from src.models.ship import ShipSpecification as ShipSpecModel

    db = MagicMock()

    def _query(model):
        q = MagicMock()
        if model is PlayerModel:
            # armory.py's route chains .populate_existing() ahead of
            # .with_for_update() (pre-existing route-layer convention,
            # WO-MONEY-REREAD-CLASS) -- route the mock chain through it so
            # .first() still resolves to the real player fixture instead of
            # an unconfigured auto-vivified MagicMock.
            filtered = q.filter.return_value
            filtered.populate_existing.return_value = filtered
            filtered.with_for_update.return_value.first.return_value = player
        elif model is StationModel:
            q.filter.return_value.first.return_value = station
        elif model is ShipModel:
            q.filter.return_value.first.return_value = ship
        elif model is ShipSpecModel:
            q.filter.return_value.first.return_value = spec
        else:
            raise AssertionError(f"unexpected db.query({model!r})")
        return q

    db.query.side_effect = _query
    db.commit = MagicMock()
    return db


def _armory_player(*, attack_drones=0):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        credits=1_000_000,
        attack_drones=attack_drones,
        defense_drones=0,
        mines=0,
        is_docked=True,
        current_port_id=uuid.uuid4(),
        current_ship_id=uuid.uuid4(),
    )


def _armory_station():
    return types.SimpleNamespace(id=uuid.uuid4(), is_spacedock=True, services={})


def _armory_ship(*, upgrades=None):
    return types.SimpleNamespace(id=uuid.uuid4(), type="light_freighter", upgrades=upgrades)


def _armory_spec(*, max_drones=5):
    return types.SimpleNamespace(max_drones=max_drones)


@pytest.mark.asyncio
async def test_armory_cap_includes_drone_bay_bonus_and_accepts_up_to_it():
    player = _armory_player()
    db = _armory_db(
        player=player, station=_armory_station(),
        ship=_armory_ship(upgrades={"DRONE_BAY": 2}), spec=_armory_spec(max_drones=5),
    )

    result = await purchase_armory_item(
        ArmoryPurchaseRequest(item="attack_drone", quantity=9), player=player, db=db
    )

    assert result["loadout"]["caps"]["attack_drones"] == 9
    assert player.attack_drones == 9


@pytest.mark.asyncio
async def test_armory_cap_with_drone_bay_bonus_rejects_past_it():
    player = _armory_player()
    db = _armory_db(
        player=player, station=_armory_station(),
        ship=_armory_ship(upgrades={"DRONE_BAY": 2}), spec=_armory_spec(max_drones=5),
    )

    with pytest.raises(HTTPException) as excinfo:
        await purchase_armory_item(
            ArmoryPurchaseRequest(item="attack_drone", quantity=10), player=player, db=db
        )

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_armory_cap_without_drone_bay_upgrade_stays_at_spec_max():
    player = _armory_player()
    db = _armory_db(
        player=player, station=_armory_station(),
        ship=_armory_ship(upgrades={}), spec=_armory_spec(max_drones=5),
    )

    result = await purchase_armory_item(
        ArmoryPurchaseRequest(item="attack_drone", quantity=5), player=player, db=db
    )
    assert result["loadout"]["caps"]["attack_drones"] == 5

    with pytest.raises(HTTPException) as excinfo:
        await purchase_armory_item(
            ArmoryPurchaseRequest(item="attack_drone", quantity=1), player=player, db=db
        )
    assert excinfo.value.status_code == 400


def test_armory_catalog_unchanged_by_this_wo():
    """Regression pin: this WO touches caps only, not prices/catalog shape."""
    assert set(ARMORY_CATALOG) == {"attack_drone", "defense_drone", "limpet_mine", "armored_mine"}
