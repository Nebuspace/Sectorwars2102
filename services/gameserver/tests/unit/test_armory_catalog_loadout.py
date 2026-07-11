"""Route-level tests for DEFECT-armory-caps-missing-until-purchase.

GET /armory/catalog used to return {items: [...]} only — no loadout, no
caps — because caps were only ever computed inside POST /purchase. The
SpaceDock "Current Ship Loadout" box (SpaceDockInterface.tsx) derefs
armoryLoadout.caps.* unconditionally once a `loadout` key is present on the
response (only gated by `if (data.loadout)`), so the fix must make caps
available on the catalog GET too, WITHOUT ever shipping `loadout: {caps:
null}` (that would be an unconditional-deref crash).

This file proves the shared _armory_caps / _current_loadout helpers armory.py
now uses, using a fake sync Session mirroring the exact
`db.query(Model).filter(...).first()` idiom armory.py calls (SQLAlchemy's
sync ORM API — this route has never been async-session based, unlike
DroneService), the pattern established in
tests/unit/test_research_unlock_route.py's `_FakeSession` / `_FakeQuery`,
generalized to dispatch by model class since armory.py queries four
different models (Player, Station, Ship, ShipSpecification) across its two
endpoints.

No real DB / no TestClient (the Mac has no live Postgres) — route handlers
are called directly.
"""
import types
import uuid

import pytest

from src.api.routes import armory as route
from src.models.player import Player
from src.models.ship import Ship, ShipSpecification
from src.models.station import Station
from src.services.drone_service import DroneService


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #

class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeSession:
    """Dispatches db.query(Model) to a fixed per-model row. armory.py never
    queries more than one row per model in a single request, so a flat
    Model -> row map is sufficient (no ordering/side_effect list needed)."""

    def __init__(self, *, player=None, station=None, ship=None, spec=None):
        self._by_model = {
            Player: player,
            Station: station,
            Ship: ship,
            ShipSpecification: spec,
        }
        self.committed = False

    def query(self, model):
        assert model in self._by_model, f"unexpected query model {model}"
        return _FakeQuery(self._by_model[model])

    def commit(self):
        self.committed = True


def make_player(*, current_ship_id=None, attack_drones=0, defense_drones=0,
                 mines=0, credits=100_000, is_docked=True, current_port_id=None):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        current_ship_id=current_ship_id,
        attack_drones=attack_drones,
        defense_drones=defense_drones,
        mines=mines,
        credits=credits,
        is_docked=is_docked,
        current_port_id=current_port_id,
    )


def make_ship(*, drone_bay_level=0, ship_type="light_freighter"):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        type=ship_type,
        upgrades={"DRONE_BAY": drone_bay_level} if drone_bay_level else {},
    )


def make_spec(*, ship_type="light_freighter", max_drones=10):
    return types.SimpleNamespace(type=ship_type, max_drones=max_drones)


def make_station(*, is_spacedock=True, services=None):
    return types.SimpleNamespace(id=uuid.uuid4(), is_spacedock=is_spacedock, services=services or {})


def _expected_caps_pre_refactor_formula(ship, spec):
    """A literal re-implementation of the ORIGINAL (pre-helper) purchase-
    handler caps block (armory.py:190-195 before this fix), kept here ONLY
    to diff against _armory_caps's output and prove the extraction didn't
    drift the formula."""
    drone_bay_bonus = DroneService._drone_bay_bonus(ship)
    return {
        "attack_drones": spec.max_drones + drone_bay_bonus,
        "defense_drones": spec.max_drones + drone_bay_bonus,
        "mines": route.MINES_CAP,
    }


# --------------------------------------------------------------------------- #
# (a) _current_loadout / _armory_caps — ship+spec present, formula identity
# --------------------------------------------------------------------------- #

def test_armory_caps_matches_pre_refactor_formula_exactly():
    """Explicit diff: _armory_caps's output must equal the literal formula
    the purchase handler used to compute inline, for a ship carrying a
    Drone Bay upgrade (nonzero bonus is the case most likely to drift)."""
    ship = make_ship(drone_bay_level=3)
    spec = make_spec(max_drones=10)

    caps = route._armory_caps(ship, spec)

    assert caps == _expected_caps_pre_refactor_formula(ship, spec)
    # And the concrete numbers, so a future edit to either formula trips this
    # test even if both sides of the diff drifted identically:
    assert caps == {"attack_drones": 16, "defense_drones": 16, "mines": 25}


def test_current_loadout_returns_counts_and_caps_when_ship_and_spec_resolve():
    ship = make_ship(drone_bay_level=2)
    spec = make_spec(max_drones=8)
    player = make_player(current_ship_id=ship.id, attack_drones=3, defense_drones=1, mines=2)
    db = _FakeSession(player=player, ship=ship, spec=spec)

    loadout = route._current_loadout(player, db)

    assert loadout == {
        "attack_drones": 3,
        "defense_drones": 1,
        "mines": 2,
        "caps": {"attack_drones": 12, "defense_drones": 12, "mines": 25},
    }


def test_current_loadout_none_when_no_current_ship_id():
    player = make_player(current_ship_id=None)
    db = _FakeSession(player=player, ship=None, spec=None)

    assert route._current_loadout(player, db) is None


def test_current_loadout_none_when_ship_row_missing():
    """current_ship_id set but the Ship row itself doesn't resolve (deleted /
    orphaned FK) -> None, not a crash."""
    ghost_ship_id = uuid.uuid4()
    player = make_player(current_ship_id=ghost_ship_id)
    db = _FakeSession(player=player, ship=None, spec=None)

    assert route._current_loadout(player, db) is None


def test_current_loadout_none_when_spec_missing():
    """Ship resolves but its type has no ShipSpecification row -> None."""
    ship = make_ship(ship_type="mystery_hull")
    player = make_player(current_ship_id=ship.id)
    db = _FakeSession(player=player, ship=ship, spec=None)

    assert route._current_loadout(player, db) is None


# --------------------------------------------------------------------------- #
# (b) GET /armory/catalog — the crash-avoidance regression guard
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_catalog_omits_loadout_key_for_shipless_player():
    """The key regression guard: a shipless player's catalog response must
    have NO `loadout` key at all (not `loadout: {caps: None}`), since the
    frontend derefs armoryLoadout.caps.* unconditionally once `loadout` is
    present on the response."""
    player = make_player(current_ship_id=None)
    db = _FakeSession(player=player)

    response = await route.get_armory_catalog(player=player, db=db)

    assert "loadout" not in response
    assert "items" in response and len(response["items"]) == len(route.ARMORY_CATALOG)


@pytest.mark.asyncio
async def test_catalog_includes_loadout_with_caps_for_player_with_ship():
    ship = make_ship(drone_bay_level=0)
    spec = make_spec(max_drones=15)
    player = make_player(current_ship_id=ship.id, attack_drones=4, defense_drones=2, mines=0)
    db = _FakeSession(player=player, ship=ship, spec=spec)

    response = await route.get_armory_catalog(player=player, db=db)

    assert response["loadout"] == {
        "attack_drones": 4,
        "defense_drones": 2,
        "mines": 0,
        "caps": {"attack_drones": 15, "defense_drones": 15, "mines": 25},
    }


# --------------------------------------------------------------------------- #
# (c) POST /armory/purchase — loadout block still carries correct caps
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_purchase_response_loadout_caps_match_helper_formula():
    ship = make_ship(drone_bay_level=1)  # +2 bonus
    spec = make_spec(max_drones=10)
    station = make_station(is_spacedock=True)
    player = make_player(
        current_ship_id=ship.id,
        attack_drones=3,
        defense_drones=0,
        mines=0,
        credits=100_000,
        is_docked=True,
        current_port_id=station.id,
    )
    db = _FakeSession(player=player, station=station, ship=ship, spec=spec)
    request = route.ArmoryPurchaseRequest(item="attack_drone", quantity=5)

    result = await route.purchase_armory_item(request=request, player=player, db=db)

    assert result["loadout"] == {
        "attack_drones": 8,  # 3 + 5
        "defense_drones": 0,
        "mines": 0,
        "caps": {"attack_drones": 12, "defense_drones": 12, "mines": 25},
    }
    assert db.committed is True


@pytest.mark.asyncio
async def test_purchase_still_rejects_over_cap_using_shared_caps():
    """The cap-enforcement branch (armory.py :208-215, unchanged by this fix)
    must still reject using the SAME caps the helper now also serves to
    GET /catalog — proves the refactor didn't loosen enforcement."""
    ship = make_ship(drone_bay_level=0)
    spec = make_spec(max_drones=5)
    station = make_station(is_spacedock=True)
    player = make_player(
        current_ship_id=ship.id,
        attack_drones=4,
        credits=100_000,
        is_docked=True,
        current_port_id=station.id,
    )
    db = _FakeSession(player=player, station=station, ship=ship, spec=spec)
    request = route.ArmoryPurchaseRequest(item="attack_drone", quantity=2)  # 4+2 > 5 cap

    with pytest.raises(Exception) as exc_info:
        await route.purchase_armory_item(request=request, player=player, db=db)

    assert getattr(exc_info.value, "status_code", None) == 400
    assert db.committed is False
