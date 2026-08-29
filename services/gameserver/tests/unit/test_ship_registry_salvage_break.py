"""WO-BUILD-SHIP-EJECT-BOARD-ROUTES sub-part 3: the salvage-break mechanic
(ship-registry.md "Salvage break", ADR-0049 SK14).

This was the one genuinely missing piece of the WO -- eject/board/set-pin/
request-pin-reset were already shipped by an earlier pass (verified before
building: grepped every route + service symbol the WO named). The Ship
model's ``salvage_break_in_progress_by_id`` / ``salvage_break_started_at``
columns existed but were never read or written anywhere in the codebase
before this change.

DB-free: pins ``start_salvage_break`` / ``_complete_salvage_break`` /
``cancel_salvage_break_for_salvager``'s own decision logic, mirroring
test_ship_registry_eject_board.py's fake-session convention.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.models.ship import ShipStatus, ShipType
from src.services.ship_registry_service import (
    SALVAGE_BREAK_DEFAULT_DURATION,
    SALVAGE_BREAK_DURATIONS,
    ShipRegistryError,
    _complete_salvage_break,
    build_salvage_break_started_event,
    cancel_salvage_break_for_salvager,
    list_sector_salvage_breaks,
    serialize_salvage_break_public,
    start_salvage_break,
)


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, *, ship_result=None):
        self._ship_result = ship_result
        self.flushed = False

    def query(self, entity):
        return _FakeQuery(self._ship_result)

    def flush(self):
        self.flushed = True


def make_player(**overrides):
    defaults = dict(id=uuid.uuid4(), current_sector_id=5)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_ship(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        name="Hull",
        type=ShipType.LIGHT_FREIGHTER,
        status=None,
        is_destroyed=False,
        sector_id=5,
        current_pilot_id=None,
        hatch_pin_code="ABC123",
        registration_number="REG-A47B-2103",
        salvage_break_in_progress_by_id=None,
        salvage_break_started_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- start_salvage_break -----------------------------------------------------

def test_escape_pod_cannot_be_salvaged():
    salvager = make_player()
    ship = make_ship(type=ShipType.ESCAPE_POD)
    with pytest.raises(ShipRegistryError) as exc:
        start_salvage_break(_FakeSession(), ship=ship, salvager=salvager)
    assert exc.value.code == "ERR_ESCAPE_POD_CANNOT_BE_SALVAGED"


def test_destroyed_ship_rejects():
    salvager = make_player()
    ship = make_ship(is_destroyed=True)
    with pytest.raises(ShipRegistryError) as exc:
        start_salvage_break(_FakeSession(), ship=ship, salvager=salvager)
    assert exc.value.code == "ERR_SHIP_DESTROYED"


def test_harmonizing_ship_rejects():
    salvager = make_player()
    ship = make_ship(status=ShipStatus.HARMONIZING)
    with pytest.raises(ShipRegistryError) as exc:
        start_salvage_break(_FakeSession(), ship=ship, salvager=salvager)
    assert exc.value.code == "ERR_SHIP_HARMONIZING"


def test_currently_piloted_ship_is_not_drifting_rejects():
    salvager = make_player()
    ship = make_ship(current_pilot_id=uuid.uuid4())
    with pytest.raises(ShipRegistryError) as exc:
        start_salvage_break(_FakeSession(), ship=ship, salvager=salvager)
    assert exc.value.code == "ERR_SHIP_NOT_DRIFTING"


def test_different_sector_rejects():
    salvager = make_player(current_sector_id=5)
    ship = make_ship(sector_id=9)
    with pytest.raises(ShipRegistryError) as exc:
        start_salvage_break(_FakeSession(), ship=ship, salvager=salvager)
    assert exc.value.code == "ERR_DIFFERENT_SECTOR"


def test_already_in_progress_by_another_salvager_rejects_with_eta():
    other_salvager_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    ship = make_ship(
        salvage_break_in_progress_by_id=other_salvager_id,
        salvage_break_started_at=started_at,
    )
    salvager = make_player()
    with pytest.raises(ShipRegistryError) as exc:
        start_salvage_break(_FakeSession(), ship=ship, salvager=salvager)
    assert exc.value.code == "ERR_SALVAGE_BREAK_IN_PROGRESS"
    assert str(other_salvager_id) in exc.value.message


def test_already_in_progress_by_the_same_salvager_also_rejects():
    """Re-calling while your OWN break is in progress must not silently
    restart the timer (a UI double-click footgun) -- treated as a flat
    conflict regardless of who owns the in-progress lock."""
    salvager = make_player()
    started_at = datetime.now(timezone.utc)
    ship = make_ship(
        salvage_break_in_progress_by_id=salvager.id,
        salvage_break_started_at=started_at,
    )
    with pytest.raises(ShipRegistryError) as exc:
        start_salvage_break(_FakeSession(), ship=ship, salvager=salvager)
    assert exc.value.code == "ERR_SALVAGE_BREAK_IN_PROGRESS"
    assert ship.salvage_break_started_at == started_at  # NOT reset


def test_successful_start_sets_lock_fields_and_flushes():
    salvager = make_player()
    ship = make_ship(type=ShipType.LIGHT_FREIGHTER)
    db = _FakeSession()

    result = start_salvage_break(db, ship=ship, salvager=salvager)

    assert ship.salvage_break_in_progress_by_id == salvager.id
    assert ship.salvage_break_started_at is not None
    assert db.flushed is True
    assert result["ship_id"] == str(ship.id)
    assert result["duration_seconds"] == int(timedelta(hours=1).total_seconds())


@pytest.mark.parametrize(
    "ship_type,expected_hours",
    [
        (ShipType.SCOUT_SHIP, 1),
        (ShipType.FAST_COURIER, 1),
        (ShipType.CITIZEN_CLIPPER, 1),
        (ShipType.LIGHT_FREIGHTER, 1),
        (ShipType.CARGO_HAULER, 4),
        (ShipType.DEFENDER, 4),
        (ShipType.COLONY_SHIP, 4),
        (ShipType.CARRIER, 12),
        (ShipType.WARP_JUMPER, 12),
    ],
)
def test_duration_matches_canon_class_table(ship_type, expected_hours):
    salvager = make_player()
    ship = make_ship(type=ship_type)
    result = start_salvage_break(_FakeSession(), ship=ship, salvager=salvager)
    assert result["duration_seconds"] == int(timedelta(hours=expected_hours).total_seconds())


def test_unmapped_ship_type_falls_back_to_default_duration():
    salvager = make_player()
    ship = make_ship(type=ShipType.NPC_MARSHAL_INTERDICTOR)
    result = start_salvage_break(_FakeSession(), ship=ship, salvager=salvager)
    assert result["duration_seconds"] == int(SALVAGE_BREAK_DEFAULT_DURATION.total_seconds())
    assert ShipType.NPC_MARSHAL_INTERDICTOR not in SALVAGE_BREAK_DURATIONS


# --- _complete_salvage_break --------------------------------------------------

def test_complete_clears_pin_and_lock_fields():
    ship = make_ship(
        hatch_pin_code="ABC123",
        salvage_break_in_progress_by_id=uuid.uuid4(),
        salvage_break_started_at=datetime.now(timezone.utc),
    )

    _complete_salvage_break(ship)

    assert ship.hatch_pin_code is None
    assert ship.salvage_break_in_progress_by_id is None
    assert ship.salvage_break_started_at is None


def test_complete_is_idempotent_on_an_already_clear_ship():
    ship = make_ship(hatch_pin_code=None)
    _complete_salvage_break(ship)  # must not raise
    assert ship.hatch_pin_code is None


# --- cancel_salvage_break_for_salvager ---------------------------------------

def test_cancel_clears_the_break_when_found():
    salvager_id = uuid.uuid4()
    ship = make_ship(
        salvage_break_in_progress_by_id=salvager_id,
        salvage_break_started_at=datetime.now(timezone.utc),
    )
    db = _FakeSession(ship_result=ship)

    cancel_salvage_break_for_salvager(db, salvager_id, reason="test")

    assert ship.salvage_break_in_progress_by_id is None
    assert ship.salvage_break_started_at is None
    assert db.flushed is True


def test_cancel_is_a_noop_when_no_break_found():
    db = _FakeSession(ship_result=None)
    cancel_salvage_break_for_salvager(db, uuid.uuid4(), reason="test")  # must not raise
    assert db.flushed is False


# --- LEG-333 peer visibility -------------------------------------------------


class _ListQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _ListSession:
    def __init__(self, ships):
        self._ships = ships

    def query(self, entity):
        return _ListQuery(self._ships)


def test_serialize_salvage_break_public_none_when_idle():
    assert serialize_salvage_break_public(make_ship()) is None


def test_peer_in_sector_sees_in_progress_break_and_eta():
    """Second player observes another hull's in-progress salvage-break via the
    sector presence contract (`list_sector_salvage_breaks`)."""
    salvager_id = uuid.uuid4()
    started = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    target = make_ship(
        type=ShipType.CARGO_HAULER,
        registration_number="REG-PEER-2102",
        salvage_break_in_progress_by_id=salvager_id,
        salvage_break_started_at=started,
        sector_id=42,
    )
    idle = make_ship(sector_id=42, registration_number="REG-IDLE-2102")
    db = _ListSession([target, idle])

    visible = list_sector_salvage_breaks(db, 42)

    assert len(visible) == 1
    peer_view = visible[0]
    assert peer_view["registration_number"] == "REG-PEER-2102"
    assert peer_view["salvage_break_in_progress_by_id"] == str(salvager_id)
    assert peer_view["eta_hours"] == 4.0
    assert peer_view["completes_at"] == (started + timedelta(hours=4)).isoformat()
    assert peer_view["ship_id"] == str(target.id)


def test_salvage_break_started_ws_event_carries_peer_fields():
    salvager_id = uuid.uuid4()
    started = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    ship = make_ship(
        salvage_break_in_progress_by_id=salvager_id,
        salvage_break_started_at=started,
        registration_number="REG-WS-2102",
    )
    event = build_salvage_break_started_event(ship, sector_id=7)
    assert event["type"] == "salvage_break_started"
    assert event["sector_id"] == 7
    assert event["registration_number"] == "REG-WS-2102"
    assert event["salvage_break_in_progress_by_id"] == str(salvager_id)
    assert "completes_at" in event
    assert "eta_hours" in event
