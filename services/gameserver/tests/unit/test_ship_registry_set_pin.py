"""WO-BUILD-SHIP-PIN-VISIBILITY-AND-SET / WO-BUILD-SHIP-PIN-PORT-RESET-DELAYED
-- hatch-pin changing + port-gated delayed reset (ship-registry.md "Hatch pin
lock": "the current pilot -- owner OR borrower -- can change the pin while
aboard. A borrower who changes the pin locks the owner out of their own
ship; the owner's recourse is to file a stolen report." / "Pin recovery: the
registered owner can always reset the pin via a port admin action (1-hour
real-time delay before the new pin takes effect...)").

set_pin deliberately does NOT special-case owner vs borrower -- both may
change the pin, with the borrower-lockout consequence being the
canon-intended (if adversarial) outcome, not a bug to guard against here.
request_pin_reset is the separate recovery path for an owner who is NOT the
current pilot (their ship could be anywhere) and needs the registration
alone to regain control -- port-gated, delayed, not instant.

DB-free: neither set_pin nor request_pin_reset issues queries of its own (no
old-ship lookup, unlike eject/board), so a bare stub with a no-op flush() is
enough.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.services.ship_registry_service import (
    PIN_RESET_DELAY,
    ShipRegistryError,
    _apply_pending_pin_reset,
    request_pin_reset,
    set_pin,
)


class _FakeSession:
    def flush(self):
        pass


def make_player(**overrides):
    defaults = dict(id=uuid.uuid4(), is_docked=False, current_port_id=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_ship(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        current_pilot_id=None,
        registered_owner_id=uuid.uuid4(),
        hatch_pin_code="ABC123",
        pending_hatch_pin_code=None,
        pending_hatch_pin_effective_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_set_pin_not_current_pilot_rejects():
    player = make_player()
    ship = make_ship(current_pilot_id=uuid.uuid4())  # someone else aboard
    with pytest.raises(ShipRegistryError) as exc:
        set_pin(_FakeSession(), ship=ship, player=player, new_pin="NEWPIN1")
    assert exc.value.code == "ERR_NOT_CURRENT_PILOT"
    assert ship.hatch_pin_code == "ABC123"  # unchanged


def test_set_pin_nobody_aboard_rejects():
    player = make_player()
    ship = make_ship(current_pilot_id=None)
    with pytest.raises(ShipRegistryError) as exc:
        set_pin(_FakeSession(), ship=ship, player=player, new_pin="NEWPIN1")
    assert exc.value.code == "ERR_NOT_CURRENT_PILOT"


@pytest.mark.parametrize("bad_pin", ["", "AB", "ABC", "ABCDEFGHI", "AB-123", "AB 123"])
def test_set_pin_malformed_rejects(bad_pin):
    player = make_player()
    ship = make_ship(current_pilot_id=player.id)
    with pytest.raises(ShipRegistryError) as exc:
        set_pin(_FakeSession(), ship=ship, player=player, new_pin=bad_pin)
    assert exc.value.code == "ERR_INVALID_PIN"
    assert ship.hatch_pin_code == "ABC123"  # unchanged


def test_set_pin_owner_aboard_succeeds_and_normalizes_uppercase():
    player = make_player()
    ship = make_ship(current_pilot_id=player.id, registered_owner_id=player.id)
    result = set_pin(_FakeSession(), ship=ship, player=player, new_pin="newpin1")
    assert result == {"ship_id": str(ship.id), "hatch_pin_code": "NEWPIN1"}
    assert ship.hatch_pin_code == "NEWPIN1"


def test_set_pin_borrower_aboard_can_lock_out_the_owner():
    owner_id = uuid.uuid4()
    borrower = make_player()
    ship = make_ship(current_pilot_id=borrower.id, registered_owner_id=owner_id)
    result = set_pin(_FakeSession(), ship=ship, player=borrower, new_pin="LOCK99")
    assert result["hatch_pin_code"] == "LOCK99"
    assert ship.hatch_pin_code == "LOCK99"  # owner no longer knows this pin


@pytest.mark.parametrize("length", [4, 8])
def test_set_pin_accepts_boundary_lengths(length):
    player = make_player()
    ship = make_ship(current_pilot_id=player.id)
    pin = "A" * length
    result = set_pin(_FakeSession(), ship=ship, player=player, new_pin=pin)
    assert result["hatch_pin_code"] == pin


# --- request_pin_reset -----------------------------------------------------

_PORT_ID = uuid.uuid4()


def test_request_pin_reset_not_registered_owner_rejects():
    owner = make_player()
    stranger = make_player()
    ship = make_ship(registered_owner_id=owner.id)
    with pytest.raises(ShipRegistryError) as exc:
        request_pin_reset(_FakeSession(), ship=ship, owner=stranger, port_id=_PORT_ID, new_pin="NEWPIN1")
    assert exc.value.code == "ERR_NOT_REGISTERED_OWNER"
    assert ship.pending_hatch_pin_effective_at is None


def test_request_pin_reset_not_at_port_rejects():
    owner = make_player(is_docked=False, current_port_id=None)
    ship = make_ship(registered_owner_id=owner.id)
    with pytest.raises(ShipRegistryError) as exc:
        request_pin_reset(_FakeSession(), ship=ship, owner=owner, port_id=_PORT_ID, new_pin="NEWPIN1")
    assert exc.value.code == "ERR_NOT_AT_PORT"


def test_request_pin_reset_wrong_port_rejects():
    other_port = uuid.uuid4()
    owner = make_player(is_docked=True, current_port_id=other_port)
    ship = make_ship(registered_owner_id=owner.id)
    with pytest.raises(ShipRegistryError) as exc:
        request_pin_reset(_FakeSession(), ship=ship, owner=owner, port_id=_PORT_ID, new_pin="NEWPIN1")
    assert exc.value.code == "ERR_NOT_AT_PORT"


def test_request_pin_reset_already_pending_rejects():
    owner = make_player(is_docked=True, current_port_id=_PORT_ID)
    ship = make_ship(
        registered_owner_id=owner.id,
        pending_hatch_pin_effective_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    with pytest.raises(ShipRegistryError) as exc:
        request_pin_reset(_FakeSession(), ship=ship, owner=owner, port_id=_PORT_ID, new_pin="NEWPIN1")
    assert exc.value.code == "ERR_PIN_RESET_ALREADY_PENDING"


def test_request_pin_reset_malformed_pin_rejects():
    owner = make_player(is_docked=True, current_port_id=_PORT_ID)
    ship = make_ship(registered_owner_id=owner.id)
    with pytest.raises(ShipRegistryError) as exc:
        request_pin_reset(_FakeSession(), ship=ship, owner=owner, port_id=_PORT_ID, new_pin="AB")
    assert exc.value.code == "ERR_INVALID_PIN"
    assert ship.pending_hatch_pin_effective_at is None


def test_request_pin_reset_success_sets_pending_columns_and_leaves_live_pin_untouched():
    owner = make_player(is_docked=True, current_port_id=_PORT_ID)
    ship = make_ship(registered_owner_id=owner.id, hatch_pin_code="OLDPIN1")

    before = datetime.now(timezone.utc)
    result = request_pin_reset(_FakeSession(), ship=ship, owner=owner, port_id=_PORT_ID, new_pin="newpin2")
    after = datetime.now(timezone.utc)

    assert ship.hatch_pin_code == "OLDPIN1"  # live pin unchanged until the sweep applies it
    assert ship.pending_hatch_pin_code == "NEWPIN2"
    assert ship.pending_hatch_pin_effective_at is not None
    assert before + PIN_RESET_DELAY <= ship.pending_hatch_pin_effective_at <= after + PIN_RESET_DELAY
    assert result["ship_id"] == str(ship.id)
    assert result["effective_at"] == ship.pending_hatch_pin_effective_at.isoformat()


# --- _apply_pending_pin_reset -----------------------------------------------

def test_apply_pending_pin_reset_copies_and_clears():
    ship = make_ship(
        hatch_pin_code="OLDPIN1",
        pending_hatch_pin_code="NEWPIN2",
        pending_hatch_pin_effective_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    _apply_pending_pin_reset(ship)
    assert ship.hatch_pin_code == "NEWPIN2"
    assert ship.pending_hatch_pin_code is None
    assert ship.pending_hatch_pin_effective_at is None
