"""WO-BUILD-SHIP-EJECT-BOARD-REGISTRY-STATES -- eject/board (ship-registry.md
"Eject and board" + "Hatch pin lock").

Ruling context: these two routes were the missing piece keeping the
canonical Drift/Borrow registry states structurally unreachable -- the data
model (Ship.current_pilot_id, sync_current_pilot, Stolen/Wanted wiring) was
already correct and dormant. The salvage-break bypass stays dormant (canon
itself tags it "Design-only -- not in code today"); board() simply 409s a
pin-less attempt on a locked, non-owned ship, matching canon's own stated
"Failed boarding... returns 403/409 Forbidden" behavior for that case.

DB-free: pins ``eject_ship``/``board_ship``'s own decision logic (the ERR_*
eligibility gates, the turn-cost derivation, and that the right arguments
reach ``sync_current_pilot``) -- ``sync_current_pilot`` itself and
``regenerate_turns`` are pre-existing, already-relied-upon shared helpers
and are monkeypatched out here rather than re-verified, mirroring
test_ship_registry_contested_transfer.py's fake-session convention and this
session's test_contraband_sell_bust_worst_severity.py's "stub the trusted
collaborator, assert on what it was called with" approach.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.models.ship import ShipStatus, ShipType
from src.services import ship_registry_service as svc
from src.services.ship_registry_service import ShipRegistryError, board_ship, eject_ship


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._result


class _FakeSession:
    """Routes db.query(Ship) to a single canned ship result -- both
    eject_ship (old-ship lookup) and board_ship (old-ship lookup for the
    boarder's current ship) only ever issue one Ship query per call in the
    scenarios tested here."""

    def __init__(self, *, ship_result=None):
        self._ship_result = ship_result

    def query(self, entity):
        return _FakeQuery(self._ship_result)

    def flush(self):
        pass


def make_player(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        current_ship_id=None,
        current_sector_id=5,
        is_docked=False,
        turns=1000,
        lifetime_turns_spent=0,
        is_wanted=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_ship(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        type=ShipType.LIGHT_FREIGHTER,
        status=None,
        is_destroyed=False,
        sector_id=5,
        current_pilot_id=None,
        registered_owner_id=uuid.uuid4(),
        hatch_pin_code="ABC123",
        stolen_status=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture(autouse=True)
def _no_op_turn_regen(monkeypatch):
    # regenerate_turns is imported at module level in ship_registry_service
    # (from src.services.turn_service import regenerate_turns) -- patch the
    # name bound in THIS module's namespace, not the source module's.
    monkeypatch.setattr(svc, "regenerate_turns", lambda db, player: None)


@pytest.fixture
def fake_sync_current_pilot(monkeypatch):
    calls = []

    def _fake(player, new_ship, *, old_ship=None, db=None):
        calls.append({"player": player, "new_ship": new_ship, "old_ship": old_ship, "db": db})
        if new_ship is not None:
            new_ship.current_pilot_id = player.id

    monkeypatch.setattr("src.services.ship_service.sync_current_pilot", _fake)
    return calls


@pytest.fixture
def fake_ensure_escape_pod(monkeypatch):
    pod = make_ship(type=ShipType.ESCAPE_POD, registered_owner_id=None, hatch_pin_code=None)
    created = {}

    class _FakeShipService:
        def __init__(self, db):
            self.db = db

        def _ensure_escape_pod(self, player, sector_id):
            created["player"] = player
            created["sector_id"] = sector_id
            return pod

    monkeypatch.setattr("src.services.ship_service.ShipService", _FakeShipService)
    return pod, created


# --- eject_ship -----------------------------------------------------------

def test_eject_no_current_ship_rejects():
    player = make_player(current_ship_id=None)
    with pytest.raises(ShipRegistryError) as exc:
        eject_ship(_FakeSession(), player=player)
    assert exc.value.code == "ERR_NO_CURRENT_SHIP"


def test_eject_dangling_current_ship_pointer_clears_and_rejects():
    player = make_player(current_ship_id=uuid.uuid4())
    db = _FakeSession(ship_result=None)  # old_ship lookup returns nothing
    with pytest.raises(ShipRegistryError) as exc:
        eject_ship(db, player=player)
    assert exc.value.code == "ERR_NO_CURRENT_SHIP"
    assert player.current_ship_id is None


def test_eject_already_in_escape_pod_rejects():
    pod = make_ship(type=ShipType.ESCAPE_POD)
    player = make_player(current_ship_id=pod.id)
    db = _FakeSession(ship_result=pod)
    with pytest.raises(ShipRegistryError) as exc:
        eject_ship(db, player=player)
    assert exc.value.code == "ERR_ALREADY_IN_ESCAPE_POD"


def test_eject_insufficient_turns_rejects(fake_sync_current_pilot, fake_ensure_escape_pod):
    ship = make_ship()
    player = make_player(current_ship_id=ship.id, is_docked=False, turns=0)
    db = _FakeSession(ship_result=ship)
    with pytest.raises(ShipRegistryError) as exc:
        eject_ship(db, player=player)
    assert exc.value.code == "ERR_INSUFFICIENT_TURNS"


def test_eject_docked_is_free_and_calls_sync_current_pilot(fake_sync_current_pilot, fake_ensure_escape_pod):
    ship = make_ship()
    player = make_player(current_ship_id=ship.id, is_docked=True, turns=50)
    db = _FakeSession(ship_result=ship)

    result = eject_ship(db, player=player)

    assert result["turns_spent"] == 0
    assert player.turns == 50  # unchanged
    assert player.current_ship_id == fake_ensure_escape_pod[0].id
    assert len(fake_sync_current_pilot) == 1
    call = fake_sync_current_pilot[0]
    assert call["old_ship"] is ship
    assert call["new_ship"] is fake_ensure_escape_pod[0]


def test_eject_in_space_costs_one_turn(fake_sync_current_pilot, fake_ensure_escape_pod):
    ship = make_ship()
    player = make_player(current_ship_id=ship.id, is_docked=False, turns=50)
    db = _FakeSession(ship_result=ship)

    result = eject_ship(db, player=player)

    assert result["turns_spent"] == 1
    assert player.turns == 49


# --- board_ship -------------------------------------------------------------

def test_board_destroyed_ship_rejects(fake_sync_current_pilot):
    ship = make_ship(is_destroyed=True)
    player = make_player()
    with pytest.raises(ShipRegistryError) as exc:
        board_ship(_FakeSession(), ship=ship, boarder=player)
    assert exc.value.code == "ERR_SHIP_DESTROYED"


def test_board_harmonizing_ship_rejects(fake_sync_current_pilot):
    ship = make_ship(status=ShipStatus.HARMONIZING)
    player = make_player()
    with pytest.raises(ShipRegistryError) as exc:
        board_ship(_FakeSession(), ship=ship, boarder=player)
    assert exc.value.code == "ERR_SHIP_HARMONIZING"


def test_board_already_piloting_rejects(fake_sync_current_pilot):
    player = make_player()
    ship = make_ship(current_pilot_id=player.id)
    with pytest.raises(ShipRegistryError) as exc:
        board_ship(_FakeSession(), ship=ship, boarder=player)
    assert exc.value.code == "ERR_ALREADY_PILOTING"


def test_board_different_sector_rejects(fake_sync_current_pilot):
    player = make_player(current_sector_id=5)
    ship = make_ship(sector_id=9)
    with pytest.raises(ShipRegistryError) as exc:
        board_ship(_FakeSession(), ship=ship, boarder=player)
    assert exc.value.code == "ERR_DIFFERENT_SECTOR"


def test_board_stranger_no_pin_rejects(fake_sync_current_pilot):
    player = make_player()
    ship = make_ship(hatch_pin_code="ABC123")  # registered_owner_id != player.id
    with pytest.raises(ShipRegistryError) as exc:
        board_ship(_FakeSession(), ship=ship, boarder=player, pin=None)
    assert exc.value.code == "ERR_SHIP_LOCKED"


def test_board_stranger_wrong_pin_rejects(fake_sync_current_pilot):
    player = make_player()
    ship = make_ship(hatch_pin_code="ABC123")
    with pytest.raises(ShipRegistryError) as exc:
        board_ship(_FakeSession(), ship=ship, boarder=player, pin="WRONG1")
    assert exc.value.code == "ERR_SHIP_LOCKED"


def test_board_stranger_correct_pin_succeeds_as_borrowed(fake_sync_current_pilot):
    player = make_player(turns=50, is_docked=True)
    ship = make_ship(hatch_pin_code="ABC123", stolen_status=False)
    result = board_ship(_FakeSession(), ship=ship, boarder=player, pin="ABC123")
    assert result["boarded"] is True
    assert result["state"] == "borrowed"
    assert player.current_ship_id == ship.id
    assert len(fake_sync_current_pilot) == 1


def test_board_owner_no_pin_succeeds_as_owner_aboard(fake_sync_current_pilot):
    player = make_player(turns=50, is_docked=True)
    ship = make_ship(registered_owner_id=player.id, hatch_pin_code="ABC123")
    result = board_ship(_FakeSession(), ship=ship, boarder=player, pin=None)
    assert result["state"] == "owner_aboard"


def test_board_stolen_ship_reports_stolen_piloted_and_wanted(fake_sync_current_pilot):
    player = make_player(turns=50, is_docked=True, is_wanted=True)
    ship = make_ship(hatch_pin_code="ABC123", stolen_status=True)
    result = board_ship(_FakeSession(), ship=ship, boarder=player, pin="ABC123")
    assert result["state"] == "stolen_piloted"
    assert result["now_wanted"] is True


def test_board_insufficient_turns_rejects(fake_sync_current_pilot):
    player = make_player(turns=0, is_docked=False)
    ship = make_ship(registered_owner_id=player.id)
    with pytest.raises(ShipRegistryError) as exc:
        board_ship(_FakeSession(), ship=ship, boarder=player)
    assert exc.value.code == "ERR_INSUFFICIENT_TURNS"


def test_board_docked_is_free_undocked_costs_one_turn(fake_sync_current_pilot):
    player_docked = make_player(turns=50, is_docked=True)
    ship_a = make_ship(registered_owner_id=player_docked.id)
    result_a = board_ship(_FakeSession(), ship=ship_a, boarder=player_docked)
    assert result_a["turns_spent"] == 0
    assert player_docked.turns == 50

    player_space = make_player(turns=50, is_docked=False)
    ship_b = make_ship(registered_owner_id=player_space.id)
    result_b = board_ship(_FakeSession(), ship=ship_b, boarder=player_space)
    assert result_b["turns_spent"] == 1
    assert player_space.turns == 49


def test_board_old_ship_passed_to_sync_current_pilot(fake_sync_current_pilot):
    old_ship = make_ship()
    player = make_player(current_ship_id=old_ship.id, turns=50, is_docked=True)
    new_ship = make_ship(registered_owner_id=player.id)
    db = _FakeSession(ship_result=old_ship)

    board_ship(db, ship=new_ship, boarder=player, pin=None)

    call = fake_sync_current_pilot[0]
    assert call["old_ship"] is old_ship
    assert call["new_ship"] is new_ship
